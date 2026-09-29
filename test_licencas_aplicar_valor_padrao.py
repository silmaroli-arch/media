"""Testa o botao "Aplicar valor padrao a todos" em /dono/usuarios (pedido do
Silvan, 2026-09-29) - ver dono.licencas_aplicar_valor_padrao.

Cobre:
1. Sem valor padrao configurado: aviso, nada muda.
2. Medico sem valor recebe o padrao (mensal e anual); medico com valor
   proprio NAO e sobrescrito.
3. Mes em aberto (nao pago, sem link/Pix, sem valor) recebe o valor; mes
   pago e mes com link ja gerado NAO mudam.
4. Medico em ciclo anual nao tem os meses mensais alterados.
5. Rodar de novo nao muda nada (idempotente).
6. Nao-dono nao acessa a rota.

Sem rede. Para rodar:
    DATABASE_URL=sqlite:///teste_aplicar_valor_padrao.db python test_licencas_aplicar_valor_padrao.py
"""
from datetime import date

from app import create_app
from app.extensions import db
from app.db_utils import resetar_banco
from app.models import Usuario, LicencaPagamento, PlataformaConfig, garantir_meses_licenca

app = create_app()
client = app.test_client()

with app.app_context():
    resetar_banco(db)
    db.session.commit()


def checar(nome, condicao):
    print(f"[{'OK' if condicao else 'FALHOU'}] {nome}")
    assert condicao, nome


def cadastrar(nome, email, cpf, crm):
    r = client.post("/cadastro", data={
        "nome": nome, "papel": "medico", "cpf": cpf, "crm_numero": crm, "crm_uf": "ES",
        "data_nascimento": "10/05/1980", "email": email, "senha": "123456",
    }, follow_redirects=True)
    assert r.status_code == 200
    client.get("/logout")
    with app.app_context():
        return Usuario.query.filter_by(email=email).first().id


sem_valor = cadastrar("Dr. Sem Valor", "semvalor@example.com", "111.222.333-97", "11111")
com_valor = cadastrar("Dr. Com Valor", "comvalor@example.com", "529.982.247-25", "22222")
anual = cadastrar("Dra. Anual", "anual@example.com", "390.533.447-05", "33333")

with app.app_context():
    for uid in (sem_valor, com_valor, anual):
        u = Usuario.query.get(uid)
        u.valor_licenca_mensal = None
        u.valor_licenca_anual = None
        u.licenca_status = "ativa"
        garantir_meses_licenca(u)
    Usuario.query.get(com_valor).valor_licenca_mensal = 300.00
    Usuario.query.get(anual).ciclo_licenca = "anual"
    db.session.commit()
    mes = date.today().replace(day=1)
    p_sem = LicencaPagamento.query.filter_by(usuario_id=sem_valor, mes=mes).first().id
    p_com = LicencaPagamento.query.filter_by(usuario_id=com_valor, mes=mes).first().id
    p_anual = LicencaPagamento.query.filter_by(usuario_id=anual, mes=mes).first().id
    # mes com link ja gerado (outro medico "sem valor", mes passado inventado)
    extra = LicencaPagamento(usuario_id=sem_valor, mes=date(2020, 1, 1), pago=False,
                             valor=None, mp_init_point="https://x/pay")
    pago = LicencaPagamento(usuario_id=sem_valor, mes=date(2020, 2, 1), pago=True, valor=None)
    db.session.add_all([extra, pago])
    db.session.commit()
    extra_id, pago_id = extra.id, pago.id

# ---------- 6. Nao-dono ----------
client.post("/login", data={"identificador": "semvalor@example.com", "senha": "123456"})
r = client.post("/dono/usuarios/aplicar-valor-padrao")
checar("Medico comum nao acessa a rota (nao 200)", r.status_code in (302, 401, 403, 404))
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

# ---------- 1. Sem padrao ----------
with app.app_context():
    c = PlataformaConfig.obter()
    c.valor_licenca_padrao = None
    c.valor_licenca_anual_padrao = None
    db.session.commit()
r = client.post("/dono/usuarios/aplicar-valor-padrao", follow_redirects=True)
checar("Sem padrao: aviso aparece", "Defina primeiro o valor" in r.get_data(as_text=True))
with app.app_context():
    checar("Sem padrao: nada mudou", Usuario.query.get(sem_valor).valor_licenca_mensal is None)

# ---------- 2/3/4. Aplicando ----------
with app.app_context():
    c = PlataformaConfig.obter()
    c.valor_licenca_padrao = 150.00
    c.valor_licenca_anual_padrao = 1500.00
    db.session.commit()
r = client.post("/dono/usuarios/aplicar-valor-padrao", follow_redirects=True)
checar("Aplicar responde 200 com resumo", r.status_code == 200 and "Valor padrão aplicado" in r.get_data(as_text=True))
with app.app_context():
    checar("Sem valor recebeu o mensal padrao", float(Usuario.query.get(sem_valor).valor_licenca_mensal) == 150.00)
    checar("Sem valor recebeu o anual padrao", float(Usuario.query.get(sem_valor).valor_licenca_anual) == 1500.00)
    checar("Valor proprio NAO foi sobrescrito", float(Usuario.query.get(com_valor).valor_licenca_mensal) == 300.00)
    checar("Mes em aberto do sem-valor recebeu 150", float(LicencaPagamento.query.get(p_sem).valor) == 150.00)
    checar("Mes em aberto do com-valor recebeu o valor DELE (300)", float(LicencaPagamento.query.get(p_com).valor) == 300.00)
    checar("Mes com link ja gerado nao mudou", LicencaPagamento.query.get(extra_id).valor is None)
    checar("Mes pago nao mudou", LicencaPagamento.query.get(pago_id).valor is None)
    checar("Ciclo anual: mes mensal nao foi tocado", LicencaPagamento.query.get(p_anual).valor is None)

# ---------- 5. Idempotente ----------
r = client.post("/dono/usuarios/aplicar-valor-padrao", follow_redirects=True)
checar("Segunda vez: nada a atualizar", "Nada a atualizar" in r.get_data(as_text=True))
print("\nTodos os testes passaram.")
