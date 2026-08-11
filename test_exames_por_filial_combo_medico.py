"""Testa a tela "Exames por filial" redesenhada com o combobox de tipo de
associação ("Exame × Filial" / "Exame × Médico"). O tipo "medico" mostra uma
grade com o médico responsável e os médicos extras de cada exame já
existente em cada filial, e permite criar uma nova associação (marcar um
médico extra) sem mexer em preço. Usa o seed's Grupo Saúde Total (única
empresa do seed com mais de uma filial), com um segundo médico cadastrado
no próprio teste (o seed só tem um)."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Clinica, Exame, PreparoModelo

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
    medico_grupo = Usuario.query.filter_by(email="medico@gruposaude.com").first()
    centro_id, medico_id = centro.id, medico_grupo.id
    modelo = PreparoModelo(clinica_id=centro_id, nome="Preparo Combo Teste", instrucoes="Jejum de 8 horas.")
    db.session.add(modelo)
    db.session.commit()
    modelo_id = modelo.id

login("secretaria@gruposaude.com", "123456")
client.post("/equipe/clinica", data={"clinica_id": str(centro_id)}, follow_redirects=True)

# Cadastra um segundo médico na filial Centro (o seed só tem um médico no Grupo Saúde Total).
client.post("/equipe/equipe-membros/novo", data={
    "nome": "Dr. Segundo Médico", "email": "segundo.medico@gruposaude.com", "papel": "medico",
    "filial_ids": [str(centro_id)],
}, follow_redirects=True)
with app.app_context():
    outro_medico = Usuario.query.filter_by(email="segundo.medico@gruposaude.com").first()
    outro_medico_id = outro_medico.id

# Cadastro genérico do exame (sem filial/médico/preço) e depois define médico/preço na Centro.
client.post("/equipe/exames/novo", data={
    "nome": "Exame Combo Teste", "descricao": "Exame", "duracao_minutos": "20",
    "preparo_modelo_id": str(modelo_id),
}, follow_redirects=True)
with app.app_context():
    exame = Exame.query.filter_by(nome="Exame Combo Teste").first()
    exame_id = exame.id
    exame_preco_original = exame.preco
client.post(f"/equipe/exames/por-filial/{exame_id}/atualizar", data={
    "tipo_origem": "filial", "medico_id": str(medico_id), "preco": "150,00",
}, follow_redirects=True)

# O combobox aparece e o padrão é "filial" (preserva comportamento antigo).
r0 = client.get("/equipe/exames/por-filial")
html0 = r0.get_data(as_text=True)
checar("Tela responde 200 (tipo padrão)", r0.status_code == 200)
checar("Combobox de tipo de associação aparece", "Tipo de associação" in html0)
checar("Opção Exame × Filial aparece", "Exame × Filial" in html0)
checar("Opção Exame × Médico aparece", "Exame × Médico" in html0)
checar("Grade da matriz por filial ainda aparece por padrão", "Ajustar médico/preço nesta filial" in html0)

# tipo=medico mostra a grade de médico responsável + extras, sem os campos de preço.
r1 = client.get("/equipe/exames/por-filial?tipo=medico")
html1 = r1.get_data(as_text=True)
checar("Tela com tipo=medico responde 200", r1.status_code == 200)
checar("Mostra cabeçalho 'Médico responsável'", "Médico responsável" in html1)
checar("Mostra cabeçalho 'Outros médicos (extras)'", "Outros médicos (extras)" in html1)
checar("Mostra o exame na grade de médico", "Exame Combo Teste" in html1)
checar("NÃO mostra campo de preço nesta grade", 'name="preco"' not in html1)
checar("Checkbox do outro médico aparece na grade", f'value="{outro_medico_id}"' in html1)

# Cria a associação Exame × Médico: marca o outro médico como extra.
r2 = client.post(f"/equipe/exames/por-filial/{exame_id}/atualizar", data={
    "tipo_origem": "medico", "atualizar_extras": "1",
    "medico_id": str(medico_id), "medicos_extra_ids": str(outro_medico_id),
}, follow_redirects=True)
checar("Salvar associação de médico extra responde 200", r2.status_code == 200)
checar("Mensagem de sucesso aparece", "atualizado" in r2.get_data(as_text=True))
with app.app_context():
    exame_depois = Exame.query.get(exame_id)
    checar("Médico extra foi associado", any(m.id == outro_medico_id for m in exame_depois.medicos_extra))
    checar("Médico responsável não mudou", exame_depois.medico_id == medico_id)
    checar("Preço não foi alterado (nem enviado)", float(exame_depois.preco) == 150.0)

# Remove a associação: desmarca o checkbox (não envia mais o id na lista).
r3 = client.post(f"/equipe/exames/por-filial/{exame_id}/atualizar", data={
    "tipo_origem": "medico", "atualizar_extras": "1", "medico_id": str(medico_id),
}, follow_redirects=True)
checar("Remover associação de médico extra responde 200", r3.status_code == 200)
with app.app_context():
    exame_final = Exame.query.get(exame_id)
    checar("Médico extra foi removido", not any(m.id == outro_medico_id for m in exame_final.medicos_extra))

# A tela "Exame × Filial" continua funcionando normalmente (sem atualizar_extras),
# sem mexer nos médicos extras mesmo sem enviar o campo.
r4 = client.post(f"/equipe/exames/por-filial/{exame_id}/atualizar", data={
    "tipo_origem": "filial", "medico_id": str(medico_id), "preco": "321,00",
}, follow_redirects=True)
checar("Atualizar pela grade de filial (sem atualizar_extras) responde 200", r4.status_code == 200)
with app.app_context():
    exame_pos_filial = Exame.query.get(exame_id)
    checar("Preço foi atualizado pela grade de filial", float(exame_pos_filial.preco) == 321.0)

client.get("/logout")
print("\nTodos os testes do combobox Exame × Filial / Exame × Médico passaram.")
