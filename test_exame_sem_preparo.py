"""Testa que o Modelo de preparo é uma escolha OBRIGATÓRIA no cadastro de um
novo exame (não vem mais "Nenhum" pré-selecionado, a pessoa precisa
escolher algo), mas uma das opções continua sendo "Nenhum" - um exame pode
legitimamente não precisar de preparo nenhum (ex.: uma consulta simples).
O que não é permitido é deixar o campo sem escolher nada."""
from app import create_app
from app.extensions import db
from app.models import Exame, PreparoModelo, Clinica

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


login("secretaria@clinicavitoria.com", "123456")

# O formulário mostra "Nenhum" como opção (não como pré-selecionada) e um
# placeholder "Selecione..." desabilitado, forçando uma escolha ativa.
r0 = client.get("/equipe/exames/novo")
html0 = r0.get_data(as_text=True)
checar("Formulário de novo exame responde 200", r0.status_code == 200)
checar("Campo de modelo de preparo é obrigatório (required)", 'name="preparo_modelo_id" class="form-select" required' in html0)
checar("Tem a opção 'Nenhum' disponível para escolher", 'value="nenhum"' in html0)
checar("A opção 'Nenhum' NÃO vem pré-selecionada", 'value="nenhum">Nenhum' in html0 and 'value="nenhum" selected' not in html0)
checar("Vem com um placeholder 'Selecione...' desabilitado (nada pré-escolhido)", 'value="" disabled selected' in html0)

# Tentar cadastrar sem escolher NADA é bloqueado.
r1 = client.post("/equipe/exames/novo", data={
    "nome": "Consulta", "descricao": "Consulta", "duracao_minutos": "20",
    # preparo_modelo_id de propósito ausente
}, follow_redirects=True)
checar("Cadastro sem escolher nada responde 200 (mostra erro, não quebra)", r1.status_code == 200)
checar("Mostra aviso pedindo para escolher uma opção", "Escolha uma opção de modelo de preparo" in r1.get_data(as_text=True))
with app.app_context():
    checar("Exame 'Consulta' NÃO foi criado sem escolher nada", Exame.query.filter_by(nome="Consulta").first() is None)

# Escolhendo explicitamente "Nenhum" funciona - o exame nasce sem preparo.
r2 = client.post("/equipe/exames/novo", data={
    "nome": "Consulta", "descricao": "Consulta", "duracao_minutos": "20",
    "preparo_modelo_id": "nenhum",
}, follow_redirects=True)
checar("Cadastro escolhendo 'Nenhum' responde 200", r2.status_code == 200)
checar("Mensagem de sucesso aparece", "Exame cadastrado com sucesso" in r2.get_data(as_text=True))
with app.app_context():
    consulta = Exame.query.filter_by(nome="Consulta").first()
    checar("Exame 'Consulta' foi criado", consulta is not None)
    checar("Exame foi criado sem nenhum modelo de preparo vinculado", consulta.preparo_modelo_id is None)
    checar("Propriedade .preparo retorna None sem quebrar", consulta.preparo is None)

# Escolhendo um modelo de verdade também funciona.
with app.app_context():
    clinica_vitoria_id = Clinica.query.filter_by(nome="Clínica Vitória").first().id
    modelo_id = PreparoModelo.query.filter_by(clinica_id=clinica_vitoria_id).first().id
r3 = client.post("/equipe/exames/novo", data={
    "nome": "Colonoscopia com preparo", "descricao": "Colono", "duracao_minutos": "45",
    "preparo_modelo_id": str(modelo_id),
}, follow_redirects=True)
checar("Cadastro escolhendo um modelo de verdade responde 200", r3.status_code == 200)
with app.app_context():
    exame_com_preparo = Exame.query.filter_by(nome="Colonoscopia com preparo").first()
    checar("Exame foi criado com o modelo escolhido", exame_com_preparo.preparo_modelo_id == modelo_id)

# A lista de exames mostra a mensagem neutra para quem não tem preparo.
r = client.get("/equipe/exames")
html = r.get_data(as_text=True)
checar("Lista de exames mostra 'Sem modelo de preparo' para a Consulta", "Sem modelo de preparo" in html)
checar("Lista de exames continua mostrando exames com preparo (ex.: Colonoscopia)", "Colonoscopia" in html)

client.get("/logout")
print("\nTodos os testes de modelo de preparo obrigatório (com opção 'Nenhum') passaram.")
