"""Testa a mudança: o link de auto-cadastro de paciente é da EMPRESA (o
paciente é da empresa, não de uma filial) e fica no PAINEL, não mais nos
Dados Cadastrais de cada filial.

- O Painel mostra o link (gerando o código da empresa na primeira vez).
- O cadastro feito pelo link da empresa cria o paciente na empresa
  (empresa_id), sem filial.
- Links ANTIGOS (código legado por filial) continuam funcionando e também
  criam o paciente na empresa da filial.
- "Gerar novo link" troca o código da empresa e invalida o link antigo
  (inclusive os códigos legados por filial).
- A tela de Dados Cadastrais da filial não mostra mais o link."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Empresa, Clinica, Paciente

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


# Grupo Saúde Total: 2 filiais - o link tem que valer pra empresa inteira.
login("secretaria@gruposaude.com", "123456")

r = client.get("/equipe/")
html_painel = r.get_data(as_text=True)
checar("Painel mostra o card do link de cadastro de pacientes", "Cadastro de pacientes pelo app" in html_painel)
checar("Painel tem o botão Copiar", "copiarLinkCadastro" in html_painel)

with app.app_context():
    grupo = Empresa.query.filter_by(nome="Grupo Saúde Total").first()
    checar("Código da EMPRESA foi gerado ao abrir o painel", bool(grupo.codigo_cadastro_paciente))
    codigo_empresa = grupo.codigo_cadastro_paciente
    grupo_id = grupo.id
    centro = Clinica.query.filter_by(nome="Grupo Saúde Total - Centro").first()
    # Simula um link ANTIGO por filial (legado), como os já divulgados.
    centro.codigo_cadastro_paciente = "legadocentro"[:8]
    db.session.commit()
    codigo_legado = centro.codigo_cadastro_paciente

checar("O link do painel usa o código da empresa", codigo_empresa in html_painel)

r = client.get("/equipe/clinica/configuracoes")
checar("Dados Cadastrais da filial NÃO mostra mais o card do link",
       "Cadastro de pacientes pelo app" not in r.get_data(as_text=True))
client.get("/logout")

# ---------- Cadastro pelo link da EMPRESA ----------

url_cadastro = f"/paciente/cadastro/{codigo_empresa}"
url_legado = f"/paciente/cadastro/{codigo_legado}"

r = client.get(url_cadastro)
checar("Link da empresa abre a tela de auto-cadastro", r.status_code == 200)
checar("A tela mostra o nome da EMPRESA", "Grupo Saúde Total" in r.get_data(as_text=True))

r = client.post(url_cadastro, data={
    "nome": "Paciente Pelo Link Empresa", "cpf": "808.909.101-11",
    "telefone": "(27) 95555-0101", "data_nascimento": "05/05/1995",
}, follow_redirects=True)
checar("Auto-cadastro pelo link da empresa funciona", r.status_code == 200)
with app.app_context():
    p1 = Paciente.query.filter_by(nome="Paciente Pelo Link Empresa").first()
    checar("Paciente criado NA EMPRESA (empresa_id), sem filial",
           p1 is not None and p1.empresa_id == grupo_id and p1.clinica_id is None)
    checar("Cadastro entra como pendente (aguarda aceite da equipe)", p1.status_cadastro == "pendente")
client.get("/logout")

# ---------- Link ANTIGO (código legado por filial) continua funcionando ----------

r = client.get(url_legado)
checar("Link legado por filial ainda abre a tela de auto-cadastro", r.status_code == 200)
r = client.post(url_legado, data={
    "nome": "Paciente Pelo Link Legado", "cpf": "707.808.909-10",
    "telefone": "(27) 95555-0202", "data_nascimento": "06/06/1996",
}, follow_redirects=True)
checar("Auto-cadastro pelo link legado funciona", r.status_code == 200)
with app.app_context():
    p2 = Paciente.query.filter_by(nome="Paciente Pelo Link Legado").first()
    checar("Paciente do link legado TAMBÉM entra na empresa (não na filial)",
           p2 is not None and p2.empresa_id == grupo_id and p2.clinica_id is None)
client.get("/logout")

# ---------- Gerar novo link invalida o antigo (empresa E legados) ----------

login("secretaria@gruposaude.com", "123456")
r = client.post("/equipe/clinica/codigo-cadastro-paciente/regenerar", follow_redirects=True)
checar("Gerar novo link responde 200 e volta pro painel", r.status_code == 200 and "Novo link de cadastro gerado" in r.get_data(as_text=True))
with app.app_context():
    grupo2 = Empresa.query.get(grupo_id)
    checar("O código da empresa mudou", grupo2.codigo_cadastro_paciente != codigo_empresa)
    checar("Os códigos legados por filial foram zerados",
           all(c.codigo_cadastro_paciente is None for c in grupo2.filiais))
client.get("/logout")

r = client.get(url_cadastro)
checar("O link antigo da empresa deixou de funcionar", "Link de cadastro inválido" in r.get_data(as_text=True))
r = client.get(url_legado)
checar("O link legado por filial também deixou de funcionar", "Link de cadastro inválido" in r.get_data(as_text=True))

print("\nTodos os testes do link de cadastro por empresa passaram.")
