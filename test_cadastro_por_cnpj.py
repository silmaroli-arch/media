"""Testa a resolução do problema relatado: pessoas da mesma clínica
podiam se cadastrar sem uma saber da outra e cada uma acabava criando a
sua PRÓPRIA empresa/filial - duplicando o que deveria ser um único
registro. Regra: o CNPJ da clínica é um campo OPCIONAL no cadastro
público único (não existe mais "modo" escolhido por botão) e, se já
existir uma Clinica cadastrada com esse CNPJ, quem se cadastra é
vinculado direto a ela (ClinicaMembro), sem criar empresa/filial nova,
sem convite e sem aceite de ninguém - quem digita o CNPJ já está
confirmando que atua ali. Se o CNPJ não for informado (ou for inédito),
uma empresa nova é fundada."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Empresa, Clinica, ClinicaMembro

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


CNPJ_CLINICA = "12.345.608/0001-37"  # CNPJ VÁLIDO (dígitos verificadores conferem)

# ---------- Endpoint de busca: CNPJ inédito ----------

r = client.get("/cadastro/verificar-cnpj?cnpj=" + CNPJ_CLINICA)
checar("Busca por CNPJ inédito responde 200", r.status_code == 200)
checar("CNPJ inédito não é encontrado", r.get_json()["encontrada"] is False)

# ---------- Primeira médica funda a clínica informando o CNPJ ----------

r = client.post("/cadastro", data={
    "nome": "Dra. Ana Bela Vista",
    "cpf": "852.963.741-00", "crm_numero": "88888", "crm_uf": "ES",
    "email": "ana@clinicabelavista.com",
    "senha": "123456",
    "papel": "medico",
    "nome_filial": "Clínica Bela Vista - Sede",
    "telefone_filial": "(27) 90000-0009",
    "cnpj_filial": CNPJ_CLINICA,
    "cep_filial": "29055-360", "rua_filial": "Rua Saúl de Navarro", "numero_filial": "320",
    "bairro_filial": "Praia do Canto", "cidade_filial": "Vitória", "uf_filial": "ES",
}, follow_redirects=True)
checar("Cadastro da fundadora responde 200", r.status_code == 200)

with app.app_context():
    # Não existe mais um "nome da empresa" separado do nome do local - a
    # empresa nasce com o mesmo nome informado pro local de atendimento.
    empresa = Empresa.query.filter_by(nome="Clínica Bela Vista - Sede").first()
    ana = Usuario.query.filter_by(email="ana@clinicabelavista.com").first()
    checar("Empresa foi criada", empresa is not None)
    checar("Ana é a fundadora (âncora empresa_fundadora_id)", ana.empresa_fundadora_id == empresa.id)
    checar("Ana recebeu todas as permissões administrativas (é fundadora)",
           ana.perm_pacientes and ana.perm_equipe and ana.perm_filiais and ana.perm_dados_clinica)
    filial = Clinica.query.filter_by(empresa_id=empresa.id).first()
    checar("A filial foi criada com o CNPJ informado", filial is not None and filial.cnpj == CNPJ_CLINICA)
    filial_id = filial.id
client.get("/logout")

# ---------- Cadastro sem informar CNPJ não é mais rejeitado - o CNPJ é ----------
# ---------- opcional; sem ele, nasce uma empresa nova e pessoal ----------

with app.app_context():
    total_empresas_antes = Empresa.query.count()

r = client.post("/cadastro", data={
    "nome": "Dr. Sem Cnpj",
    "cpf": "123.456.789-09", "crm_numero": "99999", "crm_uf": "ES",
    "email": "semcnpj@example.com",
    "senha": "123456",
    "papel": "medico",
    "nome_filial": "Consultório do Dr. Sem Cnpj",
    "telefone_filial": "(27) 90000-0010",
}, follow_redirects=True)
checar("Cadastro sem CNPJ funciona normalmente (CNPJ é opcional)", r.status_code == 200)
with app.app_context():
    checar("Uma empresa nova e pessoal foi criada mesmo sem CNPJ",
           Empresa.query.filter_by(nome="Consultório do Dr. Sem Cnpj").first() is not None)
    checar("Só essa empresa nova foi criada (nada além do esperado)",
           Empresa.query.count() == total_empresas_antes + 1)
client.get("/logout")

# ---------- Cadastro com CNPJ inválido é rejeitado ----------

r = client.post("/cadastro", data={
    "nome": "Dr. Cnpj Invalido",
    "cpf": "123.456.789-09", "crm_numero": "99999", "crm_uf": "ES",
    "email": "cnpjinvalido@example.com",
    "senha": "123456",
    "papel": "medico",
    "nome_filial": "Consultório",
    "telefone_filial": "(27) 90000-0011",
    "cnpj_filial": "11.111.111/1111-11",
}, follow_redirects=True)
checar("Cadastro com CNPJ inválido é rejeitado", "CNPJ inválido" in r.get_data(as_text=True))

# ---------- Busca: agora o CNPJ já é encontrado, com o nome da clínica ----------

r = client.get("/cadastro/verificar-cnpj?cnpj=" + CNPJ_CLINICA)
dados = r.get_json()
checar("Busca por CNPJ já cadastrado encontra a clínica", dados["encontrada"] is True)
checar("Busca devolve o nome da clínica encontrada", dados["nome"] == "Clínica Bela Vista - Sede")
checar("Busca devolve o telefone da clínica encontrada (pra já preencher o campo)",
       dados["telefone"] == "(27) 90000-0009")
checar("Busca devolve o endereço da clínica encontrada (pra já preencher os campos)",
       dados["cep"] == "29055-360" and dados["rua"] == "Rua Saúl de Navarro"
       and dados["numero"] == "320" and dados["bairro"] == "Praia do Canto"
       and dados["cidade"] == "Vitória" and dados["uf"] == "ES")

# ---------- Segundo médico se cadastra com o MESMO CNPJ, sem saber da Ana ----------

with app.app_context():
    total_empresas_antes_bruno = Empresa.query.count()

r = client.post("/cadastro", data={
    "nome": "Dr. Bruno Bela Vista",
    "cpf": "123.456.789-09", "crm_numero": "12345", "crm_uf": "ES",
    "email": "bruno@clinicabelavista.com",
    "senha": "123456",
    "papel": "medico",
    # nome_filial/telefone_filial/CNPJ da filial são ignorados quando o
    # CNPJ já existe - a filial já existe, com os dados que a Ana informou.
    "nome_filial": "Nome que nem chega a ser usado",
    "telefone_filial": "(27) 90000-0099",
    "cnpj_filial": CNPJ_CLINICA,
}, follow_redirects=True)
checar("Cadastro do segundo médico responde 200", r.status_code == 200)
texto = r.get_data(as_text=True)
checar("Mensagem avisa que a clínica já existia e ele já foi vinculado a ela",
       "já cadastrada" in texto.lower() and "clínica bela vista" in texto.lower())

with app.app_context():
    checar("NENHUMA empresa nova foi criada para o Bruno (o CNPJ já existia)",
           Empresa.query.count() == total_empresas_antes_bruno)
    bruno = Usuario.query.filter_by(email="bruno@clinicabelavista.com").first()
    checar("Bruno foi criado", bruno is not None)
    checar("Bruno NÃO é fundador dessa empresa (quem fundou foi a Ana)",
           bruno.empresa_fundadora_id != empresa.id)
    checar("Bruno já está vinculado à MESMA filial que a Ana criou",
           ClinicaMembro.query.filter_by(clinica_id=filial_id, usuario_id=bruno.id, ativo=True).count() == 1)
    checar("A filial continua sendo uma única (não duplicou)",
           Clinica.query.filter_by(empresa_id=empresa.id).count() == 1)
    checar(
        "Bruno recebe as permissões PADRÃO do papel (médico = sem admin), não todas — "
        "só quem fundou a empresa recebe automaticamente",
        bruno.perm_pacientes is False and bruno.perm_equipe is False
        and bruno.perm_filiais is False and bruno.perm_dados_clinica is False,
    )
client.get("/logout")

# Bruno já consegue entrar direto (login normal) e ver a clínica dele.
r = client.post("/login", data={"email": "bruno@clinicabelavista.com", "senha": "123456"}, follow_redirects=True)
checar("Bruno já entra direto, vinculado à clínica encontrada pelo CNPJ", "Clínica Bela Vista" in r.get_data(as_text=True))
client.get("/logout")

# ---------- Secretária também pode informar o CNPJ e entrar na mesma clínica ----------
# (é exatamente o cenário relatado: secretária cadastrada sem informar o
# CNPJ acabava isolada numa empresa própria, sem ver os médicos da mesma
# clínica - agora, informando o mesmo CNPJ, ela entra vinculada a eles.)

r = client.post("/cadastro", data={
    "nome": "Secretária da Bela Vista",
    "cpf": "111.444.777-35",
    "email": "secretaria@clinicabelavista.com",
    "senha": "123456",
    "papel": "secretaria",
    "nome_filial": "Nome que nem chega a ser usado",
    "telefone_filial": "(27) 90000-0098",
    "cnpj_filial": CNPJ_CLINICA,
}, follow_redirects=True)
checar("Cadastro da secretária com o mesmo CNPJ responde 200", r.status_code == 200)
with app.app_context():
    secretaria = Usuario.query.filter_by(email="secretaria@clinicabelavista.com").first()
    checar("Secretária foi criada", secretaria is not None)
    checar("Secretária está vinculada à MESMA filial dos médicos (mesmo CNPJ)",
           ClinicaMembro.query.filter_by(clinica_id=filial_id, usuario_id=secretaria.id, ativo=True).count() == 1)
client.get("/logout")

print("\nTodos os testes de cadastro por CNPJ passaram.")
