"""Testa que o assistente de configuração inicial (medico.onboarding) agora
tem uma etapa (opcional) para adicionar mais locais de atendimento (filiais)
- antes o "Dados Cadastrais" só permitia preencher os dados da filial atual,
sem nenhum jeito, dentro do assistente, de cadastrar uma segunda filial."""
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

# A tela de onboarding mostra a nova etapa opcional de adicionar filiais.
r = client.get("/equipe/configuracao-inicial")
html = r.get_data(as_text=True)
checar("Onboarding responde 200", r.status_code == 200)
checar("Mostra a etapa 'Adicionar mais locais de atendimento'", "Adicionar mais locais de atendimento" in html)
checar("A etapa está marcada como opcional", "opcional" in html)

with app.app_context():
    total_filiais_antes = Clinica.query.filter_by(empresa_id=empresa_id).count()
    checar("Empresa começa com 1 única filial (Clínica Vitória)", total_filiais_antes == 1)

# Preenche a etapa vindo do próprio assistente (via ?voltar_onboarding=1).
r2 = client.get("/equipe/filiais/nova?voltar_onboarding=1")
html2 = r2.get_data(as_text=True)
checar("Formulário de nova filial carrega o campo oculto voltar_onboarding", 'name="voltar_onboarding" value="1"' in html2)

r3 = client.post("/equipe/filiais/nova", data={
    "nome": "Clínica Vitória - Praia do Canto", "voltar_onboarding": "1",
}, follow_redirects=True)
checar("Cadastro da nova filial responde 200", r3.status_code == 200)

with app.app_context():
    checar(
        "Agora a empresa tem 2 filiais",
        Clinica.query.filter_by(empresa_id=empresa_id).count() == 2,
    )

# Como veio do assistente (voltar_onboarding=1), volta para a tela do assistente, não para
# a lista de filiais.
checar("Depois de salvar, volta para a tela do assistente (onboarding)", "/equipe/configuracao-inicial" in r3.request.path)

# A etapa agora aparece como concluída no assistente.
r4 = client.get("/equipe/configuracao-inicial")
html4 = r4.get_data(as_text=True)
idx = html4.find("Adicionar mais locais de atendimento")
trecho = html4[max(0, idx - 400):idx]
checar("A etapa de filiais aparece marcada como concluída (ícone de check antes do título)", "bi-check-circle-fill" in trecho)

# A etapa de filiais é OPCIONAL - não deve aparecer no aviso "Faltam X etapas" do Painel,
# mesmo estando pendente (não preenchida).
with app.app_context():
    # Remove a filial extra pra simular o caso "ainda não preencheu essa etapa opcional".
    extra = Clinica.query.filter_by(empresa_id=empresa_id, nome="Clínica Vitória - Praia do Canto").first()
    db.session.delete(extra)
    db.session.commit()

r5 = client.get("/equipe/")
html5 = r5.get_data(as_text=True)
if "configuração inicial da clínica ainda não está completa" in html5.lower():
    checar(
        "Etapa opcional de filiais NÃO aparece no aviso 'Faltam X etapas' do Painel",
        "Adicionar mais locais de atendimento" not in html5,
    )

client.get("/logout")
print("\nTodos os testes de múltiplas filiais no onboarding passaram.")
