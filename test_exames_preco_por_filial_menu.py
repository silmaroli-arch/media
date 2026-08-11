"""Testa que:
1) "Associar exames entre filiais" virou um item de menu próprio (não um
   botão dentro da tela de Exames & Preparo);
2) o preço do exame não aparece mais como campo em NENHUM formulário de
   exame (nem no cadastro, nem na edição) - o cadastro é genérico e o preço
   só é definido/ajustado depois, por local, na tela "Exames por filial"."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Exame, PreparoModelo, Clinica

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


login("secretaria@clinicavitoria.com", "123456")

with app.app_context():
    medico_id = Usuario.query.filter_by(email="medico@clinicavitoria.com").first().id
    modelo_id = PreparoModelo.query.filter_by(
        clinica_id=Clinica.query.filter_by(nome="Clínica Vitória").first().id
    ).first().id

# A tela de Exames & Preparo não tem mais o botão "Associar exames entre filiais".
r = client.get("/equipe/exames")
html = r.get_data(as_text=True)
checar("Tela de Exames responde 200", r.status_code == 200)
checar(
    "Não tem mais o botão dentro do cabeçalho da tela de Exames",
    'btn-outline-secondary"><i class="bi bi-diagram-3"></i> Associar exames entre filiais' not in html,
)

# O link para a tela só aparece uma vez: no menu lateral (presente em toda página), não mais
# como um segundo botão dentro do conteúdo da tela de Exames.
checar(
    "O link 'Associar exames entre filiais' aparece só uma vez (no menu lateral)",
    html.count('href="/equipe/exames/por-filial"') == 1,
)

# O formulário de "Novo exame" também não pede mais preço - o cadastro é genérico.
r2 = client.get("/equipe/exames/novo")
html2 = r2.get_data(as_text=True)
checar("Formulário de novo exame NÃO tem o campo de preço", 'name="preco"' not in html2)

# Cadastra o exame (genérico) e define o preço depois, via "Exames por filial".
client.post("/equipe/exames/novo", data={
    "nome": "Eletrocardiograma", "descricao": "ECG", "duracao_minutos": "20",
    "preparo_modelo_id": str(modelo_id),
}, follow_redirects=True)
with app.app_context():
    exame_id = Exame.query.filter_by(nome="Eletrocardiograma").first().id
client.post(f"/equipe/exames/por-filial/{exame_id}/atualizar", data={
    "medico_id": str(medico_id), "preco": "120,00",
}, follow_redirects=True)

# O formulário de EDITAR esse exame não tem o campo de preço editável.
r3 = client.get(f"/equipe/exames/{exame_id}/editar")
html3 = r3.get_data(as_text=True)
checar("Formulário de editar exame responde 200", r3.status_code == 200)
checar("Formulário de editar exame NÃO tem input de preço", 'name="preco"' not in html3)
checar("Formulário de editar exame mostra o preço atual como texto (não editável)", "120,00" in html3)
checar("Formulário de editar exame indica onde alterar o preço", "Associar exames entre filiais" in html3)

client.get("/logout")
print("\nTodos os testes de menu e preço por filial passaram.")
