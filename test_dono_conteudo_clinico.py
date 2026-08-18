"""Testa o DONO do conteúdo clínico:

- O modelo de preparo e o exame só podem ser editados pelo MÉDICO que os
  criou - nem a secretária, nem outro médico.
- Um médico não pode ser associado a um exame do qual não é o dono
  (nem como responsável da associação, nem como médico extra).
- Conteúdo antigo (sem dono registrado) e conteúdo criado pela
  secretária seguem o comportamento antigo - editáveis pela equipe.
"""
from app import create_app
from app.extensions import db
from app.models import Usuario, Grupo, GrupoMembro, Exame, PreparoModelo

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha="123456"):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


# Cenário: Grupo Saúde Total - Centro. Dr. Eduardo (dono do conteúdo) + Dra.
# Gilda (segunda médica, criada aqui) + secretária Camila. Fatia 5: o que
# era uma Empresa com filiais virou um Grupo atômico - "Centro" e "Praia"
# são dois Grupos distintos que compartilham a mesma equipe (ver seed.py).
with app.app_context():
    centro = Grupo.query.filter_by(nome="Grupo Saúde Total - Centro").first()
    centro_id = centro.id
    eduardo = Usuario.query.filter_by(email="medico@gruposaude.com").first()
    eduardo_id = eduardo.id

    gilda = Usuario(nome="Dra. Gilda Segunda", email="gilda@gruposaude.com", tipo="medico")
    gilda.set_senha("123456")
    gilda.definir_permissoes_padrao()
    db.session.add(gilda)
    db.session.flush()
    db.session.add(GrupoMembro(grupo_id=centro_id, usuario_id=gilda.id, papel="membro", ativo=True))
    db.session.commit()
    gilda_id = gilda.id

# ---------- Eduardo cria o exame e o modelo (vira o dono) ----------

# Dr. Eduardo atua nos dois grupos ("Centro" e "Praia", ver seed.py) -
# precisa escolher explicitamente com qual está trabalhando agora.
login("medico@gruposaude.com")
client.post("/equipe/clinica", data={"empresa_id": str(centro_id)}, follow_redirects=True)
r = client.post("/equipe/preparo-modelos/novo", data={
    "nome": "Preparo Do Eduardo", "instrucoes": "Jejum de 8 horas.",
    "observacoes_medicamentos": "",
}, follow_redirects=True)
checar("Eduardo cria o modelo de preparo", "cadastrado com sucesso" in r.get_data(as_text=True).lower())

r = client.post("/equipe/exames/novo", data={
    "nome": "Exame Do Eduardo", "descricao": "", "duracao_minutos": "30",
    "preparo_modelo_id": "nenhum",
}, follow_redirects=True)
checar("Eduardo cria o exame", "cadastrado com sucesso" in r.get_data(as_text=True).lower())

with app.app_context():
    modelo = PreparoModelo.query.filter_by(nome="Preparo Do Eduardo").first()
    exame = Exame.query.filter_by(nome="Exame Do Eduardo").first()
    checar("O modelo registra o Eduardo como criador", modelo.criado_por_id == eduardo_id)
    checar("O exame registra o Eduardo como criador", exame.criado_por_id == eduardo_id)
    modelo_id, exame_id = modelo.id, exame.id

# O dono edita normalmente.
r = client.post(f"/equipe/preparo-modelos/{modelo_id}/editar", data={
    "nome": "Preparo Do Eduardo", "instrucoes": "Jejum de 10 horas.",
    "observacoes_medicamentos": "",
}, follow_redirects=True)
checar("O DONO edita o modelo normalmente", "atualizado" in r.get_data(as_text=True))
client.get("/logout")

# ---------- Secretária NÃO edita conteúdo do médico ----------

login("secretaria@gruposaude.com")
client.post("/equipe/clinica", data={"empresa_id": str(centro_id)}, follow_redirects=True)
r = client.get("/equipe/preparo-modelos")
html = r.get_data(as_text=True)
checar("Na lista, a secretária vê quem é o dono do modelo",
       "Só Dr. Eduardo Nunes pode editar" in html)

r = client.get(f"/equipe/preparo-modelos/{modelo_id}/editar", follow_redirects=True)
checar("Secretária é barrada ao editar o modelo",
       "que criou este modelo de preparo, pode editá-lo" in r.get_data(as_text=True))
r = client.post(f"/equipe/preparo-modelos/{modelo_id}/remover", follow_redirects=True)
checar("Secretária é barrada ao remover o modelo",
       "pode removê-lo" in r.get_data(as_text=True))
r = client.get(f"/equipe/exames/{exame_id}/editar", follow_redirects=True)
checar("Secretária é barrada ao editar o exame",
       "que criou este exame, pode editá-lo" in r.get_data(as_text=True))

# ---------- Associação: só o dono pode ser o médico do exame ----------

r = client.post("/equipe/exames/por-filial/associar", data={
    "nome": "Exame Do Eduardo", "clinica_destino_id": str(centro_id),
    "medico_id": str(gilda_id),
}, follow_redirects=True)
checar("Associar OUTRO médico ao exame do Eduardo é bloqueado",
       "só ele pode ser" in r.get_data(as_text=True))
with app.app_context():
    checar("Nenhuma associação foi criada",
           Exame.query.filter_by(nome="Exame Do Eduardo", associado=True).count() == 0)

r = client.post("/equipe/exames/por-filial/associar", data={
    "nome": "Exame Do Eduardo", "clinica_destino_id": str(centro_id),
    "medico_id": str(eduardo_id),
}, follow_redirects=True)
checar("Associar o exame com o PRÓPRIO dono funciona",
       "associado" in r.get_data(as_text=True).lower())

# Tentar adicionar a Gilda como médica extra também é bloqueado.
r = client.post("/equipe/exames/por-filial/associar", data={
    "nome": "Exame Do Eduardo", "clinica_destino_id": str(centro_id),
    "medico_id": str(gilda_id),
}, follow_redirects=True)
checar("Adicionar outro médico como EXTRA também é bloqueado",
       "só ele pode ser" in r.get_data(as_text=True))
with app.app_context():
    assoc = Exame.query.filter_by(nome="Exame Do Eduardo", grupo_id=centro_id, associado=True).first()
    checar("A associação continua só com o dono",
           assoc.medico_id == eduardo_id and len(assoc.medicos_extra) == 0)
    assoc_id = assoc.id

# Editar a associação trocando o médico pra Gilda: bloqueado.
r = client.post(f"/equipe/exames/por-filial/{assoc_id}/atualizar", data={
    "medico_id": str(gilda_id),
}, follow_redirects=True)
checar("Trocar o médico da associação pra quem não é dono é bloqueado",
       "só ele pode ser" in r.get_data(as_text=True))

# A secretária segue conseguindo editar a associação (mantendo o dono).
r = client.post(f"/equipe/exames/por-filial/{assoc_id}/atualizar", data={
    "medico_id": str(eduardo_id),
}, follow_redirects=True)
checar("Secretária segue editando a associação normalmente",
       "atualizado" in r.get_data(as_text=True))
client.get("/logout")

# ---------- Outro médico também não edita ----------

login("gilda@gruposaude.com")
r = client.get(f"/equipe/preparo-modelos/{modelo_id}/editar", follow_redirects=True)
checar("Outro médico também é barrado no modelo",
       "pode editá-lo" in r.get_data(as_text=True))
client.get("/logout")

# ---------- Conteúdo SEM dono (legado / criado pela secretária) ----------

login("secretaria@gruposaude.com")
client.post("/equipe/clinica", data={"empresa_id": str(centro_id)}, follow_redirects=True)
r = client.post("/equipe/preparo-modelos/novo", data={
    "nome": "Preparo Da Secretaria", "instrucoes": "Sem restrições.",
    "observacoes_medicamentos": "",
}, follow_redirects=True)
checar("Secretária cria um modelo próprio", "cadastrado com sucesso" in r.get_data(as_text=True).lower())
with app.app_context():
    m2 = PreparoModelo.query.filter_by(nome="Preparo Da Secretaria").first()
    m2_id = m2.id
    # Modelo LEGADO: sem criador registrado.
    m2_legado = PreparoModelo(grupo_id=centro_id, nome="Preparo Legado", instrucoes="Antigo.")
    db.session.add(m2_legado)
    db.session.commit()
    legado_id = m2_legado.id

r = client.post(f"/equipe/preparo-modelos/{m2_id}/editar", data={
    "nome": "Preparo Da Secretaria", "instrucoes": "Sem restrições mesmo.",
    "observacoes_medicamentos": "",
}, follow_redirects=True)
checar("Modelo criado pela secretária continua editável por ela",
       "atualizado" in r.get_data(as_text=True))
r = client.post(f"/equipe/preparo-modelos/{legado_id}/editar", data={
    "nome": "Preparo Legado", "instrucoes": "Atualizado.",
    "observacoes_medicamentos": "",
}, follow_redirects=True)
checar("Modelo LEGADO (sem dono) continua editável (compatibilidade)",
       "atualizado" in r.get_data(as_text=True))
client.get("/logout")

print("\nTodos os testes de dono do conteúdo clínico passaram.")
