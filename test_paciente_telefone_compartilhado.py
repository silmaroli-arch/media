"""Testa a correção do bug: cadastrar um segundo paciente (ex.: filho) com o
mesmo telefone do responsável, na MESMA clínica, mas com data de nascimento
diferente, não deve mais ser bloqueado. Cobre as duas rotas afetadas:
- app/routes_medico.py -> medico.pacientes_novo (equipe cadastra o paciente)
- app/routes_auth.py -> auth.cadastro_paciente (autocadastro do paciente pelo link público)
Continua bloqueando o caso real de duplicata: mesmo telefone E mesma data de
nascimento na mesma clínica (a própria pessoa tentando se cadastrar de novo).

Roda contra SQLite local (não usa a DATABASE_URL do .env) e depende do
seed.py já ter rodado antes (usa a Clínica Vitória e a secretária padrão).
"""
from app import create_app
from app.extensions import db
from app.models import Usuario, Paciente, Clinica

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


TELEFONE_FAMILIA = "(27) 97777-6666"

# ---------- Equipe cadastra pai e filho com o mesmo telefone ----------
login("secretaria@clinicavitoria.com", "123456")

r = client.post("/equipe/pacientes/novo", data={
    "nome": "Pai Teste", "cpf": "555.444.333-22",
    "telefone": TELEFONE_FAMILIA, "data_nascimento": "10/05/1980",
}, follow_redirects=True)
checar("Cadastro do pai funciona", "cadastrado" in r.get_data(as_text=True).lower() or r.status_code == 200)
with app.app_context():
    checar("Paciente 'Pai Teste' foi criado", Paciente.query.filter_by(cpf="555.444.333-22").first() is not None)

r = client.post("/equipe/pacientes/novo", data={
    "nome": "Filho Teste", "cpf": "111.999.888-77",
    "telefone": TELEFONE_FAMILIA, "data_nascimento": "20/03/2015",
}, follow_redirects=True)
html = r.get_data(as_text=True)
checar(
    "Cadastro do filho com o MESMO telefone do pai (nascimento diferente) NÃO é mais bloqueado",
    "já existe" not in html.lower(),
)
with app.app_context():
    checar("Paciente 'Filho Teste' foi criado", Paciente.query.filter_by(cpf="111.999.888-77").first() is not None)
    total_com_telefone = Usuario.query.filter_by(telefone="27977776666").count()
    checar("Existem 2 contas distintas com o mesmo telefone (pai e filho)", total_com_telefone == 2)

# Tentar cadastrar de novo com o MESMO telefone E a MESMA data de nascimento do pai -> ainda deve bloquear.
r = client.post("/equipe/pacientes/novo", data={
    "nome": "Pai Duplicado", "cpf": "000.111.222-33",
    "telefone": TELEFONE_FAMILIA, "data_nascimento": "10/05/1980",
}, follow_redirects=True)
checar(
    "Duplicata REAL (mesmo telefone E mesma data de nascimento) continua bloqueada",
    "já existe um paciente cadastrado com esse telefone e data de nascimento" in r.get_data(as_text=True).lower(),
)
with app.app_context():
    checar("Paciente duplicado NÃO foi criado", Paciente.query.filter_by(cpf="000.111.222-33").first() is None)

client.get("/logout")

# ---------- Autocadastro público (link da clínica) com o mesmo telefone, nascimento diferente ----------
with app.app_context():
    clinica_vitoria = Clinica.query.filter_by(nome="Clínica Vitória").first()
    codigo = clinica_vitoria.codigo_cadastro_paciente
    if not codigo:
        import secrets
        codigo = secrets.token_urlsafe(8)
        clinica_vitoria.codigo_cadastro_paciente = codigo
        db.session.commit()

TELEFONE_FAMILIA2 = "(27) 96666-5555"

r = client.post(f"/paciente/cadastro/{codigo}", data={
    "nome": "Mãe Autocadastro", "cpf": "222.333.444-55",
    "telefone": TELEFONE_FAMILIA2, "data_nascimento": "01/01/1985",
}, follow_redirects=True)
with app.app_context():
    checar("Autocadastro da mãe funciona", Paciente.query.filter_by(cpf="222.333.444-55").first() is not None)
client.get("/logout")

r = client.post(f"/paciente/cadastro/{codigo}", data={
    "nome": "Filha Autocadastro", "cpf": "666.777.888-99",
    "telefone": TELEFONE_FAMILIA2, "data_nascimento": "15/09/2018",
}, follow_redirects=True)
html = r.get_data(as_text=True)
checar(
    "Autocadastro da filha com o MESMO telefone da mãe (nascimento diferente) NÃO é mais bloqueado",
    "já existe" not in html.lower(),
)
with app.app_context():
    checar("Paciente 'Filha Autocadastro' foi criado", Paciente.query.filter_by(cpf="666.777.888-99").first() is not None)
client.get("/logout")

print("\nTodos os testes de telefone compartilhado entre familiares passaram.")
