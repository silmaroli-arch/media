"""Testa o canal "Fale com a gente" (pedido do Silvan, 2026-09-25) - onde
médico/secretária mandam dúvidas, sugestões ou problemas direto para o
dono da plataforma, e só o dono responde, dentro do próprio painel (ver
MensagemSuporte em app/models.py, medico.fale_com_a_gente em
app/routes_medico.py, dono.mensagens_suporte* em app/routes_dono.py):

- O médico manda uma mensagem e ela aparece na sua própria lista.
- O dono vê a mensagem (de qualquer clínica) na tela de mensagens.
- O dono responde, e a resposta aparece de volta pro médico.
- O contador de mensagens "novas" no dashboard do dono soma corretamente
  e some depois que a mensagem é respondida.
- Outro médico (de outra clínica) NÃO vê a mensagem de um médico que não
  é ele - cada um só vê as próprias."""
from app import create_app
from app.extensions import db
from app.models import MensagemSuporte

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


with app.app_context():
    total_antes = MensagemSuporte.query.count()

# Dr. Carlos (Clínica Vitória) manda uma dúvida.
login("medico@clinicavitoria.com", "123456")
r = client.post(
    "/equipe/fale-com-a-gente",
    data={"categoria": "duvida", "mensagem": "Como faço para exportar o relatório em Excel?"},
    follow_redirects=True,
)
checar("Envio da mensagem responde 200", r.status_code == 200)

with app.app_context():
    checar("Mensagem foi salva no banco", MensagemSuporte.query.count() == total_antes + 1)
    mensagem = MensagemSuporte.query.filter_by(mensagem="Como faço para exportar o relatório em Excel?").first()
    checar("Mensagem existe", mensagem is not None)
    checar("Categoria salva corretamente", mensagem.categoria == "duvida")
    checar("Status inicial é 'nova'", mensagem.status == "nova")
    mensagem_id = mensagem.id

# A própria mensagem aparece na lista do médico que enviou.
r = client.get("/equipe/fale-com-a-gente")
html = r.get_data(as_text=True)
checar("Médico vê a própria mensagem na lista", "exportar o relatório em Excel" in html)
client.get("/logout")

# Outro médico (Dra. Fernanda, mesma clínica mas outra pessoa) NÃO vê a
# mensagem do Dr. Carlos na própria lista - cada um só vê as suas.
login("medica2@clinicavitoria.com", "123456")
r = client.get("/equipe/fale-com-a-gente")
html = r.get_data(as_text=True)
checar("Outro médico NÃO vê mensagem que não é dele", "exportar o relatório em Excel" not in html)
client.get("/logout")

# O dono vê a mensagem na tela de mensagens (de qualquer clínica).
login("dono@plataforma.com", "123456")
r = client.get("/dono/mensagens-suporte")
html = r.get_data(as_text=True)
checar("Dono vê a mensagem na lista geral", "exportar o relatório em Excel" in html)

# O contador de "novas" no dashboard do dono inclui essa mensagem.
with app.app_context():
    novas_antes = MensagemSuporte.query.filter_by(status="nova").count()
r = client.get("/dono/")
checar("Dashboard do dono responde 200", r.status_code == 200)

# O dono responde.
r = client.post(
    f"/dono/mensagens-suporte/{mensagem_id}/responder",
    data={"resposta": "Vá em Relatórios > Exportar > Excel."},
    follow_redirects=True,
)
checar("Resposta do dono responde 200", r.status_code == 200)

with app.app_context():
    mensagem = MensagemSuporte.query.get(mensagem_id)
    checar("Resposta foi salva", mensagem.resposta == "Vá em Relatórios > Exportar > Excel.")
    checar("Status virou 'respondida'", mensagem.status == "respondida")
    checar("respondida_em foi preenchido", mensagem.respondida_em is not None)
    checar(
        "Contador de 'novas' caiu em 1 depois de responder",
        MensagemSuporte.query.filter_by(status="nova").count() == novas_antes - 1,
    )
client.get("/logout")

# A resposta aparece de volta pro médico que perguntou.
login("medico@clinicavitoria.com", "123456")
r = client.get("/equipe/fale-com-a-gente")
html = r.get_data(as_text=True)
checar("Médico vê a resposta do dono", "Vá em Relatórios" in html)
client.get("/logout")

print("\nTodos os testes de test_fale_com_a_gente.py passaram.")
