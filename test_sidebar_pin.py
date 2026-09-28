"""Testa o menu lateral em gaveta (off-canvas): em qualquer tamanho de
tela (celular, tablet ou desktop) o menu fica escondido por padrão e abre
por cima do conteúdo ao clicar no hamburguer, fechando ao clicar de novo,
no fundo escurecido ou num link de navegação - substituiu o antigo menu
fixo com botão de "pino" (ver base.html). Como é comportamento client-side
(CSS/JS), o teste só confere que o HTML/JS necessários estão presentes e
que os elementos/JS do antigo modelo de pino não sobraram."""
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
checar("Tem o botão de abrir/fechar o menu na barra superior", 'id="sidebarToggleBtn"' in html)
checar("Tem o fundo escurecido da gaveta", 'class="sidebar-backdrop' in html)
checar("O CSS da gaveta (off-canvas, escondida por padrão) está presente",
       "transform: translateX(-100%)" in html)
checar("O CSS de abrir a gaveta está presente", "body.sidebar-open .app-sidebar" in html)
checar("O JS de abrir/fechar a gaveta está presente",
       "fecharMenuSidebar" in html and "abrirMenuSidebar" in html)
checar("Não sobrou nada do antigo botão de fixar/pino", 'id="sidebarPinBtn"' not in html)
checar("Não sobrou nada do antigo CSS/JS de esconder por pino",
       "sidebar-hidden" not in html and "sidebarPinado" not in html)

client.get("/logout")

# Paciente (não-staff) não tem menu lateral, então não deve ter o botão de
# abrir/fechar (o menu dele é o offcanvas próprio, ver menuPacienteOffcanvas).
r2 = client.get("/login")
html2 = r2.get_data(as_text=True)
checar("Tela de login (sem staff logado) não tem o botão da gaveta do menu lateral",
       'id="sidebarToggleBtn"' not in html2)

print("\nTodos os testes do menu lateral em gaveta passaram.")
