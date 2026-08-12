"""Testa o ajuste: ao SALVAR os Dados Cadastrais (ou Dados Fiscais) de um
local, a tela fecha - a pessoa volta para "Meus locais de atendimento"
(de onde abriu a tela), com a mensagem de sucesso dizendo de qual local
era. Antes, salvar continuava na própria tela do formulário.

O botão "Cancelar" já voltava pra lista; agora salvar também volta. Quem
chega pela etapa do assistente de configuração inicial continua voltando
pro assistente (comportamento do voltar_onboarding preservado)."""
from app import create_app
from app.extensions import db
from app.models import Clinica

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


client.post("/login", data={"email": "secretaria@gruposaude.com", "senha": "123456"}, follow_redirects=True)

with app.app_context():
    centro = Clinica.query.filter_by(nome="Grupo Saúde Total - Centro").first()
    centro_id = centro.id

# ---------- Salvar Dados Cadastrais fecha a tela ----------

r = client.post(f"/equipe/clinica/configuracoes/{centro_id}", data={
    "nome": "Grupo Saúde Total - Centro",
    "razao_social": "Grupo Saude Total LTDA",
    "cnpj": "11.222.333/0001-44",
    "telefone": "(27) 3333-0000",
    "email_contato": "centro@gruposaude.com",
    "cep": "29055-360", "rua": "Rua Teste", "numero": "10",
    "complemento": "", "bairro": "Centro", "cidade": "Vitória", "uf": "ES",
}, follow_redirects=False)
checar("Salvar redireciona (não fica na mesma tela)", r.status_code in (301, 302))
checar("O destino é 'Meus locais de atendimento'", r.headers["Location"].endswith("/equipe/filiais"))

r2 = client.post(f"/equipe/clinica/configuracoes/{centro_id}", data={
    "nome": "Grupo Saúde Total - Centro",
    "razao_social": "Grupo Saude Total LTDA",
    "cnpj": "11.222.333/0001-44",
    "telefone": "(27) 3333-0000",
    "email_contato": "centro@gruposaude.com",
    "cep": "29055-360", "rua": "Rua Teste", "numero": "10",
    "complemento": "", "bairro": "Centro", "cidade": "Vitória", "uf": "ES",
}, follow_redirects=True)
html = r2.get_data(as_text=True)
checar("A lista de locais abre com a mensagem de sucesso", "Dados Cadastrais de" in html and "atualizados com sucesso" in html)
checar("A mensagem diz de QUAL local eram os dados", "Grupo Saúde Total - Centro" in html)
with app.app_context():
    checar("Razão social foi persistida", Clinica.query.get(centro_id).razao_social == "Grupo Saude Total LTDA")

# ---------- Salvar Dados Fiscais também fecha a tela ----------

r = client.post(f"/equipe/clinica/dados-fiscais/{centro_id}", data={
    "inscricao_estadual": "123456789",
    "regime_tributario": "Simples Nacional",
    "cnae": "8640-2/12",
}, follow_redirects=False)
checar("Salvar Dados Fiscais também redireciona pra lista de locais",
       r.status_code in (301, 302) and r.headers["Location"].endswith("/equipe/filiais"))

# ---------- Vindo do assistente, volta pro assistente ----------

r = client.post(f"/equipe/clinica/configuracoes/{centro_id}", data={
    "voltar_onboarding": "1",
    "nome": "Grupo Saúde Total - Centro",
    "razao_social": "Grupo Saude Total LTDA",
    "cnpj": "11.222.333/0001-44",
    "telefone": "(27) 3333-0000",
    "email_contato": "centro@gruposaude.com",
    "cep": "", "rua": "", "numero": "", "complemento": "", "bairro": "", "cidade": "", "uf": "",
}, follow_redirects=False)
checar("Vindo do assistente de configuração, salvar volta pro assistente",
       r.status_code in (301, 302) and "configuracao-inicial" in r.headers["Location"])

client.get("/logout")
print("\nTodos os testes de fechar a tela ao salvar passaram.")
