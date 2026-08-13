"""Bug relatado: na tela "Modelos de preparo", um médico via na LISTA os
modelos de preparo criados por OUTROS médicos da mesma clínica (mesmo já
não podendo editá-los - ver test_dono_conteudo_clinico.py). O esperado é
que o médico só visualize os próprios modelos (mais os sem dono
registrado, legado/criado pela secretária) - mesmo padrão já usado em
"Exames"/"Associar exames" pra Exame.dono_medico. Isso vale tanto na lista
quanto no dropdown de escolher modelo ao cadastrar/editar um exame."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Clinica, ClinicaMembro, PreparoModelo

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha="123456"):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


with app.app_context():
    centro = Clinica.query.filter_by(nome="Grupo Saúde Total - Centro").first()
    centro_id = centro.id
    eduardo = Usuario.query.filter_by(email="medico@gruposaude.com").first()
    eduardo_id = eduardo.id

    gilda = Usuario(nome="Dra. Gilda Terceira", email="gilda3@gruposaude.com", tipo="medico")
    gilda.set_senha("123456")
    gilda.definir_permissoes_padrao()
    db.session.add(gilda)
    db.session.flush()
    db.session.add(ClinicaMembro(clinica_id=centro_id, usuario_id=gilda.id))
    db.session.commit()
    gilda_id = gilda.id

    modelo_eduardo = PreparoModelo(
        clinica_id=centro_id, nome="Preparo Privado Do Eduardo", instrucoes="Jejum de 8 horas.",
        criado_por_id=eduardo_id,
    )
    modelo_legado = PreparoModelo(
        clinica_id=centro_id, nome="Preparo Legado Sem Dono", instrucoes="Sem restrições.",
    )
    db.session.add_all([modelo_eduardo, modelo_legado])
    db.session.commit()

login("medico@gruposaude.com")
r = client.get("/equipe/preparo-modelos")
html = r.get_data(as_text=True)
checar("Eduardo vê o próprio modelo na lista", "Preparo Privado Do Eduardo" in html)
checar("Eduardo vê o modelo legado (sem dono) na lista", "Preparo Legado Sem Dono" in html)
r_novo = client.get("/equipe/exames/novo")
checar("Eduardo vê o próprio modelo no dropdown de novo exame", "Preparo Privado Do Eduardo" in r_novo.get_data(as_text=True))
client.get("/logout")

login("gilda3@gruposaude.com")
r2 = client.get("/equipe/preparo-modelos")
html2 = r2.get_data(as_text=True)
checar("Gilda NÃO vê o modelo do Eduardo na lista", "Preparo Privado Do Eduardo" not in html2)
checar("Gilda continua vendo o modelo legado (sem dono)", "Preparo Legado Sem Dono" in html2)
r2_novo = client.get("/equipe/exames/novo")
checar(
    "Gilda NÃO vê o modelo do Eduardo no dropdown de novo exame",
    "Preparo Privado Do Eduardo" not in r2_novo.get_data(as_text=True),
)
client.get("/logout")

login("secretaria@gruposaude.com")
r3 = client.get("/equipe/preparo-modelos")
html3 = r3.get_data(as_text=True)
checar(
    "Secretária continua vendo os modelos de todos os médicos",
    "Preparo Privado Do Eduardo" in html3 and "Preparo Legado Sem Dono" in html3,
)
client.get("/logout")

print("\nTodos os testes de 'lista de modelos de preparo só mostra os do próprio médico' passaram.")
