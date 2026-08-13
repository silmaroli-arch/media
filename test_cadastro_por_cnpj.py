"""Testa a resolução do problema relatado: dois médicos da mesma clínica
podiam se cadastrar (modo "empresa") sem um saber do outro e cada um
acabava criando a sua PRÓPRIA empresa/filial - duplicando o que deveria
ser um único registro. Regra nova: o CNPJ da clínica agora é obrigatório
no cadastro (modo "empresa") e, se já existir uma Clinica cadastrada com
esse CNPJ, quem se cadastra é vinculado direto a ela (ClinicaMembro),
sem criar empresa/filial nova, sem convite e sem aceite de ninguém -
quem digita o CNPJ já está confirmando que atua ali. Só quando o CNPJ é
inédito é que uma empresa nova é fundada."""
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

# ---------- Primeiro médico funda a clínica com esse CNPJ ----------

r = client.post("/cadastro", data={
    "modo": "empresa",
    "nome_empresa": "Clínica Bela Vista",
    "nome": "Dra. Ana Bela Vista",
    "cpf": "852.963.741-00", "crm_numero": "88888", "crm_uf": "ES",
    "email": "ana@clinicabelavista.com",
    "senha": "123456",
    "papel": "medico",
    "nome_filial": "Clínica Bela Vista - Sede",
    "telefone_filial": "(27) 90000-0009",
    "cnpj_filial": CNPJ_CLINICA,
}, follow_redirects=True)
checar("Cadastro da fundadora responde 200", r.status_code == 200)

with app.app_context():
    empresa = Empresa.query.filter_by(nome="Clínica Bela Vista").first()
    ana = Usuario.query.filter_by(email="ana@clinicabelavista.com").first()
    checar("Empresa foi criada", empresa is not None)
    checar("Ana é a fundadora (âncora empresa_fundadora_id)", ana.empresa_fundadora_id == empresa.id)
    checar("Ana recebeu todas as permissões administrativas (é fundadora)",
           ana.perm_pacientes and ana.perm_equipe and ana.perm_filiais and ana.perm_dados_clinica)
    filial = Clinica.query.filter_by(empresa_id=empresa.id).first()
    checar("A filial foi criada com o CNPJ informado", filial is not None and filial.cnpj == CNPJ_CLINICA)
    filial_id = filial.id
client.get("/logout")

# ---------- Cadastro sem informar CNPJ (modo empresa) é rejeitado ----------

r = client.post("/cadastro", data={
    "modo": "empresa",
    "nome_empresa": "Clínica Sem CNPJ",
    "nome": "Dr. Sem Cnpj",
    "cpf": "123.456.789-09", "crm_numero": "99999", "crm_uf": "ES",
    "email": "semcnpj@example.com",
    "senha": "123456",
    "papel": "medico",
    "nome_filial": "Consultório",
    "telefone_filial": "(27) 90000-0010",
}, follow_redirects=True)
checar("Cadastro 'empresa' sem CNPJ é rejeitado", "Informe o CNPJ da sua clínica" in r.get_data(as_text=True))
with app.app_context():
    checar("Nenhuma empresa foi criada", Empresa.query.filter_by(nome="Clínica Sem CNPJ").first() is None)

# ---------- Cadastro com CNPJ inválido é rejeitado ----------

r = client.post("/cadastro", data={
    "modo": "empresa",
    "nome_empresa": "Clínica CNPJ Invalido",
    "nome": "Dr. Cnpj Invalido",
    "cpf": "123.456.789-09", "crm_numero": "99999", "crm_uf": "ES",
    "email": "cnpjinvalido@example.com",
    "senha": "123456",
    "papel": "medico",
    "nome_filial": "Consultório",
    "telefone_filial": "(27) 90000-0011",
    "cnpj_filial": "11.111.111/1111-11",
}, follow_redirects=True)
checar("Cadastro 'empresa' com CNPJ inválido é rejeitado", "CNPJ inválido" in r.get_data(as_text=True))

# ---------- Busca: agora o CNPJ já é encontrado, com o nome da clínica ----------

r = client.get("/cadastro/verificar-cnpj?cnpj=" + CNPJ_CLINICA)
dados = r.get_json()
checar("Busca por CNPJ já cadastrado encontra a clínica", dados["encontrada"] is True)
checar("Busca devolve o nome da clínica encontrada", dados["nome"] == "Clínica Bela Vista - Sede")

# ---------- Segundo médico se cadastra com o MESMO CNPJ, sem saber da Ana ----------

r = client.post("/cadastro", data={
    "modo": "empresa",
    "nome_empresa": "Clínica Bela Vista (nome que nem chega a ser usado)",
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
    checar("NENHUMA empresa nova foi criada para o Bruno",
           Empresa.query.filter_by(nome="Clínica Bela Vista (nome que nem chega a ser usado)").first() is None)
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

# ---------- Modo "independente" continua sem exigir CNPJ (empresa oculta/pessoal) ----------

r = client.post("/cadastro", data={
    "modo": "independente", "papel": "medico",
    "nome": "Dra. Autonoma Sem Cnpj",
    "cpf": "111.444.777-35", "crm_numero": "54321", "crm_uf": "ES",
    "email": "autonoma.semcnpj@example.com",
    "senha": "123456",
    "telefone_filial": "(27) 90000-0012",
}, follow_redirects=True)
checar("Cadastro 'independente' sem CNPJ continua funcionando normalmente", r.status_code == 200)
with app.app_context():
    checar("Conta da médica independente foi criada mesmo sem CNPJ",
           Usuario.query.filter_by(email="autonoma.semcnpj@example.com").first() is not None)
client.get("/logout")

print("\nTodos os testes de cadastro por CNPJ passaram.")
