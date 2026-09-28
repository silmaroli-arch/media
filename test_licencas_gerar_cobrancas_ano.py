"""Testa a geração em massa de cobranças Mercado Pago pra "o resto do
ano", pedida pelo Silvan em 2026-09-25 (ver dono.licencas_gerar_cobrancas_
ano em app/routes_dono.py) - em vez de entrar usuário por usuário/mês por
mês na tela de pagamentos gerando "na mão" (usuario_licenca_pagamento_
cobrar), o dono aperta um botão só em /dono/usuarios e o sistema gera a
cobrança dos meses que faltam neste ano civil (do mês seguinte ao atual
até dezembro) pra TODO médico em ciclo mensal, respeitando 3 critérios
decididos com ele:

- só médico em ciclo MENSAL (quem está no ciclo anual usa o próprio botão
  de cobrança anual, que já cobre o ano inteiro - gerar mensal pra ele
  aqui cobraria em duplicado);
- só meses ainda NÃO PAGOS (não gera cobrança pra quem já pagou, Pix ou
  acordo informal);
- só onde ainda NÃO existe cobrança gerada (não substitui/duplica um link
  já ativo).

Não faz nenhuma chamada de rede de verdade - `requests.post` de
app/mercadopago_integration.py é substituído por uma versão falsa (mesmo
padrão de test_licenca_pagamento_valor_e_gateway.py).

Roda contra SQLite local (não usa a DATABASE_URL do .env) e reseta esse
banco de teste no início — não toca no banco real. Para rodar:
    DATABASE_URL=sqlite:///teste_licencas_ano.db python test_licencas_gerar_cobrancas_ano.py
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

with app.app_context():
    resetar_banco(db)
    PlataformaConfig.obter().valor_licenca_padrao = 200.00
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


post_original = mp_integration.requests.post


def post_falso(url, json=None, headers=None, timeout=None):
    pref_id = f"pref-{json['external_reference']}"
    return RespostaFalsa({"id": pref_id, "init_point": f"https://mercadopago.example/pay/{pref_id}"})


def _qtd_meses(inicio, fim):
    """Quantidade de meses entre `inicio` e `fim`, inclusive (ambos no dia
    1) - usado só pra checar a quantidade de linhas de LicencaPagamento
    que a rota em massa deveria ter criado/afetado."""
    return (fim.year - inicio.year) * 12 + (fim.month - inicio.month) + 1


# ---------- Setup: dono + 4 médicos cobrindo os 4 cenários ----------
with app.app_context():
    dono = Usuario(nome="Dono Teste", email="dono.anocobranca@example.com", tipo="dono")
    dono.set_senha("123456")
    db.session.add(dono)

    # A: ciclo mensal, com valor, sem nada gerado ainda -> deve GERAR.
    medico_a = Usuario(nome="Dr. A Gerar", email="medico.a.gerar@example.com", tipo="medico", ativo=True)
    medico_a.set_senha("123456")
    medico_a.valor_licenca_mensal = 200.00
    medico_a.ciclo_licenca = "mensal"

    # B: ciclo ANUAL -> não deve ser tocado (fica de fora do critério).
    medico_b = Usuario(nome="Dra. B Anual", email="medico.b.anual@example.com", tipo="medico", ativo=True)
    medico_b.set_senha("123456")
    medico_b.valor_licenca_mensal = 300.00
    medico_b.ciclo_licenca = "anual"

    # C: ciclo mensal, SEM valor definido -> deve pular (ValueError).
    medico_c = Usuario(nome="Dr. C Sem Valor", email="medico.c.sem-valor@example.com", tipo="medico", ativo=True)
    medico_c.set_senha("123456")
    medico_c.ciclo_licenca = "mensal"
    medico_c.valor_licenca_mensal = None

    db.session.add_all([medico_a, medico_b, medico_c])
    db.session.commit()
    medico_a_id, medico_b_id, medico_c_id = medico_a.id, medico_b.id, medico_c.id
    dono_email = dono.email

hoje = date.today()

with app.app_context():
    # D: ciclo mensal, com um mês futuro que já foi pago na mão E outro
    # que já tem cobrança gerada - nenhum dos dois deve ser tocado.
    medico_d = Usuario(nome="Dra. D Casos Especiais", email="medico.d.especial@example.com", tipo="medico", ativo=True)
    medico_d.set_senha("123456")
    medico_d.valor_licenca_mensal = 250.00
    medico_d.ciclo_licenca = "mensal"
    db.session.add(medico_d)
    db.session.commit()
    medico_d_id = medico_d.id

client.post("/login", data={"identificador": dono_email, "senha": "123456"})

if hoje.month == 12:
    # Caso de borda: não há mês restante no ano civil - a rota deve avisar
    # e não criar nada, sem quebrar.
    mp_integration.requests.post = post_falso
    try:
        r = client.post("/dono/usuarios/gerar-cobrancas-ano", follow_redirects=True)
    finally:
        mp_integration.requests.post = post_original
    checar("Em dezembro, a rota responde 200 mesmo sem meses restantes", r.status_code == 200)
    checar("Aviso de 'já estamos em dezembro' aparece", "estamos em dezembro" in r.get_data(as_text=True))
    with app.app_context():
        checar("Nenhum LicencaPagamento foi criado pro médico A", LicencaPagamento.query.filter_by(usuario_id=medico_a_id).count() == 0)
    print("\nTodos os testes de test_licencas_gerar_cobrancas_ano.py passaram (caso de borda: dezembro).")
else:
    mes_inicio = date(hoje.year, hoje.month + 1, 1)
    mes_fim = date(hoje.year, 12, 1)

    with app.app_context():
        medico_d = Usuario.query.get(medico_d_id)
        garantir_meses_licenca(medico_d, fim=mes_fim)
        db.session.commit()
        pagamento_ja_pago = LicencaPagamento.query.filter_by(usuario_id=medico_d_id, mes=mes_inicio).first()
        pagamento_ja_pago.pago = True
        pagamento_ja_pago.pago_em = date.today()
        if mes_inicio != mes_fim:
            mes_seguinte = date(mes_inicio.year, mes_inicio.month + 1, 1) if mes_inicio.month < 12 else date(mes_inicio.year + 1, 1, 1)
            pagamento_ja_tem_link = LicencaPagamento.query.filter_by(usuario_id=medico_d_id, mes=mes_seguinte).first()
            if pagamento_ja_tem_link:
                pagamento_ja_tem_link.mp_init_point = "https://mercadopago.example/pay/ja-existia"
                pagamento_ja_tem_link.mp_preference_id = "pref-ja-existia"
        db.session.commit()

    mp_integration.requests.post = post_falso
    try:
        r = client.post("/dono/usuarios/gerar-cobrancas-ano", follow_redirects=True)
    finally:
        mp_integration.requests.post = post_original

    checar("Rota responde 200", r.status_code == 200)
    html = r.get_data(as_text=True)
    checar("Mensagem de resumo aparece (cobranças geradas)", "cobrança" in html)

    with app.app_context():
        # A: cada mês restante ganhou mp_init_point.
        pagamentos_a = LicencaPagamento.query.filter(
            LicencaPagamento.usuario_id == medico_a_id,
            LicencaPagamento.mes >= mes_inicio,
            LicencaPagamento.mes <= mes_fim,
        ).all()
        checar("Médico A teve os meses restantes criados", len(pagamentos_a) == _qtd_meses(mes_inicio, mes_fim))
        checar("Todos os meses do médico A ganharam link de pagamento", all(p.mp_init_point for p in pagamentos_a))
        checar("Nenhum mês do médico A foi marcado como pago (só a cobrança foi gerada)", all(not p.pago for p in pagamentos_a))

        # B (ciclo anual): não deve ter NENHUM LicencaPagamento criado pela
        # rota em massa (ela nem chama garantir_meses_licenca pra ele).
        checar(
            "Médico B (ciclo anual) não foi afetado pela geração em massa",
            LicencaPagamento.query.filter_by(usuario_id=medico_b_id).count() == 0,
        )

        # C (sem valor): meses foram criados (garantir_meses_licenca roda
        # pra todo médico mensal), mas NENHUM ganhou link (ValueError pulado).
        pagamentos_c = LicencaPagamento.query.filter(
            LicencaPagamento.usuario_id == medico_c_id,
            LicencaPagamento.mes >= mes_inicio,
            LicencaPagamento.mes <= mes_fim,
        ).all()
        checar("Médico C sem valor: nenhum mês ganhou link de pagamento", all(not p.mp_init_point for p in pagamentos_c))

        # D: o mês já pago continua pago e SEM link; o mês que já tinha
        # link manteve o link ORIGINAL (não foi substituído).
        pagamento_ja_pago_depois = LicencaPagamento.query.filter_by(usuario_id=medico_d_id, mes=mes_inicio).first()
        checar("Mês já pago do médico D continua pago", pagamento_ja_pago_depois.pago is True)
        checar("Mês já pago do médico D não ganhou link (não gera cobrança de quem já pagou)", pagamento_ja_pago_depois.mp_init_point is None)

        if mes_inicio != mes_fim:
            mes_seguinte = date(mes_inicio.year, mes_inicio.month + 1, 1) if mes_inicio.month < 12 else date(mes_inicio.year + 1, 1, 1)
            pagamento_com_link_antigo = LicencaPagamento.query.filter_by(usuario_id=medico_d_id, mes=mes_seguinte).first()
            checar(
                "Mês do médico D que já tinha cobrança manteve o link ORIGINAL (não duplicou)",
                pagamento_com_link_antigo.mp_init_point == "https://mercadopago.example/pay/ja-existia",
            )

    print("\nTodos os testes de test_licencas_gerar_cobrancas_ano.py passaram.")

client.get("/logout")
