"""Testa o novo fluxo de cadastro público 'médico independente' (modo=independente):
não deve exigir nome_empresa/nome_filial, deve criar papel=medico, permitir múltiplos
locais de atendimento depois via /equipe/filiais/nova, e não deve quebrar o fluxo
'empresa' original.

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

# ---------- Cadastro modo independente ----------
r = client.post("/cadastro", data={
    "modo": "independente",
    "nome": "Dr. João Autônomo",
    "email": "joao.autonomo@example.com",
    "senha": "123456",
}, follow_redirects=True)
checar("Cadastro independente responde 200", r.status_code == 200)

with app.app_context():
    usuario = Usuario.query.filter_by(email="joao.autonomo@example.com").first()
    checar("Usuário foi criado", usuario is not None)
    checar("Papel é 'medico' automaticamente (não pedimos na tela)", usuario.tipo == "medico")
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

# ---------- Validação: modo independente sem nome/email/senha é rejeitado ----------
r = client.post("/cadastro", data={"modo": "independente", "nome": "", "email": "", "senha": ""})
checar("Cadastro independente sem dados obrigatórios é rejeitado", r.status_code == 200 and "Preencha todos os campos" in r.get_data(as_text=True))

# ---------- Fluxo 'empresa' original continua funcionando sem alterações ----------
r = client.post("/cadastro", data={
    "modo": "empresa",
    "nome_empresa": "Clínica Teste Regressão",
    "nome_filial": "Unidade Central",
    "nome": "Dra. Empresa Teste",
    "email": "dra.empresa.teste@example.com",
    "senha": "123456",
    "papel": "medico",
}, follow_redirects=True)
with app.app_context():
    empresa2 = Empresa.query.filter_by(nome="Clínica Teste Regressão").first()
    checar("Fluxo 'empresa' tradicional continua criando empresa com o nome informado", empresa2 is not None)
    filial2 = Clinica.query.filter_by(empresa_id=empresa2.id).first()
    checar("Fluxo 'empresa' tradicional continua criando filial com o nome informado", filial2.nome == "Unidade Central")

print("\nTodos os testes do fluxo médico independente passaram.")
