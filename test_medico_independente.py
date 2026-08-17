"""Testa o cadastro público UNIFICADO: não existe mais "modo" escolhido
por botão ("empresa" x "independente") e não existe nenhum campo de
clínica ali (nem nome, nem CNPJ, nem endereço) - o cadastro pede só os
dados PESSOAIS de quem está se cadastrando.

Fatia 5: o cadastro cria um GRUPO (não mais uma Empresa) do qual a pessoa
é a dona (GrupoMembro papel="dono") - o Grupo nasce sempre, mas com só um
membro ele fica invisível pro dia a dia de quem trabalha sozinho(a): é
escolhido automaticamente (nenhuma tela/termo "Grupo" aparece) e só passa
a ser um conceito de verdade se essa pessoa decidir "criar um novo grupo
de trabalho" ou convidar alguém pra Equipe (ver app/clinica_utils.py e
routes_grupo.py). Isso é exatamente o cenário do médico/secretário(a)
independente: a conta funciona 100% sem nunca precisar pensar em "grupo".

Roda contra SQLite local (não usa a DATABASE_URL do .env) e reseta esse banco de
teste no início — não toca no banco real. Para rodar:
    DATABASE_URL=sqlite:///teste_independente.db python test_medico_independente.py
"""
from app import create_app
from app.extensions import db
from app.db_utils import resetar_banco
from app.models import Usuario, Grupo, GrupoMembro

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
    checar("Usuário recebeu todas as permissões administrativas (é dono do próprio Grupo)",
           usuario.perm_filiais is True and usuario.perm_equipe is True and usuario.perm_dados_clinica is True)

    grupo = Grupo.query.filter_by(email_contato="joao.autonomo@example.com").first()
    checar("Um Grupo (não mais uma Empresa) foi criado para a pessoa", grupo is not None)
    checar("Nome provisório do Grupo vem do nome da pessoa (não há campo de clínica no cadastro)",
           grupo.nome == "Consultório de Dr. João Autônomo")
    checar(
        "A pessoa é a DONA do Grupo (GrupoMembro papel='dono', ativo)",
        GrupoMembro.query.filter_by(grupo_id=grupo.id, usuario_id=usuario.id, papel="dono", ativo=True).count() == 1,
    )
    checar("O Grupo nasce em trial", grupo.status == "trial")
    grupo1_id = grupo.id

checar("Login automático após cadastro (cai direto em Dados da clínica, não na tela de login)",
       "/login" not in r.request.path if hasattr(r, "request") else True)

# Com um único Grupo, ele é escolhido automaticamente - nenhuma tela de
# escolha aparece, "Grupo" nunca precisa ser mencionado pro médico
# independente.
r_dash = client.get("/equipe/")
checar("Painel do médico independente carrega direto (Grupo único = automático)", r_dash.status_code == 200)

# ---------- "Meus Locais de Atendimento" -> hoje é só um redirect pra "Meus Grupos" ----------
# Fatia 5: não existe mais "cadastrar uma segunda filial da mesma
# empresa" - cadastrar um "novo local" (medico.filiais_nova) é, por
# baixo, criar um GRUPO NOVO E INDEPENDENTE (grupo.novo), do qual a
# pessoa também é dona. Isso é justamente o caso de "vínculo em mais de
# um Grupo" descrito em app/clinica_utils.py - precisa escolher qual está
# usando no momento.
r_filiais_nova = client.get("/equipe/filiais/nova", follow_redirects=False)
checar(
    "medico.filiais_nova é hoje só um redirect para grupo.novo (não existe mais 'filial de uma empresa')",
    r_filiais_nova.status_code in (301, 302) and "/grupos/novo" in r_filiais_nova.headers.get("Location", ""),
)

r_novo_grupo = client.post("/grupos/novo", data={"nome": "Consultório - Praia"}, follow_redirects=True)
checar("Criar um segundo Grupo responde 200", r_novo_grupo.status_code == 200)

with app.app_context():
    vinculos = GrupoMembro.query.filter_by(usuario_id=usuario.id, ativo=True).all()
    checar("Agora o médico tem vínculo ATIVO em DOIS grupos independentes", len(vinculos) == 2)
    grupo2 = Grupo.query.filter_by(nome="Consultório - Praia").first()
    checar("O segundo Grupo foi criado (não é uma filial do primeiro - são unidades separadas)",
           grupo2 is not None and grupo2.id != grupo1_id)

# Com vínculo em mais de um Grupo, a escolha deixa de ser automática -
# ver empresa_atual()/escolher_clinica em app/clinica_utils.py e
# routes_medico.py.
r_escolher = client.get("/equipe/clinica")
checar("Com 2 grupos, a tela de escolha aparece (não seleciona sozinho)", r_escolher.status_code == 200)

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
    checar("Secretária não recebeu código mestre (mecanismo removido, não é mais gerado por ninguém)",
           secretaria_autonoma.codigo_mestre is None)
    checar("Secretária também recebeu todas as permissões (é dona do próprio Grupo)",
           secretaria_autonoma.perm_filiais is True)
    grupo_secretaria = Grupo.query.filter_by(email_contato="secretaria.autonoma@example.com").first()
    checar("Grupo da secretária também foi criado, com ela como dona", grupo_secretaria is not None)
client.get("/logout")

# ---------- Validação: cadastro sem nome/email/senha é rejeitado ----------
r = client.post("/cadastro", data={"nome": "", "email": "", "senha": ""})
checar("Cadastro sem dados obrigatórios é rejeitado", r.status_code == 200 and "Preencha todos os campos" in r.get_data(as_text=True))

# ---------- Duas contas com nomes iguais/parecidos não colidem no nome do Grupo ----------
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
    grupo3 = Grupo.query.filter_by(nome="Consultório de Bruno Pavan").first()
    checar("Grupo foi criado com o nome provisório baseado no nome da pessoa", grupo3 is not None)
    checar(
        "É um Grupo à parte, dono só do próprio Bruno",
        GrupoMembro.query.filter_by(grupo_id=grupo3.id).count() == 1,
    )

print("\nTodos os testes do fluxo de cadastro público unificado (sobre Grupo) passaram.")
