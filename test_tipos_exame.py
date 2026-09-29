"""Testa a lista de TIPOS DE EXAME e o campo "Tipo de exame" do cadastro de
preparo (pedido do Silvan, 2026-09-29 - fatia 1 da "terceira IA"/base de
conhecimento) - ver app.models.TipoExame, app.tipos_exame_padrao e
medico.preparo_modelos_novo/editar.

Cobre:
1. Lista padrao: semear e idempotente e nao mexe em tipo que o dono alterou.
2. Sem nenhum tipo cadastrado (banco vazio), o preparo novo NAO exige tipo.
3. Com tipos ativos: preparo novo SEM tipo e recusado, COM tipo e salvo.
4. Tipo invalido/inativo nao e aceito em preparo novo.
5. Editar preparo: troca de tipo funciona, e tipo ja inativado continua valido.
6. Dono: adicionar, renomear, inativar e a tela lista (nome duplicado recusado).
7. Medico comum nao acessa /dono/tipos-exame.

Sem rede. Para rodar:
    DATABASE_URL=sqlite:///teste_tipos_exame.db python test_tipos_exame.py
"""
from app import create_app
from app.extensions import db
from app.db_utils import resetar_banco
from app.models import Usuario, PreparoModelo, TipoExame
from app.tipos_exame_padrao import semear_tipos_exame, TIPOS_EXAME_PADRAO

app = create_app()
client = app.test_client()

with app.app_context():
    resetar_banco(db)
    db.session.commit()


def checar(nome, condicao):
    print(f"[{'OK' if condicao else 'FALHOU'}] {nome}")
    assert condicao, nome


# ---------- Setup: um medico e o dono ----------
r = client.post("/cadastro", data={
    "nome": "Dr. Tipo Teste", "papel": "medico", "cpf": "111.222.333-97", "crm_numero": "77777",
    "crm_uf": "ES", "data_nascimento": "10/05/1980", "email": "tipo.medico@example.com", "senha": "123456",
}, follow_redirects=True)
client.get("/logout")

with app.app_context():
    dono = Usuario.query.filter_by(tipo="dono").first()
    if not dono:
        dono = Usuario(nome="Dono Teste", email="dono.teste@example.com", tipo="dono")
        dono.set_senha("123456")
        db.session.add(dono)
        db.session.commit()
    dono_email = dono.email


def login(email):
    client.get("/logout")
    client.post("/login", data={"identificador": email, "senha": "123456"})


def novo_preparo(nome, **extra):
    dados = {"nome": nome, "instrucoes": "Jejum de 8 horas.", "observacoes_medicamentos": ""}
    dados.update(extra)
    return client.post("/equipe/preparo-modelos/novo", data=dados, follow_redirects=True)


# ---------- 2. Banco sem tipos: nao exige ----------
login("tipo.medico@example.com")
r = novo_preparo("Preparo Sem Lista")
checar("Sem tipos cadastrados, preparo novo e salvo sem tipo", "cadastrado com sucesso" in r.get_data(as_text=True).lower())
with app.app_context():
    checar("Preparo ficou sem tipo", PreparoModelo.query.filter_by(nome="Preparo Sem Lista").first().tipo_exame_id is None)

# ---------- 1. Semear ----------
with app.app_context():
    criados = semear_tipos_exame(db, TipoExame)
    checar("Semear cria toda a lista padrao", criados == len(TIPOS_EXAME_PADRAO) and TipoExame.query.count() == len(TIPOS_EXAME_PADRAO))
    colono = TipoExame.query.filter_by(nome="Colonoscopia").first()
    colono.especialidades = "Especialidade Editada"
    outro = TipoExame.query.filter_by(nome="Retossigmoidoscopia").first()
    outro.ativo = False
    db.session.commit()
    checar("Semear de novo nao cria nada", semear_tipos_exame(db, TipoExame) == 0)
    colono = TipoExame.query.filter_by(nome="Colonoscopia").first()
    checar("Especialidade editada pelo dono foi preservada", colono.especialidades == "Especialidade Editada")
    checar("Tipo inativado pelo dono continua inativo", TipoExame.query.filter_by(nome="Retossigmoidoscopia").first().ativo is False)
    colono_id = colono.id
    inativo_id = TipoExame.query.filter_by(nome="Retossigmoidoscopia").first().id
    endo_id = TipoExame.query.filter_by(nome="Endoscopia digestiva alta").first().id

# ---------- 3. Com tipos: obrigatorio ----------
html = client.get("/equipe/preparo-modelos/novo").get_data(as_text=True)
checar("Formulario mostra o dropdown de tipo", 'name="tipo_exame_id"' in html and "Colonoscopia" in html)
checar("Dropdown nao lista tipo inativo", "Retossigmoidoscopia" not in html)

r = novo_preparo("Preparo Sem Tipo")
checar("Sem escolher tipo: recusado com aviso", "Escolha o tipo de exame" in r.get_data(as_text=True))
with app.app_context():
    checar("Nada foi criado sem tipo", PreparoModelo.query.filter_by(nome="Preparo Sem Tipo").first() is None)

r = novo_preparo("Preparo Colono", tipo_exame_id=str(colono_id))
checar("Com tipo: salvo", "cadastrado com sucesso" in r.get_data(as_text=True).lower())
with app.app_context():
    m = PreparoModelo.query.filter_by(nome="Preparo Colono").first()
    checar("tipo_exame_id gravado", m is not None and m.tipo_exame_id == colono_id)
    modelo_id = m.id

# ---------- 4. Invalido / inativo ----------
r = novo_preparo("Preparo Inativo", tipo_exame_id=str(inativo_id))
checar("Tipo inativo nao e aceito em preparo novo", "Tipo de exame inválido" in r.get_data(as_text=True))
r = novo_preparo("Preparo Lixo", tipo_exame_id="abc")
checar("Tipo nao numerico e recusado", "Tipo de exame inválido" in r.get_data(as_text=True))

# ---------- 5. Editar ----------
r = client.post(f"/equipe/preparo-modelos/{modelo_id}/editar", data={
    "nome": "Preparo Colono", "instrucoes": "Jejum de 8 horas.", "observacoes_medicamentos": "",
    "tipo_exame_id": str(endo_id),
}, follow_redirects=True)
with app.app_context():
    checar("Editar troca o tipo", PreparoModelo.query.get(modelo_id).tipo_exame_id == endo_id)

with app.app_context():
    TipoExame.query.get(endo_id).ativo = False
    db.session.commit()
r = client.post(f"/equipe/preparo-modelos/{modelo_id}/editar", data={
    "nome": "Preparo Colono", "instrucoes": "Jejum de 10 horas.", "observacoes_medicamentos": "",
    "tipo_exame_id": str(endo_id),
}, follow_redirects=True)
with app.app_context():
    m = PreparoModelo.query.get(modelo_id)
    checar("Tipo que o preparo ja tinha continua valido mesmo inativado", m.tipo_exame_id == endo_id and "10 horas" in m.instrucoes)
with app.app_context():
    TipoExame.query.get(endo_id).ativo = True
    db.session.commit()

# ---------- 7. Medico comum ----------
r = client.get("/dono/tipos-exame")
checar("Medico comum nao acessa a tela do dono", r.status_code in (302, 401, 403))

# ---------- 6. Dono ----------
login(dono_email)
html = client.get("/dono/tipos-exame").get_data(as_text=True)
checar("Dono ve a lista", "Tipos de exame que exigem preparo" in html and "Colonoscopia" in html)
client.post("/dono/tipos-exame/novo", data={"nome": "Exame Novo Do Dono", "especialidades": "Clínica médica"}, follow_redirects=True)
with app.app_context():
    novo = TipoExame.query.filter_by(nome="Exame Novo Do Dono").first()
    checar("Dono adicionou um tipo", novo is not None and novo.ativo)
    novo_id = novo.id
r = client.post("/dono/tipos-exame/novo", data={"nome": "exame novo do dono"}, follow_redirects=True)
checar("Nome duplicado (sem diferenciar maiusculas) e recusado", "Já existe" in r.get_data(as_text=True))
client.post(f"/dono/tipos-exame/{novo_id}/editar", data={"nome": "Exame Renomeado", "especialidades": "Urologia"}, follow_redirects=True)
with app.app_context():
    t = TipoExame.query.get(novo_id)
    checar("Dono renomeou e trocou especialidades", t.nome == "Exame Renomeado" and t.lista_especialidades == ["Urologia"])
client.post(f"/dono/tipos-exame/{novo_id}/alternar", follow_redirects=True)
with app.app_context():
    checar("Dono inativou", TipoExame.query.get(novo_id).ativo is False)
client.post(f"/dono/tipos-exame/{novo_id}/alternar", follow_redirects=True)
with app.app_context():
    checar("Dono reativou", TipoExame.query.get(novo_id).ativo is True)

print("\nTodos os testes passaram.")
