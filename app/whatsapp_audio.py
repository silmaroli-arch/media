"""Transcrição de áudios recebidos por WhatsApp (pedido do Silvan,
2026-09-12): antes desta função, uma mensagem de áudio era simplesmente
IGNORADA em silêncio (ver docstring antiga de `_extrair_mensagens_de_texto`
em app/routes_whatsapp.py, que só reconhecia `type == "text"`) - o
paciente não recebia nem uma orientação de volta. Agora o áudio é baixado
da Graph API e transcrito por IA (Whisper da OpenAI), e o TEXTO resultante
é tratado exatamente como se o paciente tivesse digitado aquilo -
reaproveita 100% da lógica já existente em
app.whatsapp_conversa.processar_mensagem (identificação, menu, perguntas
etc.), sem duplicar nada (ver app/routes_whatsapp.py:webhook).

Configuração necessária:
- WHATSAPP_META_ACCESS_TOKEN: já usado pelo resto do módulo de WhatsApp
  para ENVIAR mensagens (ver app/whatsapp_envio.py) - usado aqui também
  para BAIXAR o arquivo de áudio da Graph API (as duas chamadas, buscar a
  URL temporária da mídia e baixar o arquivo em si, exigem o mesmo Bearer
  token).
- OPENAI_API_KEY: já usado pelo chat de dúvidas do paciente (ver
  app.ia_preparo._cliente_openai) - usado aqui para transcrever o áudio
  via Whisper (endpoint de transcrição da OpenAI, `whisper-1`).

"Falha aberta" no mesmo padrão do resto do módulo de WhatsApp: sem
qualquer uma das duas variáveis configurada, ou se qualquer etapa falhar
por qualquer motivo (áudio corrompido, formato não reconhecido, chamada à
API fora do ar, timeout etc.), `texto_de_audio_whatsapp` devolve None -
quem chama (ver app/routes_whatsapp.py:webhook) manda de volta
`MENSAGEM_AUDIO_NAO_TRANSCRITO` nesse caso, pedindo pro paciente escrever,
em vez de travar o webhook ou deixar a pessoa sem resposta nenhuma."""
import os

from flask import current_app

from app.whatsapp_envio import GRAPH_API_BASE

MENSAGEM_AUDIO_NAO_TRANSCRITO = (
    "Não conseguimos entender esse áudio. Pode escrever sua mensagem em texto, por favor?"
)

# Limite de tamanho aceito para transcrição (bytes) - mensagens de voz do
# WhatsApp raramente passam de 1-2 MB (o próprio app já compacta bastante
# o áudio); um limite generoso aqui é só para não gastar tempo/custo
# tentando transcrever um arquivo enorme por engano (a Whisper da OpenAI
# também tem seu próprio limite de 25 MB por chamada).
TAMANHO_MAXIMO_AUDIO_BYTES = 20 * 1024 * 1024

# A Meta manda a mensagem de voz do WhatsApp quase sempre como
# "audio/ogg; codecs=opus" - a lib da OpenAI exige um NOME de arquivo com
# extensão pra saber como decodificar (não aceita só os bytes crus), daí
# esta tabela; ".ogg" é o padrão de reserva quando o mime_type não bate
# com nada conhecido aqui.
_EXTENSAO_POR_MIME = {
    "audio/ogg": "ogg",
    "audio/opus": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp4": "mp4",
    "audio/amr": "amr",
    "audio/aac": "aac",
}


def _cliente_openai():
    """Mesmo padrão de app.ia_preparo._cliente_openai - nunca deixa a
    falta da lib/chave, ou um erro ao construir o client, derrubar o
    webhook do WhatsApp."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        import openai
        return openai.OpenAI(api_key=api_key)
    except Exception:
        return None


def _baixar_audio_whatsapp(media_id):
    """Baixa o arquivo de áudio da Graph API a partir do id de mídia que
    vem no payload do webhook (`value.messages[].audio.id`) - são duas
    chamadas: a primeira devolve uma URL temporária (e o mime_type) para
    a mídia, a segunda baixa o arquivo em si - as duas exigem o mesmo
    Bearer token de acesso usado pelo resto do módulo (ver
    app.whatsapp_envio.enviar_mensagem_whatsapp). Devolve (bytes,
    mime_type), ou (None, None) em qualquer falha (sem token configurado,
    id inexistente/expirado, arquivo maior que TAMANHO_MAXIMO_AUDIO_BYTES,
    erro de rede etc.)."""
    access_token = os.environ.get("WHATSAPP_META_ACCESS_TOKEN")
    if not access_token:
        return None, None

    import requests

    api_version = os.environ.get("WHATSAPP_META_API_VERSION", "v22.0")
    cabecalho = {"Authorization": f"Bearer {access_token}"}
    try:
        resposta_info = requests.get(
            f"{GRAPH_API_BASE}/{api_version}/{media_id}", headers=cabecalho, timeout=10,
        )
        resposta_info.raise_for_status()
        info = resposta_info.json()
        url_midia = info.get("url")
        tamanho = info.get("file_size")
        if not url_midia or (tamanho and tamanho > TAMANHO_MAXIMO_AUDIO_BYTES):
            return None, None

        resposta_arquivo = requests.get(url_midia, headers=cabecalho, timeout=20)
        resposta_arquivo.raise_for_status()
        return resposta_arquivo.content, info.get("mime_type")
    except Exception:
        current_app.logger.exception("Falha ao baixar áudio do WhatsApp (media_id=%s).", media_id)
        return None, None


def transcrever_audio(audio_bytes, mime_type):
    """Manda os bytes do áudio para a Whisper da OpenAI (endpoint de
    transcrição) e devolve o texto reconhecido, ou None em qualquer falha
    (sem OPENAI_API_KEY configurada, erro de rede, áudio ilegível etc.).
    `language="pt"` de propósito - toda a aplicação (e o público dela) é
    em português do Brasil, e forçar o idioma evita que a Whisper tente
    "adivinhar" errado em áudios curtos ou com ruído."""
    cliente = _cliente_openai()
    if not cliente:
        return None

    extensao = next(
        (ext for mime, ext in _EXTENSAO_POR_MIME.items() if mime_type and mime_type.startswith(mime)),
        "ogg",
    )
    try:
        resposta = cliente.audio.transcriptions.create(
            model="whisper-1",
            file=("audio." + extensao, audio_bytes),
            language="pt",
        )
        return (resposta.text or "").strip() or None
    except Exception:
        current_app.logger.exception("Falha ao transcrever áudio do WhatsApp via Whisper.")
        return None


def texto_de_audio_whatsapp(media_id):
    """Orquestra baixar + transcrever - devolve o texto reconhecido, ou
    None se qualquer uma das etapas falhar (ver docstrings acima). Ponto
    de entrada único usado por app/routes_whatsapp.py:webhook."""
    audio_bytes, mime_type = _baixar_audio_whatsapp(media_id)
    if not audio_bytes:
        return None
    return transcrever_audio(audio_bytes, mime_type)
