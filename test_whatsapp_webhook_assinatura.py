"""Testa o webhook de WhatsApp (Fatia 7, passos 2 e 3 do plano - ver
PLANO_WHATSAPP.md): precisa aceitar (200, com a resposta da lógica de
conversa) requisições com assinatura Twilio válida e recusar (200, sem
processar nada, corpo vazio) requisições sem TWILIO_AUTH_TOKEN
configurado, sem assinatura, ou com assinatura inválida - nunca devolver
um status de erro HTTP (a Twilio reentregaria a mesma mensagem várias
vezes). A lógica de conversa em si (identificação por CPF + data de
nascimento) tem seu próprio teste em test_whatsapp_identificacao.py - aqui
só confirma que o webhook está de fato encaminhando pra ela."""
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

# 4) Com assinatura válida (calculada com o mesmo Auth Token configurado):
# aceita e encaminha para app.whatsapp_conversa - "oi" não é CPF/data
# válidos, então a resposta é o pedido de identificação.
validador = RequestValidator("token_de_teste_fake")
assinatura_valida = validador.compute_signature(URL_WEBHOOK, PARAMS)
r4 = client.post("/whatsapp/webhook", data=PARAMS, headers={"X-Twilio-Signature": assinatura_valida})
checar("Assinatura válida: responde 200", r4.status_code == 200)
corpo_r4 = r4.get_data(as_text=True)
checar("Assinatura válida: encaminhou pra lógica de conversa (pede CPF)", "<Message>" in corpo_r4 and "CPF" in corpo_r4)

os.environ.pop("TWILIO_AUTH_TOKEN", None)
print("\nTodos os testes do webhook de WhatsApp (passos 2 e 3) passaram.")
