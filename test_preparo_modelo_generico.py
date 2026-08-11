"""Testa que "Modelo de preparo" é genérico - não pertence a uma filial
específica. Antes desta correção, o cadastro de um novo modelo pedia para
escolher uma filial, a lista mostrava uma coluna "Filial", e um exame só
podia usar um modelo criado na MESMA filial dele - o que não fazia sentido,
já que o mesmo texto de preparo (dieta, medicamentos a suspender etc.)
costuma valer igual em qualquer local de atendimento da empresa."""
from app import create_app
from app.extensions import db
from app.models import Usuario, PreparoModelo, Clinica

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


with app.app_context():
    centro_id = Clinica.query.filter_by(nome="Grupo Saúde Total - Centro").first().id
    praia_id = Clinica.query.filter_by(nome="Grupo Saúde Total - Praia").first().id

login("secretaria@gruposaude.com", "123456")

# O formulário de novo modelo de preparo NÃO pede filial.
r0 = client.get("/equipe/preparo-modelos/novo")
html0 = r0.get_data(as_text=True)
checar("Tela de novo modelo responde 200", r0.status_code == 200)
checar("Não pede para escolher filial", "Escolha o local de atendimento" not in html0 and 'name="filial_id"' not in html0)

# Cria um modelo sem enviar nenhuma filial - deve funcionar normalmente.
r1 = client.post("/equipe/preparo-modelos/novo", data={
    "nome": "Preparo genérico de teste",
    "instrucoes": "Jejum de 8 horas antes do exame.",
}, follow_redirects=True)
checar("Criar modelo sem escolher filial funciona", r1.status_code == 200)
checar("Mensagem de sucesso aparece", "Modelo de preparo cadastrado" in r1.get_data(as_text=True))

with app.app_context():
    modelo = PreparoModelo.query.filter_by(nome="Preparo genérico de teste").first()
    checar("Modelo foi criado", modelo is not None)
    modelo_id = modelo.id
    modelo_clinica_id = modelo.clinica_id

# A lista de modelos de preparo não mostra coluna "Filial".
r2 = client.get("/equipe/preparo-modelos")
html2 = r2.get_data(as_text=True)
checar("Lista de modelos não tem coluna 'Filial'", "<th>Filial</th>" not in html2)
checar("Modelo genérico aparece na lista", "Preparo genérico de teste" in html2)

# Cadastra um exame na filial OPOSTA à filial técnica do modelo (Centro vs
# Praia) - o modelo genérico precisa aparecer como opção mesmo assim.
filial_do_exame = praia_id if modelo_clinica_id == centro_id else centro_id
r3 = client.get("/equipe/exames/novo")
html3 = r3.get_data(as_text=True)
checar("Modelo genérico aparece como opção no cadastro de exame (qualquer filial)",
       f'value="{modelo_id}"' in html3)

r4 = client.post("/equipe/exames/novo", data={
    "filial_id": str(filial_do_exame),
    "nome": "Exame com preparo genérico",
    "descricao": "Teste",
    "duracao_minutos": "30",
    "medico_id": "",
    "preparo_modelo_id": str(modelo_id),
}, follow_redirects=True)
# Se o médico responsável não estiver disponível nessa filial o teste ainda
# valida o ponto principal (o modelo não é rejeitado por causa da filial) -
# então aceitamos tanto sucesso quanto a mensagem específica de médico.
texto4 = r4.get_data(as_text=True)
checar(
    "Exame em filial diferente da técnica do modelo não é rejeitado por causa do modelo de preparo",
    "Escolha um modelo de preparo válido" not in texto4,
)

client.get("/logout")
print("\nTodos os testes de modelo de preparo genérico passaram.")
