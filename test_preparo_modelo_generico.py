"""Testa que "Modelo de preparo" é genérico - não pertence a uma filial
específica. Antes desta correção, o cadastro de um novo modelo pedia para
escolher uma filial, a lista mostrava uma coluna "Filial", e um exame só
podia usar um modelo criado na MESMA filial dele - o que não fazia sentido,
já que o mesmo texto de preparo (dieta, medicamentos a suspender etc.)
costuma valer igual em qualquer local de atendimento da empresa.

Fatia 5: "filial" virou sinônimo de Grupo (1 Grupo = 1 unidade completa,
não existe mais uma empresa por cima com várias filiais) - então o cenário
"mesma empresa, filial diferente" não existe mais (Centro e Praia agora
são 2 Grupos/tenants distintos, cada um com seu próprio catálogo de
modelos/exames). O que este teste continua verificando é o ponto central:
dentro de UM Grupo, o modelo de preparo não pede/depende de uma
sub-localização - é reaproveitável por qualquer exame do próprio Grupo."""
from app import create_app
from app.models import PreparoModelo, Grupo

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


with app.app_context():
    centro_id = Grupo.query.filter_by(nome="Grupo Saúde Total - Centro").first().id

login("secretaria@gruposaude.com", "123456")
# Secretária tem vínculo ativo em 2 Grupos (Centro e Praia) - precisa
# escolher explicitamente qual está usando nesta sessão.
client.post("/equipe/clinica", data={"clinica_id": str(centro_id)}, follow_redirects=True)

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
    checar("Modelo ficou ancorado no Grupo ativo (Centro)", modelo.grupo_id == centro_id)
    modelo_id = modelo.id

# A lista de modelos de preparo não mostra coluna "Filial".
r2 = client.get("/equipe/preparo-modelos")
html2 = r2.get_data(as_text=True)
checar("Lista de modelos não tem coluna 'Filial'", "<th>Filial</th>" not in html2)
checar("Modelo genérico aparece na lista", "Preparo genérico de teste" in html2)

# O modelo genérico aparece como opção pra qualquer exame do MESMO Grupo,
# sem precisar de nenhuma sub-localização.
r3 = client.get("/equipe/exames/novo")
html3 = r3.get_data(as_text=True)
checar("Modelo genérico aparece como opção no cadastro de exame",
       f'value="{modelo_id}"' in html3)

r4 = client.post("/equipe/exames/novo", data={
    "nome": "Exame com preparo genérico",
    "descricao": "Teste",
    "duracao_minutos": "30",
    "preparo_modelo_id": str(modelo_id),
}, follow_redirects=True)
checar("Exame cadastrado usando o modelo genérico funciona", "cadastrado com sucesso" in r4.get_data(as_text=True))

client.get("/logout")
print("\nTodos os testes de modelo de preparo genérico passaram.")
