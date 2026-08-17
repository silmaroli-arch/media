"""Testa a reorganização do menu lateral: "Cadastro geral" deixou de existir
- como só tinha "Pacientes" dentro, virou um item direto no menu (sem
submenu), logo abaixo de "Grupos de trabalho". O menu "Médico" também
deixou de existir - só tinha "Meus exames agendados" e "Minha agenda em
todos os locais" dentro; "Meus exames agendados" virou item direto (logo
depois de "Pacientes") e "Minha agenda em todos os locais" saiu do menu.
O menu "Relatórios" deixou de existir. O menu "IA" passou a se chamar
"Médico + IA". "Equipe" e "Meus locais de atendimento" já tinham saído do
menu antes (eram redirect/duplicado do fluxo de "Grupos de trabalho" - ver
base.html) - o teste confirma que não aparecem mais como link, mas que as
rotas (e o botão "Cancelar" de Dados Cadastrais/Fiscais, que ainda aponta
pra URL antiga) continuam funcionando."""
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

# As checagens de conteúdo/ordem do menu ficam restritas ao <nav> lateral -
# a página em si (ex.: tabela de agendamentos) também mostra "Médico" como
# cabeçalho de coluna, o que não tem nada a ver com o menu.
inicio_menu = html0.find('<nav class="app-sidebar')
fim_menu = html0.find('</nav>', inicio_menu)
menu = html0[inicio_menu:fim_menu]
checar("Menu lateral encontrado na página", inicio_menu != -1 and fim_menu != -1)

checar("Menu 'Pessoas' não existe mais", ">Pessoas<" not in menu)
checar("Menu 'Cadastro geral' não existe mais (virou item direto)", ">Cadastro geral<" not in menu)
checar("Menu tem o item direto 'Pacientes'", "Pacientes" in menu)
checar("Menu não contém mais 'Equipe' (virou redirect p/ Grupos de trabalho)", ">Equipe<" not in menu)
checar("Menu não contém mais 'Meus locais de atendimento'", "Meus locais de atendimento" not in menu)
checar("Menu 'Médico' não existe mais (virou item direto)", ">Médico<" not in menu)
checar("Menu tem o item direto 'Meus exames agendados'", "Meus exames agendados" in menu)
checar("Menu não contém mais 'Minha agenda em todos os locais'", "Minha agenda em todos os locais" not in menu)
checar("Menu 'Relatórios' não existe mais", ">Relatórios<" not in menu)
checar("Menu 'IA' virou 'Médico + IA'", ">Médico + IA<" in menu and ">IA<" not in menu)

# Ordem no menu lateral: Grupos de trabalho > Pacientes > Meus exames
# agendados > Configuração de exames > Médico + IA.
idx_grupos = menu.find("Grupos de trabalho")
idx_pacientes = menu.find("Pacientes")
idx_exames_agendados = menu.find("Meus exames agendados")
idx_config_exames = menu.find("Configuração de exames")
idx_medico_ia = menu.find("Médico + IA")
checar(
    "Ordem do menu lateral é a esperada",
    idx_grupos != -1 and idx_pacientes != -1 and idx_exames_agendados != -1
    and idx_config_exames != -1 and idx_medico_ia != -1
    and idx_grupos < idx_pacientes < idx_exames_agendados < idx_config_exames < idx_medico_ia,
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

# As rotas de relatórios continuam funcionando mesmo sem link no menu.
r3 = client.get("/equipe/relatorios/")
checar("Rota de relatórios (Resumo) continua acessível por URL direta", r3.status_code == 200)

client.get("/logout")

# A rota "Minha agenda em todos os locais" continua funcionando mesmo sem
# link no menu (só médicos têm acesso). Usa a médica que atua só num Grupo
# (medico@clinicavitoria.com atua em dois e cairia na tela de escolher
# Grupo antes de conseguir abrir a agenda).
login("medica2@clinicavitoria.com", "123456")
r4 = client.get("/equipe/minha-agenda-completa")
checar("Rota 'Minha agenda em todos os locais' continua acessível por URL direta", r4.status_code == 200)

client.get("/logout")
print("\nTodos os testes da reorganização do menu lateral passaram.")
