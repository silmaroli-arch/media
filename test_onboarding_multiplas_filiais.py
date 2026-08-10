"""Testa a etapa "Meus Locais de Atendimento" do assistente de configuração
inicial (medico.onboarding) - fusão das antigas etapas "Dados Cadastrais" e
"Adicionar mais locais de atendimento" num item só, que leva direto para a
tela principal "Meus locais de atendimento" (medico.filiais_lista), de onde
dá pra editar os dados da filial atual e também cadastrar filiais extras.
Também testa que a etapa "Convidar mais gente para a equipe" agora leva
para a tela principal de Equipe (medico.equipe_lista), não mais direto para
o formulário de "Adicionar médico/secretária"."""
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
    secretaria = Usuario.query.filter_by(email="secretaria@clinicavitoria.com").first()
    secretaria.perm_filiais = True
    db.session.commit()
    clinica_vitoria = Clinica.query.filter_by(nome="Clínica Vitória").first()
    empresa_id = clinica_vitoria.empresa_id

login("secretaria@clinicavitoria.com", "123456")

# A tela de onboarding mostra a etapa única "Meus Locais de Atendimento" -
# não tem mais "Dados Cadastrais" nem "Adicionar mais locais de
# atendimento" como itens separados.
r = client.get("/equipe/configuracao-inicial")
html = r.get_data(as_text=True)
checar("Onboarding responde 200", r.status_code == 200)
checar("Mostra a etapa única 'Meus Locais de Atendimento'", "Meus Locais de Atendimento" in html)
checar("Não tem mais o item separado 'Dados Cadastrais'", "Dados Cadastrais" not in html)
checar("Não tem mais o item separado 'Adicionar mais locais de atendimento'", "Adicionar mais locais de atendimento" not in html)

# Como a clínica do seed já tem telefone e e-mail de contato preenchidos, a
# etapa já aparece concluída, e o botão é "Revisar" (não "Preencher agora").
idx = html.find("Meus Locais de Atendimento")
trecho = html[max(0, idx - 400):idx + 400]
checar("A etapa aparece concluída (telefone/e-mail já preenchidos no seed)", "bi-check-circle-fill" in trecho)
checar("O botão da etapa aponta para a tela principal de locais", "/equipe/filiais" in trecho)

# A etapa "Convidar mais gente para a equipe" agora leva para a lista
# principal de Equipe, não mais direto pro formulário de cadastro.
idx_equipe = html.find("Convidar mais gente para a equipe")
trecho_equipe = html[max(0, idx_equipe - 200):idx_equipe + 400]
checar("Etapa de equipe aponta para a tela principal de Equipe", "/equipe/equipe-membros\"" in trecho_equipe)
checar("Etapa de equipe NÃO aponta direto para o formulário de cadastro", "/equipe/equipe-membros/novo" not in trecho_equipe)

# Clicando em "Meus Locais de Atendimento" (vindo do assistente), cai na
# tela principal de locais, onde ainda é possível cadastrar uma filial
# extra normalmente.
r2 = client.get("/equipe/filiais?voltar_onboarding=1")
checar("Tela principal de locais responde 200", r2.status_code == 200)

with app.app_context():
    total_filiais_antes = Clinica.query.filter_by(empresa_id=empresa_id).count()
    checar("Empresa começa com 1 única filial (Clínica Vitória)", total_filiais_antes == 1)

r3 = client.post("/equipe/filiais/nova", data={
    "nome": "Clínica Vitória - Praia do Canto",
}, follow_redirects=True)
checar("Cadastro da nova filial responde 200", r3.status_code == 200)

with app.app_context():
    checar(
        "Agora a empresa tem 2 filiais",
        Clinica.query.filter_by(empresa_id=empresa_id).count() == 2,
    )

client.get("/logout")
print("\nTodos os testes da etapa unificada de locais/equipe no onboarding passaram.")
