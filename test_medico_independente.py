"""Testa o cadastro público UNIFICADO: não existe mais "modo" escolhido
por botão ("empresa" x "independente") e não existe nenhum campo de
clínica ali (nem nome, nem CNPJ, nem endereço) - o cadastro pede só os
dados PESSOAIS de quem está se cadastrando.

Fatia 6: o cadastro NÃO cria mais um Grupo. A conta nasce solo, plenamente
usável (pacientes/exames/agendamentos ficam com escopo pessoal via
criado_por_id/cadastrado_por_id - ver app/clinica_utils.py:
filtro_escopo_atual()) sem nunca precisar de um Grupo. Um Grupo de
verdade só nasce se a pessoa decidir criar um grupo de trabalho ou
convidar alguém pra Equipe (ver routes_grupo.py:novo()) - nesse momento o
histórico pessoal dela é migrado pro Grupo recém-criado (ver
migrar_dados_pessoais_para_grupo()). Isso é exatamente o cenário do
médico/secretário(a) independente: a conta funciona 100% sem nunca
precisar pensar em "grupo".

Roda contra SQLite local (não usa a DATABASE_URL do .env) e reseta esse banco de
teste no início — não toca no banco real. Para rodar:
    DATABASE_URL=sqlite:///teste_independente.db python test_medico_independente.py
"""
from app import create_app
from app.extensions import db
from app.db_utils import resetar_banco
from app.models import Usuario, GrupoMembro, Paciente, Exame, Agendamento, GrupoPaciente

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
    checar("Usuário recebeu todas as permissões administrativas (mesmo sem Grupo)",
           usuario.perm_filiais is True and usuario.perm_equipe is True and usuario.perm_dados_clinica is True)

    checar("NENHUM Grupo foi criado no cadastro (Fatia 6: Grupo é opcional)",
           GrupoMembro.query.filter_by(usuario_id=usuario.id).first() is None)
    usuario_id = usuario.id

checar("Login automático após cadastro (cai direto no painel, não na tela de login)",
       "/login" not in r.request.path if hasattr(r, "request") else True)

# Sem Grupo nenhum, a conta é solo — o painel carrega direto (staff_required
# deixou de deslogar quem nunca teve Grupo, ver Fatia 6 passo 1).
r_dash = client.get("/equipe/")
checar("Painel do médico independente carrega direto, mesmo sem nenhum Grupo", r_dash.status_code == 200)

# ---------- Uso pleno da conta solo: paciente/exame/agendamento sem Grupo ----------
r_pac = client.post("/equipe/pacientes/novo", data={
    "nome": "Paciente do Dr. João", "cpf": "111.222.333-96", "telefone": "(27) 98888-2222",
    "data_nascimento": "01/01/1990",
}, follow_redirects=True)
checar("Conta solo consegue cadastrar paciente sem Grupo", r_pac.status_code == 200)

with app.app_context():
    paciente = Paciente.query.filter_by(cpf="11122233396").first() or Paciente.query.filter_by(cpf="111.222.333-96").first()
    checar("Paciente foi criado com dono pessoal (cadastrado_por_id), sem empresa/grupo",
           paciente is not None and paciente.cadastrado_por_id == usuario_id and paciente.empresa_id is None)
    paciente_id = paciente.id

r_exame = client.post("/equipe/exames/novo", data={
    "nome": "Consulta Avulsa", "descricao": "desc", "preparo_modelo_id": "nenhum",
    "duracao_minutos": "30",
}, follow_redirects=True)
checar("Conta solo consegue cadastrar exame sem Grupo", r_exame.status_code == 200)

with app.app_context():
    exame = Exame.query.filter_by(nome="Consulta Avulsa").first()
    checar("Exame foi criado com dono pessoal (criado_por_id), sem grupo_id",
           exame is not None and exame.criado_por_id == usuario_id and exame.grupo_id is None)
    exame_id = exame.id

r_assoc = client.post("/equipe/exames/por-filial/associar", data={
    "nome": "Consulta Avulsa", "medico_id": str(usuario_id), "preco": "200,00",
}, follow_redirects=True)
checar("Conta solo consegue associar médico/preço ao exame, sem Grupo", r_assoc.status_code == 200)

r_agenda = client.post("/equipe/agenda/novo", data={
    "paciente_id": str(paciente_id), "exame_id": str(exame_id),
    "data_hora": "2026-09-10T09:00", "observacoes": "",
}, follow_redirects=True)
checar("Conta solo consegue criar agendamento sem Grupo", r_agenda.status_code == 200)

with app.app_context():
    agendamento = Agendamento.query.filter_by(paciente_id=paciente_id, exame_id=exame_id).first()
    checar("Agendamento foi criado com dono pessoal (criado_por_id), sem grupo_id",
           agendamento is not None and agendamento.criado_por_id == usuario_id and agendamento.grupo_id is None)

# ---------- Criar um grupo de trabalho de verdade migra o histórico pessoal ----------
r_novo_grupo = client.post("/grupos/novo", data={"nome": "Consultório - Praia"}, follow_redirects=True)
checar("Criar um Grupo de trabalho responde 200", r_novo_grupo.status_code == 200)

with app.app_context():
    vinculo = GrupoMembro.query.filter_by(usuario_id=usuario_id, ativo=True, papel="dono").first()
    checar("Agora o médico tem vínculo ATIVO (dono) no Grupo recém-criado", vinculo is not None)
    grupo = vinculo.grupo
    checar("O Grupo foi criado com o nome escolhido", grupo.nome == "Consultório - Praia")

    checar("O paciente pessoal foi migrado pro Grupo (GrupoPaciente)",
           GrupoPaciente.query.filter_by(grupo_id=grupo.id, paciente_id=paciente_id).first() is not None)
    exame_migrado = Exame.query.get(exame_id)
    checar("O exame pessoal foi migrado pro Grupo", exame_migrado.grupo_id == grupo.id)
    agendamento_migrado = Agendamento.query.filter_by(paciente_id=paciente_id, exame_id=exame_id).first()
    checar("O agendamento pessoal foi migrado pro Grupo", agendamento_migrado.grupo_id == grupo.id)

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
    checar("Secretária também recebeu todas as permissões (mesmo sem Grupo)",
           secretaria_autonoma.perm_filiais is True)
    checar("NENHUM Grupo foi criado para a secretária no cadastro",
           GrupoMembro.query.filter_by(usuario_id=secretaria_autonoma.id).first() is None)
client.get("/logout")

# ---------- Validação: cadastro sem nome/email/senha é rejeitado ----------
r = client.post("/cadastro", data={"nome": "", "email": "", "senha": ""})
checar("Cadastro sem dados obrigatórios é rejeitado", r.status_code == 200 and "Preencha todos os campos" in r.get_data(as_text=True))

# ---------- Duas contas solo com nomes iguais/parecidos não colidem (não há Grupo pra colidir) ----------
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
    bruno = Usuario.query.filter_by(email="bruno.pavan@example.com").first()
    checar("Conta solo do Bruno foi criada, sem nenhum Grupo",
           bruno is not None and GrupoMembro.query.filter_by(usuario_id=bruno.id).first() is None)

print("\nTodos os testes do fluxo de cadastro público (conta solo, Fatia 6) passaram.")
