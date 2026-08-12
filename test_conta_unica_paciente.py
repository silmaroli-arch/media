"""Testa a CONTA ÚNICA do paciente (fase 1):

A base de pacientes vale para o aplicativo todo no sentido de PESSOA: a
mesma pessoa (telefone + data de nascimento) tem UMA conta de login, com
um CADASTRO (Paciente) por empresa que frequenta.

- Cadastrar a mesma pessoa numa segunda empresa (pela equipe ou pelo link
  de auto-cadastro) NÃO cria outra conta: reaproveita a existente, criando
  só o cadastro daquela empresa.
- Telefone compartilhado (família) com data de nascimento DIFERENTE
  continua criando contas separadas - são pessoas diferentes.
- No login, a pessoa escolhe qual clínica quer acessar agora (um cadastro
  por empresa) e a área do paciente usa o cadastro escolhido.
- PRIVACIDADE (LGPD): cada clínica continua vendo SÓ o cadastro dela -
  a conta é global só para o próprio paciente.
"""
from app import create_app
from app.extensions import db
from app.models import Usuario, Empresa, Clinica, Paciente, Exame, normalizar_telefone

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login_equipe(email, senha="123456"):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


TEL = "(27) 98888-7001"
TEL_NORM = normalizar_telefone(TEL)

with app.app_context():
    grupo = Empresa.query.filter_by(nome="Grupo Saúde Total").first()
    vitoria_emp = Clinica.query.filter_by(nome="Clínica Vitória").first().empresa
    sp_emp = Clinica.query.filter_by(nome="Clínica São Paulo").first().empresa
    grupo_id, vitoria_emp_id, sp_emp_id = grupo.id, vitoria_emp.id, sp_emp.id
    # Garante um código de auto-cadastro pra Clínica SP (o link é por empresa).
    if not sp_emp.codigo_cadastro_paciente:
        sp_emp.codigo_cadastro_paciente = "TESTESP1"
        db.session.commit()
    codigo_sp = sp_emp.codigo_cadastro_paciente
    centro = Clinica.query.filter_by(nome="Grupo Saúde Total - Centro").first()
    medico_grupo = Usuario.query.filter_by(email="medico@gruposaude.com").first()
    exame_grupo = Exame(clinica_id=centro.id, medico_id=medico_grupo.id, nome="Exame Conta Unica Grupo",
                        descricao="", duracao_minutos=30, medico_confirmado=True, associado=True)
    db.session.add(exame_grupo)
    db.session.commit()

# ---------- 1ª empresa: cria a conta ----------

login_equipe("secretaria@gruposaude.com")
r = client.post("/equipe/pacientes/novo", data={
    "nome": "Ana Portatil", "cpf": "710.820.930-04", "email": "",
    "telefone": TEL, "data_nascimento": "1991-04-15",
}, follow_redirects=True)
checar("Cadastro na 1ª empresa funciona", "cadastrado" in r.get_data(as_text=True).lower())
client.get("/logout")

with app.app_context():
    contas = Usuario.query.filter_by(telefone=TEL_NORM, tipo="paciente").all()
    checar("Uma conta criada", len(contas) == 1)
    conta_id = contas[0].id

# ---------- 2ª empresa (pela equipe): REUSA a conta ----------

login_equipe("secretaria@clinicavitoria.com")
r = client.post("/equipe/pacientes/novo", data={
    "nome": "Ana Portatil", "cpf": "710.820.930-04", "email": "",
    "telefone": TEL, "data_nascimento": "1991-04-15",
}, follow_redirects=True)
checar("Cadastro na 2ª empresa funciona", "cadastrado" in r.get_data(as_text=True).lower())

with app.app_context():
    contas = Usuario.query.filter_by(telefone=TEL_NORM, tipo="paciente").all()
    checar("CONTINUA uma conta só (não criou outra)", len(contas) == 1 and contas[0].id == conta_id)
    conta = contas[0]
    checar("A conta agora tem DOIS cadastros (um por empresa)",
           len(conta.pacientes) == 2
           and {p.empresa_id for p in conta.pacientes} == {grupo_id, vitoria_emp_id})

# PRIVACIDADE: cada equipe vê só o cadastro dela.
r = client.get("/equipe/pacientes")
checar("Clínica Vitória vê a Ana (cadastro dela)", "Ana Portatil" in r.get_data(as_text=True))
client.get("/logout")
login_equipe("secretaria@clinicasp.com")
r = client.get("/equipe/pacientes")
checar("Clínica SP NÃO vê a Ana (ainda não é paciente lá)", "Ana Portatil" not in r.get_data(as_text=True))
client.get("/logout")

# ---------- 3ª empresa (importação pelo CPF): também reusa ----------

# O link de auto-cadastro por clínica foi desativado: agora a SECRETÁRIA
# da 3ª empresa importa a Ana pelo CPF (ver medico.pacientes_importar).
login_equipe("secretaria@clinicasp.com")
r = client.post("/equipe/pacientes/importar", data={"cpf": "710.820.930-04"},
                follow_redirects=True)
checar("Importar pelo CPF na 3ª empresa funciona", "importado(a) da plataforma" in r.get_data(as_text=True))
client.get("/logout")
with app.app_context():
    contas = Usuario.query.filter_by(telefone=TEL_NORM, tipo="paciente").all()
    checar("A importação também NÃO criou outra conta", len(contas) == 1)
    checar("Agora são TRÊS cadastros na mesma conta",
           len(contas[0].pacientes) == 3)
    pac_sp = next(p for p in contas[0].pacientes if p.empresa_id == sp_emp_id)
    checar("O cadastro importado pela equipe já entra aprovado",
           pac_sp.status_cadastro == "aprovado")

# ---------- Família: mesmo telefone, nascimento diferente = OUTRA conta ----------

login_equipe("secretaria@gruposaude.com")
r = client.post("/equipe/pacientes/novo", data={
    "nome": "Filho Da Ana", "cpf": "710.820.930-15", "email": "",
    "telefone": TEL, "data_nascimento": "2015-10-20",
}, follow_redirects=True)
checar("Familiar com o mesmo telefone e OUTRO nascimento é cadastrado", "cadastrado" in r.get_data(as_text=True).lower())
client.get("/logout")
with app.app_context():
    contas = Usuario.query.filter_by(telefone=TEL_NORM, tipo="paciente").all()
    checar("Pessoa diferente = conta separada (agora 2 contas no telefone)",
           len(contas) == 2)

# ---------- Login: uma conta, escolha do cadastro (empresa) ----------

with app.app_context():
    conta = Usuario.query.get(conta_id)
    pac_grupo = next(p for p in conta.pacientes if p.empresa_id == grupo_id)
    # Aprova o cadastro do Grupo pra área liberar o agendamento.
    pac_grupo.status_cadastro = "aprovado"
    db.session.commit()
    pac_grupo_id = pac_grupo.id

# Área UNIFICADA: os 3 cadastros são da MESMA conta, então o login entra
# DIRETO (sem tela de escolha) - a troca de clínica é feita lá dentro.
r = client.post("/login-paciente", data={"telefone": TEL, "data_nascimento": "15/04/1991"},
                follow_redirects=True)
html = r.get_data(as_text=True)
checar("Login da Ana entra direto (sem tela de escolha)",
       'name="paciente_id_escolhido"' not in html and "Olá, Ana Portatil" in html)
checar("O painel mostra que ela é paciente em 3 clínicas",
       "paciente em 3 clínicas" in html)
r = client.get("/paciente/agendar")
checar("O cadastro ATIVO é o aprovado (exames do Grupo aparecem)",
       "Exame Conta Unica Grupo" in r.get_data(as_text=True))

# Trocar pra um cadastro que não é da conta é rejeitado.
r = client.post("/paciente/trocar-clinica", data={"paciente_id": "999999"}, follow_redirects=True)
checar("Trocar pra cadastro de outra conta é rejeitado",
       "Escolha inválida" in r.get_data(as_text=True))
client.get("/logout")

# O filho (outra conta) loga direto, sem tela de escolha.
r = client.post("/login-paciente", data={"telefone": TEL, "data_nascimento": "20/10/2015"},
                follow_redirects=True)
checar("O filho loga direto (uma conta, um cadastro)",
       "mais de uma clínica" not in r.get_data(as_text=True) and r.status_code == 200)
client.get("/logout")

print("\nTodos os testes de conta única do paciente passaram.")
