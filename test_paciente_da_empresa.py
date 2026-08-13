"""Testa a mudança "o cliente é só cliente": o paciente pertence à
EMPRESA, não a uma filial.

- O cadastro de paciente (equipe) não pede/define filial nenhuma - cria o
  paciente com Paciente.empresa_id.
- A filial só é escolhida na hora de marcar a consulta: pela equipe em
  "Agendar exame" (medico.agenda_novo, que já tem seletor de filial), e
  pelo próprio paciente no pedido de agendamento
  (paciente.solicitar_agendamento) - onde os exames de TODAS as filiais da
  empresa aparecem, com o nome da filial junto, e a filial do agendamento
  criado é a do exame escolhido.
- Um paciente da empresa pode ser agendado em qualquer filial dela.
- Cadastros antigos (só com a filial legada clinica_id) continuam
  aparecendo/funcionando (compatibilidade)."""
from datetime import date

from app import create_app
from app.extensions import db
from app.models import Usuario, Empresa, Clinica, Paciente, Exame, Agendamento, PreparoModelo, normalizar_telefone

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


# Grupo Saúde Total tem 2 filiais (Centro e Praia) - o cenário certo pra
# provar que o paciente não fica preso a nenhuma delas.
with app.app_context():
    grupo = Empresa.query.filter_by(nome="Grupo Saúde Total").first()
    grupo_id = grupo.id
    centro = Clinica.query.filter_by(nome="Grupo Saúde Total - Centro").first()
    praia = Clinica.query.filter_by(nome="Grupo Saúde Total - Praia").first()
    centro_id, praia_id = centro.id, praia.id
    medico_grupo = Usuario.query.filter_by(email="medico@gruposaude.com").first()
    medico_grupo_id = medico_grupo.id

login("secretaria@gruposaude.com", "123456")

# ---------- Cadastro do paciente: sem filial, direto na empresa ----------

r = client.get("/equipe/pacientes/novo")
html_form = r.get_data(as_text=True)
checar("Formulário de novo paciente NÃO pede filial", 'name="clinica_id"' not in html_form)

r = client.post("/equipe/pacientes/novo", data={
    "nome": "cliente da empresa", "cpf": "321.654.987-00", "email": "",
    "telefone": "(27) 97777-0001", "data_nascimento": "1992-05-10",
}, follow_redirects=True)
checar("Cadastro do paciente sem filial funciona", "cadastrado" in r.get_data(as_text=True).lower())

with app.app_context():
    cliente = Paciente.query.filter_by(nome="Cliente da Empresa").first()
    checar("Paciente criado com empresa_id (pertence à empresa)", cliente.empresa_id == grupo_id)
    checar("Paciente NÃO tem filial nenhuma (clinica_id vazio)", cliente.clinica_id is None)
    cliente_id = cliente.id

r = client.get("/equipe/pacientes")
html_lista = r.get_data(as_text=True)
checar("Paciente aparece na lista (nome formatado como nome próprio)", "Cliente da Empresa" in html_lista)
checar("Lista de pacientes NÃO tem coluna de filial", "<th>Filial</th>" not in html_lista)

# CPF duplicado é bloqueado por EMPRESA (não mais por filial).
r = client.post("/equipe/pacientes/novo", data={
    "nome": "Cliente Repetido", "cpf": "321.654.987-00", "email": "",
    "telefone": "(27) 97777-0002", "data_nascimento": "1993-06-11",
}, follow_redirects=True)
checar("CPF repetido na mesma empresa é rejeitado", "já existe um paciente com esse cpf nesta empresa" in r.get_data(as_text=True).lower())

# CEP incompleto (ex.: "29055") é rejeitado - não pode salvar o endereço
# pela metade (rua/bairro/cidade/UF sempre em branco).
r = client.post("/equipe/pacientes/novo", data={
    "nome": "Cliente Cep Incompleto", "cpf": "321.654.987-70", "email": "",
    "telefone": "(27) 97777-0009", "data_nascimento": "1994-07-12", "cep": "29055",
}, follow_redirects=True)
checar("CEP incompleto é rejeitado no cadastro pela equipe",
       "CEP incompleto" in r.get_data(as_text=True))
with app.app_context():
    checar("Ninguém foi criado com o CEP incompleto",
           Paciente.query.filter_by(cpf="321.654.987-70").first() is None)

# ---------- Agendar o MESMO paciente em DUAS filiais diferentes ----------

with app.app_context():
    # Um exame em cada filial, pro teste de agendamento.
    modelo_centro = PreparoModelo(clinica_id=centro_id, nome="Preparo Agenda Centro", instrucoes="Jejum.")
    db.session.add(modelo_centro)
    db.session.flush()
    exame_centro = Exame(
        clinica_id=centro_id, medico_id=medico_grupo_id, nome="Consulta no Centro",
        descricao="", duracao_minutos=30, preparo_modelo_id=modelo_centro.id,
    )
    exame_praia = Exame(
        clinica_id=praia_id, medico_id=medico_grupo_id, nome="Consulta na Praia",
        descricao="", duracao_minutos=30, preparo_modelo_id=modelo_centro.id,
    )
    db.session.add_all([exame_centro, exame_praia])
    db.session.commit()
    exame_centro_id, exame_praia_id = exame_centro.id, exame_praia.id

r = client.post("/equipe/agenda/novo", data={
    "filial_id": str(centro_id), "paciente_id": str(cliente_id), "exame_id": str(exame_centro_id),
    "medico_id": str(medico_grupo_id), "data_hora": "2026-09-01T09:00",
}, follow_redirects=True)
checar("Paciente da empresa pode ser agendado na filial Centro", "criado com sucesso" in r.get_data(as_text=True).lower())

r = client.post("/equipe/agenda/novo", data={
    "filial_id": str(praia_id), "paciente_id": str(cliente_id), "exame_id": str(exame_praia_id),
    "medico_id": str(medico_grupo_id), "data_hora": "2026-09-02T09:00",
}, follow_redirects=True)
checar("O MESMO paciente pode ser agendado na filial Praia", "criado com sucesso" in r.get_data(as_text=True).lower())

with app.app_context():
    ags = Agendamento.query.filter_by(paciente_id=cliente_id).order_by(Agendamento.data_hora).all()
    checar("Cada agendamento carrega a SUA filial (Centro e Praia)",
           len(ags) == 2 and ags[0].clinica_id == centro_id and ags[1].clinica_id == praia_id)

client.get("/logout")

# ---------- Isolamento entre empresas continua valendo ----------

login("secretaria@clinicavitoria.com", "123456")
r = client.get("/equipe/pacientes")
checar("Paciente do Grupo Saúde Total NÃO aparece para a Clínica Vitória", "Cliente da Empresa" not in r.get_data(as_text=True))
client.get("/logout")

# ---------- Área do paciente: exames de todas as filiais, filial pelo exame ----------

r = client.post("/login-paciente", data={"cpf": "321.654.987-00", "data_nascimento": "10/05/1992"}, follow_redirects=True)
checar("Paciente da empresa loga normalmente (CPF + nascimento)", r.status_code == 200)

r = client.get("/paciente/agendar")
html_agendar = r.get_data(as_text=True)
checar("Pedido de agendamento lista exames das DUAS filiais", "Consulta no Centro" in html_agendar and "Consulta na Praia" in html_agendar)
# O fluxo agora é em etapas: escolhe o EXAME (só nome) e depois o LOCAL -
# a filial aparece no dropdown de local depois da escolha do exame (ver
# test_agendamento_paciente_exame_local.py para o fluxo completo).
r = client.get("/paciente/agendar?exame_nome=Consulta na Praia")
checar("Escolhido o exame, o local em que ele é feito aparece",
       "Grupo Saúde Total - Praia" in r.get_data(as_text=True))

# Compatibilidade: paciente antigo (só com a filial legada) continua funcionando.
client.get("/logout")
with app.app_context():
    tel_legado = normalizar_telefone("(27) 96666-0009")
    usuario_legado = Usuario(nome="Paciente Legado", telefone=tel_legado, tipo="paciente")
    db.session.add(usuario_legado)
    db.session.flush()
    legado = Paciente(
        clinica_id=centro_id,  # modelo antigo: só a filial, sem empresa_id
        usuario_id=usuario_legado.id,
        nome="Paciente Legado", cpf="444.555.666-77",
        data_nascimento=date(1980, 1, 2), telefone=tel_legado,
    )
    db.session.add(legado)
    db.session.commit()

login("secretaria@gruposaude.com", "123456")
r = client.get("/equipe/pacientes")
checar("Paciente legado (só filial, sem empresa_id) continua aparecendo na lista", "Paciente Legado" in r.get_data(as_text=True))
client.get("/logout")

r = client.post("/login-paciente", data={"cpf": "444.555.666-77", "data_nascimento": "02/01/1980"}, follow_redirects=True)
checar("Paciente legado loga normalmente", r.status_code == 200)
r = client.get("/paciente/agendar")
checar("Paciente legado também vê exames de todas as filiais da empresa",
       "Consulta no Centro" in r.get_data(as_text=True) and "Consulta na Praia" in r.get_data(as_text=True))
client.get("/logout")

print("\nTodos os testes de paciente da empresa (sem filial no cadastro) passaram.")
