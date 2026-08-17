"""Testa a tela "Exames por filial" (medico.exames_por_filial) - hoje só
"Associar exames": um cadastro básico (Exame, Médico, Preço) que promove um
exame do catálogo (cadastrado genérico, sem associação) para uma associação
de verdade, dentro do Grupo atual.

Fatia 5 (passo 4): não existe mais "escolher a filial de destino" nessa
tela - cada Grupo já É a própria unidade (não existe mais "várias filiais
da mesma empresa" para associar o mesmo exame). O seed's "Grupo Saúde
Total" virou dois Grupos independentes (Centro/Praia) que compartilham a
mesma equipe (médico/secretária com vínculo ativo nos dois) - para atender
nos dois, a pessoa cadastra/associa o exame em CADA Grupo separadamente,
trocando qual está ativo (medico.escolher_clinica), em vez de escolher um
"destino" na mesma tela como acontecia no modelo antigo."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Grupo, Exame, PreparoModelo

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


with app.app_context():
    centro = Grupo.query.filter_by(nome="Grupo Saúde Total - Centro").first()
    praia = Grupo.query.filter_by(nome="Grupo Saúde Total - Praia").first()
    medico_grupo = Usuario.query.filter_by(email="medico@gruposaude.com").first()
    centro_id, praia_id, medico_id, medico_nome = centro.id, praia.id, medico_grupo.id, medico_grupo.nome
    # Modelo de preparo é obrigatório no cadastro do exame - o Grupo Saúde
    # Total não tem nenhum no seed, então criamos um em cada Grupo (o
    # catálogo de modelos também é escopado por Grupo agora).
    modelo_centro = PreparoModelo(grupo_id=centro_id, nome="Preparo Grupo Saúde - Centro", instrucoes="Jejum de 8 horas.")
    modelo_praia = PreparoModelo(grupo_id=praia_id, nome="Preparo Grupo Saúde - Praia", instrucoes="Jejum de 8 horas.")
    db.session.add_all([modelo_centro, modelo_praia])
    db.session.commit()
    modelo_centro_id, modelo_praia_id = modelo_centro.id, modelo_praia.id

login("secretaria@gruposaude.com", "123456")

# Precisa escolher explicitamente qual dos dois Grupos está ativo (a
# secretária tem vínculo ativo nos dois - ver seed.py).
client.post("/equipe/clinica", data={"clinica_id": str(centro_id)}, follow_redirects=True)

# Cadastro do exame é genérico (sem filial, sem médico, sem preço) - nasce
# só como item de catálogo no Grupo ATIVO (Centro); médico e preço são
# definidos logo em seguida, na própria tela "Exames por filial".
r = client.post("/equipe/exames/novo", data={
    "nome": "Ultrassom Abdominal", "descricao": "Ultrassom", "duracao_minutos": "30",
    "preparo_modelo_id": str(modelo_centro_id),
}, follow_redirects=True)
checar("Cadastro genérico do exame responde 200", r.status_code == 200)

with app.app_context():
    exame_origem_id = Exame.query.filter_by(grupo_id=centro_id, nome="Ultrassom Abdominal").first().id

# A tela "Associar exames" não pede mais destino - só nome/médico/preço,
# dentro do Grupo ativo.
r = client.get("/equipe/exames/por-filial")
html = r.get_data(as_text=True)
checar("Tela responde 200", r.status_code == 200)
checar(
    "Formulário tem só os 2 campos (sem escolher filial de destino)",
    'name="nome"' in html and 'name="medico_id"' in html
    and 'name="clinica_destino_id"' not in html,
)

# Associa o exame no Grupo Centro.
r0 = client.post("/equipe/exames/por-filial/associar", data={
    "nome": "Ultrassom Abdominal", "medico_id": str(medico_id),
}, follow_redirects=True)
checar("Associar o exame no Grupo Centro responde 200", r0.status_code == 200)
with app.app_context():
    exame_centro = Exame.query.get(exame_origem_id)
    checar("A associação promoveu o registro do catálogo (sem duplicar)",
           Exame.query.filter_by(grupo_id=centro_id, nome="Ultrassom Abdominal").count() == 1)
    checar("Exame do Centro ficou associado, com médico", exame_centro.associado)

r = client.get("/equipe/exames/por-filial")
html = r.get_data(as_text=True)
checar("Mostra o nome do exame na lista", "Ultrassom Abdominal" in html)
checar("Mostra o médico responsável na linha", medico_nome in html)

# Tentar associar de novo (já existe) é bloqueado com aviso, sem duplicar.
r3 = client.post("/equipe/exames/por-filial/associar", data={
    "nome": "Ultrassom Abdominal", "medico_id": str(medico_id),
}, follow_redirects=True)
checar("Segunda tentativa de associar mostra aviso de duplicidade", "já está associado" in r3.get_data(as_text=True))
with app.app_context():
    checar(
        "Não criou um segundo exame duplicado no Grupo Centro",
        Exame.query.filter_by(grupo_id=centro_id, nome="Ultrassom Abdominal").count() == 1,
    )

# O formulário de EDITAR o exame continua funcionando normalmente (não
# mexe em médico/associação, só nos dados de catálogo do exame).
r6 = client.post(f"/equipe/exames/{exame_origem_id}/editar", data={
    "nome": "Ultrassom Abdominal", "descricao": "Ultrassom", "duracao_minutos": "30",
    "preparo_modelo_id": str(modelo_centro_id),
}, follow_redirects=True)
checar("Editar exame responde 200", r6.status_code == 200)
with app.app_context():
    checar("Editar o exame não desfaz a associação", Exame.query.get(exame_origem_id).associado)

# ---------- Grupo Praia: catálogo/associação independentes do Centro ----------
# Como cada Grupo já é a própria unidade, servir o mesmo exame na Praia
# exige cadastrá-lo (e associá-lo) lá também - trocando qual Grupo está
# ativo, em vez de escolher um "destino" na mesma tela.
client.post("/equipe/clinica", data={"clinica_id": str(praia_id)}, follow_redirects=True)

r = client.post("/equipe/exames/novo", data={
    "nome": "Ultrassom Abdominal", "descricao": "Ultrassom", "duracao_minutos": "30",
    "preparo_modelo_id": str(modelo_praia_id),
}, follow_redirects=True)
checar("Cadastro genérico do exame na Praia responde 200", r.status_code == 200)

with app.app_context():
    exame_praia_id = Exame.query.filter_by(grupo_id=praia_id, nome="Ultrassom Abdominal").first().id

# Associa com o mesmo médico (que atende os dois grupos).
r2 = client.post("/equipe/exames/por-filial/associar", data={
    "nome": "Ultrassom Abdominal", "medico_id": str(medico_id),
}, follow_redirects=True)
checar("Associar no Grupo Praia responde 200", r2.status_code == 200)
checar("Mensagem de sucesso aparece", "associado" in r2.get_data(as_text=True))

with app.app_context():
    exame_centro = Exame.query.get(exame_origem_id)
    exame_praia = Exame.query.get(exame_praia_id)
    checar("São registros distintos (um por Grupo, não duplicado)", exame_centro.id != exame_praia.id)
    checar("Duração cadastrada na Praia", exame_praia.duracao_minutos == 30)
    checar("Médico responsável na Praia é o mesmo (atende os dois Grupos)", exame_praia.medico_id == medico_id)

r4 = client.get("/equipe/exames/por-filial")
html4 = r4.get_data(as_text=True)
checar("Lista da Praia mostra o exame associado", "Ultrassom Abdominal" in html4)

client.get("/logout")
print("\nTodos os testes de exames por filial (Grupo atômico) passaram.")
