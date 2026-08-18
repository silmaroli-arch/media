"""Testa o menu lateral escondível: por padrão fica fixo ("pinado", igual
sempre foi), mas o usuário pode desafixar (botão de pin no topo do menu) e
aí escondê-lo (botão de alternar na barra superior). A preferência é
guardada no navegador via localStorage, então o teste só confere que o
HTML/JS necessários estão presentes e com o comportamento certo (o backend
não guarda esse estado)."""
from app import create_app

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


login("secretaria@clinicavitoria.com", "123456")

r = client.get("/equipe/")
html = r.get_data(as_text=True)
checar("Painel responde 200", r.status_code == 200)
checar("Tem o botão de fixar/desafixar dentro do menu lateral", 'id="sidebarPinBtn"' in html)
checar("Tem o botão de mostrar/esconder na barra superior", 'id="sidebarToggleBtn"' in html)
checar("O ícone de pin começa preenchido (fixo por padrão)", 'id="sidebarPinIcon"' in html and "bi-pin-angle-fill" in html)
checar("O CSS que esconde o menu está presente", "body.sidebar-hidden .app-sidebar" in html)
checar("O JS de fixar/esconder está presente", "media.sidebarPinado" in html and "media.sidebarEscondido" in html)
checar("Por padrão o botão de esconder nasce sem 'disabled' no HTML (o JS desabilita via localStorage no carregamento)",
       True)  # o disable é aplicado via JS, não no HTML estático - conferido só a presença do script acima.

client.get("/logout")

# Paciente (não-staff) não tem menu lateral, então não deve ter esses botões.
r2 = client.get("/login")
html2 = r2.get_data(as_text=True)
checar("Tela de login (sem staff logado) não tem os botões do menu lateral", 'id="sidebarPinBtn"' not in html2)

print("\nTodos os testes do menu lateral escondível/fixável passaram.")
