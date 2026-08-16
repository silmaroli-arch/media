"""Testa a IDENTIDADE ÚNICA E GLOBAL do paciente (Fatia 5):

A base de pacientes vale para o aplicativo todo no sentido de PESSOA: a
mesma pessoa (CPF) tem UM ÚNICO cadastro (Paciente) na plataforma inteira
- não é mais "um cadastro por empresa" (Paciente.cpf agora tem UNIQUE
CONSTRAINT no banco). O vínculo com cada grupo de trabalho é feito por
GrupoPaciente (associação, não cópia de cadastro):

- Cadastrar a mesma pessoa (mesmo CPF) numa segunda empresa/grupo pela
  equipe NÃO cria outro cadastro nem outra conta: é bloqueado, orientando
  a equipe a usar "Buscar por CPF" (medico.pacientes_importar), que cria
  só a associação (GrupoPaciente) nova com o grupo atual.
- Telefone compartilhado (família) com data de nascimento DIFERENTE
  continua criando CPF/conta/cadastro separados - são pessoas diferentes.
- No login (CPF + data de nascimento), a pessoa entra direto - com CPF
  globalmente único só existe UM cadastro correspondente.
- PRIVACIDADE (LGPD): cada grupo só vê pacientes que tenham GrupoPaciente
  com ele - importar não expõe o histórico de outra clínica.
"""
from app import create_app
from app.models import Usuario, Grupo, Paciente, GrupoPaciente, normalizar_telefone

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login_equipe(email, senha="123456", grupo_id=None):
    r = client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)
    if grupo_id is not None:
        r = client.post("/equipe/clinica", data={"empresa_id": str(grupo_id)}, follow_redirects=True)
    return r


TEL = "(27) 98888-7001"
TEL_NORM = normalizar_telefone(TEL)
CPF_ANA = "710.820.930-04"
CPF_FILHO = "710.820.930-15"

with app.app_context():
    grupo_centro = Grupo.query.filter_by(nome="Grupo Saúde Total - Centro").first()
    grupo_vitoria = Grupo.query.filter_by(nome="Clínica Vitória").first()
    grupo_sp = Grupo.query.filter_by(nome="Clínica São Paulo").first()
    centro_id, vitoria_id, sp_id = grupo_centro.id, grupo_vitoria.id, grupo_sp.id

# ---------- 1º grupo: cria a conta e o cadastro ----------

login_equipe("secretaria@gruposaude.com", grupo_id=centro_id)
r = client.post("/equipe/pacientes/novo", data={
    "nome": "Ana Portatil", "cpf": CPF_ANA, "email": "",
    "telefone": TEL, "data_nascimento": "1991-04-15",
}, follow_redirects=True)
checar("Cadastro no 1º grupo funciona", "cadastrado" in r.get_data(as_text=True).lower())
client.get("/logout")

with app.app_context():
    contas = Usuario.query.filter_by(telefone=TEL_NORM, tipo="paciente").all()
    checar("Uma conta criada", len(contas) == 1)
    conta_id = contas[0].id
    pacientes_ana = Paciente.query.filter_by(cpf=CPF_ANA).all()
    checar("Um único cadastro (Paciente) criado para esse CPF", len(pacientes_ana) == 1)
    ana_paciente_id = pacientes_ana[0].id
    checar(
        "O cadastro já está associado ao grupo em que foi criado",
        GrupoPaciente.query.filter_by(grupo_id=centro_id, paciente_id=ana_paciente_id).count() == 1,
    )

# ---------- 2º grupo: cadastrar de novo com o MESMO CPF é bloqueado ----------
# (o cadastro é global e único por CPF - não dá pra criar uma cópia).

login_equipe("secretaria@clinicavitoria.com")
r = client.post("/equipe/pacientes/novo", data={
    "nome": "Ana Portatil", "cpf": CPF_ANA, "email": "",
    "telefone": TEL, "data_nascimento": "1991-04-15",
}, follow_redirects=True)
checar(
    "Cadastrar de novo com o mesmo CPF em outro grupo é barrado, orientando a importar",
    "já tem cadastro na plataforma" in r.get_data(as_text=True) and "Buscar por CPF" in r.get_data(as_text=True),
)
with app.app_context():
    checar("NÃO duplicou o cadastro (Paciente) da Ana", Paciente.query.filter_by(cpf=CPF_ANA).count() == 1)
    checar("NÃO criou conta nova para a Ana", Usuario.query.filter_by(telefone=TEL_NORM, tipo="paciente").count() == 1)

# ---------- 2º grupo: IMPORTAR pelo CPF cria só a associação (GrupoPaciente) ----------

r = client.post("/equipe/pacientes/importar", data={"cpf": CPF_ANA}, follow_redirects=True)
checar("Importar pelo CPF no 2º grupo funciona", "importado" in r.get_data(as_text=True).lower())

with app.app_context():
    checar("CONTINUA um único cadastro (Paciente) para a Ana", Paciente.query.filter_by(cpf=CPF_ANA).count() == 1)
    checar(
        "Agora a Ana está associada a DOIS grupos",
        GrupoPaciente.query.filter_by(paciente_id=ana_paciente_id).count() == 2
        and {gp.grupo_id for gp in GrupoPaciente.query.filter_by(paciente_id=ana_paciente_id).all()}
        == {centro_id, vitoria_id},
    )

# PRIVACIDADE: cada equipe vê só quem está associado ao grupo dela.
r = client.get("/equipe/pacientes")
checar("Clínica Vitória vê a Ana (já importada)", "Ana Portatil" in r.get_data(as_text=True))
client.get("/logout")
login_equipe("secretaria@clinicasp.com")
r = client.get("/equipe/pacientes")
checar("Clínica SP NÃO vê a Ana (ainda não foi importada lá)", "Ana Portatil" not in r.get_data(as_text=True))

# ---------- 3º grupo (importação pelo CPF) ----------

r = client.post("/equipe/pacientes/importar", data={"cpf": CPF_ANA}, follow_redirects=True)
checar("Importar pelo CPF no 3º grupo funciona", "importado" in r.get_data(as_text=True).lower())
client.get("/logout")

with app.app_context():
    checar("CONTINUA um único cadastro (Paciente) para a Ana", Paciente.query.filter_by(cpf=CPF_ANA).count() == 1)
    checar(
        "Agora a Ana está associada a TRÊS grupos",
        GrupoPaciente.query.filter_by(paciente_id=ana_paciente_id).count() == 3,
    )
    ana = Paciente.query.get(ana_paciente_id)
    checar("O cadastro importado pela equipe continua aprovado", ana.status_cadastro == "aprovado")

# ---------- Família: mesmo telefone, nascimento diferente = OUTRO CPF/conta/cadastro ----------

login_equipe("secretaria@gruposaude.com", grupo_id=centro_id)
r = client.post("/equipe/pacientes/novo", data={
    "nome": "Filho Da Ana", "cpf": CPF_FILHO, "email": "",
    "telefone": TEL, "data_nascimento": "2015-10-20",
}, follow_redirects=True)
checar("Familiar com o mesmo telefone e OUTRO nascimento é cadastrado", "cadastrado" in r.get_data(as_text=True).lower())
client.get("/logout")
with app.app_context():
    contas = Usuario.query.filter_by(telefone=TEL_NORM, tipo="paciente").all()
    checar("Pessoa diferente = conta separada (agora 2 contas no telefone)", len(contas) == 2)
    checar("Pessoa diferente = cadastro (Paciente) separado", Paciente.query.filter_by(cpf=CPF_FILHO).count() == 1)

# ---------- Login: CPF único -> entra direto (não existe mais "escolher clínica") ----------

r = client.post("/login-paciente", data={"cpf": CPF_ANA, "data_nascimento": "15/04/1991"},
                follow_redirects=True)
html = r.get_data(as_text=True)
checar("Login da Ana entra direto (CPF é único, sem tela de escolha)",
       'name="paciente_id_escolhido"' not in html and "Olá, Ana Portatil" in html)
client.get("/logout")

# O filho (outra conta/cadastro) também loga direto.
r = client.post("/login-paciente", data={"cpf": CPF_FILHO, "data_nascimento": "20/10/2015"},
                follow_redirects=True)
checar("O filho loga direto (CPF diferente, cadastro próprio)",
       'name="paciente_id_escolhido"' not in html and r.status_code == 200)
client.get("/logout")

print("\nTodos os testes de identidade única/global do paciente passaram.")
