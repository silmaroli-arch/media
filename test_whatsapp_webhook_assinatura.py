"""Testa o webhook de WhatsApp (Fatia 7, passo 2 do plano - ver
PLANO_WHATSAPP.md): ainda não tem lógica de identificação/resposta, só
precisa aceitar (200) requisições com assinatura Twilio válida e recusar
(200, sem processar nada) requisições sem TWILIO_AUTH_TOKEN configurado,
sem assinatura, ou com assinatura inválida - nunca devolver um status de
erro HTTP (a Twilio reentregaria a mesma mensagem várias vezes)."""
import os

from twilio.request_validator import RequestValidator

from app import create_app

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


URL_WEBHOOK = "http://localhost/whatsapp/webhook"
PARAMS = {"From": "whatsapp:+5527999998888", "Body": "oi"}

# 1) Sem TWILIO_AUTH_TOKEN configurado: recusa, mas sempre responde 200
# (nunca devolve erro HTTP para o provedor).
os.environ.pop("TWILIO_AUTH_TOKEN", None)
r1 = client.post("/whatsapp/webhook", data=PARAMS)
checar("Sem TWILIO_AUTH_TOKEN: responde 200 mesmo recusando", r1.status_code == 200)
checar("Sem TWILIO_AUTH_TOKEN: corpo é TwiML vazio", "<Response>" in r1.get_data(as_text=True))

# 2) Com token configurado, mas sem cabeçalho de assinatura: recusa.
os.environ["TWILIO_AUTH_TOKEN"] = "token_de_teste_fake"
r2 = client.post("/whatsapp/webhook", data=PARAMS)
checar("Sem assinatura: responde 200 mesmo recusando", r2.status_code == 200)

# 3) Com assinatura calculada para outro token (inválida): recusa.
validador_errado = RequestValidator("token_errado")
assinatura_errada = validador_errado.compute_signature(URL_WEBHOOK, PARAMS)
r3 = client.post("/whatsapp/webhook", data=PARAMS, headers={"X-Twilio-Signature": assinatura_errada})
checar("Assinatura calculada com token errado: responde 200 mesmo recusando", r3.status_code == 200)

# 4) Com assinatura válida (calculada com o mesmo Auth Token configurado): aceita.
validador = RequestValidator("token_de_teste_fake")
assinatura_valida = validador.compute_signature(URL_WEBHOOK, PARAMS)
r4 = client.post("/whatsapp/webhook", data=PARAMS, headers={"X-Twilio-Signature": assinatura_valida})
checar("Assinatura válida: responde 200", r4.status_code == 200)
checar("Assinatura válida: corpo é TwiML vazio", "<Response>" in r4.get_data(as_text=True))

os.environ.pop("TWILIO_AUTH_TOKEN", None)
print("\nTodos os testes do webhook de WhatsApp (passo 2) passaram.")
