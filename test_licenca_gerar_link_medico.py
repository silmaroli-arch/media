"""Testa o botao "Gerar link de pagamento" da tela "Minha licenca" do
medico (pedido do Silvan, 2026-09-29): autoatendimento do link de Checkout
Pro, mes a mes - ver medico.minha_licenca_gerar_link.

Cobre:
1. Mes em aberto sem link: a tela mostra o botao "Gerar link de pagamento".
2. Sem credenciais (MERCADOPAGO_ACCESS_TOKEN): mensagem clara, nada gravado.
3. Com credenciais (falsas, via monkeypatch de requests.post): o link e
   gravado, a tela passa a mostrar "Pagar agora" no lugar do botao, e a
   external_reference e "licenca_pagamento:<id>" (o mesmo webhook confirma).
4. Medico sem valor mensal definido: mensagem clara, nenhuma chamada de rede.
5. Isolamento: um medico nao consegue gerar link de pagamento de outro (404).
6. Mes ja pago: recusado.
7. Ciclo ANUAL: mes que nao e o vigente e recusado; o mes vigente usa a
   cobranca anual (external_reference "licenca_anual:<id>").

Nao faz nenhuma chamada de rede de verdade. Roda contra SQLite local. Para rodar:
    DATABASE_URL=sqlite:///teste_licenca_gerar_link.db python test_licenca_gerar_link_medico.py
"""
import os
from datetime import date

from app import create_app
from app.extensions import db
from app.db_utils import resetar_banco
from app.models import Usuario, LicencaPagamento, PlataformaConfig, garantir_meses_licenca
import app.mercadopago_integration as mp_integration

app = create_app()
client = app.test_client()

# Campos que o cadastro exige hoje (telefone, endereco e confirmacao de senha).
EXTRA_CADASTRO = {
    "senha_confirmacao": "123456", "telefone": "(27) 99999-1234", "cep": "29010-000", "rua": "Rua A",
    "numero": "1", "bairro": "Centro", "cidade": "Vitoria", "uf": "ES",
}

with app.app_context():
    resetar_banco(db)
    PlataformaConfig.obter().valor_licenca_padrao = 220.00
    PlataformaConfig.obter().valor_licenca_anual_padrao = 2000.00
    db.session.commit()


def checar(nome, condicao):
    print(f"[{'OK' if condicao else 'FALHOU'}] {nome}")
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


def cadastrar_medico(nome, email, cpf, crm):
    r = client.post("/cadastro", data={**EXTRA_CADASTRO,
        "nome": nome, "papel": "medico", "cpf": cpf, "crm_numero": crm, "crm_uf": "ES",
        "data_nascimento": "10/05/1980", "email": email, "senha": "123456",
    }, follow_redirects=True)
    assert r.status_code == 200
    client.get("/logout")
    with app.app_context():
        return Usuario.query.filter_by(email=email).first().id


medico_id = cadastrar_medico("Dr. Link Teste", "link.medico@example.com", "111.222.333-97", "44444")
outro_id = cadastrar_medico("Dra. Outra", "link.outra@example.com", "529.982.247-25", "55555")

with app.app_context():
    for uid in (medico_id, outro_id):
        u = Usuario.query.get(uid)
        u.licenca_status = "ativa"
        u.valor_licenca_mensal = 220.00 if uid == medico_id else 150.00
        garantir_meses_licenca(u)
    db.session.commit()
    mes_atual = date.today().replace(day=1)
    pag_id = LicencaPagamento.query.filter_by(usuario_id=medico_id, mes=mes_atual).first().id
    pag_outro_id = LicencaPagamento.query.filter_by(usuario_id=outro_id, mes=mes_atual).first().id

client.post("/login", data={"identificador": "link.medico@example.com", "senha": "123456"})
URL = f"/equipe/minha-licenca/pagamentos/{pag_id}/link"

# ---------- 1. Botao aparece ----------
html = client.get("/equipe/minha-licenca").get_data(as_text=True)
checar("Botao 'Gerar link de pagamento' aparece no mes em aberto", "Gerar link de pagamento" in html)
checar("Botao aponta pra rota nova", URL in html)

# ---------- 2. Sem credenciais ----------
os.environ.pop("MERCADOPAGO_ACCESS_TOKEN", None)
r = client.post(URL, follow_redirects=True)
checar("Sem credenciais responde 200", r.status_code == 200)
checar("Mensagem de indisponivel aparece", "ainda não está disponível" in r.get_data(as_text=True))
with app.app_context():
    checar("Nenhum link gravado sem credenciais", LicencaPagamento.query.get(pag_id).mp_init_point is None)

# ---------- 3. Com credenciais ----------
os.environ["MERCADOPAGO_ACCESS_TOKEN"] = "TESTE-token-falso"
chamadas = []
post_original = mp_integration.requests.post


def post_falso(url, json=None, headers=None, timeout=None):
    chamadas.append((url, json))
    return RespostaFalsa({"id": "pref-link-1", "init_point": "https://mercadopago.example/pay/pref-link-1"})


mp_integration.requests.post = post_falso
try:
    r = client.post(URL, follow_redirects=True)
finally:
    mp_integration.requests.post = post_original

checar("Gerar link responde 200", r.status_code == 200)
checar("Uma chamada ao Checkout Pro", len(chamadas) == 1 and "/checkout/preferences" in chamadas[0][0])
checar("external_reference no formato do webhook", chamadas[0][1]["external_reference"] == f"licenca_pagamento:{pag_id}")
checar("Valor cobrado e o mensal do medico", chamadas[0][1]["items"][0]["unit_price"] == 220.00)
with app.app_context():
    p = LicencaPagamento.query.get(pag_id)
    checar("mp_init_point gravado", p.mp_init_point == "https://mercadopago.example/pay/pref-link-1")
    checar("Ainda nao esta pago", p.pago is False)
html = client.get("/equipe/minha-licenca").get_data(as_text=True)
checar("Tela mostra 'Pagar agora' com o link", "https://mercadopago.example/pay/pref-link-1" in html)
checar("Botao de gerar some do mes que ja tem link", URL not in html)

# ---------- 4. Sem valor definido ----------
with app.app_context():
    u = Usuario.query.get(medico_id)
    u.valor_licenca_mensal = None
    prox = date.today().replace(day=1)
    p2 = LicencaPagamento.query.get(pag_id)
    p2.mp_init_point = None
    p2.valor = None
    db.session.commit()
chamadas.clear()
mp_integration.requests.post = post_falso
try:
    r = client.post(URL, follow_redirects=True)
finally:
    mp_integration.requests.post = post_original
checar("Sem valor definido: mensagem clara", "valor de licença" in r.get_data(as_text=True))
checar("Sem valor definido: nenhuma chamada de rede", len(chamadas) == 0)
with app.app_context():
    Usuario.query.get(medico_id).valor_licenca_mensal = 220.00
    db.session.commit()

# ---------- 5. Isolamento ----------
r = client.post(f"/equipe/minha-licenca/pagamentos/{pag_outro_id}/link")
checar("Gerar link de pagamento de outro medico da 404", r.status_code == 404)

# ---------- 6. Mes ja pago ----------
with app.app_context():
    LicencaPagamento.query.get(pag_id).pago = True
    db.session.commit()
r = client.post(URL, follow_redirects=True)
checar("Mes ja pago e recusado", "já está pago" in r.get_data(as_text=True))
with app.app_context():
    LicencaPagamento.query.get(pag_id).pago = False
    db.session.commit()

# ---------- 7. Ciclo anual ----------
with app.app_context():
    u = Usuario.query.get(medico_id)
    u.ciclo_licenca = "anual"
    u.valor_licenca_anual = 2000.00
    garantir_meses_licenca(u, fim=date(date.today().year + 1, 1, 1))
    db.session.commit()
    futuro = LicencaPagamento.query.filter(
        LicencaPagamento.usuario_id == medico_id, LicencaPagamento.mes > mes_atual
    ).first()
    futuro_id = futuro.id

chamadas.clear()
mp_integration.requests.post = post_falso
try:
    r = client.post(f"/equipe/minha-licenca/pagamentos/{futuro_id}/link", follow_redirects=True)
    checar("Anual: mes futuro e recusado", "ciclo anual" in r.get_data(as_text=True) and len(chamadas) == 0)
    r = client.post(URL, follow_redirects=True)
finally:
    mp_integration.requests.post = post_original
checar("Anual: mes vigente gera cobranca anual", len(chamadas) == 1 and chamadas[0][1]["external_reference"] == f"licenca_anual:{pag_id}")
checar("Anual: valor cobrado e o anual", chamadas[0][1]["items"][0]["unit_price"] == 2000.00)

print("\nTodos os testes passaram.")
