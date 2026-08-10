"""Testa a nova tela "Editar" de um membro da equipe (medico.equipe_editar),
acessível pelo botão "Editar" na lista de Equipe - antes não existia
nenhum jeito de editar o nome de uma pessoa ou de ajustar em quais filiais
ela atua todas de uma vez (só dava pra "+ Associar a outra filial" uma de
cada vez, e só quando havia alguma filial ainda não vinculada). Usa o
seed's Grupo Saúde Total (filiais Centro e Praia, mesma empresa)."""
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
    centro_id, praia_id, medico_id = centro.id, praia.id, medico_grupo.id
    checar(
        "Médico do seed já está vinculado às duas filiais (pré-condição)",
        ClinicaMembro.query.filter_by(usuario_id=medico_id).count() == 2,
    )

login("secretaria@gruposaude.com", "123456")
client.post("/equipe/clinica", data={"clinica_id": str(centro_id)}, follow_redirects=True)

# A lista de Equipe agora tem um botão "Editar" para cada pessoa.
r0 = client.get("/equipe/equipe-membros")
html0 = r0.get_data(as_text=True)
checar("Lista de equipe responde 200", r0.status_code == 200)
checar("Tem um link para editar o médico", f'/equipe/equipe-membros/{medico_id}/editar' in html0)

# Tela de edição mostra o nome atual, e-mail fixo e as duas filiais marcadas.
r1 = client.get(f"/equipe/equipe-membros/{medico_id}/editar")
html1 = r1.get_data(as_text=True)
checar("Tela de editar responde 200", r1.status_code == 200)
checar("Mostra o nome atual no campo", 'value="Dr. Eduardo Nunes"' in html1)
checar("E-mail aparece mas é somente leitura", "medico@gruposaude.com" in html1 and "readonly" in html1)
checar("Checkbox da Centro vem marcado", f'value="{centro_id}" id="filial_{centro_id}"\n          checked' in html1 or f'id="filial_{centro_id}"\n          checked' in html1)

# Edita o nome e DESMARCA a filial Praia (deixando só Centro).
r2 = client.post(f"/equipe/equipe-membros/{medico_id}/editar", data={
    "nome": "Dr. Eduardo Nunes Filho", "filial_ids": [str(centro_id)],
}, follow_redirects=True)
checar("Salvar edição responde 200", r2.status_code == 200)
checar("Mensagem de sucesso aparece", "atualizados" in r2.get_data(as_text=True))

with app.app_context():
    medico_atualizado = Usuario.query.get(medico_id)
    checar("Nome foi atualizado", medico_atualizado.nome == "Dr. Eduardo Nunes Filho")
    checar(
        "Vínculo com a Praia foi removido (ficou só com o Centro)",
        ClinicaMembro.query.filter_by(usuario_id=medico_id).count() == 1,
    )
    checar(
        "O vínculo restante é o do Centro",
        ClinicaMembro.query.filter_by(usuario_id=medico_id).first().clinica_id == centro_id,
    )

# Reassocia a Praia marcando as duas de novo.
r3 = client.post(f"/equipe/equipe-membros/{medico_id}/editar", data={
    "nome": "Dr. Eduardo Nunes Filho", "filial_ids": [str(centro_id), str(praia_id)],
}, follow_redirects=True)
checar("Readicionar a Praia responde 200", r3.status_code == 200)
with app.app_context():
    checar(
        "Voltou a ter os dois vínculos (Centro e Praia)",
        ClinicaMembro.query.filter_by(usuario_id=medico_id).count() == 2,
    )

# Desmarcar TODAS as filiais é bloqueado (não pode ficar sem nenhuma).
r4 = client.post(f"/equipe/equipe-membros/{medico_id}/editar", data={
    "nome": "Dr. Eduardo Nunes Filho",
}, follow_redirects=True)
checar("Desmarcar todas as filiais mostra aviso", "Marque pelo menos uma filial" in r4.get_data(as_text=True))
with app.app_context():
    checar(
        "Não removeu nenhum vínculo quando a validação bloqueou o salvamento",
        ClinicaMembro.query.filter_by(usuario_id=medico_id).count() == 2,
    )

# Nome vazio também é bloqueado.
r5 = client.post(f"/equipe/equipe-membros/{medico_id}/editar", data={
    "nome": "", "filial_ids": [str(centro_id)],
}, follow_redirects=True)
checar("Nome vazio mostra aviso", "Informe o nome" in r5.get_data(as_text=True))
with app.app_context():
    checar("Nome não foi apagado", Usuario.query.get(medico_id).nome == "Dr. Eduardo Nunes Filho")

client.get("/logout")
print("\nTodos os testes da tela de editar equipe passaram.")
