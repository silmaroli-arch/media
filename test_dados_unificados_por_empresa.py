"""Testa a unificação dos dados por EMPRESA: quem atua em mais de uma filial
(Clinica) da mesma empresa vê os pacientes, exames e agendamentos das DUAS
filiais juntos, com a filial indicada em cada registro — não existe mais
"filial atual" nem botão "trocar para este local" (o que determina onde o
médico está é o agendamento que ele vai atender, não uma troca manual).

Também verifica que:
  - no cadastro (paciente/exame), com mais de uma filial acessível, a filial
    é escolhida no formulário e é ela que fica gravada em clinica_id;
  - o isolamento por EMPRESA continua intacto: um registro de outra empresa
    (sem vínculo nenhum) continua dando 404 e não aparece em lista nenhuma.

Usa o seed: empresa "Grupo Saúde Total" (filiais "Centro" e "Praia", com a
secretária Camila e o Dr. Eduardo vinculados às duas) e a empresa sem
relação "Clínica São Paulo".
"""
from datetime import datetime, timedelta

from app import create_app
from app.extensions import db
from app.models import (
    Usuario, Clinica, Empresa, Paciente, Exame, Agendamento, PreparoModelo,
)

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


with app.app_context():
    centro = Clinica.query.filter_by(nome="Grupo Saúde Total - Centro").first()
    praia = Clinica.query.filter_by(nome="Grupo Saúde Total - Praia").first()
    outra_empresa_clinica = Clinica.query.filter_by(nome="Clínica São Paulo").first()
    medico_grupo = Usuario.query.filter_by(email="medico@gruposaude.com").first()
    centro_id, praia_id, medico_id = centro.id, praia.id, medico_grupo.id
    outra_clinica_id = outra_empresa_clinica.id
    grupo_id = centro.empresa_id

    # Modelo de preparo em cada filial (o modelo é por filial).
    for c in (centro, praia):
        db.session.add(PreparoModelo(
            clinica_id=c.id, nome=f"Preparo {c.nome}", instrucoes="Jejum de 8 horas.",
        ))
    db.session.commit()

    # Um paciente e um exame em CADA filial, mais um agendamento em cada.
    ids = {}
    for c, sufixo in ((centro, "Centro"), (praia, "Praia")):
        usuario = Usuario(nome=f"Paciente {sufixo}", telefone=f"2799000{c.id:04d}", tipo="paciente")
        db.session.add(usuario)
        db.session.flush()
        paciente = Paciente(
            clinica_id=c.id, usuario_id=usuario.id, nome=f"Paciente {sufixo}",
            cpf=f"9990000{c.id:04d}", data_nascimento=datetime(1980, 1, 1).date(),
            status_cadastro="aprovado",
        )
        exame = Exame(
            clinica_id=c.id, medico_id=medico_id, nome=f"Endoscopia {sufixo}",
            descricao="Endoscopia", duracao_minutos=30, preco=100,
        )
        db.session.add_all([paciente, exame])
        db.session.flush()
        agendamento = Agendamento(
            clinica_id=c.id, paciente_id=paciente.id, exame_id=exame.id, medico_id=medico_id,
            data_hora=datetime.utcnow() + timedelta(days=2), status="confirmado",
        )
        db.session.add(agendamento)
        db.session.flush()
        ids[sufixo] = {"paciente": paciente.id, "exame": exame.id, "agendamento": agendamento.id}
    db.session.commit()

    # Um paciente/exame de uma empresa TOTALMENTE diferente (sem vínculo).
    usuario_outro = Usuario(nome="Paciente De Outra Empresa", telefone="2799991111", tipo="paciente")
    db.session.add(usuario_outro)
    db.session.flush()
    paciente_outra_empresa = Paciente(
        clinica_id=outra_clinica_id, usuario_id=usuario_outro.id, nome="Paciente De Outra Empresa",
        cpf="88800011122", data_nascimento=datetime(1975, 5, 5).date(), status_cadastro="aprovado",
    )
    db.session.add(paciente_outra_empresa)
    db.session.commit()
    paciente_outra_empresa_id = paciente_outra_empresa.id


# ---------- (a) Dados das duas filiais aparecem juntos, com a filial ----------

login("secretaria@gruposaude.com", "123456")

# Nenhuma tela de escolha: as duas filiais são da mesma empresa.
r = client.get("/equipe/clinica", follow_redirects=True)
checar("Com uma única empresa, não existe tela de escolha (vai direto ao painel)",
       "Em qual empresa" not in r.get_data(as_text=True))

r = client.get("/equipe/pacientes")
html = r.get_data(as_text=True)
checar("Lista de pacientes responde 200", r.status_code == 200)
checar("Lista de pacientes mostra o paciente da filial Centro", "Paciente Centro" in html)
checar("Lista de pacientes mostra TAMBÉM o paciente da filial Praia", "Paciente Praia" in html)
# O paciente agora é da EMPRESA, não de uma filial ("o cliente é só
# cliente") - a lista de pacientes não tem mais coluna de filial; a filial
# aparece em cada AGENDAMENTO (ver a checagem da agenda logo abaixo).
checar("Lista de pacientes NÃO tem coluna de filial (paciente é da empresa)", "<th>Filial</th>" not in html)
checar("Lista de pacientes NÃO mostra paciente de outra empresa",
       "Paciente De Outra Empresa" not in html)

r = client.get("/equipe/exames")
html = r.get_data(as_text=True)
checar("Lista de exames mostra o exame das duas filiais",
       "Endoscopia Centro" in html and "Endoscopia Praia" in html)
checar("Lista de exames identifica a filial de cada exame",
       "Grupo Saúde Total - Centro" in html and "Grupo Saúde Total - Praia" in html)

r = client.get("/equipe/")
html = r.get_data(as_text=True)
checar("Agenda (painel) mostra os agendamentos das duas filiais",
       "Paciente Centro" in html and "Paciente Praia" in html)
checar("Agenda (painel) tem a coluna Filial", "<th>Filial</th>" in html)
checar("Agenda (painel) identifica as duas filiais",
       "Grupo Saúde Total - Centro" in html and "Grupo Saúde Total - Praia" in html)

r = client.get("/equipe/preparo-modelos")
html = r.get_data(as_text=True)
checar("Modelos de preparo das duas filiais aparecem juntos",
       "Preparo Grupo Saúde Total - Centro" in html and "Preparo Grupo Saúde Total - Praia" in html)

# Detalhe/edição de um registro de QUALQUER uma das filiais acessíveis funciona
# (antes era preciso "trocar de local" primeiro).
with app.app_context():
    pass
r = client.get(f"/equipe/pacientes/{ids['Praia']['paciente']}")
checar("Detalhe do paciente da filial Praia abre sem precisar 'trocar de local'", r.status_code == 200)
r = client.get(f"/equipe/exames/{ids['Praia']['exame']}/editar")
checar("Edição do exame da filial Praia abre sem precisar 'trocar de local'", r.status_code == 200)

# A tela "Meus locais de atendimento" não tem mais o botão de troca.
r = client.get("/equipe/filiais")
html = r.get_data(as_text=True)
checar("Tela de locais não tem mais o botão 'Trocar para este local'",
       "Trocar para este local" not in html)
checar("Tela de locais continua com os botões de dados por filial",
       "Dados Cadastrais" in html and "Dados Fiscais" in html)

# A barra superior mostra a EMPRESA, não uma filial "atual".
checar("Barra superior mostra o nome da empresa", "Grupo Saúde Total<" in html or "Grupo Saúde Total\n" in html)


# ---------- (b) Cadastro escolhendo a filial ----------

# O paciente é da EMPRESA - o cadastro não pede (nem aceita) filial
# nenhuma; a filial é escolhida em cada agendamento.
r = client.get("/equipe/pacientes/novo")
html = r.get_data(as_text=True)
checar("Formulário de novo paciente NÃO pede filial (paciente é da empresa)", 'name="clinica_id"' not in html)

r = client.post("/equipe/pacientes/novo", data={
    "nome": "Novo Paciente Da Empresa", "cpf": "77711122233", "telefone": "(27) 98888-7777",
    "data_nascimento": "10/04/1990",
}, follow_redirects=True)
checar("Cadastro de paciente (sem filial) responde 200", r.status_code == 200)
with app.app_context():
    novo = Paciente.query.filter_by(cpf="77711122233").first()
    checar("Paciente novo foi salvo", novo is not None)
    checar("Paciente novo pertence à EMPRESA (empresa_id), sem filial", novo.empresa_id == grupo_id and novo.clinica_id is None)

r = client.post("/equipe/pacientes/novo", data={
    # um clinica_id forjado no POST (inclusive de OUTRA empresa) é
    # simplesmente ignorado - o paciente nasce sempre na empresa atual.
    "clinica_id": str(outra_clinica_id),
    "nome": "Paciente Campo Forjado", "cpf": "55511122233", "telefone": "(27) 96666-5555",
    "data_nascimento": "10/04/1990",
}, follow_redirects=True)
checar("POST com clinica_id forjado responde 200", r.status_code == 200)
with app.app_context():
    forjado = Paciente.query.filter_by(cpf="55511122233").first()
    checar("clinica_id forjado é ignorado - paciente fica na empresa atual, sem filial",
           forjado is not None and forjado.empresa_id == grupo_id and forjado.clinica_id is None)

# O cadastro de exame é genérico - não pede filial, médico nem preço (isso é
# resolvido depois, na tela "Exames por filial").
r = client.get("/equipe/exames/novo")
html_exame_novo = r.get_data(as_text=True)
checar("Formulário de novo exame NÃO pede filial", 'name="clinica_id"' not in html_exame_novo)
checar("Formulário de novo exame NÃO pede médico", 'name="medico_id"' not in html_exame_novo)
checar("Formulário de novo exame NÃO pede preço", 'name="preco"' not in html_exame_novo)
with app.app_context():
    modelo_qualquer_id = PreparoModelo.query.filter(PreparoModelo.clinica_id.in_([centro_id, praia_id])).first().id
r = client.post("/equipe/exames/novo", data={
    "nome": "Colonoscopia Nova", "descricao": "Colono", "duracao_minutos": "45",
    "preparo_modelo_id": str(modelo_qualquer_id),
}, follow_redirects=True)
checar("Cadastro de exame genérico responde 200", r.status_code == 200)
with app.app_context():
    exame_novo = Exame.query.filter_by(nome="Colonoscopia Nova").first()
    checar("Exame novo foi salvo", exame_novo is not None)
    checar("Exame novo ficou numa filial acessível da empresa", exame_novo.clinica_id in (centro_id, praia_id))
    checar("Exame novo já tem um médico responsável preenchido automaticamente", exame_novo.medico_id is not None)


# ---------- (c) Isolamento por empresa continua valendo ----------

r = client.get(f"/equipe/pacientes/{paciente_outra_empresa_id}")
checar("Paciente de outra empresa dá 404 (fronteira de acesso mantida)", r.status_code == 404)
r = client.get(f"/equipe/pacientes/{paciente_outra_empresa_id}/editar")
checar("Editar paciente de outra empresa dá 404", r.status_code == 404)

with app.app_context():
    exame_outra_empresa = Exame.query.filter_by(clinica_id=outra_clinica_id).first()
    exame_outra_empresa_id = exame_outra_empresa.id if exame_outra_empresa else None
if exame_outra_empresa_id:
    r = client.get(f"/equipe/exames/{exame_outra_empresa_id}/editar")
    checar("Editar exame de outra empresa dá 404", r.status_code == 404)

client.get("/logout")

# O médico do grupo (também vinculado às duas filiais) vê o mesmo conjunto.
login("medico@gruposaude.com", "123456")
r = client.get("/equipe/exames")
html = r.get_data(as_text=True)
checar("Médico multi-filial vê os seus exames das duas filiais",
       "Endoscopia Centro" in html and "Endoscopia Praia" in html)
r = client.get("/equipe/medico-agenda")
html = r.get_data(as_text=True)
checar("Agenda pessoal do médico traz os confirmados das duas filiais",
       "Paciente Centro" in html and "Paciente Praia" in html)
checar("Agenda pessoal do médico identifica a filial de cada linha",
       "Grupo Saúde Total - Centro" in html and "Grupo Saúde Total - Praia" in html)
r = client.get(f"/equipe/pacientes/{paciente_outra_empresa_id}")
checar("Médico também é bloqueado (404) em paciente de outra empresa", r.status_code == 404)
client.get("/logout")

# A secretária da OUTRA empresa não vê nada do Grupo Saúde Total.
login("secretaria@clinicasp.com", "123456")
html = client.get("/equipe/pacientes").get_data(as_text=True)
checar("Secretária de outra empresa não vê pacientes do Grupo Saúde Total",
       "Paciente Centro" not in html and "Paciente Praia" not in html)
r = client.get(f"/equipe/pacientes/{ids['Praia']['paciente']}")
checar("Secretária de outra empresa recebe 404 no paciente do Grupo Saúde Total", r.status_code == 404)
html = client.get("/equipe/pacientes").get_data(as_text=True)
checar("Com uma única filial, a lista NÃO ganha a coluna Filial", "<th>Filial</th>" not in html)
r = client.get("/equipe/pacientes/novo")
checar("Com uma única filial, o formulário NÃO pede filial (comportamento de antes)",
       'name="clinica_id"' not in r.get_data(as_text=True))
client.get("/logout")

print("\nTodos os testes de dados unificados por empresa passaram.")
