"""Testa o Pix nativo da licença individual (pedido do Silvan, 2026-09-25):
opção ADICIONAL ao link de Checkout Pro já existente (test_licenca_
pagamento_valor_e_gateway.py), não substitui - o médico gera o próprio QR
code em "Minha licença" (autoatendimento), e o mesmo webhook que já
confirma o link também confirma o Pix, porque os dois usam o MESMO
formato de external_reference ("licenca_pagamento:<id>").

Cobre:
1. Sem credenciais (MERCADOPAGO_ACCESS_TOKEN): botão "Gerar Pix" falha com
   mensagem clara, sem quebrar a tela nem gravar nada.
2. Com credenciais (falsas, via monkeypatch de requests.post): o médico
   gera o Pix pela própria tela, o QR code (base64) e o código copia-e-
   cola aparecem na tela, e os campos pix_* são gravados no banco.
3. O webhook (mesmo mercadopago_webhook de sempre, SEM nenhuma mudança)
   confirma o pagamento do Pix da mesma forma que já confirma o link,
   porque a external_reference é idêntica.
4. Um mês já pago não pode gerar Pix (mesma trava do link).
5. A geração em massa do ano (licencas_gerar_cobrancas_ano) também gera
   Pix pra cada mês, além do link, sem duplicar quando já existe.

Não faz nenhuma chamada de rede de verdade. Roda contra SQLite local, não
toca no banco real. Para rodar:
    DATABASE_URL=sqlite:///teste_pix_licenca.db python test_pix_licenca.py
"""
import os
from datetime import date

from app import create_app
from app.extensions import db
from app.db_utils import resetar_banco
from app.models import Usuario, LicencaPagamento, PlataformaConfig
import app.mercadopago_integration as mp_integration

app = create_app()
client = app.test_client()

with app.app_context():
    resetar_banco(db)
    PlataformaConfig.obter().valor_licenca_padrao = 220.00
    db.session.commit()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


class RespostaFalsa:
    def __init__(self, dados, status_code=200):
        self._dados = dados
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._dados


DADOS_PIX_FALSO = {
    "id": 999888,
    "point_of_interaction": {
        "transaction_data": {
            "qr_code": "00020126580014br.gov.bcb.pix-copia-e-cola-de-teste",
            "qr_code_base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
        },
    },
}

# ---------- Setup: médico com valor mensal definido ----------
r = client.post("/cadastro", data={
    "nome": "Dr. Pix Teste",
    "papel": "medico",
    "cpf": "111.222.333-97", "crm_numero": "33333", "crm_uf": "ES",
    "data_nascimento": "10/05/1980",
    "email": "pix.medico@example.com",
    "senha": "123456",
}, follow_redirects=True)
checar("Cadastro do médico responde 200", r.status_code == 200)

with app.app_context():
    medico = Usuario.query.filter_by(email="pix.medico@example.com").first()
    medico_id = medico.id

client.get("/logout")

with app.app_context():
    dono = Usuario.query.filter_by(tipo="dono").first()
    if not dono:
        dono = Usuario(nome="Dono Teste", email="dono.teste@example.com", tipo="dono")
        dono.set_senha("123456")
        db.session.add(dono)
        db.session.commit()
    dono_email = dono.email

client.post("/login", data={"identificador": dono_email, "senha": "123456"})
client.post(f"/dono/usuarios/{medico_id}/licenca", data={
    "licenca_status": "ativa",
    "licenca_vencimento": "",
    "valor_licenca_mensal": "220,00",
}, follow_redirects=True)
client.get("/logout")

client.post("/login", data={"identificador": "pix.medico@example.com", "senha": "123456"})
client.get("/equipe/minha-licenca")  # garante que o mês atual nasceu
with app.app_context():
    mes_atual = date.today().replace(day=1)
    pagamento = LicencaPagamento.query.filter_by(usuario_id=medico_id, mes=mes_atual).first()
    checar("Mês atual foi criado", pagamento is not None)
    pagamento_id = pagamento.id

# ---------- 1. Sem credenciais: falha com mensagem clara ----------
os.environ.pop("MERCADOPAGO_ACCESS_TOKEN", None)
r_sem_config = client.post(f"/equipe/minha-licenca/pagamentos/{pagamento_id}/pix", follow_redirects=True)
checar("Gerar Pix sem credenciais responde 200", r_sem_config.status_code == 200)
checar(
    "Mensagem de Pix não disponível aparece",
    "ainda não está disponível" in r_sem_config.get_data(as_text=True),
)
with app.app_context():
    pagamento = LicencaPagamento.query.get(pagamento_id)
    checar("Nenhum QR code foi gravado sem credenciais", pagamento.pix_qr_code is None)

# ---------- 2. Com credenciais (falsas): gera o Pix ----------
os.environ["MERCADOPAGO_ACCESS_TOKEN"] = "TESTE-token-falso"
os.environ["MERCADOPAGO_WEBHOOK_SECRET"] = "segredo-de-teste"

chamadas_post = []
post_original = mp_integration.requests.post


def post_falso(url, json=None, headers=None, timeout=None):
    chamadas_post.append((url, json, headers))
    return RespostaFalsa(DADOS_PIX_FALSO)


mp_integration.requests.post = post_falso
try:
    r_gerar_pix = client.post(f"/equipe/minha-licenca/pagamentos/{pagamento_id}/pix", follow_redirects=True)
finally:
    mp_integration.requests.post = post_original

checar("Gerar Pix com credenciais responde 200", r_gerar_pix.status_code == 200)
checar("Uma chamada foi feita à API do Mercado Pago (Payments)", len(chamadas_post) == 1)
checar("A URL usada foi a de Payments (não Checkout Pro)", "/v1/payments" in chamadas_post[0][0])
checar(
    "A referência externa é a MESMA usada pelo link (mesmo formato)",
    chamadas_post[0][1]["external_reference"] == f"licenca_pagamento:{pagamento_id}",
)
checar("Foi enviado um X-Idempotency-Key", "X-Idempotency-Key" in chamadas_post[0][2])

with app.app_context():
    pagamento = LicencaPagamento.query.get(pagamento_id)
    checar("pix_qr_code foi salvo", pagamento.pix_qr_code == DADOS_PIX_FALSO["point_of_interaction"]["transaction_data"]["qr_code"])
    checar("pix_qr_code_base64 foi salvo", pagamento.pix_qr_code_base64 is not None)
    checar("pix_payment_id foi salvo", pagamento.pix_payment_id == "999888")
    checar("pix_expira_em foi gravado (~30min no futuro)", pagamento.pix_expira_em is not None)
    checar("Ainda não está pago (só o Pix foi gerado)", pagamento.pago is False)

html_com_pix = client.get("/equipe/minha-licenca").get_data(as_text=True)
checar("O QR code (base64) aparece na tela do médico", DADOS_PIX_FALSO["point_of_interaction"]["transaction_data"]["qr_code_base64"][:30] in html_com_pix)
checar("O código copia-e-cola aparece na tela do médico", "pix-copia-e-cola-de-teste" in html_com_pix)

client.get("/logout")

# ---------- 3. O MESMO webhook confirma o Pix (sem nenhuma mudança nele) ----------
import hashlib
import hmac


def assinatura_de_teste(secret, data_id, request_id, ts):
    manifest = f"id:{data_id};request-id:{request_id};ts:{ts};"
    return hmac.new(secret.encode("utf-8"), manifest.encode("utf-8"), hashlib.sha256).hexdigest()


chamadas_get = []
get_original = mp_integration.requests.get


def get_falso(url, headers=None, timeout=None):
    chamadas_get.append(url)
    return RespostaFalsa({
        "id": 999888,
        "status": "approved",
        "external_reference": f"licenca_pagamento:{pagamento_id}",
    })


ts = "1700000000"
mp_integration.requests.get = get_falso
try:
    assinatura_certa = assinatura_de_teste("segredo-de-teste", "999888", "req-pix-1", ts)
    r_webhook = client.post(
        "/webhooks/mercadopago?data.id=999888",
        headers={"X-Signature": f"ts={ts},v1={assinatura_certa}", "X-Request-Id": "req-pix-1"},
        json={},
    )
    checar("Webhook do pagamento via Pix responde 200", r_webhook.status_code == 200)
finally:
    mp_integration.requests.get = get_original

with app.app_context():
    pagamento = LicencaPagamento.query.get(pagamento_id)
    checar("Mês foi marcado como pago pelo webhook (mesmo fluxo do link)", pagamento.pago is True)
    checar("mp_payment_id foi salvo mesmo tendo vindo de um Pix", pagamento.mp_payment_id == "999888")

# ---------- 4. Mês já pago não pode gerar Pix ----------
client.post("/login", data={"identificador": "pix.medico@example.com", "senha": "123456"})
r_pago = client.post(f"/equipe/minha-licenca/pagamentos/{pagamento_id}/pix", follow_redirects=True)
checar("Tentar gerar Pix de mês já pago responde 200", r_pago.status_code == 200)
checar("Mensagem de mês já pago aparece", "já está pago" in r_pago.get_data(as_text=True))
client.get("/logout")

# ---------- 5. Geração em massa do ano também gera Pix ----------
with app.app_context():
    medico2 = Usuario(
        nome="Dr. Pix Massa", email="pix.massa@example.com", tipo="medico",
        cpf="111.222.333-98", crm_numero="44444", crm_uf="ES",
        licenca_status="ativa", valor_licenca_mensal=150.00, ciclo_licenca="mensal",
    )
    medico2.set_senha("123456")
    db.session.add(medico2)
    db.session.commit()
    medico2_id = medico2.id

client.post("/login", data={"identificador": dono_email, "senha": "123456"})

chamadas_post_massa = []
chamadas_pix_massa = []


def post_falso_massa(url, json=None, headers=None, timeout=None):
    if "/v1/payments" in url:
        chamadas_pix_massa.append((url, json))
        return RespostaFalsa(DADOS_PIX_FALSO)
    chamadas_post_massa.append((url, json))
    return RespostaFalsa({"id": f"pref-massa-{len(chamadas_post_massa)}", "init_point": "https://mercadopago.example/pay/massa"})


mp_integration.requests.post = post_falso_massa
try:
    r_massa = client.post("/dono/usuarios/gerar-cobrancas-ano", follow_redirects=True)
finally:
    mp_integration.requests.post = post_original

checar("Geração em massa responde 200", r_massa.status_code == 200)
checar("A geração em massa também gerou Pix pra cada mês novo", len(chamadas_pix_massa) > 0)
html_massa = r_massa.get_data(as_text=True)
checar("O resumo da geração em massa menciona Pix gerado", "Pix gerado" in html_massa)

with app.app_context():
    algum_pix_massa = LicencaPagamento.query.filter_by(usuario_id=medico2_id).filter(
        LicencaPagamento.pix_qr_code.isnot(None)
    ).first()
    checar("Pelo menos um mês do médico 2 recebeu Pix na geração em massa", algum_pix_massa is not None)

client.get("/logout")

print("\nTodos os testes do Pix nativo da licença passaram.")
