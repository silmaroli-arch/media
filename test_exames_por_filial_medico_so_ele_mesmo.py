"""Bug relatado: na tela "Associar exames" (medico.exames_por_filial), o
select de "Médico" mostrava TODOS os médicos da filial (ex.: "Bruno Pavan"
e "Thatja Pavan"), mesmo quando quem estava logado era um médico
associando o PRÓPRIO exame — o esperado é aparecer só ele mesmo, já que um
médico só pode ser o responsável pelos seus próprios exames (ver
Exame.dono_medico / _dono_medico_do_exame). Usa a Clínica Vitória do seed,
que tem dois médicos (Dr. Carlos Andrade e Dra. Fernanda Lima)."""
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
    grupo_vitoria = Grupo.query.filter_by(nome="Clínica Vitória").first()
    carlos = Usuario.query.filter_by(email="medico@clinicavitoria.com").first()
    fernanda = Usuario.query.filter_by(email="medica2@clinicavitoria.com").first()
    clinica_id, carlos_id, fernanda_id = grupo_vitoria.id, carlos.id, fernanda.id
    modelo = PreparoModelo(grupo_id=clinica_id, nome="Preparo Teste Medico Filial", instrucoes="Jejum de 4 horas.")
    db.session.add(modelo)
    db.session.commit()
    modelo_id = modelo.id

# Carlos atende em duas clínicas (Vitória e SP) - precisa selecionar qual
# filial está usando nesta sessão antes de mexer em exames.
login("medico@clinicavitoria.com", "123456")
client.post("/equipe/clinica", data={"clinica_id": str(clinica_id)}, follow_redirects=True)

# Carlos cadastra um exame genérico (catálogo, sem médico/filial confirmados).
client.post("/equipe/exames/novo", data={
    "nome": "Espirometria Teste", "descricao": "Espirometria", "duracao_minutos": "20",
    "preparo_modelo_id": str(modelo_id),
}, follow_redirects=True)

r = client.get("/equipe/exames/por-filial")
html = r.get_data(as_text=True)
checar("Tela responde 200 para o médico", r.status_code == 200)
checar(
    "O select de médico mostra só o próprio médico logado (Carlos), não a colega Fernanda",
    f'value="{carlos_id}"' in html and f'value="{fernanda_id}"' not in html,
)

# Associar escolhendo a si mesmo funciona normalmente.
r2 = client.post("/equipe/exames/por-filial/associar", data={
    "nome": "Espirometria Teste", "medico_id": str(carlos_id), "preco": "120,00",
}, follow_redirects=True)
checar("Associar escolhendo a si mesmo funciona", "associado com" in r2.get_data(as_text=True))

# Tentar forçar outro médico via POST direto (contornando o HTML) é
# bloqueado no servidor, não só escondido no dropdown.
with app.app_context():
    modelo2 = PreparoModelo(grupo_id=clinica_id, nome="Preparo Teste Medico Filial 2", instrucoes="Sem preparo.")
    db.session.add(modelo2)
    db.session.commit()
    modelo2_id = modelo2.id
client.post("/equipe/exames/novo", data={
    "nome": "Espirometria Teste 2", "descricao": "Espirometria", "duracao_minutos": "20",
    "preparo_modelo_id": str(modelo2_id),
}, follow_redirects=True)
r3 = client.post("/equipe/exames/por-filial/associar", data={
    "nome": "Espirometria Teste 2", "medico_id": str(fernanda_id), "preco": "120,00",
}, follow_redirects=True)
checar(
    "Servidor bloqueia médico tentando se associar como outro médico, mesmo via POST direto",
    "só pode se associar como responsável pelos seus próprios exames" in r3.get_data(as_text=True),
)
with app.app_context():
    checar(
        "Exame NÃO foi associado com a Fernanda como responsável",
        Exame.query.filter_by(grupo_id=clinica_id, nome="Espirometria Teste 2", associado=True).first() is None,
    )

client.get("/logout")

# A secretária continua vendo todos os médicos normalmente (não é dona de
# exame nenhum, então precisa escolher entre todos).
login("secretaria@clinicavitoria.com", "123456")
r4 = client.get("/equipe/exames/por-filial")
html4 = r4.get_data(as_text=True)
checar(
    "Secretária continua vendo os dois médicos no select",
    f'value="{carlos_id}"' in html4 and f'value="{fernanda_id}"' in html4,
)
client.get("/logout")

print("\nTodos os testes de 'médico só se vê a si mesmo em Associar exames' passaram.")
