"""Testa o cadastro público UNIFICADO: não existe mais "modo" escolhido
por botão ("empresa" x "independente") - o CNPJ da clínica é só um campo
OPCIONAL dentro do próprio formulário. Sem CNPJ (ou informando um CNPJ
inédito), o cadastro não exige nome_empresa (não existe mais esse campo
separado - a empresa nasce com o nome do próprio local de atendimento),
permite escolher o papel (médico ou secretário(a)), permite múltiplos
locais de atendimento depois via /equipe/filiais/nova, e continua
funcionando normalmente quando um CNPJ novo é informado (ver
test_cadastro_por_cnpj.py para o fluxo de CNPJ já existente).

Roda contra SQLite local (não usa a DATABASE_URL do .env) e reseta esse banco de
teste no início — não toca no banco real. Para rodar:
    DATABASE_URL=sqlite:///teste_independente.db python test_medico_independente.py
"""
from app import create_app
from app.extensions import db
from app.db_utils import resetar_banco
from app.models import Usuario, Empresa, Clinica, ClinicaMembro

app = create_app()
client = app.test_client()

with app.app_context():
    resetar_banco(db)

def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome

# ---------- Login: um único link de cadastro, genérico, sem "modo" nenhum ----------
r_login = client.get("/login")
html_login = r_login.get_data(as_text=True)
checar("Link de criar conta manda pro cadastro único, sem parâmetro de modo",
       "/cadastro" in html_login and "modo=" not in html_login)

# ---------- Cadastro: uma única tela, sem escolha de modo ----------
r_cad = client.get("/cadastro")
html_cad = r_cad.get_data(as_text=True)
checar("NÃO existe mais grupo de botões pra escolher o tipo de conta (concentrado numa única tela)",
       'id="grupo-modo"' not in html_cad and "Tenho uma clínica/empresa" not in html_cad
       and "Sou profissional independente" not in html_cad)
checar("NÃO existe mais um campo separado de \"nome da empresa\"",
       'name="nome_empresa"' not in html_cad)
checar("O campo de CNPJ da clínica é opcional (sem o atributo required)",
       'name="cnpj_filial"' in html_cad and 'placeholder="00.000.000/0000-00"' in html_cad
       and 'name="cnpj_filial" id="cnpj_filial" class="form-control" placeholder="00.000.000/0000-00" required' not in html_cad)

# ---------- Cadastro sem informar CNPJ, papel médico ----------
r = client.post("/cadastro", data={
    "nome": "Dr. João Autônomo",
    "papel": "medico",
    "cpf": "852.963.741-00", "crm_numero": "44444", "crm_uf": "ES",
    "email": "joao.autonomo@example.com",
    "senha": "123456",
    "nome_filial": "Consultório do Dr. João",
    "telefone_filial": "(27) 90000-0004",
}, follow_redirects=True)
checar("Cadastro sem CNPJ responde 200", r.status_code == 200)

with app.app_context():
    usuario = Usuario.query.filter_by(email="joao.autonomo@example.com").first()
    checar("Usuário foi criado", usuario is not None)
    checar("Papel escolhido (médico) foi respeitado", usuario.tipo == "medico")
    checar("Usuário recebeu perm_filiais (pode cadastrar novos locais sozinho)", usuario.perm_filiais is True)

    empresa = Empresa.query.filter_by(email_contato="joao.autonomo@example.com").first()
    checar("Empresa (pessoal, sem CNPJ) foi criada", empresa is not None)
    checar("Nome da empresa = nome do local informado (não pedimos mais um nome de empresa separado)",
           empresa.nome == "Consultório do Dr. João")

    filial = Clinica.query.filter_by(empresa_id=empresa.id).first()
    checar("Local de atendimento (filial) foi criado automaticamente", filial is not None)
    checar("Nome do local é o que a pessoa informou", filial.nome == "Consultório do Dr. João")

checar("Login automático após cadastro (cai direto no onboarding, não na tela de login)",
       "/login" not in r.request.path if hasattr(r, "request") else True)

# ---------- Médico cadastra um SEGUNDO local sozinho ----------
r = client.post("/equipe/filiais/nova", data={"nome": "Consultório - Praia"}, follow_redirects=True)
checar("Médico consegue cadastrar um segundo local (perm_filiais já vem habilitada)",
       "cadastrado com sucesso" in r.get_data(as_text=True).lower())

with app.app_context():
    total_filiais = Clinica.query.filter_by(empresa_id=empresa.id).count()
    checar("Agora existem 2 locais de atendimento na mesma empresa", total_filiais == 2)

client.get("/logout")

# ---------- Cadastro sem CNPJ, papel secretária (não é mais exclusivo de médico) ----------
r = client.post("/cadastro", data={
    "nome": "Secretária Autônoma",
    "papel": "secretaria",
    "cpf": "123.456.789-09",
    "email": "secretaria.autonoma@example.com",
    "senha": "123456",
    "nome_filial": "Consultório da Secretária Autônoma",
    "telefone_filial": "(27) 90000-0099",
}, follow_redirects=True)
checar("Cadastro como secretária responde 200", r.status_code == 200)
with app.app_context():
    secretaria_autonoma = Usuario.query.filter_by(email="secretaria.autonoma@example.com").first()
    checar("Secretária foi criada com o papel certo", secretaria_autonoma is not None and secretaria_autonoma.tipo == "secretaria")
    checar("Secretária não recebeu código mestre (isso é só de médico)", secretaria_autonoma.codigo_mestre is None)
    checar("Secretária também recebeu todas as permissões (é fundadora da própria conta)",
           secretaria_autonoma.perm_filiais is True)
client.get("/logout")

# ---------- Validação: cadastro sem nome/email/senha é rejeitado ----------
r = client.post("/cadastro", data={"nome": "", "email": "", "senha": ""})
checar("Cadastro sem dados obrigatórios é rejeitado", r.status_code == 200 and "Preencha todos os campos" in r.get_data(as_text=True))

# ---------- Cadastro informando um CNPJ NOVO (inédito) continua funcionando ----------
# O cadastro público pede os dados completos do primeiro local de
# atendimento (nome, telefone, endereço, CNPJ opcional) - já cria e
# vincula quem se cadastrou a esse primeiro local (ver
# test_cadastro_empresa_sem_filial.py para o fluxo completo, e
# test_cadastro_por_cnpj.py para o caso de CNPJ já existente).
r = client.post("/cadastro", data={
    "nome": "Dra. Empresa Teste",
    "cpf": "852.963.741-00", "crm_numero": "55555", "crm_uf": "ES",
    "email": "dra.empresa.teste@example.com",
    "senha": "123456",
    "papel": "medico",
    "nome_filial": "Clínica Teste Regressão - Sede",
    "telefone_filial": "(27) 90000-0005",
    "cnpj_filial": "12.345.606/0001-48",
}, follow_redirects=True)
checar("Cadastro com CNPJ novo responde 200 (cai no assistente de configuração inicial)", r.status_code == 200)
with app.app_context():
    empresa2 = Empresa.query.filter_by(nome="Clínica Teste Regressão - Sede").first()
    checar("Empresa nova foi criada com o nome do local informado", empresa2 is not None)
    checar(
        "O primeiro local de atendimento já foi criado, com os dados completos",
        Clinica.query.filter_by(empresa_id=empresa2.id, nome="Clínica Teste Regressão - Sede").count() == 1,
    )
    dra_empresa = Usuario.query.filter_by(email="dra.empresa.teste@example.com").first()
    checar("Usuário fica vinculado à empresa via empresa_fundadora_id", dra_empresa.empresa_fundadora_id == empresa2.id)
    checar("Usuário já fica vinculado ao primeiro local criado no cadastro",
           ClinicaMembro.query.filter_by(usuario_id=dra_empresa.id).count() == 1)

# ---------- Se "nome_filial" chegar em branco (ex.: alguém preenche sem ----------
# ---------- passar pelo JS que marca o campo como obrigatório), o local ----------
# ---------- nasce com o nome da própria pessoa, como último recurso -----------
client.get("/logout")
r = client.post("/cadastro", data={
    "nome": "Bruno Pavan",
    "cpf": "168.995.350-09", "crm_numero": "77777", "crm_uf": "ES",
    "email": "bruno.pavan@example.com",
    "senha": "123456",
    "papel": "medico",
    "nome_filial": "",
    "telefone_filial": "(27) 90000-0006",
}, follow_redirects=True)
checar("Cadastro com nome_filial em branco ainda responde 200", r.status_code == 200)
with app.app_context():
    empresa3 = Empresa.query.filter_by(nome="Bruno Pavan").first()
    checar("Empresa foi criada com o nome da pessoa, como último recurso", empresa3 is not None)
    filial3 = Clinica.query.filter_by(empresa_id=empresa3.id).first()
    checar(
        "Local nasce com o nome da pessoa quando nome_filial chega em branco",
        filial3 is not None and filial3.nome == "Bruno Pavan",
    )

print("\nTodos os testes do fluxo de cadastro público unificado passaram.")
