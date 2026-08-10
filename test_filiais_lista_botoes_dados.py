"""Testa que os menus "Dados Cadastrais" e "Dados Fiscais" deixaram de
existir no menu lateral - agora são acessados por botões na linha de cada
local, na tela "Meus locais de atendimento" (medico.filiais_lista). Também
garante que essa tela continua acessível para quem só tem perm_dados_clinica
(sem perm_filiais), já que agora ela é a porta de entrada para editar os
dados da filial."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Clinica

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


with app.app_context():
    clinica = Clinica.query.filter_by(nome="Clínica Vitória").first()
    clinica_id = clinica.id

login("secretaria@clinicavitoria.com", "123456")

# O menu lateral não tem mais "Dados Cadastrais" nem "Dados Fiscais" como
# itens próprios - só "Meus locais de atendimento".
r0 = client.get("/equipe/")
html0 = r0.get_data(as_text=True)
checar("Painel responde 200", r0.status_code == 200)
checar("Menu lateral não tem mais o item 'Dados Cadastrais'", ">Dados Cadastrais<" not in html0)
checar("Menu lateral não tem mais o item 'Dados Fiscais'", ">Dados Fiscais<" not in html0)
checar("Menu lateral continua com 'Meus locais de atendimento'", "Meus locais de atendimento" in html0)

# "Meus locais de atendimento" agora tem os botões "Dados Cadastrais" e
# "Dados Fiscais" na linha de cada local.
r1 = client.get("/equipe/filiais")
html1 = r1.get_data(as_text=True)
checar("Tela de locais responde 200", r1.status_code == 200)
checar("Tem o botão 'Dados Cadastrais' apontando pro local certo", f'/equipe/clinica/configuracoes/{clinica_id}' in html1)
checar("Tem o botão 'Dados Fiscais' apontando pro local certo", f'/equipe/clinica/dados-fiscais/{clinica_id}' in html1)

client.get("/logout")

# Uma pessoa com perm_dados_clinica mas SEM perm_filiais continua
# conseguindo acessar "Meus locais de atendimento" (é a porta de entrada
# pros dados da filial) e as telas de Dados Cadastrais/Fiscais.
with app.app_context():
    so_dados_clinica = Usuario(nome="Só Dados Clínica", email="sodadosclinica@clinicavitoria.com", tipo="secretaria")
    so_dados_clinica.set_senha("123456")
    so_dados_clinica.perm_pacientes = False
    so_dados_clinica.perm_equipe = False
    so_dados_clinica.perm_filiais = False
    so_dados_clinica.perm_dados_clinica = True
    db.session.add(so_dados_clinica)
    db.session.flush()
    from app.models import ClinicaMembro
    db.session.add(ClinicaMembro(clinica_id=clinica_id, usuario_id=so_dados_clinica.id, ativo=True))
    db.session.commit()

login("sodadosclinica@clinicavitoria.com", "123456")
r2 = client.get("/equipe/filiais")
checar("Pessoa só com perm_dados_clinica consegue acessar 'Meus locais de atendimento'", r2.status_code == 200)
html2 = r2.get_data(as_text=True)
checar("Não vê o botão 'Novo local' (não tem perm_filiais)", "Novo local" not in html2)
checar("Vê os botões de Dados Cadastrais/Fiscais (tem perm_dados_clinica)", "Dados Cadastrais" in html2 and "Dados Fiscais" in html2)

r3 = client.get(f"/equipe/clinica/configuracoes/{clinica_id}")
checar("Consegue abrir Dados Cadastrais", r3.status_code == 200)
r4 = client.get(f"/equipe/clinica/dados-fiscais/{clinica_id}")
checar("Consegue abrir Dados Fiscais", r4.status_code == 200)

client.get("/logout")
print("\nTodos os testes dos botões de Dados Cadastrais/Fiscais em Meus locais passaram.")
