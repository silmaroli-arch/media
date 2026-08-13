"""Bug relatado: um médico com perm_pacientes (ex.: o fundador de uma
clínica que também tem outros médicos na equipe) via, na tela "Perguntas
dos pacientes" e no contador do Painel, perguntas sobre exames de OUTROS
médicos da mesma clínica — não só as suas e as gerais (sem exame). O
esperado é que perm_pacientes só dê acesso às perguntas GERAIS (sem exame),
igual já valia antes; perguntas de exame continuam restritas a quem é
responsável por aquele exame (ver Exame.medico_pode_atender). Usa a
Clínica Vitória do seed (Dr. Carlos Andrade e Dra. Fernanda Lima)."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Clinica, Paciente, Exame, PerguntaPendente

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


with app.app_context():
    clinica_vitoria = Clinica.query.filter_by(nome="Clínica Vitória").first()
    carlos = Usuario.query.filter_by(email="medico@clinicavitoria.com").first()
    fernanda = Usuario.query.filter_by(email="medica2@clinicavitoria.com").first()
    paciente = Paciente.query.filter_by(cpf="123.456.789-00").first()
    clinica_id, carlos_id, fernanda_id, paciente_id = clinica_vitoria.id, carlos.id, fernanda.id, paciente.id

    # Carlos é o "administrador" da clínica (perm_pacientes=True), mesmo
    # com a Fernanda também atendendo lá - cenário do relato.
    carlos.perm_pacientes = True

    exame_carlos = Exame(
        clinica_id=clinica_id, medico_id=carlos_id, nome="Exame do Carlos Teste",
        preco=100, associado=True, medico_confirmado=True,
    )
    exame_fernanda = Exame(
        clinica_id=clinica_id, medico_id=fernanda_id, nome="Exame da Fernanda Teste",
        preco=100, associado=True, medico_confirmado=True,
    )
    db.session.add_all([exame_carlos, exame_fernanda])
    db.session.commit()

    pergunta_do_carlos = PerguntaPendente(
        clinica_id=clinica_id, paciente_id=paciente_id, exame_id=exame_carlos.id,
        pergunta="Posso comer antes do exame do Carlos?", status="pendente",
    )
    pergunta_da_fernanda = PerguntaPendente(
        clinica_id=clinica_id, paciente_id=paciente_id, exame_id=exame_fernanda.id,
        pergunta="Posso comer antes do exame da Fernanda?", status="pendente",
    )
    pergunta_geral = PerguntaPendente(
        clinica_id=clinica_id, paciente_id=paciente_id, exame_id=None,
        pergunta="Qual o horário de funcionamento da clínica?", status="pendente",
    )
    db.session.add_all([pergunta_do_carlos, pergunta_da_fernanda, pergunta_geral])
    db.session.commit()
    id_pergunta_fernanda = pergunta_da_fernanda.id

login("medico@clinicavitoria.com", "123456")
client.post("/equipe/clinica", data={"clinica_id": str(clinica_id)}, follow_redirects=True)

r = client.get("/equipe/perguntas")
html = r.get_data(as_text=True)
checar("Carlos (com perm_pacientes) vê a pergunta do próprio exame", "Posso comer antes do exame do Carlos?" in html)
checar("Carlos vê a pergunta GERAL (sem exame), por ter perm_pacientes", "Qual o horário de funcionamento da clínica?" in html)
checar(
    "Carlos NÃO vê a pergunta do exame da Fernanda, mesmo tendo perm_pacientes",
    "Posso comer antes do exame da Fernanda?" not in html,
)

r_dash = client.get("/equipe/")
checar("Painel do Carlos responde 200", r_dash.status_code == 200)

# Tentar responder diretamente (contornando a tela) a pergunta que é do
# exame da Fernanda também é bloqueado no servidor.
r2 = client.post(f"/equipe/perguntas/{id_pergunta_fernanda}/responder", data={
    "resposta": "Não pode.",
}, follow_redirects=True)
checar(
    "Servidor bloqueia Carlos tentando responder pergunta do exame da Fernanda",
    "só pode responder perguntas sobre os seus próprios exames" in r2.get_data(as_text=True),
)
with app.app_context():
    checar(
        "Pergunta da Fernanda continua sem resposta",
        PerguntaPendente.query.get(id_pergunta_fernanda).status != "respondida",
    )
client.get("/logout")

login("medica2@clinicavitoria.com", "123456")
client.post("/equipe/clinica", data={"clinica_id": str(clinica_id)}, follow_redirects=True)
r3 = client.get("/equipe/perguntas")
html3 = r3.get_data(as_text=True)
checar("Fernanda vê a pergunta do próprio exame", "Posso comer antes do exame da Fernanda?" in html3)
checar("Fernanda NÃO vê a pergunta do exame do Carlos", "Posso comer antes do exame do Carlos?" not in html3)
checar(
    "Fernanda (sem perm_pacientes) NÃO vê a pergunta geral",
    "Qual o horário de funcionamento da clínica?" not in html3,
)
client.get("/logout")

# Secretária continua vendo tudo, como antes.
login("secretaria@clinicavitoria.com", "123456")
r4 = client.get("/equipe/perguntas")
html4 = r4.get_data(as_text=True)
checar(
    "Secretária continua vendo as perguntas de todos os médicos e as gerais",
    "Posso comer antes do exame do Carlos?" in html4
    and "Posso comer antes do exame da Fernanda?" in html4
    and "Qual o horário de funcionamento da clínica?" in html4,
)
client.get("/logout")

print("\nTodos os testes de 'médico só vê perguntas dos próprios exames' passaram.")
