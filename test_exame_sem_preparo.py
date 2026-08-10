"""Testa que dá pra cadastrar um exame/procedimento (ex.: 'Consulta') sem
vincular nenhum modelo de preparo - antes o formulário e a rota exigiam um
modelo de preparo obrigatoriamente, mesmo para procedimentos simples que
não precisam de nenhuma instrução prévia."""
from app import create_app
from app.extensions import db
from app.models import Exame, Clinica

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


login("secretaria@clinicavitoria.com", "123456")

with app.app_context():
    from app.models import Usuario
    medico_id = Usuario.query.filter_by(email="medico@clinicavitoria.com").first().id

r = client.post("/equipe/exames/novo", data={
    "nome": "Consulta", "descricao": "Consulta", "duracao_minutos": "20", "preco": "0",
    "medico_id": str(medico_id),
    # preparo_modelo_id de propósito ausente/vazio
}, follow_redirects=True)
checar("Cadastro de exame sem modelo de preparo responde 200", r.status_code == 200)
checar("Não pede mais 'modelo de preparo obrigatório'", "modelo de preparo são obrigatórios" not in r.get_data(as_text=True).lower())

with app.app_context():
    consulta = Exame.query.filter_by(nome="Consulta").first()
    checar("Exame 'Consulta' foi criado", consulta is not None)
    checar("Exame foi criado sem nenhum modelo de preparo vinculado", consulta.preparo_modelo_id is None)
    checar("Propriedade .preparo retorna None sem quebrar", consulta.preparo is None)

# A lista de exames mostra a mensagem neutra (não mais em vermelho/alarmante).
r = client.get("/equipe/exames")
html = r.get_data(as_text=True)
checar("Lista de exames mostra 'Sem modelo de preparo' para a Consulta", "Sem modelo de preparo" in html)

# Exames que JÁ têm preparo continuam funcionando normalmente (sem regressão).
r = client.get("/equipe/exames")
checar("Lista de exames continua mostrando exames com preparo (ex.: Colonoscopia)", "Colonoscopia" in html)

client.get("/logout")
print("\nTodos os testes de exame sem modelo de preparo passaram.")
