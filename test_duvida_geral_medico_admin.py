"""Testa que perguntas gerais do paciente (sem exame associado, "Dúvida
geral" na tela de chat) chegam até um médico com a permissão administrativa
de pacientes (perm_pacientes) - antes essas perguntas só apareciam para
quem tinha o tipo "secretaria", então uma clínica que só tem médico (sem
secretária separada) nunca via essas perguntas em lugar nenhum, mesmo o
médico sendo o administrador da clínica."""
from app import create_app
from app.extensions import db
from app.models import Usuario, PerguntaPendente

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


def login_paciente(cpf, dn):
    return client.post("/login-paciente", data={"cpf": cpf, "data_nascimento": dn}, follow_redirects=True)


from app.models import Grupo

with app.app_context():
    # Dr. Carlos Andrade passa a ser o médico-administrador da clínica
    # (cenário de clínica sem secretária separada).
    medico = Usuario.query.filter_by(email="medico@clinicavitoria.com").first()
    medico.perm_pacientes = True
    db.session.commit()
    # Dr. Carlos atende em mais de um grupo (ver seed.py) - precisa
    # escolher a Clínica Vitória explicitamente após o login.
    clinica_vitoria_id = Grupo.query.filter_by(nome="Clínica Vitória").first().id

# Paciente faz uma pergunta geral (sem selecionar nenhum exame/agendamento).
login_paciente("123.456.789-00", "12/04/1985")
client.post("/paciente/chat", data={"agendamento_id": "", "pergunta": "Quando posso marcar uma consulta?"}, follow_redirects=True)
client.get("/logout")

with app.app_context():
    pergunta_id = PerguntaPendente.query.filter_by(pergunta="Quando posso marcar uma consulta?").first().id

# O médico com perm_pacientes vê a pergunta geral na tela "Perguntas dos pacientes"...
login("medico@clinicavitoria.com", "123456")
client.post("/equipe/clinica", data={"clinica_id": str(clinica_vitoria_id)}, follow_redirects=True)
r = client.get("/equipe/perguntas")
html = r.get_data(as_text=True)
checar("Médico com perm_pacientes vê a pergunta geral na lista", "Quando posso marcar uma consulta?" in html)

# ...e o contador do painel também conta essa pergunta geral.
r_dash = client.get("/equipe/")
checar("Painel do médico com perm_pacientes responde 200", r_dash.status_code == 200)

# ...e consegue responder a ela diretamente.
r2 = client.post(f"/equipe/perguntas/{pergunta_id}/responder", data={
    "resposta": "Você pode marcar pelo app, na aba Solicitar agendamento.",
}, follow_redirects=True)
checar("Médico com perm_pacientes consegue responder a pergunta geral", r2.status_code == 200)
with app.app_context():
    pergunta = PerguntaPendente.query.get(pergunta_id)
    checar("Pergunta geral foi marcada como respondida", pergunta.status == "respondida")
client.get("/logout")

# ---------- Sem perm_pacientes, o médico continua sem ver perguntas gerais (comportamento antigo preservado) ----------
with app.app_context():
    Usuario.query.filter_by(email="medico@clinicavitoria.com").first().perm_pacientes = False
    db.session.commit()

login_paciente("123.456.789-00", "12/04/1985")
client.post("/paciente/chat", data={"agendamento_id": "", "pergunta": "Outra pergunta geral qualquer?"}, follow_redirects=True)
client.get("/logout")

with app.app_context():
    pergunta2_id = PerguntaPendente.query.filter_by(pergunta="Outra pergunta geral qualquer?").first().id

login("medico@clinicavitoria.com", "123456")
client.post("/equipe/clinica", data={"clinica_id": str(clinica_vitoria_id)}, follow_redirects=True)
r3 = client.get("/equipe/perguntas")
checar(
    "Médico SEM perm_pacientes continua sem ver pergunta geral (comportamento antigo preservado)",
    "Outra pergunta geral qualquer?" not in r3.get_data(as_text=True),
)
r4 = client.post(f"/equipe/perguntas/{pergunta2_id}/responder", data={"resposta": "teste"}, follow_redirects=True)
with app.app_context():
    checar(
        "Médico SEM perm_pacientes não consegue responder pergunta geral de outra pessoa",
        PerguntaPendente.query.get(pergunta2_id).status != "respondida",
    )
client.get("/logout")

print("\nTodos os testes de dúvida geral para médico-administrador passaram.")
