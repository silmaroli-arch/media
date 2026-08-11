"""Testa que o cadastro de um novo exame (medico.exames_novo) não pede mais
filial, médico responsável nem preço - o cadastro fica genérico (nome,
descrição, duração, preparo), e a associação de médico/filial/preço
específicos passa a acontecer só na tela "Exames por filial" (ou depois, ao
editar o exame). Cobre tanto secretária quanto médico cadastrando."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Exame, Clinica, PreparoModelo

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


# --- Secretária cadastrando ---
login("secretaria@clinicavitoria.com", "123456")

r0 = client.get("/equipe/exames/novo")
html0 = r0.get_data(as_text=True)
checar("Tela de novo exame responde 200", r0.status_code == 200)
checar("Não pede filial", 'name="filial_id"' not in html0 and "Escolha o local de atendimento" not in html0)
checar("Não pede médico responsável", 'name="medico_id"' not in html0)
checar("Não pede preço", 'name="preco"' not in html0)
checar("Não mostra 'Outros médicos'", "Outros médicos que também atendem" not in html0)
checar("Explica que a associação por filial vem depois", "Exames por filial" in html0)

with app.app_context():
    clinica_vitoria_id = Clinica.query.filter_by(nome="Clínica Vitória").first().id
    modelo_vitoria_id = PreparoModelo.query.filter_by(clinica_id=clinica_vitoria_id).first().id

r1 = client.post("/equipe/exames/novo", data={
    "nome": "Exame genérico de teste",
    "descricao": "Descrição de teste",
    "duracao_minutos": "20",
    "preparo_modelo_id": str(modelo_vitoria_id),
}, follow_redirects=True)
checar("Cadastro sem filial/médico/preço funciona", r1.status_code == 200)
checar("Mensagem de sucesso aparece", "Exame cadastrado com sucesso" in r1.get_data(as_text=True))

with app.app_context():
    exame = Exame.query.filter_by(nome="Exame genérico de teste").first()
    checar("Exame foi criado", exame is not None)
    checar("Médico responsável foi preenchido automaticamente (obrigatório no banco)", exame.medico_id is not None)
    checar("Preço fica em branco até ser definido em 'Exames por filial'", exame.preco is None)
    exame_id = exame.id

client.get("/logout")

# --- Médico cadastrando (vira responsável automaticamente, sem perguntar) ---
login("medico@clinicavitoria.com", "123456")
with app.app_context():
    clinica_vitoria_id = Clinica.query.filter_by(nome="Clínica Vitória").first().id
client.post("/equipe/clinica", data={"clinica_id": str(clinica_vitoria_id)}, follow_redirects=True)

r2 = client.get("/equipe/exames/novo")
html2 = r2.get_data(as_text=True)
checar("Médico também não vê seleção de filial/médico ao cadastrar", 'name="filial_id"' not in html2 and 'name="medico_id"' not in html2)

r3 = client.post("/equipe/exames/novo", data={
    "nome": "Exame do médico teste",
    "descricao": "",
    "duracao_minutos": "15",
    "preparo_modelo_id": str(modelo_vitoria_id),
}, follow_redirects=True)
checar("Médico consegue cadastrar sem escolher a si mesmo", r3.status_code == 200 and "Exame cadastrado com sucesso" in r3.get_data(as_text=True))

with app.app_context():
    dr_carlos = Usuario.query.filter_by(email="medico@clinicavitoria.com").first()
    exame_medico = Exame.query.filter_by(nome="Exame do médico teste").first()
    checar("Médico que cadastrou já é o responsável automaticamente", exame_medico.medico_id == dr_carlos.id)

client.get("/logout")
print("\nTodos os testes de cadastro genérico de exame passaram.")
