"""Testa que o formulário "Adicionar médico ou secretária" (medico.equipe_novo)
agora permite marcar mais de uma filial de uma vez (checkboxes filial_ids),
em vez do combobox de seleção única de antes - que obrigava recadastrar a
mesma pessoa para cada filial extra. Usa o seed's Grupo Saúde Total (filiais
Centro e Praia, mesma empresa) e Clínica Vitória (empresa separada, para
testar a criação de conta nova em duas filiais de uma vez)."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Clinica, ClinicaMembro

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


with app.app_context():
    centro = Clinica.query.filter_by(nome="Grupo Saúde Total - Centro").first()
    praia = Clinica.query.filter_by(nome="Grupo Saúde Total - Praia").first()
    medico_grupo = Usuario.query.filter_by(email="medico@gruposaude.com").first()
    centro_id, praia_id, medico_grupo_id = centro.id, praia.id, medico_grupo.id
    checar(
        "Médico do seed já está vinculado às duas filiais do Grupo Saúde Total (pré-condição)",
        ClinicaMembro.query.filter_by(usuario_id=medico_grupo_id).count() == 2,
    )

login("secretaria@gruposaude.com", "123456")
client.post("/equipe/clinica", data={"clinica_id": str(centro_id)}, follow_redirects=True)

# O formulário agora mostra checkboxes (filial_ids) para cada filial, não mais
# um único combobox (filial_id).
r0 = client.get("/equipe/equipe-membros/novo")
html0 = r0.get_data(as_text=True)
checar("Formulário responde 200", r0.status_code == 200)
checar("Não usa mais o combobox de seleção única", 'name="filial_id"' not in html0)
checar("Tem um checkbox para a filial Centro", f'name="filial_ids" value="{centro_id}"' in html0)
checar("Tem um checkbox para a filial Praia", f'name="filial_ids" value="{praia_id}"' in html0)

# Não marcar nenhuma filial é bloqueado com aviso, sem criar nada.
r1 = client.post("/equipe/equipe-membros/novo", data={
    "nome": "Sem Filial", "email": "semfilial@gruposaude.com", "papel": "secretaria",
}, follow_redirects=True)
checar("Sem marcar filial mostra aviso", "Escolha em qual" in r1.get_data(as_text=True))
with app.app_context():
    checar(
        "Não criou conta nenhuma quando não marcou filial",
        Usuario.query.filter_by(email="semfilial@gruposaude.com").first() is None,
    )

# Cadastra uma pessoa NOVA marcando as DUAS filiais de uma vez.
r2 = client.post("/equipe/equipe-membros/novo", data={
    "nome": "Juliana Prado", "email": "juliana.prado@gruposaude.com", "papel": "secretaria",
    "filial_ids": [str(centro_id), str(praia_id)],
}, follow_redirects=True)
checar("Cadastro de conta nova com 2 filiais responde 200", r2.status_code == 200)
html2 = r2.get_data(as_text=True)
checar("Mensagem de sucesso cita as duas filiais", "Centro" in html2 and "Praia" in html2)

with app.app_context():
    nova = Usuario.query.filter_by(email="juliana.prado@gruposaude.com").first()
    checar("Conta nova foi criada", nova is not None)
    vinculos = ClinicaMembro.query.filter_by(usuario_id=nova.id).all()
    checar("Criou exatamente 2 vínculos (um por filial marcada)", len(vinculos) == 2)
    vinculadas_ids = {v.clinica_id for v in vinculos}
    checar("Vínculos são exatamente Centro e Praia", vinculadas_ids == {centro_id, praia_id})

# Vincular um e-mail JÁ EXISTENTE (o médico do seed) a filiais marcadas onde ele já
# está em ambas -> deve avisar "já faz parte de todas" e não duplicar vínculos.
r3 = client.post("/equipe/equipe-membros/novo", data={
    "email": "medico@gruposaude.com", "papel": "medico",
    "filial_ids": [str(centro_id), str(praia_id)],
}, follow_redirects=True)
checar(
    "Marcar filiais onde a pessoa já está em todas mostra aviso específico",
    "já faz parte de todas as filiais marcadas" in r3.get_data(as_text=True),
)
with app.app_context():
    checar(
        "Não duplicou nenhum vínculo do médico do seed",
        ClinicaMembro.query.filter_by(usuario_id=medico_grupo_id).count() == 2,
    )

client.get("/logout")

# Cenário de e-mail existente com vínculo PARCIAL: cadastra uma pessoa nova só
# na Praia e depois reenvia o mesmo e-mail marcando as duas filiais - só a
# filial nova (Centro) deve ser adicionada, sem duplicar o vínculo da Praia.
login("secretaria@gruposaude.com", "123456")
client.post("/equipe/clinica", data={"clinica_id": str(centro_id)}, follow_redirects=True)
r4 = client.post("/equipe/equipe-membros/novo", data={
    "nome": "Rafael Souza", "email": "rafael.souza@gruposaude.com", "papel": "secretaria",
    "filial_ids": [str(praia_id)],
}, follow_redirects=True)
checar("Cadastro inicial do Rafael só na Praia responde 200", r4.status_code == 200)
with app.app_context():
    rafael = Usuario.query.filter_by(email="rafael.souza@gruposaude.com").first()
    checar("Rafael tem exatamente 1 vínculo (Praia)", ClinicaMembro.query.filter_by(usuario_id=rafael.id).count() == 1)
    rafael_id = rafael.id

# Reenvia o mesmo e-mail marcando as DUAS filiais - só a nova (Centro) deve
# ser adicionada, sem duplicar o vínculo já existente com a Praia.
r5 = client.post("/equipe/equipe-membros/novo", data={
    "email": "rafael.souza@gruposaude.com", "papel": "secretaria",
    "filial_ids": [str(centro_id), str(praia_id)],
}, follow_redirects=True)
checar("Vincular filial adicional a conta existente responde 200", r5.status_code == 200)
html5 = r5.get_data(as_text=True)
checar("Mensagem de sucesso cita só a filial nova (Centro)", "vinculado" in html5 and "Centro" in html5)

with app.app_context():
    vinculos_rafael = ClinicaMembro.query.filter_by(usuario_id=rafael_id).all()
    checar("Rafael agora tem exatamente 2 vínculos (Centro + Praia, sem duplicar a Praia)", len(vinculos_rafael) == 2)
    ids_rafael = {v.clinica_id for v in vinculos_rafael}
    checar("Vínculos do Rafael são Centro e Praia", ids_rafael == {centro_id, praia_id})

client.get("/logout")
print("\nTodos os testes de múltiplas filiais no cadastro de equipe passaram.")
