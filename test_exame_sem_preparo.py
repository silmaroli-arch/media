"""Testa que o Modelo de preparo passou a ser OBRIGATÓRIO no cadastro de um
novo exame - o formulário não vem mais com "Nenhum" pré-selecionado, e a
pessoa precisa escolher um modelo explicitamente. Se a clínica ainda não
tem nenhum modelo de preparo cadastrado, o cadastro de exame é bloqueado com
uma mensagem orientando a criar um modelo primeiro. Exames JÁ existentes
sem modelo (de antes dessa regra) continuam funcionando normalmente, e a
edição de um exame continua permitindo deixar sem modelo."""
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

# O formulário de novo exame não vem mais com "Nenhum" pré-selecionado -
# tem que ser required e sem opção marcada por padrão.
r0 = client.get("/equipe/exames/novo")
html0 = r0.get_data(as_text=True)
checar("Formulário de novo exame responde 200", r0.status_code == 200)
checar("Campo de modelo de preparo é obrigatório (required)", 'name="preparo_modelo_id" class="form-select" required' in html0)
checar("Não vem mais com 'Nenhum' pré-selecionado", 'value="" disabled selected' in html0)
checar("Não oferece mais a opção 'Nenhum' no cadastro", "Nenhum — este procedimento não precisa de preparo" not in html0)

# Tentar cadastrar sem escolher um modelo é bloqueado.
r1 = client.post("/equipe/exames/novo", data={
    "nome": "Consulta", "descricao": "Consulta", "duracao_minutos": "20",
    # preparo_modelo_id de propósito ausente
}, follow_redirects=True)
checar("Cadastro sem modelo de preparo responde 200 (mostra erro, não quebra)", r1.status_code == 200)
checar("Mostra aviso pedindo para escolher um modelo", "Escolha um modelo de preparo" in r1.get_data(as_text=True))
with app.app_context():
    checar("Exame 'Consulta' NÃO foi criado sem modelo", Exame.query.filter_by(nome="Consulta").first() is None)

# Cadastrando COM um modelo escolhido funciona normalmente.
with app.app_context():
    modelo_id = PreparoModelo.query.first().id
r2 = client.post("/equipe/exames/novo", data={
    "nome": "Consulta", "descricao": "Consulta", "duracao_minutos": "20",
    "preparo_modelo_id": str(modelo_id),
}, follow_redirects=True)
checar("Cadastro com modelo escolhido responde 200", r2.status_code == 200)
checar("Mensagem de sucesso aparece", "Exame cadastrado com sucesso" in r2.get_data(as_text=True))
with app.app_context():
    consulta = Exame.query.filter_by(nome="Consulta").first()
    checar("Exame 'Consulta' foi criado", consulta is not None)
    checar("Exame foi criado com o modelo escolhido", consulta.preparo_modelo_id == modelo_id)

client.get("/logout")
print("\nTodos os testes de modelo de preparo obrigatório no cadastro passaram.")
