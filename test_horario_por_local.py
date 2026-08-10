"""Testa que o horário de atendimento do médico agora pode ser escolhido
direto na tela de "Horário de atendimento", sem precisar trocar de filial
no menu superior primeiro - e que cada local continua com horário
independente (usa o médico que já atende 2 filiais no seed: Grupo Saúde
Total, filiais Centro e Praia)."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Clinica, MedicoHorario

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


with app.app_context():
    medico_grupo = Usuario.query.filter_by(email="medico@gruposaude.com").first()
    filial_centro = Clinica.query.filter_by(nome="Grupo Saúde Total - Centro").first()
    filial_praia = Clinica.query.filter_by(nome="Grupo Saúde Total - Praia").first()
    medico_id = medico_grupo.id
    centro_id = filial_centro.id
    praia_id = filial_praia.id

login("secretaria@gruposaude.com", "123456")
client.post("/equipe/clinica", data={"clinica_id": str(centro_id)}, follow_redirects=True)

# A tela mostra o seletor de local, listando as duas filiais do médico.
r = client.get(f"/equipe/medico-horarios/{medico_id}")
html = r.get_data(as_text=True)
checar("Tela de horário mostra o seletor 'Local de atendimento'", "Local de atendimento" in html)
checar("Seletor lista a filial Centro", "Grupo Saúde Total - Centro" in html)
checar("Seletor lista a filial Praia", "Grupo Saúde Total - Praia" in html)

# Salva um horário de segunda-feira para o Centro (clinica atual, sem precisar de ?clinica_id).
r = client.post(f"/equipe/medico-horarios/{medico_id}", data={
    "clinica_id": str(centro_id),
    "dia_0_ativo": "on", "dia_0_inicio": "08:00", "dia_0_fim": "12:00",
}, follow_redirects=True)
checar("Salvar horário do Centro responde 200", r.status_code == 200)

# Escolhe a Praia diretamente pela tela (via ?clinica_id), SEM trocar de filial no menu, e salva outro horário.
r = client.post(f"/equipe/medico-horarios/{medico_id}?clinica_id={praia_id}", data={
    "clinica_id": str(praia_id),
    "dia_0_ativo": "on", "dia_0_inicio": "14:00", "dia_0_fim": "18:00",
}, follow_redirects=True)
checar("Salvar horário da Praia (escolhido na própria tela) responde 200", r.status_code == 200)

with app.app_context():
    horario_centro = MedicoHorario.query.filter_by(clinica_id=centro_id, medico_id=medico_id, dia_semana=0).first()
    horario_praia = MedicoHorario.query.filter_by(clinica_id=praia_id, medico_id=medico_id, dia_semana=0).first()
    checar("Horário do Centro foi salvo (08:00-12:00)", horario_centro and horario_centro.hora_inicio.strftime("%H:%M") == "08:00")
    checar("Horário da Praia foi salvo (14:00-18:00), independente do Centro", horario_praia and horario_praia.hora_inicio.strftime("%H:%M") == "14:00")
    checar("Os dois horários são registros distintos (não sobrescreveram um ao outro)", horario_centro.id != horario_praia.id)

# Ao visualizar a tela filtrando pela Praia via querystring, mostra o horário certo (14:00), não o do Centro.
r = client.get(f"/equipe/medico-horarios/{medico_id}?clinica_id={praia_id}")
html = r.get_data(as_text=True)
checar("Tela filtrada pela Praia mostra o horário 14:00 (não o 08:00 do Centro)", 'value="14:00"' in html)

client.get("/logout")
print("\nTodos os testes de horário por local de atendimento passaram.")
