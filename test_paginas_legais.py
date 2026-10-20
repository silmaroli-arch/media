"""Testa as páginas legais do site (pedido do Silvan, 2026-10-20 - o Google
exige Política de Privacidade para publicar o "Continuar com Google"):
/privacidade e /termos abrem sem login, mostram controlador e e-mail de
contato, e têm link no rodapé do site e no cadastro."""
from app import create_app

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


r = client.get("/privacidade")
html = r.get_data(as_text=True)
checar("Política de Privacidade abre sem login", r.status_code == 200 and "Política de Privacidade" in html)
checar("Mostra o controlador e o e-mail de privacidade",
       "Silvan Martins de Oliveira" in html and "mailto:privacidade@media.med.br" in html)
checar("Cita a LGPD e os direitos do titular", "LGPD" in html and "Seus direitos" in html)

r = client.get("/termos")
html = r.get_data(as_text=True)
checar("Termos de Uso abrem sem login", r.status_code == 200 and "Termos de Uso" in html)
checar("Termos dizem que o MedIA não substitui o médico", "não substitui o médico" in html)

for url in ["/", "/login", "/cadastro", "/login-paciente"]:
    html = client.get(url).get_data(as_text=True)
    checar(f"Rodapé de {url} tem links para Privacidade e Termos", 'href="/privacidade"' in html and 'href="/termos"' in html)

html = client.get("/cadastro").get_data(as_text=True)
checar("Cadastro avisa que criar a conta é concordar com os termos", "você concorda com os" in html)

print("\nTodos os testes das páginas legais passaram.")
