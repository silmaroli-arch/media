"""Testa que a lista "Meus pacientes" (medico.pacientes_lista) agora mostra
uma coluna de Status do cadastro (Aprovado/Pendente/Rejeitado) para cada
paciente - antes a lista não trazia nenhuma indicação de status, obrigando
a abrir os Detalhes de cada paciente para saber se o cadastro estava
pendente ou rejeitado."""
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

# Paciente cadastrado pela equipe já nasce aprovado.
r = client.post("/equipe/pacientes/novo", data={
    "nome": "Paciente Status Aprovado", "cpf": "111.222.333-44",
    "telefone": "(27) 91234-5678", "data_nascimento": "01/01/1990",
}, follow_redirects=True)

with app.app_context():
    paciente_id = Paciente.query.filter_by(cpf="111.222.333-44").first().id

# Rejeita o cadastro para ter um exemplo com status "rejeitado" na lista.
client.post(f"/equipe/pacientes/{paciente_id}/cadastro/decidir", data={
    "acao": "rejeitar", "origem": "detalhe",
}, follow_redirects=True)

r = client.get("/equipe/pacientes")
html = r.get_data(as_text=True)
checar("Lista de pacientes responde 200", r.status_code == 200)
checar("Lista de pacientes tem a coluna 'Status'", "Status" in html)
checar("Lista mostra o badge 'Aprovado' (paciente aprovado por padrão)", "Aprovado" in html)
checar("Lista mostra o badge 'Rejeitado' (paciente que acabamos de rejeitar)", "Rejeitado" in html)

client.get("/logout")
print("\nTodos os testes de status na lista de pacientes passaram.")
