"""Anúncio do dono como popup (pedido do Silvan, 2026-10-20): no envio do
anúncio, o dono marca se ele abre como popup ou fica só no sino.

- Marcado: abre numa janela na tela que a pessoa abrir, até ela clicar em
  "Entendi" (que marca como lido). Continua aparecendo no sino.
- Desmarcado: só no sino, sem popup.
- O "Entendi" só marca anúncios de popup do próprio usuário."""
from app import create_app
from app.models import Notificacao, Usuario

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


with app.app_context():
    medico_id = Usuario.query.filter_by(email="medico@clinicavitoria.com").first().id
    secretaria_id = Usuario.query.filter_by(email="secretaria@clinicavitoria.com").first().id

dono = app.test_client()
dono.post("/login", data={"identificador": "dono@plataforma.com", "senha": "123456"})
html = dono.get("/dono/anuncios").get_data(as_text=True)
checar("Tela de anúncios tem a opção 'Abrir como popup'", 'name="popup"' in html and "Abrir como popup" in html)

# ---------- Sem popup: só no sino ----------
r = dono.post("/dono/anuncios/enviar", data={
    "destinatario": str(medico_id), "titulo": "Aviso so no sino", "mensagem": "Sem janela.",
}, follow_redirects=True)
checar("Anúncio sem popup é enviado", "Anúncio enviado para 1 pessoa." in r.get_data(as_text=True))

medico = app.test_client()
medico.post("/login", data={"identificador": "medico@clinicavitoria.com", "senha": "123456"})
html = medico.get("/equipe/", follow_redirects=True).get_data(as_text=True)
checar("Sem popup: aparece no sino", "Aviso so no sino" in html)
checar("Sem popup: não abre janela", 'id="modalAnuncios"' not in html)

# ---------- Com popup ----------
r = dono.post("/dono/anuncios/enviar", data={
    "destinatario": str(medico_id), "titulo": "Manutencao programada", "mensagem": "Sistema fora do ar domingo.",
    "popup": "1",
}, follow_redirects=True)
checar("Anúncio com popup é enviado e o resumo avisa", "(abre como popup)" in r.get_data(as_text=True))
with app.app_context():
    n = Notificacao.query.filter_by(usuario_id=medico_id, titulo="Manutencao programada").first()
    checar("Notificação gravada com popup=True", n is not None and n.popup is True and n.lida is False)
    popup_id = n.id

html = medico.get("/equipe/", follow_redirects=True).get_data(as_text=True)
checar("Com popup: a janela abre ao entrar", 'id="modalAnuncios"' in html and "Sistema fora do ar domingo." in html)
checar("... só fecha pelo 'Entendi' (sem fechar clicando fora)", 'data-bs-backdrop="static"' in html and "Entendi" in html)
checar("... e também aparece no sino", html.count("Manutencao programada") >= 2)
html = medico.get("/equipe/", follow_redirects=True).get_data(as_text=True)
checar("Sem clicar em 'Entendi', abre de novo na próxima tela", 'id="modalAnuncios"' in html)

html_sec = app.test_client()
html_sec.post("/login", data={"identificador": "secretaria@clinicavitoria.com", "senha": "123456"})
checar("Quem não é destinatário não vê o popup", 'id="modalAnuncios"' not in html_sec.get("/equipe/", follow_redirects=True).get_data(as_text=True))

# Outro usuário tentando marcar o anúncio do médico como lido: não muda nada.
html_sec.post("/equipe/notificacoes/popup/entendi", data={"notificacao_id": str(popup_id)})
with app.app_context():
    checar("'Entendi' de outro usuário não marca o anúncio do médico", Notificacao.query.get(popup_id).lida is False)

medico.post("/equipe/notificacoes/popup/entendi", data={"notificacao_id": str(popup_id)})
with app.app_context():
    checar("'Entendi' marca o anúncio como lido", Notificacao.query.get(popup_id).lida is True)
html = medico.get("/equipe/", follow_redirects=True).get_data(as_text=True)
checar("Depois do 'Entendi', a janela não abre mais", 'id="modalAnuncios"' not in html)
checar("... e o anúncio continua no sino (como lido)", "Manutencao programada" in html)

# ---------- Vários de uma vez: uma janela só ----------
dono.post("/dono/anuncios/enviar", data={"destinatario": "todos", "titulo": "Aviso A", "mensagem": "Primeiro.", "popup": "1"})
dono.post("/dono/anuncios/enviar", data={"destinatario": "todos", "titulo": "Aviso B", "mensagem": "Segundo.", "popup": "1"})
html = medico.get("/equipe/", follow_redirects=True).get_data(as_text=True)
checar("Dois anúncios de popup aparecem juntos numa janela só",
       html.count('id="modalAnuncios"') == 1 and "Primeiro." in html and "Segundo." in html and "Avisos do MedIA" in html)
html = html_sec.get("/equipe/", follow_redirects=True).get_data(as_text=True)
checar("'Todo mundo' com popup também abre para a secretária", 'id="modalAnuncios"' in html and "Primeiro." in html)

print("\nTodos os testes de anúncio com popup passaram.")
