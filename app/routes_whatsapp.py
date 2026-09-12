"""Fatia 7 (migração): área de WhatsApp — paciente conversa com o número
único da aplicação para ver informações do preparo do exame e fazer
perguntas, agora usando a API direta da Meta (WhatsApp Cloud API) em vez
da Twilio (ver PLANO_WHATSAPP.md, seção "Migração para Meta Cloud API
direta", para o histórico da decisão).

Diferenças principais em relação ao webhook da Twilio que este arquivo
substituiu:
- A Meta exige um "handshake" de verificação via GET antes de aceitar o
  webhook (ver `webhook_verificar` abaixo) — cadastrado uma única vez em
  WhatsApp Manager > Configuração > Webhooks, e de novo sempre que a URL
  mudar.
- A assinatura de cada requisição POST vem no cabeçalho
  "X-Hub-Signature-256" (HMAC-SHA256 sobre o CORPO CRU da requisição,
  usando o App Secret) — diferente da Twilio, que assinava URL+parâmetros
  do formulário com HMAC-SHA1.
- A Meta NÃO aceita devolver o texto da resposta dentro do corpo da
  resposta do webhook (como o TwiML da Twilio permitia) — toda resposta
  precisa ser enviada por uma chamada separada à Graph API (ver
  app/whatsapp_envio.py:enviar_mensagem_whatsapp), DEPOIS de já ter
  devolvido 200 para o webhook.
- O payload é JSON (não form-encoded), com uma estrutura aninhada
  (entry -> changes -> value -> messages) — ver `_extrair_mensagens`.
  A mesma URL também recebe notificações de status de entrega/leitura
  (`value.statuses`, sem `value.messages`) — são ignoradas.
- Mensagem de ÁUDIO (pedido do Silvan, 2026-09-12): é baixada da Graph
  API e transcrita por IA (Whisper da OpenAI, ver app.whatsapp_audio) - o
  texto reconhecido é tratado como se o paciente tivesse digitado aquilo,
  sem duplicar nenhuma lógica de conversa. Antes disso, áudio era
  simplesmente ignorado (nem uma orientação de volta). Outros tipos
  (imagem, vídeo, figurinha, botão etc.) continuam ignorados.

A lógica de conversa em si (identificação por CPF + data de nascimento,
menu de opções) mora em app/whatsapp_conversa.py, sem depender de
Flask/Meta - este arquivo só faz a ponte com o provedor.

Configuração necessária (variáveis de ambiente — nunca em código nem no
repositório, ver .env.example):
- WHATSAPP_META_VERIFY_TOKEN: string arbitrária escolhida pelo Silvan,
  cadastrada IGUAL nos dois lados (aqui e no campo "Verify token" do
  WhatsApp Manager) — usada só no handshake inicial (GET) para confirmar
  que quem está configurando o webhook é realmente o dono da aplicação.
- WHATSAPP_META_APP_SECRET: o "App Secret" do app Meta (Meta for
  Developers > seu app > Configurações básicas) — usado para validar a
  assinatura de cada webhook recebido (cabeçalho X-Hub-Signature-256).
  Sem essa variável, o webhook recusa QUALQUER requisição POST (falha
  fechada: sem conseguir validar, nunca assume que é confiável).

Ver também app/whatsapp_envio.py para as variáveis de ENVIO
(WHATSAPP_META_ACCESS_TOKEN, WHATSAPP_META_PHONE_NUMBER_ID etc.)."""
import hashlib
import hmac
import os

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import WhatsappMensagemProcessada
from app.whatsapp_audio import MENSAGEM_AUDIO_NAO_TRANSCRITO, texto_de_audio_whatsapp
from app.whatsapp_conversa import normalizar_telefone_whatsapp, processar_mensagem
from app.whatsapp_envio import enviar_mensagem_whatsapp

whatsapp_bp = Blueprint("whatsapp", __name__, url_prefix="/whatsapp")


def _assinatura_valida():
    """Confere o cabeçalho X-Hub-Signature-256 (HMAC-SHA256 do CORPO CRU
    da requisição, usando o App Secret) — protege o webhook contra
    requisições forjadas por terceiros que descubram a URL. Falha
    fechada: sem WHATSAPP_META_APP_SECRET configurado, nenhuma requisição
    é aceita."""
    app_secret = os.environ.get("WHATSAPP_META_APP_SECRET")
    if not app_secret:
        current_app.logger.warning(
            "Webhook de WhatsApp recebido, mas WHATSAPP_META_APP_SECRET "
            "não está configurado — recusando por segurança."
        )
        return False

    assinatura_header = request.headers.get("X-Hub-Signature-256", "")
    if not assinatura_header.startswith("sha256="):
        return False
    assinatura_recebida = assinatura_header[len("sha256="):]

    assinatura_esperada = hmac.new(
        app_secret.encode("utf-8"), request.get_data(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(assinatura_recebida, assinatura_esperada)


def _extrair_mensagens(payload):
    """Percorre a estrutura aninhada do payload da Meta
    (entry[].changes[].value.messages[]) e devolve uma lista de tuplas
    (telefone, texto, mensagem_id, media_id_audio) - uma por mensagem de
    TEXTO ou de ÁUDIO (pedido do Silvan, 2026-09-12 - ver
    app.whatsapp_audio; antes disso, áudio era ignorado em silêncio, sem
    nenhuma orientação pro paciente). Para texto, `texto` já vem
    preenchido e `media_id_audio` é None; para áudio, `texto` vem None e
    `media_id_audio` traz o id de mídia que o `webhook()` usa pra baixar
    e transcrever (ver app.whatsapp_audio.texto_de_audio_whatsapp) - a
    transcrição em si (rede + IA) não acontece AQUI, pra esta função
    continuar sendo pura leitura do payload já recebido, sem depender de
    rede. Outros tipos (imagem, vídeo, figurinha, botão etc.) e
    notificações de status de entrega/leitura (que vêm em
    `value.statuses`, sem `value.messages`) continuam ignorados
    silenciosamente. `mensagem_id` é o campo "id" que a Meta inclui em
    toda mensagem (único por mensagem) - usado só para dedupe de
    reentrega do webhook, ver `_mensagem_ja_processada`."""
    mensagens = []
    for entrada in payload.get("entry", []):
        for mudanca in entrada.get("changes", []):
            valor = mudanca.get("value", {})
            for msg in valor.get("messages", []):
                telefone = normalizar_telefone_whatsapp(msg.get("from"))
                if not telefone:
                    continue
                mensagem_id = msg.get("id")
                tipo = msg.get("type")
                if tipo == "text":
                    mensagens.append((telefone, msg.get("text", {}).get("body", ""), mensagem_id, None))
                elif tipo == "audio":
                    mensagens.append((telefone, None, mensagem_id, msg.get("audio", {}).get("id")))
    return mensagens


def _mensagem_ja_processada(mensagem_id):
    """True se este id de mensagem (ver `_extrair_mensagens_de_texto`) já
    tinha sido registrado antes - protege contra a Meta reentregar o
    mesmo webhook mais de uma vez (comportamento documentado da Cloud
    API, sobretudo quando o processamento demora para devolver 200; ver
    docstring de `app.models.WhatsappMensagemProcessada` para o bug real
    que isso causava: o mesmo aviso chegando duplicado ao paciente).

    Sem `mensagem_id` (não deveria acontecer - a Meta sempre inclui esse
    campo em toda mensagem), processa normalmente: não dá pra dedupar o
    que não tem identificador, e falhar fechado aqui bloquearia mensagens
    de verdade sem necessidade.

    O INSERT já serve como o próprio lock contra corrida entre duas
    requisições quase simultâneas para o mesmíssimo id (webhooks
    reentregues muito próximos um do outro): a segunda cai no
    IntegrityError da constraint UNIQUE e também é tratada como
    duplicada, mesmo que a primeira SELECT (acima) não tivesse
    encontrado nada ainda."""
    if not mensagem_id:
        return False
    if WhatsappMensagemProcessada.query.filter_by(mensagem_id=mensagem_id).first():
        return True
    db.session.add(WhatsappMensagemProcessada(mensagem_id=mensagem_id))
    try:
        db.session.commit()
        return False
    except IntegrityError:
        db.session.rollback()
        return True


@whatsapp_bp.route("/webhook", methods=["GET"])
def webhook_verificar():
    """Handshake de verificação exigido pela Meta ao cadastrar (ou
    recadastrar) a URL do webhook em WhatsApp Manager — precisa devolver
    exatamente o valor de "hub.challenge" (texto puro, sem JSON) quando
    "hub.verify_token" bate com o configurado; qualquer outra coisa e a
    Meta recusa o cadastro do webhook."""
    modo = request.args.get("hub.mode")
    token_recebido = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge", "")

    token_esperado = os.environ.get("WHATSAPP_META_VERIFY_TOKEN")
    if modo == "subscribe" and token_esperado and token_recebido == token_esperado:
        return challenge, 200
    current_app.logger.warning("Verificação de webhook do WhatsApp recusada (token de verificação não confere).")
    return "Token de verificação inválido", 403


@whatsapp_bp.route("/webhook", methods=["POST"])
def webhook():
    """Recebe cada mensagem enviada ao número de WhatsApp da aplicação,
    valida a assinatura e repassa para app.whatsapp_conversa (identificação
    por CPF + data de nascimento) — diferente da Twilio, a resposta não
    volta no corpo desta requisição: é enviada por uma chamada separada à
    Graph API (ver app.whatsapp_envio.enviar_mensagem_whatsapp), depois de
    já ter devolvido 200 aqui.

    Sempre responde 200, mesmo quando recusa por assinatura inválida ou
    ausente, ou quando o payload não traz nenhuma mensagem de texto/áudio
    — devolver um erro HTTP faria a Meta reentregar a mesma notificação
    várias vezes, achando que falhou. Mesmo respondendo 200 rápido, a
    Meta às vezes reentrega o mesmo webhook de qualquer forma (bug
    relatado pelo Silvan, 2026-09-11: o mesmo aviso chegando duplicado ao
    paciente) - por isso cada mensagem é dedupada pelo próprio id que a
    Meta atribui a ela antes de processar (ver `_mensagem_ja_processada`
    e `app.models.WhatsappMensagemProcessada`)."""
    if not _assinatura_valida():
        current_app.logger.warning("Webhook de WhatsApp recusado: assinatura inválida ou ausente.")
        return jsonify(ok=True)

    payload = request.get_json(silent=True) or {}
    for telefone, corpo, mensagem_id, media_id_audio in _extrair_mensagens(payload):
        if _mensagem_ja_processada(mensagem_id):
            current_app.logger.info(
                "WhatsApp: mensagem %s (de %s) já tinha sido processada antes - "
                "ignorando reentrega do webhook da Meta.", mensagem_id, telefone,
            )
            continue

        if media_id_audio:
            # Pedido do Silvan (2026-09-12): transcreve o áudio (ver
            # app.whatsapp_audio) e trata o texto reconhecido exatamente
            # como se o paciente tivesse digitado aquilo - mesma
            # "falha aberta" do resto do módulo: sem transcrição (sem
            # OPENAI_API_KEY/WHATSAPP_META_ACCESS_TOKEN configuradas, ou
            # qualquer erro na chamada), avisa o paciente a escrever em
            # vez de processar um texto vazio ou ficar em silêncio.
            corpo = texto_de_audio_whatsapp(media_id_audio)
            if not corpo:
                current_app.logger.info(
                    "WhatsApp: não foi possível transcrever o áudio de %s (media_id=%s).",
                    telefone, media_id_audio,
                )
                enviar_mensagem_whatsapp(telefone, MENSAGEM_AUDIO_NAO_TRANSCRITO)
                continue
            current_app.logger.info("WhatsApp: áudio de %s transcrito como: %r", telefone, corpo)

        current_app.logger.info("WhatsApp recebido de %s: %r", telefone, corpo)
        resposta = processar_mensagem(telefone, corpo)
        if resposta:
            # Dentro da janela de 24h (o paciente acabou de escrever),
            # então sempre tenta como texto livre - sem
            # WHATSAPP_META_ACCESS_TOKEN/WHATSAPP_META_PHONE_NUMBER_ID
            # configuradas, é apenas pulado (ver docstring do módulo).
            enviar_mensagem_whatsapp(telefone, resposta)

    return jsonify(ok=True)
