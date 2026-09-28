"""Testa o webhook de WhatsApp (Fatia 7, migração para a API direta da
Meta - ver PLANO_WHATSAPP.md e app/routes_whatsapp.py): o handshake de
verificação (GET, hub.mode/hub.verify_token/hub.challenge) precisa
devolver o challenge só quando o token bate; o recebimento de mensagem
(POST) precisa aceitar (200, encaminhando pra lógica de conversa) só
requisições com assinatura X-Hub-Signature-256 válida, e recusar (200,
sem processar nada) requisições sem WHATSAPP_META_APP_SECRET configurado,
sem assinatura, ou com assinatura inválida - nunca devolver um status de
erro HTTP no POST (a Meta reentregaria a mesma notificação várias vezes).
A lógica de conversa em si (identificação por CPF + data de nascimento)
tem seu próprio teste em test_whatsapp_identificacao.py - aqui só
confirma que o webhook está de fato encaminhando pra ela (e, no caso da
mensagem de teste "oi", que NÃO é CPF/data, o envio de resposta de volta
é apenas pulado, já que não há WHATSAPP_META_ACCESS_TOKEN configurado
neste ambiente de teste - ver app/whatsapp_envio.py).

Também testa a dedupe por id de mensagem (`_mensagem_ja_processada`,
pedido do Silvan em 2026-09-11 depois de ver - com prints de conversa -
o mesmo aviso chegando duplicado ao paciente): mesmo com assinatura
válida, reentregar o MESMO id de mensagem (a Meta pode fazer isso de
verdade, ver docstring de app.routes_whatsapp e app.models.
WhatsappMensagemProcessada) não deve chamar `processar_mensagem` de
novo."""
import hashlib
import hmac
import json
import os
from unittest.mock import patch

from app import create_app, db
from app.models import WhatsappMensagemProcessada
import app.routes_whatsapp as routes_whatsapp_mod

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def _assinar(corpo_bytes, app_secret):
    return "sha256=" + hmac.new(app_secret.encode("utf-8"), corpo_bytes, hashlib.sha256).hexdigest()


PAYLOAD_MENSAGEM_TEXTO = {
    "object": "whatsapp_business_account",
    "entry": [{
        "id": "waba-id-teste",
        "changes": [{
            "field": "messages",
            "value": {
                "messaging_product": "whatsapp",
                "metadata": {"phone_number_id": "phone-id-teste"},
                "messages": [{
                    "from": "5527999998888",
                    "id": "wamid.teste",
                    "timestamp": "1700000000",
                    "type": "text",
                    "text": {"body": "oi"},
                }],
            },
        }],
    }],
}
CORPO_JSON = json.dumps(PAYLOAD_MENSAGEM_TEXTO).encode("utf-8")

# --- GET: handshake de verificação do webhook ---

os.environ["WHATSAPP_META_VERIFY_TOKEN"] = "token-de-verificacao-teste"

r_verify_ok = client.get("/whatsapp/webhook", query_string={
    "hub.mode": "subscribe",
    "hub.verify_token": "token-de-verificacao-teste",
    "hub.challenge": "desafio-123",
})
checar("GET com token correto: responde 200", r_verify_ok.status_code == 200)
checar("GET com token correto: devolve o challenge", r_verify_ok.get_data(as_text=True) == "desafio-123")

r_verify_errado = client.get("/whatsapp/webhook", query_string={
    "hub.mode": "subscribe",
    "hub.verify_token": "token-errado",
    "hub.challenge": "desafio-123",
})
checar("GET com token errado: recusa (403)", r_verify_errado.status_code == 403)

os.environ.pop("WHATSAPP_META_VERIFY_TOKEN", None)

# --- POST: recebimento de mensagem, validado por X-Hub-Signature-256 ---

# 1) Sem WHATSAPP_META_APP_SECRET configurado: recusa, mas sempre
# responde 200 (nunca devolve erro HTTP para o provedor).
os.environ.pop("WHATSAPP_META_APP_SECRET", None)
r1 = client.post("/whatsapp/webhook", data=CORPO_JSON, content_type="application/json")
checar("Sem WHATSAPP_META_APP_SECRET: responde 200 mesmo recusando", r1.status_code == 200)

# 2) Com App Secret configurado, mas sem cabeçalho de assinatura: recusa.
os.environ["WHATSAPP_META_APP_SECRET"] = "app-secret-de-teste"
r2 = client.post("/whatsapp/webhook", data=CORPO_JSON, content_type="application/json")
checar("Sem assinatura: responde 200 mesmo recusando", r2.status_code == 200)

# 3) Com assinatura calculada para outro App Secret (inválida): recusa.
assinatura_errada = _assinar(CORPO_JSON, "app-secret-errado")
r3 = client.post(
    "/whatsapp/webhook", data=CORPO_JSON, content_type="application/json",
    headers={"X-Hub-Signature-256": assinatura_errada},
)
checar("Assinatura calculada com App Secret errado: responde 200 mesmo recusando", r3.status_code == 200)

# 4) Com assinatura válida (calculada com o mesmo App Secret configurado):
# aceita e encaminha para app.whatsapp_conversa - "oi" não é CPF/data
# válidos, então a lógica de conversa pede a identificação (o envio da
# resposta de volta é só pulado, sem WHATSAPP_META_ACCESS_TOKEN
# configurado neste ambiente de teste - não afeta o retorno do webhook).
assinatura_valida = _assinar(CORPO_JSON, "app-secret-de-teste")
r4 = client.post(
    "/whatsapp/webhook", data=CORPO_JSON, content_type="application/json",
    headers={"X-Hub-Signature-256": assinatura_valida},
)
checar("Assinatura válida: responde 200", r4.status_code == 200)
checar("Assinatura válida: corpo é o ack esperado", r4.get_json() == {"ok": True})

# 5) Payload sem nenhuma mensagem de texto (ex.: notificação de status de
# entrega/leitura, "value.statuses" em vez de "value.messages") - aceita
# e não quebra, só não encaminha nada pra lógica de conversa.
payload_status = {
    "object": "whatsapp_business_account",
    "entry": [{"id": "waba-id-teste", "changes": [{
        "field": "messages",
        "value": {"statuses": [{"id": "wamid.teste", "status": "delivered"}]},
    }]}],
}
corpo_status = json.dumps(payload_status).encode("utf-8")
assinatura_status = _assinar(corpo_status, "app-secret-de-teste")
r5 = client.post(
    "/whatsapp/webhook", data=corpo_status, content_type="application/json",
    headers={"X-Hub-Signature-256": assinatura_status},
)
checar("Notificação de status (sem mensagem): responde 200 sem quebrar", r5.status_code == 200)

# 6) Dedupe por id de mensagem: reentregar o MESMO payload (mesmo "id":
# "wamid.teste", já processado no passo 4 acima) com assinatura válida
# de novo não deve chamar processar_mensagem uma segunda vez - simula a
# Meta reentregando o mesmo webhook (comportamento real documentado dela).
with app.app_context():
    checar(
        "O id da mensagem do passo 4 já foi registrado como processado",
        WhatsappMensagemProcessada.query.filter_by(mensagem_id="wamid.teste").first() is not None,
    )

with patch.object(routes_whatsapp_mod, "processar_mensagem") as mock_processar:
    r6 = client.post(
        "/whatsapp/webhook", data=CORPO_JSON, content_type="application/json",
        headers={"X-Hub-Signature-256": assinatura_valida},
    )
checar("Reentrega do mesmo id de mensagem: ainda responde 200", r6.status_code == 200)
checar("Reentrega do mesmo id de mensagem: NÃO chama processar_mensagem de novo", not mock_processar.called)

with app.app_context():
    checar(
        "Reentrega do mesmo id NÃO duplica o registro em WhatsappMensagemProcessada",
        WhatsappMensagemProcessada.query.filter_by(mensagem_id="wamid.teste").count() == 1,
    )

# 7) Uma mensagem NOVA (id diferente) do mesmo payload continua sendo
# processada normalmente - a dedupe é só por id, não trava mensagens
# novas do mesmo paciente.
payload_mensagem_nova = json.loads(CORPO_JSON)
payload_mensagem_nova["entry"][0]["changes"][0]["value"]["messages"][0]["id"] = "wamid.teste-2"
corpo_mensagem_nova = json.dumps(payload_mensagem_nova).encode("utf-8")
assinatura_mensagem_nova = _assinar(corpo_mensagem_nova, "app-secret-de-teste")

with patch.object(routes_whatsapp_mod, "processar_mensagem") as mock_processar2:
    r7 = client.post(
        "/whatsapp/webhook", data=corpo_mensagem_nova, content_type="application/json",
        headers={"X-Hub-Signature-256": assinatura_mensagem_nova},
    )
checar("Mensagem com id novo: responde 200", r7.status_code == 200)
checar("Mensagem com id novo (nunca visto antes): chama processar_mensagem normalmente", mock_processar2.called)

os.environ.pop("WHATSAPP_META_APP_SECRET", None)
print("\nTodos os testes do webhook de WhatsApp (Meta Cloud API) passaram.")
