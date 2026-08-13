"""Testa: "Se o médico logou no app com o seu login, deverá apenas
aparecer a agenda dele no painel."

O painel (medico.dashboard, que incorpora a agenda/calendário) agora
filtra os agendamentos pelo médico logado SEMPRE que quem entrou é um
médico - independente das permissões administrativas que ele tenha (um
médico fundador com todas as permissões continua vendo as telas
administrativas, mas a agenda mostrada é a DELE). Secretária continua
vendo a agenda de todos os médicos."""
from datetime import datetime, timedelta

from app import create_app
from app.extensions import db
from app.models import Usuario, Clinica, Paciente, Exame, Agendamento

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


# Cenário: na Clínica Vitória, um agendamento FUTURO para o Dr. Carlos e
# outro para a Dra. Fernanda - cada médico deve ver só o seu no painel.
with app.app_context():
    vitoria = Clinica.query.filter_by(nome="Clínica Vitória").first()
    carlos = Usuario.query.filter_by(email="medico@clinicavitoria.com").first()
    fernanda = Usuario.query.filter_by(email="medica2@clinicavitoria.com").first()
    joao = Paciente.query.filter_by(nome="João Pereira").first()
    pedro = Paciente.query.filter_by(nome="Pedro Souza").first()
    exame_carlos = Exame.query.filter_by(clinica_id=vitoria.id, medico_id=carlos.id).first()
    exame_fernanda = Exame.query.filter_by(clinica_id=vitoria.id, medico_id=fernanda.id).first()

    futuro = datetime.utcnow() + timedelta(days=30)
    ag_carlos = Agendamento(
        clinica_id=vitoria.id, paciente_id=joao.id, exame_id=exame_carlos.id,
        medico_id=carlos.id, data_hora=futuro.replace(hour=9, minute=0), status="agendado",
    )
    ag_fernanda = Agendamento(
        clinica_id=vitoria.id, paciente_id=pedro.id, exame_id=exame_fernanda.id,
        medico_id=fernanda.id, data_hora=futuro.replace(hour=10, minute=0), status="agendado",
    )
    db.session.add_all([ag_carlos, ag_fernanda])
    db.session.commit()
    nome_exame_carlos = exame_carlos.nome
    nome_exame_fernanda = exame_fernanda.nome

# ---------- Secretária: vê a agenda de TODOS os médicos ----------

login("secretaria@clinicavitoria.com", "123456")
html = client.get("/equipe/").get_data(as_text=True)
checar("Secretária vê o agendamento do Dr. Carlos no painel", "João Pereira" in html)
checar("Secretária vê o agendamento da Dra. Fernanda no painel", "Pedro Souza" in html)
client.get("/logout")

# ---------- Dra. Fernanda (médica, só nesta empresa): vê SÓ a agenda dela ----------

login("medica2@clinicavitoria.com", "123456")
html = client.get("/equipe/").get_data(as_text=True)
checar("Dra. Fernanda vê o agendamento DELA no painel", "Pedro Souza" in html)
checar("Dra. Fernanda NÃO vê o agendamento do Dr. Carlos no painel", "João Pereira" not in html)
client.get("/logout")

# ---------- Dr. Carlos (atua em 2 empresas): vê SÓ a agenda dele ----------

login("medico@clinicavitoria.com", "123456")
client.post("/equipe/clinica", data={"clinica_id": "1"}, follow_redirects=True)
html = client.get("/equipe/").get_data(as_text=True)
checar("Dr. Carlos vê o agendamento DELE no painel", "João Pereira" in html)
checar("Dr. Carlos NÃO vê o agendamento da Dra. Fernanda no painel", "Pedro Souza" not in html)
client.get("/logout")

# ---------- Médico fundador COM todas as permissões: agenda continua só a dele ----------

r = client.post("/cadastro", data={
    "modo": "empresa",
    "nome_empresa": "Clínica Fundador Agenda",
    "nome": "Dr. Fundador Agenda",
    "cpf": "852.963.741-00", "crm_numero": "66666", "crm_uf": "ES",
    "email": "fundador.agenda@example.com",
    "senha": "123456",
    "papel": "medico",
    "nome_filial": "Clínica Fundador Agenda - Sede",
    "telefone_filial": "(27) 90000-0006",
}, follow_redirects=True)
checar("Cadastro do médico fundador responde 200", r.status_code == 200)
with app.app_context():
    fundador = Usuario.query.filter_by(email="fundador.agenda@example.com").first()
    checar("Fundador médico tem todas as permissões (o cenário que antes vazava a agenda de todos)",
           fundador.perm_pacientes and fundador.perm_equipe)
html = client.get("/equipe/").get_data(as_text=True)
checar("Painel do médico fundador responde e não mostra agendamentos de outros médicos/empresas",
       "João Pereira" not in html and "Pedro Souza" not in html)
client.get("/logout")

print("\nTodos os testes do painel com agenda só do médico logado passaram.")
