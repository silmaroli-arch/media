"""Testa o cadastro de preparo com ESPECIALIDADE, filtro de tipos por
especialidade e "Nao encontrei o meu exame" (2026-09-30).

Sem rede. Banco recriado, sem seed:
    DATABASE_URL=sqlite:///teste_tipo_nao_encontrado.db python test_tipo_exame_nao_encontrado.py
"""
from app import create_app
from app.extensions import db
from app.db_utils import resetar_banco
from app.models import Usuario, PreparoModelo, TipoExame, TipoExameSugestao
from app.tipos_exame_padrao import semear_tipos_exame

app = create_app()
client = app.test_client()
EXTRA_CADASTRO = {
    "senha_confirmacao": "123456", "telefone": "(27) 99999-1234", "cep": "29010-000", "rua": "Rua A",
    "numero": "1", "bairro": "Centro", "cidade": "Vitoria", "uf": "ES",
}

with app.app_context():
    resetar_banco(db)
    db.session.commit()


def checar(nome, condicao):
    print(f"[{'OK' if condicao else 'FALHOU'}] {nome}")
    assert condicao, nome


def login(email):
    client.get("/logout")
    client.post("/login", data={"identificador": email, "senha": "123456"})


client.post("/cadastro", data={**EXTRA_CADASTRO,
    "nome": "Dr. Outro Exame", "papel": "medico", "cpf": "111.222.333-97", "crm_numero": "70707", "crm_uf": "ES",
    "data_nascimento": "10/05/1980", "email": "outro.exame@example.com", "senha": "123456",
    "especialidade": "Gastroenterologia"}, follow_redirects=True)
client.get("/logout")
with app.app_context():
    dono = Usuario.query.filter_by(tipo="dono").first()
    if not dono:
        dono = Usuario(nome="Dono Teste", email="dono.teste@example.com", tipo="dono")
        dono.set_senha("123456")
        db.session.add(dono)
        db.session.commit()
    dono_email = dono.email
    semear_tipos_exame(db, TipoExame)
    medico_id = Usuario.query.filter_by(email="outro.exame@example.com").first().id

login("outro.exame@example.com")
html = client.get("/equipe/preparo-modelos/novo").get_data(as_text=True)
checar("Formulario tem o campo de especialidade do medico", 'name="especialidade"' in html and "Gastroenterologia" in html)
checar("Tipos trazem as especialidades para o filtro", 'data-esp="' in html and "gastroenterologia" in html)
checar("Lista tem 'Nao encontrei o meu exame'", 'value="outro"' in html and "Não encontrei o meu exame" in html)

r = client.post("/equipe/preparo-modelos/novo", data={"nome": "Preparo Exame Novo", "instrucoes": "Jejum.", "tipo_exame_id": "outro"}, follow_redirects=True)
checar("Sem digitar o nome do exame: recusado", "Informe o nome do exame" in r.get_data(as_text=True))

r = client.post("/equipe/preparo-modelos/novo", data={
    "nome": "Preparo Exame Novo", "instrucoes": "Jejum.", "tipo_exame_id": "outro",
    "tipo_exame_outro": "Elastografia hepática", "especialidade": "Hepatologia inventada"}, follow_redirects=True)
with app.app_context():
    prep = PreparoModelo.query.filter_by(nome="Preparo Exame Novo").first()
    checar("Preparo foi salvo sem tipo", prep is not None and prep.tipo_exame_id is None)
    sug = TipoExameSugestao.query.filter_by(nome="Elastografia hepática").first()
    checar("Sugestao criada, pendente, com autor e especialidade", sug and sug.status == "pendente" and sug.autor_usuario_id == medico_id and sug.especialidade == "Gastroenterologia")
    checar("Preparo ligado a sugestao", prep.tipo_exame_sugestao_id == sug.id)
    checar("Especialidade fora da lista nao altera o cadastro", Usuario.query.get(medico_id).especialidade == "Gastroenterologia")
    prep_id, sug_id = prep.id, sug.id

client.post("/equipe/preparo-modelos/novo", data={
    "nome": "Preparo Exame Novo 2", "instrucoes": "Jejum.", "tipo_exame_id": "outro",
    "tipo_exame_outro": "elastografia hepática", "especialidade": "Hepatologia inventada"}, follow_redirects=True)
with app.app_context():
    checar("Mesmo nome pendente reaproveita a sugestao", TipoExameSugestao.query.count() == 1)


# ---------- Dono ----------
login("outro.exame@example.com")
r = client.get("/dono/tipos-exame")
checar("Medico nao acessa a tela do dono", r.status_code in (302, 401, 403))
login(dono_email)
html = client.get("/dono/tipos-exame").get_data(as_text=True)
checar("Dono ve a sugestao pendente", "Elastografia hepática" in html and "Criar tipo" in html)
client.post(f"/dono/tipos-exame/sugestoes/{sug_id}/criar", data={"nome": "Elastografia hepática", "especialidades": "Gastroenterologia, Hepatologia"})
with app.app_context():
    tipo = TipoExame.query.filter_by(nome="Elastografia hepática").first()
    checar("Tipo criado com as especialidades", tipo and "Hepatologia" in tipo.especialidades)
    prep = PreparoModelo.query.get(prep_id)
    checar("Preparo passou a apontar para o tipo novo", prep.tipo_exame_id == tipo.id and prep.tipo_exame_sugestao_id is None)
    checar("Sugestao ficou aprovada", TipoExameSugestao.query.get(sug_id).status == "aprovada")

# ---------- Vincular e rejeitar ----------
login("outro.exame@example.com")
client.post("/equipe/preparo-modelos/novo", data={"nome": "Preparo Colono Outro Nome", "instrucoes": "Jejum.", "tipo_exame_id": "outro", "tipo_exame_outro": "Colono simples"}, follow_redirects=True)
client.post("/equipe/preparo-modelos/novo", data={"nome": "Preparo Lixo", "instrucoes": "Jejum.", "tipo_exame_id": "outro", "tipo_exame_outro": "Exame inexistente xyz"}, follow_redirects=True)
with app.app_context():
    s_colono = TipoExameSugestao.query.filter_by(nome="Colono simples").first().id
    s_lixo = TipoExameSugestao.query.filter_by(nome="Exame inexistente xyz").first().id
    colono_id = TipoExame.query.filter_by(nome="Colonoscopia").first().id
login(dono_email)
client.post(f"/dono/tipos-exame/sugestoes/{s_colono}/vincular", data={"tipo_exame_id": colono_id})
client.post(f"/dono/tipos-exame/sugestoes/{s_lixo}/rejeitar")
with app.app_context():
    checar("Vincular liga o preparo ao tipo existente", PreparoModelo.query.filter_by(nome="Preparo Colono Outro Nome").first().tipo_exame_id == colono_id)
    checar("Rejeitada deixa o preparo sem tipo", TipoExameSugestao.query.get(s_lixo).status == "rejeitada"
           and PreparoModelo.query.filter_by(nome="Preparo Lixo").first().tipo_exame_id is None)

# Especialidade valida do formulario atualiza o cadastro
login("outro.exame@example.com")
client.post("/equipe/preparo-modelos/novo", data={"nome": "Preparo Muda Esp", "instrucoes": "Jejum.", "tipo_exame_id": str(colono_id), "especialidade": "Coloproctologia"}, follow_redirects=True)
with app.app_context():
    checar("Escolher especialidade no preparo atualiza o cadastro do medico", Usuario.query.get(medico_id).especialidade == "Coloproctologia")
print("\nTodos os testes passaram.")
