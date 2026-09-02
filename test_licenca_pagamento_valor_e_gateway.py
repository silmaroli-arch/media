"""Testa as 3 extensões da Fatia 8 (licença individual/calendário de
pagamento) pedidas pelo Silvan depois do aviso de inadimplência:

1. O valor mensal negociado (Usuario.valor_licenca_mensal) também aparece
   na tela "Minha licença" do próprio médico (antes só existia em
   /dono/usuarios).
2. Cada mês do calendário de pagamento guarda o valor cobrado NAQUELE mês
   (LicencaPagamento.valor) - uma fotografia do valor do médico no momento
   em que o mês nasce, não muda retroativamente se o valor mudar depois.
3. Gateway de pagamento real via Mercado Pago (Checkout Pro): o dono gera
   uma cobrança de verdade pra um mês específico
   (usuario_licenca_pagamento_cobrar), o médico vê o link "Pagar agora" na
   própria tela, e a confirmação chega automaticamente por webhook
   (mercadopago_webhook) - sem precisar do dono marcar manualmente.

Não faz nenhuma chamada de rede de verdade - as funções de
app/mercadopago_integration.py que chamam a API do Mercado Pago
(requests.post/requests.get) são substituídas por versões falsas
(monkeypatch), e a assinatura do webhook é calculada com o mesmo algoritmo
HMAC-SHA256 documentado pelo Mercado Pago, usando um secret de teste.

Roda contra SQLite local (não usa a DATABASE_URL do .env) e reseta esse
banco de teste no início — não toca no banco real. Para rodar:
    DATABASE_URL=sqlite:///teste_licenca_pagamento.db python test_licenca_pagamento_valor_e_gateway.py
"""
import hashlib
import hmac
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
    # Restruturação de 2026-09-02: valor_licenca_mensal nasce a partir do
    # padrão global (PlataformaConfig.valor_licenca_padrao) - sem isso, o
    # médico cadastrado abaixo nasceria com valor em branco, e o mês atual
    # (garantido logo no primeiro acesso autenticado dele, via
    # staff_required) fotografaria None antes do dono ter chance de
    # definir um valor individual.
    PlataformaConfig.obter().valor_licenca_padrao = 220.00
    db.session.commit()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


class RespostaFalsa:
    """Substitui requests.Response nos testes - só o suficiente pra
    criar_preferencia_pagamento/consultar_pagamento funcionarem sem rede."""

    def __init__(self, dados, status_code=200):
        self._dados = dados
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._dados


# ---------- Setup: médico com valor mensal definido ----------
r = client.post("/cadastro", data={
    "nome": "Dr. Pagamento Teste",
    "papel": "medico",
    "cpf": "111.222.333-96", "crm_numero": "22222", "crm_uf": "ES",
    "email": "pagamento.medico@example.com",
    "senha": "123456",
}, follow_redirects=True)
checar("Cadastro do médico responde 200", r.status_code == 200)

with app.app_context():
    medico = Usuario.query.filter_by(email="pagamento.medico@example.com").first()
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
r_valor = client.post(f"/dono/usuarios/{medico_id}/licenca", data={
    "licenca_status": "ativa",
    "licenca_vencimento": "",
    "valor_licenca_mensal": "220,00",
}, follow_redirects=True)
checar("Dono define o valor mensal responde 200", r_valor.status_code == 200)
client.get("/logout")

# ---------- 1. Valor mensal aparece na tela do próprio médico ----------
client.post("/login", data={"identificador": "pagamento.medico@example.com", "senha": "123456"})
r_minha = client.get("/equipe/minha-licenca")
checar("Tela 'Minha licença' responde 200", r_minha.status_code == 200)
html_minha = r_minha.get_data(as_text=True)
checar("Valor mensal aparece na tela do médico", "R$ 220.00/mês" in html_minha)

# ---------- 2. O mês nasce com o valor fotografado ----------
with app.app_context():
    mes_atual = date.today().replace(day=1)
    pagamento = LicencaPagamento.query.filter_by(usuario_id=medico_id, mes=mes_atual).first()
    checar("Mês atual foi criado ao abrir a tela do médico", pagamento is not None)
    checar("Mês atual nasceu com o valor do médico fotografado", float(pagamento.valor) == 220.00)
    pagamento_id = pagamento.id

checar("Valor do mês aparece na tela do médico", "R$ 220.00" in html_minha)

# Mudar o valor do médico depois NÃO deve alterar o valor já fotografado
# no mês (é uma fatura já "emitida", não um espelho ao vivo).
client.get("/logout")
client.post("/login", data={"identificador": dono_email, "senha": "123456"})
client.post(f"/dono/usuarios/{medico_id}/licenca", data={
    "licenca_status": "ativa",
    "licenca_vencimento": "",
    "valor_licenca_mensal": "300,00",
}, follow_redirects=True)
with app.app_context():
    pagamento = LicencaPagamento.query.get(pagamento_id)
    checar("Valor do mês já criado não muda retroativamente", float(pagamento.valor) == 220.00)

r_cal_dono = client.get(f"/dono/usuarios/{medico_id}/licenca/pagamentos")
checar("Calendário do dono responde 200", r_cal_dono.status_code == 200)
checar("Valor do mês aparece no calendário do dono", "R$ 220.00" in r_cal_dono.get_data(as_text=True))

# ---------- 3. Gateway de pagamento real (Mercado Pago) ----------
# Sem credenciais configuradas: o botão falha com uma mensagem clara, sem
# quebrar a tela nem criar nada.
os.environ.pop("MERCADOPAGO_ACCESS_TOKEN", None)
r_sem_config = client.post(
    f"/dono/usuarios/{medico_id}/licenca/pagamentos/{pagamento_id}/cobrar",
    follow_redirects=True,
)
checar("Gerar cobrança sem credenciais responde 200", r_sem_config.status_code == 200)
checar("Mensagem de gateway não configurado aparece", "não está configurado" in r_sem_config.get_data(as_text=True))
with app.app_context():
    pagamento = LicencaPagamento.query.get(pagamento_id)
    checar("Nenhuma preferência foi criada sem credenciais", pagamento.mp_preference_id is None)

# Com credenciais configuradas (falsas) - simula a chamada de criação da
# preferência no Mercado Pago.
os.environ["MERCADOPAGO_ACCESS_TOKEN"] = "TESTE-token-falso"
os.environ["MERCADOPAGO_WEBHOOK_SECRET"] = "segredo-de-teste"

chamadas_post = []
post_original = mp_integration.requests.post


def post_falso(url, json=None, headers=None, timeout=None):
    chamadas_post.append((url, json, headers))
    return RespostaFalsa({"id": "pref-123", "init_point": "https://mercadopago.example/pay/pref-123"})


mp_integration.requests.post = post_falso
try:
    r_cobrar = client.post(
        f"/dono/usuarios/{medico_id}/licenca/pagamentos/{pagamento_id}/cobrar",
        follow_redirects=True,
    )
finally:
    mp_integration.requests.post = post_original

checar("Gerar cobrança com credenciais responde 200", r_cobrar.status_code == 200)
checar("Uma chamada foi feita à API do Mercado Pago", len(chamadas_post) == 1)
checar(
    "A referência externa aponta pro pagamento certo",
    chamadas_post[0][1]["external_reference"] == f"licenca_pagamento:{pagamento_id}",
)

with app.app_context():
    pagamento = LicencaPagamento.query.get(pagamento_id)
    checar("mp_preference_id foi salvo", pagamento.mp_preference_id == "pref-123")
    checar("mp_init_point foi salvo", pagamento.mp_init_point == "https://mercadopago.example/pay/pref-123")
    checar("mp_status nasce como pendente", pagamento.mp_status == "pendente")
    checar("Ainda não está marcado como pago (só a cobrança foi gerada)", pagamento.pago is False)

client.get("/logout")

# O médico já vê o link "Pagar agora" na própria tela.
client.post("/login", data={"identificador": "pagamento.medico@example.com", "senha": "123456"})
r_minha_com_link = client.get("/equipe/minha-licenca")
checar(
    "Link 'Pagar agora' aparece na tela do médico",
    "https://mercadopago.example/pay/pref-123" in r_minha_com_link.get_data(as_text=True),
)
client.get("/logout")


def assinatura_de_teste(secret, data_id, request_id, ts):
    manifest = f"id:{data_id};request-id:{request_id};ts:{ts};"
    return hmac.new(secret.encode("utf-8"), manifest.encode("utf-8"), hashlib.sha256).hexdigest()


# ---------- Webhook: confirmação automática de pagamento ----------
chamadas_get = []
get_original = mp_integration.requests.get


def get_falso(url, headers=None, timeout=None):
    chamadas_get.append(url)
    return RespostaFalsa({
        "id": 555,
        "status": "approved",
        "external_reference": f"licenca_pagamento:{pagamento_id}",
    })


# Assinatura inválida (secret errado) deve ser recusada, sem marcar nada
# como pago - o webhook sempre responde 200 (pra não fazer o Mercado Pago
# reentregar a notificação), mas não processa o pagamento.
ts = "1700000000"
assinatura_errada = assinatura_de_teste("secret-errado", "555", "req-1", ts)
mp_integration.requests.get = get_falso
try:
    r_webhook_invalido = client.post(
        "/webhooks/mercadopago?data.id=555",
        headers={"X-Signature": f"ts={ts},v1={assinatura_errada}", "X-Request-Id": "req-1"},
        json={},
    )
    checar("Webhook com assinatura inválida ainda responde 200", r_webhook_invalido.status_code == 200)
    checar("Nenhuma chamada à API foi feita com assinatura inválida", len(chamadas_get) == 0)

    with app.app_context():
        pagamento = LicencaPagamento.query.get(pagamento_id)
        checar("Pagamento continua não pago após assinatura inválida", pagamento.pago is False)

    # Assinatura válida: o webhook consulta o pagamento na API (nunca confia
    # só no payload recebido) e marca o mês como pago.
    assinatura_certa = assinatura_de_teste("segredo-de-teste", "555", "req-2", ts)
    r_webhook_valido = client.post(
        "/webhooks/mercadopago?data.id=555",
        headers={"X-Signature": f"ts={ts},v1={assinatura_certa}", "X-Request-Id": "req-2"},
        json={},
    )
    checar("Webhook com assinatura válida responde 200", r_webhook_valido.status_code == 200)
    checar("O webhook consultou o pagamento na API do Mercado Pago", len(chamadas_get) == 1)
finally:
    mp_integration.requests.get = get_original

with app.app_context():
    pagamento = LicencaPagamento.query.get(pagamento_id)
    checar("Mês foi marcado como pago automaticamente pelo webhook", pagamento.pago is True)
    checar("pago_em foi registrado pelo webhook", pagamento.pago_em is not None)
    checar("mp_payment_id foi salvo", pagamento.mp_payment_id == "555")
    checar("mp_status foi atualizado para approved", pagamento.mp_status == "approved")

# Webhook sem MERCADOPAGO_WEBHOOK_SECRET configurado recusa por padrão
# (falha fechada), mesmo com uma assinatura "válida" pro secret antigo.
os.environ.pop("MERCADOPAGO_WEBHOOK_SECRET", None)
r_webhook_sem_secret = client.post(
    "/webhooks/mercadopago?data.id=555",
    headers={"X-Signature": f"ts={ts},v1={assinatura_certa}", "X-Request-Id": "req-3"},
    json={},
)
checar("Webhook sem secret configurado ainda responde 200 (falha fechada)", r_webhook_sem_secret.status_code == 200)

print("\nTodos os testes de valor por mês + gateway de pagamento (Mercado Pago) passaram.")
