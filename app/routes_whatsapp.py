"""Fatia 7: área de WhatsApp — paciente conversa com o número único da
aplicação para ver informações do preparo do exame e fazer perguntas.

Passo 2 do plano: o webhook recebendo mensagens do provedor (Twilio) e
validando a assinatura de cada requisição.
Passo 3 (este arquivo): a mensagem validada é encaminhada para
app/whatsapp_conversa.py, que identifica o paciente por CPF + data de
nascimento e resolve qual exame está em foco na conversa — a lógica de
conversa em si mora lá (sem depender de Flask/Twilio), este arquivo só
faz a ponte com o provedor.
Ainda faltam (próximos passos do plano): "ver informações do preparo" e
"fazer uma pergunta" de verdade (por ora a resposta é só de identificação,
ver app/whatsapp_conversa.py).

Configuração necessária (variáveis de ambiente — nunca em código nem no
repositório, ver .env.example):
- TWILIO_AUTH_TOKEN: usado para validar a assinatura de cada webhook
  recebido (cabeçalho X-Twilio-Signature) — sem essa variável configurada,
  o webhook recusa QUALQUER requisição (falha fechada: sem conseguir
  validar, nunca assume que é confiável).
- WHATSAPP_URL_PUBLICA (opcional): URL pública completa deste webhook
  (ex.: "https://media.inflor.com.br/whatsapp/webhook"), usada para
  montar a mesma URL que a Twilio usou para assinar a requisição. Só é
  necessária quando o app roda atrás de um proxy/load balancer que não
  preserva o "https://" original em request.url — que é o caso comum do
  Elastic Beanstalk (o Application Load Balancer termina o TLS e repassa
  para a instância em HTTP puro). Sem essa variável, usa request.url
  direto, o que funciona em desenvolvimento local (sem proxy no meio).
  Se a validação da assinatura estiver falhando em produção mesmo com o
  Auth Token certo, o motivo mais provável é esse — configurar essa
  variável resolve.
"""
import os

from flask import Blueprint, current_app, request

from app.whatsapp_conversa import normalizar_telefone_whatsapp, processar_mensagem

whatsapp_bp = Blueprint("whatsapp", __name__, url_prefix="/whatsapp")


def _url_validacao():
    """URL completa usada para validar a assinatura Twilio — ver nota
    sobre WHATSAPP_URL_PUBLICA no topo deste arquivo."""
    base = os.environ.get("WHATSAPP_URL_PUBLICA")
    if base:
        return base.rstrip("/") + "/whatsapp/webhook"
    return request.url


def _assinatura_valida():
    """Confere o cabeçalho X-Twilio-Signature do jeito recomendado pela
    Twilio (HMAC-SHA1 da URL + parâmetros do POST, usando o Auth Token) —
    protege o webhook contra requisições forjadas por terceiros que
    descubram a URL. Falha fechada: sem TWILIO_AUTH_TOKEN configurado (ou
    sem o pacote "twilio" instalado), nenhuma requisição é aceita."""
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    if not auth_token:
        current_app.logger.warning(
            "Webhook de WhatsApp recebido, mas TWILIO_AUTH_TOKEN não está "
            "configurado — recusando por segurança."
        )
        return False
    try:
        from twilio.request_validator import RequestValidator
    except ImportError:
        current_app.logger.warning(
            "Webhook de WhatsApp recebido, mas o pacote \"twilio\" não está "
            "instalado — recusando."
        )
        return False

    validador = RequestValidator(auth_token)
    assinatura = request.headers.get("X-Twilio-Signature", "")
    return validador.validate(_url_validacao(), request.form.to_dict(), assinatura)


def _twiml(texto=""):
    """Monta a resposta TwiML mínima - com ou sem um <Message> dentro,
    escapando o texto (evita quebrar o XML se a resposta tiver "&", "<" etc,
    algo que pode acontecer com nome de exame/paciente)."""
    from xml.sax.saxutils import escape
    corpo = f"<Message>{escape(texto)}</Message>" if texto else ""
    return (f"<Response>{corpo}</Response>", 200, {"Content-Type": "text/xml"})


@whatsapp_bp.route("/webhook", methods=["POST"])
def webhook():
    """Recebe cada mensagem enviada ao número de WhatsApp da aplicação,
    valida a assinatura e repassa para app.whatsapp_conversa (identificação
    por CPF + data de nascimento, passo 3 do plano) — a resposta que a
    lógica de conversa devolver é enviada de volta pelo mesmo canal.

    Sempre responde 200, mesmo quando recusa por assinatura inválida ou
    ausente — devolver um erro HTTP faria a Twilio reentregar a mesma
    mensagem várias vezes, achando que falhou."""
    if not _assinatura_valida():
        current_app.logger.warning("Webhook de WhatsApp recusado: assinatura inválida ou ausente.")
        return _twiml()

    telefone = normalizar_telefone_whatsapp(request.form.get("From", ""))
    corpo = request.form.get("Body", "")
    current_app.logger.info("WhatsApp recebido de %s: %r", telefone, corpo)

    if not telefone:
        current_app.logger.warning("Webhook de WhatsApp sem remetente (\"From\") - ignorado.")
        return _twiml()

    resposta = processar_mensagem(telefone, corpo)
    return _twiml(resposta)
