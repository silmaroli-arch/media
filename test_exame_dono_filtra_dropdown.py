"""Testa a correção do bug relatado na tela "Associar exames": o <select>
"Exame" do formulário "Adicionar" listava TODOS os exames do Grupo,
inclusive os que pertencem a OUTRO médico (ver Exame.dono_medico/
criado_por_id) - o médico escolhia um exame que não era dele e só
descobria, ao tentar salvar, que a associação era rejeitada (ver
_dono_medico_do_exame em app/routes_medico.py, que já bloqueia isso desde
antes). Agora, quando quem está logado é médico, o <select> já lista só
os exames DELE (ou sem dono registrado, que continuam de uso geral).
Secretária continua vendo todos, já que não é "dona" de exame nenhum."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Grupo, Exame, GrupoMembro

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha="123456"):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


with app.app_context():
    centro = Grupo.query.filter_by(nome="Grupo Saúde Total - Centro").first()
    centro_id = centro.id
    medico_eduardo = Usuario.query.filter_by(email="medico@gruposaude.com").first()
    medico_eduardo_id = medico_eduardo.id

    # Um segundo médico, também vinculado ao mesmo Grupo.
    medico_bianca = Usuario(nome="Dra. Bianca Dono", email="bianca.dono@gruposaude.com", tipo="medico")
    medico_bianca.set_senha("123456")
    db.session.add(medico_bianca)
    db.session.flush()
    db.session.add(GrupoMembro(grupo_id=centro_id, usuario_id=medico_bianca.id, papel="membro", ativo=True))
    db.session.commit()
    medico_bianca_id = medico_bianca.id

    # Um exame de cada médico, e um terceiro sem dono registrado (cadastro
    # antigo/criado pela secretária).
    exame_eduardo = Exame(
        grupo_id=centro_id, medico_id=medico_eduardo_id, nome="Exame do Eduardo",
        descricao="", duracao_minutos=30, medico_confirmado=True, criado_por_id=medico_eduardo_id,
    )
    exame_bianca = Exame(
        grupo_id=centro_id, medico_id=medico_bianca_id, nome="Exame da Bianca",
        descricao="", duracao_minutos=30, medico_confirmado=True, criado_por_id=medico_bianca_id,
    )
    exame_sem_dono = Exame(
        grupo_id=centro_id, medico_id=medico_eduardo_id, nome="Exame Sem Dono Registrado",
        descricao="", duracao_minutos=30, medico_confirmado=True, criado_por_id=None,
    )
    db.session.add_all([exame_eduardo, exame_bianca, exame_sem_dono])
    db.session.commit()

# A secretária atua nos dois grupos "Saúde Total" - precisa escolher qual
# está usando agora (Grupo é atômico - ver clinica_utils.py).
# ---------- Logado como o Dr. Eduardo: só vê o exame dele + o sem dono ----------

# O Dr. Eduardo atua nos dois grupos "Saúde Total" - precisa escolher qual
# está usando agora (Grupo é atômico - ver clinica_utils.py).
login("medico@gruposaude.com")
client.post("/equipe/clinica", data={"clinica_id": str(centro_id)}, follow_redirects=True)
r = client.get("/equipe/exames/por-filial")
html = r.get_data(as_text=True)
checar("Dr. Eduardo vê o próprio exame no <select>", 'value="Exame do Eduardo"' in html)
checar("Dr. Eduardo vê o exame sem dono registrado (comportamento antigo)",
       'value="Exame Sem Dono Registrado"' in html)
checar("Dr. Eduardo NÃO vê o exame da Dra. Bianca no <select>",
       'value="Exame da Bianca"' not in html)
client.get("/logout")

# ---------- Logada como a Dra. Bianca: só vê o exame dela + o sem dono ----------

login("bianca.dono@gruposaude.com")
r = client.get("/equipe/exames/por-filial")
html = r.get_data(as_text=True)
checar("Dra. Bianca vê o próprio exame no <select>", 'value="Exame da Bianca"' in html)
checar("Dra. Bianca vê o exame sem dono registrado (comportamento antigo)",
       'value="Exame Sem Dono Registrado"' in html)
checar("Dra. Bianca NÃO vê o exame do Dr. Eduardo no <select>",
       'value="Exame do Eduardo"' not in html)
client.get("/logout")

# ---------- Secretária continua vendo TODOS os exames (não é dona de exame nenhum) ----------

login("secretaria@gruposaude.com")
client.post("/equipe/clinica", data={"clinica_id": str(centro_id)}, follow_redirects=True)
r = client.get("/equipe/exames/por-filial")
html = r.get_data(as_text=True)
checar("Secretária vê o exame do Dr. Eduardo", 'value="Exame do Eduardo"' in html)
checar("Secretária vê o exame da Dra. Bianca", 'value="Exame da Bianca"' in html)
checar("Secretária vê o exame sem dono registrado", 'value="Exame Sem Dono Registrado"' in html)
client.get("/logout")

print("\nTodos os testes de filtro do dropdown de exames por dono passaram.")
