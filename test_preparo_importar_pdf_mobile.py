"""Testa a correção de um bug relatado pelo Silvan (2026-09-12, com print
de "Method Not Allowed" no celular): salvar um modelo de preparo depois de
importar um PDF pela tela reduzida do celular (medico.preparo_modelos_
aviso_mobile) dava erro 405.

Causa (ver docstring completa em app.routes_medico.preparo_modelos_aviso_
mobile): o popup de importação (medico/_importar_preparo_pdf.html)
substitui a página inteira via `document.write(html)` depois de extrair o
PDF, sem nunca mudar a URL da barra de endereço - que continua sendo
"/preparo-modelos/aviso-mobile" (GET-only antes desta correção). O
`<form id="form-preparo">` da tela de revisão (medico/preparo_modelo_
form.html) não tem `action` de propósito (funciona tanto em
"/preparo-modelos/novo" quanto em "/preparo-modelos/<id>/editar", cada um
submetendo pra si mesmo) - então, ao clicar em "Salvar" depois de importar
pelo celular, o navegador manda o POST pra "/preparo-modelos/aviso-mobile"
mesmo, e essa rota recusava (405) por só aceitar GET.

Este teste não simula o popup/JS em si (não há navegador de verdade aqui)
- só reproduz o efeito observado: um POST direto em "/preparo-modelos/
aviso-mobile" com os campos do formulário de preparo, exatamente como o
navegador manda depois da troca de página via document.write."""
from app import create_app
from app.models import PreparoModelo

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


login("secretaria@gruposaude.com", "123456")

# A tela em si (GET) continua funcionando normalmente.
r0 = client.get("/equipe/preparo-modelos/aviso-mobile")
checar("GET na tela do celular responde 200", r0.status_code == 200)
checar("Botão de importar continua na tela", "Importar de um PDF" in r0.get_data(as_text=True))

# POST na MESMA URL (o que o navegador manda depois de importar um PDF
# pelo celular e clicar em Salvar, ver docstring do módulo) - antes desta
# correção, isto dava 405 Method Not Allowed.
r1 = client.post("/equipe/preparo-modelos/aviso-mobile", data={
    "nome": "Preparo importado pelo celular - teste",
    "instrucoes": "Jejum de 8 horas antes do exame.",
}, follow_redirects=True)
checar("POST na tela do celular NÃO dá 405 Method Not Allowed", r1.status_code != 405)
checar("POST na tela do celular responde 200 (depois do redirect)", r1.status_code == 200)
checar("Mensagem de sucesso aparece", "Modelo de preparo cadastrado" in r1.get_data(as_text=True))

with app.app_context():
    modelo = PreparoModelo.query.filter_by(nome="Preparo importado pelo celular - teste").first()
    checar("Modelo foi criado de verdade no banco", modelo is not None)

client.get("/logout")
print("\nTodos os testes de importação de preparo pelo celular passaram.")
