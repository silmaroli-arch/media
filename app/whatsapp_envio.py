"""Fatia 7 (ajuste): envio PROATIVO de mensagem pelo WhatsApp - usado só
para mandar de volta, automaticamente, a resposta de uma pergunta que o
paciente fez por esse canal (ver app.routes_medico.perguntas_responder),
assim que o médico/equipe aprovar/responder. É o único lugar do projeto
que INICIA uma mensagem (tudo em app/routes_whatsapp.py e
app/whatsapp_conversa.py é sobre RECEBER e responder dentro do mesmo
webhook, o que a API da Twilio trata como "resposta", não como uma
mensagem nova).

Configuração necessária (variáveis de ambiente — nunca em código nem no
repositório, ver .env.example):
- TWILIO_ACCOUNT_SID: identifica a conta Twilio (sempre começa com "AC").
- TWILIO_AUTH_TOKEN: a mesma já usada para validar o webhook (ver
  app/routes_whatsapp.py) - aqui autentica a chamada de ENVIO.
- TWILIO_WHATSAPP_FROM: o número de origem, no formato "whatsapp:+1...”
  (ex.: "whatsapp:+14155238886", o número do Sandbox em desenvolvimento -
  em produção, o número comercial aprovado da clínica/empresa).

Sem qualquer uma dessas três configuradas, o envio é apenas PULADO (a
resposta continua salva normalmente no sistema, só não sai pelo
WhatsApp) - o paciente pode sempre ver a resposta acessando a área web,
então a ausência de configuração aqui nunca impede o fluxo de responder
perguntas."""
import os

from flask import current_app


def enviar_mensagem_whatsapp(telefone_destino, texto):
    """Manda `texto` para `telefone_destino` (formato "+5527999998888",
    sem o prefixo "whatsapp:" - ver normalizar_telefone_whatsapp) usando a
    API da Twilio. Nunca levanta exceção para quem chama - qualquer falha
    (configuração ausente, erro de rede, número inválido etc.) só é
    registrada no log, porque isso roda depois que a resposta da pergunta
    já foi salva com sucesso: uma falha de envio não pode desfazer nem
    bloquear essa gravação, já que a resposta continua acessível pela
    área web do paciente de qualquer forma."""
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    numero_origem = os.environ.get("TWILIO_WHATSAPP_FROM")

    if not (account_sid and auth_token and numero_origem):
        current_app.logger.info(
            "Envio de WhatsApp pulado (TWILIO_ACCOUNT_SID/TWILIO_WHATSAPP_FROM "
            "não configurados) - a resposta continua disponível na área web."
        )
        return False

    if not telefone_destino:
        current_app.logger.warning("Envio de WhatsApp pulado: sem telefone de destino.")
        return False

    try:
        from twilio.rest import Client
    except ImportError:
        current_app.logger.warning("Envio de WhatsApp pulado: pacote \"twilio\" não instalado.")
        return False

    try:
        cliente = Client(account_sid, auth_token)
        cliente.messages.create(
            from_=numero_origem,
            to=f"whatsapp:{telefone_destino}",
            body=texto,
        )
        return True
    except Exception:
        # Falha de rede/API/número inválido etc. - não propaga (ver
        # docstring), só registra para investigação.
        current_app.logger.exception("Falha ao enviar mensagem de WhatsApp para %s", telefone_destino)
        return False
