"""Testa o ajuste: ao SALVAR os Dados Cadastrais (ou Dados Fiscais) de um
local, a tela fecha - a pessoa volta para "Meus locais de atendimento"
(de onde abriu a tela), com a mensagem de sucesso dizendo de qual local
era. Antes, salvar continuava na própria tela do formulário.

O botão "Cancelar" já voltava pra lista; agora salvar também volta.

Fatia 5: não existe mais "várias filiais dentro de uma empresa" - o
`<int:filial_id>` na URL é ignorado (mantido só por compatibilidade com
links antigos), sempre edita o Grupo atual da sessão (ver
app/clinica_utils.py:empresa_atual). Por isso o teste usa a Clínica
Vitória (grupo único da secretária, sem precisar escolher entre grupos)."""
from app import create_app
from app.models import Grupo

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


client.post("/login", data={"email": "secretaria@clinicavitoria.com", "senha": "123456"}, follow_redirects=True)

with app.app_context():
    vitoria = Grupo.query.filter_by(nome="Clínica Vitória").first()
    vitoria_id = vitoria.id

# ---------- Salvar Dados Cadastrais fecha a tela ----------

r = client.post(f"/equipe/clinica/configuracoes/{vitoria_id}", data={
    "nome": "Clínica Vitória",
    "razao_social": "Clínica Vitória Diagnósticos Ltda.",
    "cnpj": "12.345.678/0001-90",
    "telefone": "(27) 3333-4444",
    "email_contato": "contato@clinicavitoria.com",
    "cep": "29055-360", "rua": "Rua Teste", "numero": "10",
    "complemento": "", "bairro": "Centro", "cidade": "Vitória", "uf": "ES",
}, follow_redirects=False)
checar("Salvar redireciona (não fica na mesma tela)", r.status_code in (301, 302))
checar("O destino é 'Meus locais de atendimento'", r.headers["Location"].endswith("/equipe/filiais"))

r2 = client.post(f"/equipe/clinica/configuracoes/{vitoria_id}", data={
    "nome": "Clínica Vitória",
    "razao_social": "Clínica Vitória Diagnósticos Ltda. Atualizada",
    "cnpj": "12.345.678/0001-90",
    "telefone": "(27) 3333-4444",
    "email_contato": "contato@clinicavitoria.com",
    "cep": "29055-360", "rua": "Rua Teste", "numero": "10",
    "complemento": "", "bairro": "Centro", "cidade": "Vitória", "uf": "ES",
}, follow_redirects=True)
html = r2.get_data(as_text=True)
checar("A lista de locais abre com a mensagem de sucesso", "Dados Cadastrais de" in html and "atualizados com sucesso" in html)
checar("A mensagem diz de QUAL local eram os dados", "Clínica Vitória" in html)
with app.app_context():
    checar("Razão social foi persistida", Grupo.query.get(vitoria_id).razao_social == "Clínica Vitória Diagnósticos Ltda. Atualizada")

# ---------- Salvar Dados Fiscais também fecha a tela ----------

r = client.post(f"/equipe/clinica/dados-fiscais/{vitoria_id}", data={
    "inscricao_estadual": "123456789",
    "regime_tributario": "Simples Nacional",
    "cnae": "8640-2/12",
}, follow_redirects=False)
checar("Salvar Dados Fiscais também redireciona pra lista de locais",
       r.status_code in (301, 302) and r.headers["Location"].endswith("/equipe/filiais"))

client.get("/logout")
print("\nTodos os testes de fechar a tela ao salvar passaram.")
