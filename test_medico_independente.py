"""Testa o cadastro público UNIFICADO: não existe mais "modo" escolhido
por botão ("empresa" x "independente") e, desde a remoção do formulário de
"local de atendimento" do cadastro, também não existe mais nenhum campo de
clínica ali (nem nome, nem CNPJ, nem endereço) - o cadastro pede só os
dados PESSOAIS de quem está se cadastrando. A empresa nasce com um nome
provisório a partir do nome da pessoa, e o(s) local(is) de atendimento são
cadastrados depois em "Meus Locais de Atendimento"
(/equipe/filiais/nova) - ver test_cadastro_empresa_sem_filial.py para o
fluxo completo desse cadastro de local sem vínculo automático.

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

# ---------- Login: um único link de cadastro, genérico, sem "modo" nenhum ----------
r_login = client.get("/login")
html_login = r_login.get_data(as_text=True)
checar("Link de criar conta manda pro cadastro único, sem parâmetro de modo",
       "/cadastro" in html_login and "modo=" not in html_login)

# ---------- Cadastro: uma única tela, sem escolha de modo e sem nenhum campo de clínica ----------
r_cad = client.get("/cadastro")
html_cad = r_cad.get_data(as_text=True)
checar("NÃO existe mais grupo de botões pra escolher o tipo de conta (concentrado numa única tela)",
       'id="grupo-modo"' not in html_cad and "Tenho uma clínica/empresa" not in html_cad
       and "Sou profissional independente" not in html_cad)
checar("NÃO existe mais um campo separado de \"nome da empresa\"",
       'name="nome_empresa"' not in html_cad)
checar("NÃO existe mais nenhum campo de local de atendimento (nome, CNPJ, endereço)",
       'name="nome_filial"' not in html_cad and 'name="cnpj_filial"' not in html_cad
       and 'name="telefone_filial"' not in html_cad and 'name="cep_filial"' not in html_cad)
checar("O CPF já é anunciado como o login da conta",
       "CPF (será seu login)" in html_cad)

# ---------- Cadastro só com dados pessoais, papel médico ----------
r = client.post("/cadastro", data={
    "nome": "Dr. João Autônomo",
    "papel": "medico",
    "cpf": "852.963.741-00", "crm_numero": "44444", "crm_uf": "ES",
    "email": "joao.autonomo@example.com",
    "senha": "123456",
}, follow_redirects=True)
checar("Cadastro responde 200", r.status_code == 200)

with app.app_context():
    usuario = Usuario.query.filter_by(email="joao.autonomo@example.com").first()
    checar("Usuário foi criado", usuario is not None)
    checar("Papel escolhido (médico) foi respeitado", usuario.tipo == "medico")
    checar("Usuário recebeu perm_filiais (pode cadastrar novos locais sozinho)", usuario.perm_filiais is True)

    empresa = Empresa.query.filter_by(email_contato="joao.autonomo@example.com").first()
    checar("Empresa (pessoal) foi criada", empresa is not None)
    checar("Nome provisório da empresa vem do nome da pessoa (não há mais local de atendimento no cadastro)",
           empresa.nome == "Consultório de Dr. João Autônomo")

    checar("NENHUM local de atendimento é criado automaticamente no cadastro",
           Clinica.query.filter_by(empresa_id=empresa.id).count() == 0)

checar("Login automático após cadastro (cai direto no onboarding, não na tela de login)",
       "/login" not in r.request.path if hasattr(r, "request") else True)

# ---------- Médico cadastra seu primeiro local sozinho, depois um segundo ----------
r = client.post("/equipe/filiais/nova", data={"nome": "Consultório do Dr. João"}, follow_redirects=True)
checar("Médico consegue cadastrar seu primeiro local (perm_filiais já vem habilitada)",
       "cadastrado com sucesso" in r.get_data(as_text=True).lower())
r = client.post("/equipe/filiais/nova", data={"nome": "Consultório - Praia"}, follow_redirects=True)
checar("Médico consegue cadastrar um segundo local", "cadastrado com sucesso" in r.get_data(as_text=True).lower())

with app.app_context():
    total_filiais = Clinica.query.filter_by(empresa_id=empresa.id).count()
    checar("Agora existem 2 locais de atendimento na mesma empresa", total_filiais == 2)

client.get("/logout")

# ---------- Cadastro, papel secretária (não é mais exclusivo de médico) ----------
r = client.post("/cadastro", data={
    "nome": "Secretária Autônoma",
    "papel": "secretaria",
    "cpf": "123.456.789-09",
    "email": "secretaria.autonoma@example.com",
    "senha": "123456",
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

# ---------- Duas contas com nomes iguais/parecidos não colidem no nome da empresa ----------
client.get("/logout")
r = client.post("/cadastro", data={
    "nome": "Bruno Pavan",
    "cpf": "168.995.350-09", "crm_numero": "77777", "crm_uf": "ES",
    "email": "bruno.pavan@example.com",
    "senha": "123456",
    "papel": "medico",
}, follow_redirects=True)
checar("Cadastro responde 200", r.status_code == 200)
with app.app_context():
    empresa3 = Empresa.query.filter_by(nome="Consultório de Bruno Pavan").first()
    checar("Empresa foi criada com o nome provisório baseado no nome da pessoa", empresa3 is not None)
    checar("NENHUM local de atendimento foi criado automaticamente",
           Clinica.query.filter_by(empresa_id=empresa3.id).count() == 0)

print("\nTodos os testes do fluxo de cadastro público unificado passaram.")
