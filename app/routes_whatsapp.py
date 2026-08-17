"""Fatia 7: área de WhatsApp — paciente conversa com o número único da
aplicação para ver informações do preparo do exame e fazer perguntas.

Passo 2 do plano (ver PLANO_WHATSAPP.md): só o webhook recebendo mensagens
do provedor (Twilio) e validando a assinatura de cada requisição — ainda
NÃO lê/grava Paciente/ConversaWhatsapp nem responde nada além de um TwiML
vazio. Serve para confirmar a conectividade ponta-a-ponta (Twilio → este
endpoint) antes de escrever a lógica de identificação por CPF + data de
nascimento (passo 3) e o resto do fluxo de conversa (passos 4 e 5).

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


@whatsapp_bp.route("/webhook", methods=["POST"])
def webhook():
    """Recebe cada mensagem enviada ao número de WhatsApp da aplicação.

    Passo 2 do plano: só valida a assinatura e loga a mensagem recebida —
    nenhuma lógica de identificação/resposta ainda (vem nos próximos
    passos). Sempre responde 200 com um TwiML vazio, mesmo quando recusa
    por assinatura inválida/ausente — devolver um erro HTTP faria a Twilio
    reentregar a mesma mensagem várias vezes, achando que falhou."""
    if not _assinatura_valida():
        current_app.logger.warning("Webhook de WhatsApp recusado: assinatura inválida ou ausente.")
        return ("<Response></Response>", 200, {"Content-Type": "text/xml"})

    remetente = request.form.get("From", "")
    corpo = request.form.get("Body", "")
    current_app.logger.info(
        "WhatsApp recebido de %s: %r (passo 2 do plano — ainda sem resposta automática)",
        remetente, corpo,
    )

    return ("<Response></Response>", 200, {"Content-Type": "text/xml"})
