"""Testa a transcrição de áudio recebido por WhatsApp (ver
app/whatsapp_audio.py - pedido do Silvan, 2026-09-12): antes desta
função, uma mensagem de áudio era simplesmente ignorada em silêncio (o
webhook só reconhecia `type == "text"`). Agora ela é baixada da Graph API
e transcrita pela Whisper da OpenAI, e o texto reconhecido é tratado como
se o paciente tivesse digitado aquilo.

Não faz nenhuma chamada de rede de verdade (nem à Meta, nem à OpenAI) -
os testes de "falha aberta" (sem as variáveis de ambiente configuradas)
não precisam de mock nenhum, e o teste do webhook fim-a-fim mocka
`texto_de_audio_whatsapp` diretamente, no mesmo padrão já usado em
test_whatsapp_webhook_assinatura.py para `processar_mensagem`."""
import hashlib
import hmac
import json
import os
from unittest.mock import patch

from app import create_app
from app.whatsapp_audio import _baixar_audio_whatsapp, _cliente_openai, texto_de_audio_whatsapp
import app.routes_whatsapp as routes_whatsapp_mod

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def _assinar(corpo_bytes, app_secret):
    return "sha256=" + hmac.new(app_secret.encode("utf-8"), corpo_bytes, hashlib.sha256).hexdigest()


def _payload_audio(media_id="media-id-teste"):
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "waba-id-teste",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"phone_number_id": "phone-id-teste"},
                    "messages": [{
                        "from": "5527999997777",
                        "id": "wamid.audio-teste",
                        "timestamp": "1700000000",
                        "type": "audio",
                        "audio": {"id": media_id, "mime_type": "audio/ogg; codecs=opus"},
                    }],
                },
            }],
        }],
    }


with app.app_context():
    # --- "Falha aberta": sem as variáveis de ambiente configuradas ---
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ.pop("WHATSAPP_META_ACCESS_TOKEN", None)

    checar("Sem OPENAI_API_KEY: _cliente_openai() devolve None", _cliente_openai() is None)
    checar(
        "Sem WHATSAPP_META_ACCESS_TOKEN: _baixar_audio_whatsapp() devolve (None, None)",
        _baixar_audio_whatsapp("media-id-qualquer") == (None, None),
    )
    checar(
        "Sem nenhuma das duas: texto_de_audio_whatsapp() devolve None (nunca quebra)",
        texto_de_audio_whatsapp("media-id-qualquer") is None,
    )

# --- Webhook fim-a-fim: mensagem de áudio ---
os.environ["WHATSAPP_META_APP_SECRET"] = "app-secret-de-teste"

payload_audio = _payload_audio()
corpo_audio = json.dumps(payload_audio).encode("utf-8")
assinatura_audio = _assinar(corpo_audio, "app-secret-de-teste")

# 1) Transcrição bem-sucedida (mockada): o texto reconhecido é encaminhado
# para app.whatsapp_conversa.processar_mensagem, exatamente como uma
# mensagem de texto normal - e a resposta dela é enviada de volta.
with patch.object(routes_whatsapp_mod, "texto_de_audio_whatsapp", return_value="Posso comer antes do exame?") as mock_transcricao, \
     patch.object(routes_whatsapp_mod, "processar_mensagem", return_value="Resposta de teste") as mock_processar, \
     patch.object(routes_whatsapp_mod, "enviar_mensagem_whatsapp") as mock_envio:
    r1 = client.post(
        "/whatsapp/webhook", data=corpo_audio, content_type="application/json",
        headers={"X-Hub-Signature-256": assinatura_audio},
    )
checar("Áudio transcrito com sucesso: webhook responde 200", r1.status_code == 200)
checar("Chamou a transcrição com o media_id certo", mock_transcricao.call_args[0][0] == "media-id-teste")
checar(
    "Encaminhou o TEXTO transcrito para processar_mensagem (não o media_id)",
    mock_processar.call_args[0] == ("+5527999997777", "Posso comer antes do exame?"),
)
checar("Mandou a resposta de volta ao paciente", mock_envio.call_args[0] == ("+5527999997777", "Resposta de teste"))

# 2) Transcrição falhou (mockada como None - ex.: sem chaves configuradas,
# ou erro na chamada): avisa o paciente a escrever, e NUNCA chama
# processar_mensagem com um texto vazio/None.
payload_audio_2 = _payload_audio(media_id="media-id-teste-2")
payload_audio_2["entry"][0]["changes"][0]["value"]["messages"][0]["id"] = "wamid.audio-teste-2"
corpo_audio_2 = json.dumps(payload_audio_2).encode("utf-8")
assinatura_audio_2 = _assinar(corpo_audio_2, "app-secret-de-teste")

with patch.object(routes_whatsapp_mod, "texto_de_audio_whatsapp", return_value=None) as mock_transcricao_falha, \
     patch.object(routes_whatsapp_mod, "processar_mensagem") as mock_processar_2, \
     patch.object(routes_whatsapp_mod, "enviar_mensagem_whatsapp") as mock_envio_2:
    r2 = client.post(
        "/whatsapp/webhook", data=corpo_audio_2, content_type="application/json",
        headers={"X-Hub-Signature-256": assinatura_audio_2},
    )
checar("Transcrição falhou: webhook ainda responde 200 (nunca propaga erro)", r2.status_code == 200)
checar("NÃO encaminhou nada para processar_mensagem", mock_processar_2.call_count == 0)
checar(
    "Mandou o aviso de 'não conseguimos entender' ao paciente",
    mock_envio_2.call_args[0][0] == "+5527999997777" and "áudio" in mock_envio_2.call_args[0][1].lower(),
)

os.environ.pop("WHATSAPP_META_APP_SECRET", None)
print("\nTodos os testes de transcrição de áudio do WhatsApp passaram.")
