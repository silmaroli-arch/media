"""Testa que dá pra reverter o status de cadastro de um paciente (aprovar
de novo depois de rejeitar, ou rejeitar mesmo já aprovado) direto na tela
de detalhe do paciente - antes, uma vez rejeitado, o cadastro ficava
travado nesse estado para sempre, sem nenhuma tela pra reverter."""
from app import create_app
from app.extensions import db
from app.models import Paciente

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


login("secretaria@clinicavitoria.com", "123456")

r = client.post("/equipe/pacientes/novo", data={
    "nome": "Paciente Reversao Teste", "cpf": "789.789.789-00",
    "telefone": "(27) 93333-2222", "data_nascimento": "12/12/1999",
}, follow_redirects=True)

with app.app_context():
    paciente = Paciente.query.filter_by(cpf="789.789.789-00").first()
    checar("Paciente de teste foi criado", paciente is not None)
    paciente_id = paciente.id

# Rejeita o cadastro.
r = client.post(f"/equipe/pacientes/{paciente_id}/cadastro/decidir", data={
    "acao": "rejeitar", "origem": "detalhe",
}, follow_redirects=True)
checar("Rejeitar responde 200", r.status_code == 200)
with app.app_context():
    checar("Status ficou 'rejeitado'", Paciente.query.get(paciente_id).status_cadastro == "rejeitado")

# A tela de detalhe mostra o botão para aprovar mesmo depois de rejeitado.
r = client.get(f"/equipe/pacientes/{paciente_id}")
html = r.get_data(as_text=True)
checar("Tela de detalhe mostra o badge 'Rejeitado'", "Rejeitado" in html)
checar("Tela de detalhe oferece o botão 'Aprovar cadastro' mesmo rejeitado", "Aprovar cadastro" in html)

# Reverte: aprova o cadastro que tinha sido rejeitado.
r = client.post(f"/equipe/pacientes/{paciente_id}/cadastro/decidir", data={
    "acao": "aceitar", "origem": "detalhe",
}, follow_redirects=True)
with app.app_context():
    checar("Status voltou para 'aprovado' depois de ter sido rejeitado", Paciente.query.get(paciente_id).status_cadastro == "aprovado")

# A ação feita com origem=detalhe volta para a tela de detalhe do paciente, não para a fila de solicitações.
checar("Redireciona de volta para a tela de detalhe do paciente", f"/equipe/pacientes/{paciente_id}" in r.request.path)

# O fluxo antigo (a partir da fila de solicitações pendentes) continua funcionando normalmente.
r2 = client.post("/equipe/pacientes/novo", data={
    "nome": "Paciente Fila Pendente", "cpf": "321.321.321-00",
    "telefone": "(27) 92222-1111", "data_nascimento": "01/01/2000",
}, follow_redirects=True)
with app.app_context():
    paciente2_id = Paciente.query.filter_by(cpf="321.321.321-00").first().id

r2 = client.post(f"/equipe/pacientes/{paciente2_id}/cadastro/decidir", data={"acao": "aceitar"}, follow_redirects=True)
checar("Fluxo antigo (sem 'origem') continua redirecionando para a fila de solicitações", "/equipe/pacientes/solicitacoes" in r2.request.path)

client.get("/logout")
print("\nTodos os testes de reversão do status de cadastro passaram.")
