"""Testa que:
1) Restruturação de 2026-09 (pedido do Silvan): "Exames", "Exames &
   Preparo" e "Associar exames entre filiais" deixaram de ter QUALQUER
   link de menu (o cadastro de exame passou a acontecer todo dentro de
   "Modelos de preparo" - ver medico.preparo_modelos_novo). As rotas
   antigas (medico.exames_*) continuam existindo e respondendo por URL
   direta, mesmo padrão já usado para equipe_lista/filiais_lista - só não
   aparecem mais em nenhum menu;
2) o preço do exame não aparece mais como campo em NENHUM formulário de
   exame (nem no cadastro, nem na edição) - o cadastro é genérico e o preço
   só é definido/ajustado depois, por local, na tela "Exames por filial"."""
from app import create_app
from app.models import Usuario, Exame, PreparoModelo, Grupo

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
        grupo_id=Grupo.query.filter_by(nome="Clínica Vitória").first().id
    ).first().id

# A rota antiga de Exames continua respondendo por URL direta (não foi
# removida do código - só não tem mais link nenhum de menu, ver
# comentário no topo do arquivo).
r = client.get("/equipe/exames")
html = r.get_data(as_text=True)
checar("Tela de Exames (antiga, sem link de menu) ainda responde 200 por URL direta", r.status_code == 200)
checar(
    "Não tem mais o botão dentro do cabeçalho da tela de Exames",
    'btn-outline-secondary"><i class="bi bi-diagram-3"></i> Associar exames entre filiais' not in html,
)

# O link para "Associar exames entre filiais" não aparece em NENHUM lugar
# (nem no menu lateral, nem dentro da tela) - a tela deixou de ser
# alcançável pela navegação normal.
checar(
    "O link 'Associar exames entre filiais' não aparece mais em lugar nenhum",
    'href="/equipe/exames/por-filial"' not in html,
)

# O menu lateral (presente em toda página) agora tem um único item
# consolidado "Exames & preparo", apontando para a tela de modelos de
# preparo - não mais para a tela antiga de exames nem para "Associar
# exames entre filiais".
checar(
    "Menu lateral tem o item consolidado 'Exames & preparo' apontando para preparo-modelos",
    'href="/equipe/preparo-modelos"' in html and "Exames &amp; preparo" in html,
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
    "medico_id": str(medico_id),
}, follow_redirects=True)

# O formulário de EDITAR esse exame não tem o campo de preço editável.
r3 = client.get(f"/equipe/exames/{exame_id}/editar")
html3 = r3.get_data(as_text=True)
checar("Formulário de editar exame responde 200", r3.status_code == 200)
checar("Formulário de editar exame NÃO tem input de preço", 'name="preco"' not in html3)

client.get("/logout")
print("\nTodos os testes de menu e preço por filial passaram.")
