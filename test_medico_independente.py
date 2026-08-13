"""Testa o novo fluxo de cadastro público 'independente' (modo=independente):
não deve exigir nome_empresa/nome_filial (gerados automaticamente), deve
permitir escolher o papel (médico ou secretário(a), igual no modo
"empresa" — deixou de ser exclusivo de médico), permitir múltiplos
locais de atendimento depois via /equipe/filiais/nova, e não deve
quebrar o fluxo 'empresa' original.

Roda contra SQLite local (não usa a DATABASE_URL do .env) e reseta esse banco de
teste no início — não toca no banco real. Para rodar:
    DATABASE_URL=sqlite:///teste_independente.db python test_medico_independente.py
"""
from app import create_app
from app.extensions import db
from app.db_utils import resetar_banco
from app.models import Usuario, Empresa, Clinica

app = create_app()
client = app.test_client()

with app.app_context():
    resetar_banco(db)

def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome

# ---------- Login: um único link de cadastro, genérico, manda pra modo=independente ----------
# ("Login como clínica" ficou temporariamente escondido dessa tela, a
# pedido do usuário - a rota modo='empresa' continua existindo, só sem
# link aqui, ver test_cadastro_empresa_sem_filial.py etc.)
r_login = client.get("/login")
html_login = r_login.get_data(as_text=True)
checar("Link de criar conta manda pra modo=independente", "/cadastro?modo=independente" in html_login)
checar("Link de 'Login como clínica' não aparece mais nesta tela (escondido, não removido)",
       "/cadastro?modo=empresa" not in html_login)

# ---------- Cadastro: chegando com modo pela URL, esconde a escolha (só mostra o tipo certo) ----------
r_cad = client.get("/cadastro?modo=independente")
html_cad = r_cad.get_data(as_text=True)
checar("A tela sabe esconder o grupo de botões quando o modo já vem escolhido",
       'id="grupo-modo"' in html_cad and "grupo-modo').style.display = 'none'" in html_cad)
checar("Existe um link discreto pra trocar de tipo caso tenha clicado errado",
       'id="link-trocar-modo"' in html_cad)

# ---------- Cadastro modo independente, papel médico ----------
r = client.post("/cadastro", data={
    "modo": "independente",
    "nome": "Dr. João Autônomo",
    "papel": "medico",
    "cpf": "852.963.741-00", "crm_numero": "44444", "crm_uf": "ES",
    "email": "joao.autonomo@example.com",
    "senha": "123456",
    "telefone_filial": "(27) 90000-0004",
}, follow_redirects=True)
checar("Cadastro independente responde 200", r.status_code == 200)

with app.app_context():
    usuario = Usuario.query.filter_by(email="joao.autonomo@example.com").first()
    checar("Usuário foi criado", usuario is not None)
    checar("Papel escolhido (médico) foi respeitado", usuario.tipo == "medico")
    checar("Usuário recebeu perm_filiais (pode cadastrar novos locais sozinho)", usuario.perm_filiais is True)

    empresa = Empresa.query.filter_by(email_contato="joao.autonomo@example.com").first()
    checar("Empresa oculta foi criada", empresa is not None)
    checar("Nome da empresa oculta = nome do médico (não pedimos nome de empresa)", empresa.nome == "Dr. João Autônomo")

    filial = Clinica.query.filter_by(empresa_id=empresa.id).first()
    checar("Local de atendimento (filial) foi criado automaticamente", filial is not None)
    checar("Nome do local padrão é 'Consultório'", filial.nome == "Consultório")

checar("Login automático após cadastro (cai direto no onboarding, não na tela de login)",
       "/login" not in r.request.path if hasattr(r, "request") else True)

# ---------- Médico independente cadastra um SEGUNDO local sozinho ----------
r = client.post("/equipe/filiais/nova", data={"nome": "Consultório - Praia"}, follow_redirects=True)
checar("Médico independente consegue cadastrar um segundo local (perm_filiais já vem habilitada)",
       "cadastrado com sucesso" in r.get_data(as_text=True).lower())

with app.app_context():
    total_filiais = Clinica.query.filter_by(empresa_id=empresa.id).count()
    checar("Agora existem 2 locais de atendimento na mesma empresa oculta", total_filiais == 2)

client.get("/logout")

# ---------- Cadastro modo independente, papel secretária (não é mais exclusivo de médico) ----------
r = client.post("/cadastro", data={
    "modo": "independente",
    "nome": "Secretária Autônoma",
    "papel": "secretaria",
    "cpf": "123.456.789-09",
    "email": "secretaria.autonoma@example.com",
    "senha": "123456",
    "telefone_filial": "(27) 90000-0099",
}, follow_redirects=True)
checar("Cadastro independente como secretária responde 200", r.status_code == 200)
with app.app_context():
    secretaria_autonoma = Usuario.query.filter_by(email="secretaria.autonoma@example.com").first()
    checar("Secretária foi criada com o papel certo", secretaria_autonoma is not None and secretaria_autonoma.tipo == "secretaria")
    checar("Secretária não recebeu código mestre (isso é só de médico)", secretaria_autonoma.codigo_mestre is None)
    checar("Secretária também recebeu todas as permissões (é fundadora da própria conta)",
           secretaria_autonoma.perm_filiais is True)
client.get("/logout")

# ---------- Validação: modo independente sem nome/email/senha é rejeitado ----------
r = client.post("/cadastro", data={"modo": "independente", "nome": "", "email": "", "senha": ""})
checar("Cadastro independente sem dados obrigatórios é rejeitado", r.status_code == 200 and "Preencha todos os campos" in r.get_data(as_text=True))

# ---------- Fluxo 'empresa' original continua funcionando ----------
# O cadastro público agora pede o nome da empresa E os dados completos do
# primeiro local de atendimento (nome, telefone, endereço) - já cria e
# vincula quem se cadastrou a esse primeiro local (ver
# test_cadastro_empresa_sem_filial.py para o fluxo completo).
r = client.post("/cadastro", data={
    "modo": "empresa",
    "nome_empresa": "Clínica Teste Regressão",
    "nome": "Dra. Empresa Teste",
    "cpf": "852.963.741-00", "crm_numero": "55555", "crm_uf": "ES",
    "email": "dra.empresa.teste@example.com",
    "senha": "123456",
    "papel": "medico",
    "nome_filial": "Clínica Teste Regressão - Sede",
    "telefone_filial": "(27) 90000-0005",
    "cnpj_filial": "12.345.606/0001-48",
}, follow_redirects=True)
checar("Cadastro 'empresa' responde 200 (cai no assistente de configuração inicial)", r.status_code == 200)
with app.app_context():
    empresa2 = Empresa.query.filter_by(nome="Clínica Teste Regressão").first()
    checar("Fluxo 'empresa' tradicional continua criando empresa com o nome informado", empresa2 is not None)
    checar(
        "Fluxo 'empresa' já cria o primeiro local de atendimento, com os dados completos",
        Clinica.query.filter_by(empresa_id=empresa2.id, nome="Clínica Teste Regressão - Sede").count() == 1,
    )
    dra_empresa = Usuario.query.filter_by(email="dra.empresa.teste@example.com").first()
    checar("Usuário fica vinculado à empresa via empresa_fundadora_id", dra_empresa.empresa_fundadora_id == empresa2.id)
    from app.models import ClinicaMembro
    checar("Usuário já fica vinculado ao primeiro local criado no cadastro",
           ClinicaMembro.query.filter_by(usuario_id=dra_empresa.id).count() == 1)

print("\nTodos os testes do fluxo médico independente passaram.")
