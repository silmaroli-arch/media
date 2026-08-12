"""Testa que a tela de solicitação de agendamento (paciente.solicitar_agendamento)
mostra a filial/local de atendimento do exame escolhido ANTES da lista de
horários disponíveis - antes só aparecia o nome do médico, sem indicar em
qual unidade/filial os horários seriam, o que ficou ambíguo desde que um
mesmo médico passou a poder atender mais de uma filial."""
from app import create_app
from app.extensions import db
from app.models import Exame

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login_paciente(cpf, data_nascimento):
    return client.post(
        "/login-paciente",
        data={"cpf": cpf, "data_nascimento": data_nascimento},
        follow_redirects=True,
    )


with app.app_context():
    exame = Exame.query.filter_by(nome="Colonoscopia").first()
    clinica_nome = exame.clinica.nome
    clinica_rua = exame.clinica.rua
    clinica_cidade = exame.clinica.cidade
    exame_id = exame.id

# João Pereira (paciente da Clínica Vitória, cadastrado no seed.py).
login_paciente("123.456.789-00", "12/04/1985")

r = client.get(f"/paciente/agendar?exame_id={exame_id}")
html = r.get_data(as_text=True)
checar("Tela de agendamento responde 200", r.status_code == 200)
checar("Mostra o rótulo 'Local de atendimento'", "Local de atendimento" in html)
checar("Mostra o nome da filial/clínica do exame", clinica_nome in html)
checar("Mostra a rua do endereço da filial", clinica_rua in html)
checar("Mostra a cidade do endereço da filial", clinica_cidade in html)

# O bloco do local de atendimento aparece ANTES do bloco de horários sugeridos.
pos_local = html.find("Local de atendimento")
pos_horarios = html.find("horários disponíveis")
checar("Bloco de local de atendimento aparece antes dos horários", 0 <= pos_local < pos_horarios)

client.get("/logout")
print("\nTodos os testes de filial no agendamento passaram.")
