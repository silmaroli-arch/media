"""Testa o sininho de notificações de médico/secretária (pedido do
Silvan, 2026-09-25 - ver Notificacao em app/models.py):

- Quando o dono responde uma mensagem do "Fale com a gente", nasce uma
  notificação pro médico que perguntou (ver
  dono.mensagens_suporte_responder em app/routes_dono.py).
- O dono consegue mandar um "anúncio" livre pra um médico específico ou
  pra todo mundo (ver dono.anuncios/dono.anuncio_enviar).
- O contador de não-lidas aparece certo (via o context processor
  injetar_notificacoes), some quando lida, e "marcar todas como lidas"
  zera tudo de uma vez.
- Abrir uma notificação marca ela como lida e redireciona pro destino
  certo (ou pro painel, quando não há destino)."""
from app import create_app
from app.extensions import db
from app.models import MensagemSuporte, Notificacao, Usuario

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


with app.app_context():
    total_notif_antes = Notificacao.query.count()

# --- Resposta do "Fale com a gente" gera notificação ---
login("medico@clinicavitoria.com", "123456")
client.post(
    "/equipe/fale-com-a-gente",
    data={"categoria": "duvida", "mensagem": "Onde vejo o histórico de pagamentos?"},
    follow_redirects=True,
)
client.get("/logout")

with app.app_context():
    mensagem = MensagemSuporte.query.filter_by(mensagem="Onde vejo o histórico de pagamentos?").first()
    mensagem_id = mensagem.id
    medico_id = mensagem.usuario_id

login("dono@plataforma.com", "123456")
client.post(
    f"/dono/mensagens-suporte/{mensagem_id}/responder",
    data={"resposta": "Na aba Licenças, dentro do seu usuário."},
    follow_redirects=True,
)
client.get("/logout")

with app.app_context():
    checar(
        "Nasceu uma notificação de resposta pro médico que perguntou",
        Notificacao.query.count() == total_notif_antes + 1,
    )
    notif_resposta = Notificacao.query.filter_by(usuario_id=medico_id, tipo="resposta_suporte").first()
    checar("Notificação de resposta existe e não está lida", notif_resposta is not None and notif_resposta.lida is False)
    checar("Mensagem da notificação é a resposta do dono", notif_resposta.mensagem == "Na aba Licenças, dentro do seu usuário.")
    notif_resposta_id = notif_resposta.id

# O sininho (via context processor) mostra a contagem de não lidas.
login("medico@clinicavitoria.com", "123456")
r = client.get("/equipe/")
html = r.get_data(as_text=True)
checar("Painel do médico responde 200 com a notificação pendente", r.status_code == 200)
checar("Sininho mostra o badge de não lida", 'bg-danger rounded-pill' in html)
checar("Título da notificação aparece no dropdown", "Resposta do" in html)

# Abrir a notificação marca como lida e redireciona pro link_endpoint.
r = client.get(f"/equipe/notificacoes/{notif_resposta_id}/abrir", follow_redirects=False)
checar("Abrir a notificação redireciona (302)", r.status_code == 302)
checar("Redireciona pra tela de 'Fale com a gente'", "/fale-com-a-gente" in r.headers["Location"])
with app.app_context():
    checar("Notificação foi marcada como lida", Notificacao.query.get(notif_resposta_id).lida is True)
client.get("/logout")

# --- Anúncio livre do dono ---
login("dono@plataforma.com", "123456")
with app.app_context():
    fernanda = Usuario.query.filter_by(email="medica2@clinicavitoria.com").first()
    fernanda_id = fernanda.id

r = client.post(
    "/dono/anuncios/enviar",
    data={"destinatario": str(fernanda_id), "titulo": "Nova função disponível", "mensagem": "Agora dá pra exportar em Excel."},
    follow_redirects=True,
)
checar("Envio de anúncio individual responde 200", r.status_code == 200)

with app.app_context():
    notif_anuncio = Notificacao.query.filter_by(usuario_id=fernanda_id, tipo="anuncio", titulo="Nova função disponível").first()
    checar("Anúncio individual chegou só pra Fernanda", notif_anuncio is not None)
    checar("Anúncio individual NÃO foi enviado pro Dr. Carlos", Notificacao.query.filter_by(usuario_id=medico_id, titulo="Nova função disponível").first() is None)

# Anúncio pra "todos".
with app.app_context():
    total_equipe = Usuario.query.filter(Usuario.tipo.in_(["medico", "secretaria"])).count()

r = client.post(
    "/dono/anuncios/enviar",
    data={"destinatario": "todos", "titulo": "Manutenção programada", "mensagem": "Sistema fora do ar às 23h."},
    follow_redirects=True,
)
checar("Envio de anúncio pra todos responde 200", r.status_code == 200)
with app.app_context():
    qtd_anuncio_todos = Notificacao.query.filter_by(titulo="Manutenção programada").count()
    checar("Anúncio 'todos' chegou pra toda a equipe (médicos + secretárias)", qtd_anuncio_todos == total_equipe)
client.get("/logout")

# --- "Marcar todas como lidas" ---
login("medica2@clinicavitoria.com", "123456")
with app.app_context():
    checar("Fernanda tem pelo menos 1 notificação não lida antes de marcar todas", Notificacao.query.filter_by(usuario_id=fernanda_id, lida=False).count() >= 1)

r = client.post("/equipe/notificacoes/marcar-todas-lidas", follow_redirects=True)
checar("Marcar todas como lidas responde 200", r.status_code == 200)
with app.app_context():
    checar("Nenhuma notificação de Fernanda ficou não lida", Notificacao.query.filter_by(usuario_id=fernanda_id, lida=False).count() == 0)
client.get("/logout")

print("\nTodos os testes de test_notificacoes.py passaram.")
