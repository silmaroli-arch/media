"""Testa a reorganização do menu lateral: "Pessoas" deixou de existir -
Pacientes virou submenu de "Cadastro geral", que agora aparece logo abaixo
de "Grupos de trabalho" (antes de "Médico"). "Equipe" e "Meus locais de
atendimento" saíram do menu (eram redirect/duplicado do fluxo de "Grupos de
trabalho" - ver base.html) - o teste confirma que não aparecem mais como
link, mas que as rotas (e o botão "Cancelar" de Dados Cadastrais/Fiscais,
que ainda aponta pra URL antiga) continuam funcionando."""
from app import create_app
from app.models import Grupo

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


with app.app_context():
    clinica_id = Grupo.query.filter_by(nome="Clínica Vitória").first().id

login("secretaria@clinicavitoria.com", "123456")

r0 = client.get("/equipe/")
html0 = r0.get_data(as_text=True)
checar("Painel responde 200", r0.status_code == 200)
checar("Menu 'Pessoas' não existe mais", ">Pessoas<" not in html0)
checar("Menu 'Cadastro geral' contém 'Pacientes'", "Pacientes" in html0)
checar("Menu não contém mais 'Equipe' (virou redirect p/ Grupos de trabalho)", ">Equipe<" not in html0)
checar("Menu não contém mais 'Meus locais de atendimento'", "Meus locais de atendimento" not in html0)

# "Cadastro geral" aparece antes de "Médico" no HTML (ordem no menu lateral).
idx_cadastro = html0.find("Cadastro geral")
idx_medico = html0.find(">Médico<")
checar("'Cadastro geral' vem antes de 'Médico' na página", idx_cadastro != -1 and idx_medico != -1 and idx_cadastro < idx_medico)

# "Cadastro geral" vem logo depois de "Grupos de trabalho".
idx_grupos = html0.find("Grupos de trabalho")
checar(
    "'Cadastro geral' vem logo após 'Grupos de trabalho'",
    idx_grupos != -1 and idx_grupos < idx_cadastro < idx_medico,
)

# Dados Cadastrais e Dados Fiscais têm botão "Cancelar" voltando pra lista de locais.
r1 = client.get(f"/equipe/clinica/configuracoes/{clinica_id}")
html1 = r1.get_data(as_text=True)
checar(
    "Dados Cadastrais tem o botão Cancelar apontando pra Meus locais",
    'href="/equipe/filiais" class="btn btn-link">Cancelar</a>' in html1,
)

r2 = client.get(f"/equipe/clinica/dados-fiscais/{clinica_id}")
html2 = r2.get_data(as_text=True)
checar(
    "Dados Fiscais tem o botão Cancelar apontando pra Meus locais",
    'href="/equipe/filiais" class="btn btn-link">Cancelar</a>' in html2,
)

client.get("/logout")
print("\nTodos os testes do menu 'Cadastro geral' unificado passaram.")
