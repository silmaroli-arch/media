"""Teste de ponta a ponta do cadastro de modelo de preparo e exame vinculado
a um grupo de trabalho (BBP MedIA, telas 5.1.13/5.1.14 e 5.1.15/5.1.16):
somente usuários do tipo Médico podem cadastrar; cada grupo tem sua própria
"clínica interna" (invisível fora do grupo) usada para satisfazer as
restrições legadas de Exame/PreparoModelo; nomes não podem se repetir dentro
do mesmo grupo; um exame só pode ser criado a partir de um modelo de preparo
que pertença ao próprio médico dentro daquele grupo."""
from app import create_app
from app.extensions import db
from app.models import (
    Usuario, Grupo, GrupoMembro, Empresa, Clinica, ClinicaMembro,
    PreparoModelo, Exame,
)

app = create_app()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


with app.app_context():
    for email in ("medico3.bbp@teste.com", "secretaria3.bbp@teste.com", "medico4.bbp@teste.com"):
        u = Usuario.query.filter_by(email=email).first()
        if u:
            GrupoMembro.query.filter_by(usuario_id=u.id).delete()
            db.session.delete(u)
    db.session.commit()

    medico1 = Usuario(nome="Dr. Rafael Lima", email="medico3.bbp@teste.com", tipo="medico",
                       cpf="567.891.234-82", crm_numero="1111", crm_uf="ES")
    medico1.set_senha("123456")
    medico1.definir_permissoes_padrao()
    secretaria = Usuario(nome="Bianca Alves", email="secretaria3.bbp@teste.com", tipo="secretaria",
                          cpf="678.912.345-82")
    secretaria.set_senha("123456")
    secretaria.definir_permissoes_padrao()
    medico2 = Usuario(nome="Dra. Camila Rocha", email="medico4.bbp@teste.com", tipo="medico",
                       cpf="789.123.456-64", crm_numero="2222", crm_uf="ES")
    medico2.set_senha("123456")
    medico2.definir_permissoes_padrao()
    db.session.add_all([medico1, secretaria, medico2])
    db.session.commit()
    medico1_id, secretaria_id, medico2_id = medico1.id, secretaria.id, medico2.id

    empresa_teste = Empresa.query.filter_by(nome="Empresa Teste BBP").first()
    if not empresa_teste:
        empresa_teste = Empresa(nome="Empresa Teste BBP", status="ativa")
        db.session.add(empresa_teste)
        db.session.commit()
    clinica_teste = Clinica.query.filter_by(nome="Clínica Teste BBP", empresa_id=empresa_teste.id).first()
    if not clinica_teste:
        clinica_teste = Clinica(nome="Clínica Teste BBP", empresa_id=empresa_teste.id)
        db.session.add(clinica_teste)
        db.session.commit()
    for uid in (medico1_id, secretaria_id, medico2_id):
        if not ClinicaMembro.query.filter_by(clinica_id=clinica_teste.id, usuario_id=uid).first():
            db.session.add(ClinicaMembro(clinica_id=clinica_teste.id, usuario_id=uid))
    db.session.commit()


client_medico1 = app.test_client()
client_secretaria = app.test_client()
client_medico2 = app.test_client()


def login(client, cpf, senha):
    return client.post("/login", data={"identificador": cpf, "senha": senha}, follow_redirects=True)


login(client_medico1, "567.891.234-82", "123456")
login(client_secretaria, "678.912.345-82", "123456")
login(client_medico2, "789.123.456-64", "123456")

r = client_medico1.post("/grupos/novo", data={"nome": "Grupo Exames BBP"}, follow_redirects=True)
with app.app_context():
    grupo = Grupo.query.filter_by(nome="Grupo Exames BBP").order_by(Grupo.id.desc()).first()
    grupo_id = grupo.id

r = client_medico1.post(f"/grupos/{grupo_id}/convidar", data={"cpf": "678.912.345-82"}, follow_redirects=True)
with app.app_context():
    from app.models import GrupoConvite
    convite = GrupoConvite.query.filter_by(grupo_id=grupo_id, usuario_convidado_id=secretaria_id).first()
    convite_id = convite.id
client_secretaria.post(f"/grupos/convites/{convite_id}/responder", data={"decisao": "aprovar"}, follow_redirects=True)

r = client_medico1.post(f"/grupos/{grupo_id}/convidar", data={"cpf": "789.123.456-64"}, follow_redirects=True)
with app.app_context():
    convite2 = GrupoConvite.query.filter_by(grupo_id=grupo_id, usuario_convidado_id=medico2_id).first()
    convite2_id = convite2.id
client_medico2.post(f"/grupos/convites/{convite2_id}/responder", data={"decisao": "aprovar"}, follow_redirects=True)


# ---------- Restrição: só médico pode cadastrar modelo de preparo ----------
r = client_secretaria.post(f"/grupos/{grupo_id}/preparo-modelos/novo", data={"nome": "Modelo Secretaria"}, follow_redirects=True)
checar("Secretária não pode cadastrar modelo de preparo", "Somente usuários do tipo Médico" in r.get_data(as_text=True))
with app.app_context():
    checar("Nenhum modelo foi criado pela secretária", PreparoModelo.query.filter_by(nome="Modelo Secretaria").first() is None)

# ---------- Cadastro de modelo de preparo pelo médico 1 ----------
r = client_medico1.get(f"/grupos/{grupo_id}/preparo-modelos/novo")
checar("Tela de cadastro de modelo de preparo carrega para médico", r.status_code == 200)

r = client_medico1.post(f"/grupos/{grupo_id}/preparo-modelos/novo", data={
    "nome": "Preparo Colonoscopia BBP", "instrucoes": "Dieta líquida nas 24h anteriores.",
    "observacoes_medicamentos": "Não suspender AAS.",
}, follow_redirects=True)
checar("Modelo de preparo cadastrado com sucesso", "cadastrado com sucesso" in r.get_data(as_text=True))

with app.app_context():
    grupo_local = Grupo.query.get(grupo_id)
    modelo1 = PreparoModelo.query.filter_by(grupo_id=grupo_local.id, nome="Preparo Colonoscopia BBP").first()
    checar("Modelo de preparo foi criado com o grupo_id do grupo", modelo1 is not None)
    modelo1_id = modelo1.id

r = client_medico1.get(f"/grupos/{grupo_id}/preparo-modelos")
checar("Modelo de preparo aparece na lista do grupo", "Preparo Colonoscopia BBP" in r.get_data(as_text=True))

# ---------- Nome duplicado no mesmo grupo é rejeitado ----------
r = client_medico2.post(f"/grupos/{grupo_id}/preparo-modelos/novo", data={
    "nome": "Preparo Colonoscopia BBP", "instrucoes": "Outra instrução qualquer.",
}, follow_redirects=True)
checar("Nome de modelo de preparo duplicado no grupo é rejeitado", "Já existe um modelo de preparo chamado" in r.get_data(as_text=True))

# ---------- Cadastro de um segundo modelo, pelo médico 2 ----------
r = client_medico2.post(f"/grupos/{grupo_id}/preparo-modelos/novo", data={
    "nome": "Preparo Endoscopia BBP", "instrucoes": "Jejum de 8h.",
}, follow_redirects=True)
checar("Segundo médico cadastra seu próprio modelo de preparo no mesmo grupo", "cadastrado com sucesso" in r.get_data(as_text=True))
with app.app_context():
    modelo2 = PreparoModelo.query.filter_by(grupo_id=grupo_local.id, nome="Preparo Endoscopia BBP").first()
    checar("Segundo modelo foi criado no MESMO grupo", modelo2 is not None and modelo2.grupo_id == grupo_local.id)
    modelo2_id = modelo2.id


# ---------- Restrição: só médico pode cadastrar exame ----------
r = client_secretaria.post(f"/grupos/{grupo_id}/exames/novo", data={"nome": "Exame Secretaria"}, follow_redirects=True)
checar("Secretária não pode cadastrar exame", "Somente usuários do tipo Médico" in r.get_data(as_text=True))

# ---------- Exame exige modelo de preparo do PRÓPRIO médico ----------
r = client_medico1.get(f"/grupos/{grupo_id}/exames/novo")
corpo = r.get_data(as_text=True)
checar("Tela de novo exame do médico 1 lista só o modelo dele", "Preparo Colonoscopia BBP" in corpo and "Preparo Endoscopia BBP" not in corpo)

r = client_medico1.post(f"/grupos/{grupo_id}/exames/novo", data={
    "nome": "Colonoscopia BBP", "preparo_modelo_id": str(modelo2_id),
}, follow_redirects=True)
checar("Médico não consegue usar modelo de preparo de outro médico (formulário não aceita o id)", "cadastrado com sucesso" not in r.get_data(as_text=True))
with app.app_context():
    checar("Nenhum exame 'Colonoscopia BBP' foi criado com o modelo errado", Exame.query.filter_by(nome="Colonoscopia BBP").first() is None)

r = client_medico1.post(f"/grupos/{grupo_id}/exames/novo", data={
    "nome": "Colonoscopia BBP", "preparo_modelo_id": str(modelo1_id),
    "duracao_minutos": "40", "preco": "350.00",
}, follow_redirects=True)
checar("Exame cadastrado com sucesso usando o modelo de preparo do próprio médico", "cadastrado com sucesso" in r.get_data(as_text=True))

with app.app_context():
    exame1 = Exame.query.filter_by(grupo_id=grupo_local.id, nome="Colonoscopia BBP").first()
    checar("Exame foi criado com o grupo_id do grupo", exame1 is not None)
    checar("Exame ficou vinculado ao modelo de preparo correto", exame1.preparo_modelo_id == modelo1_id)
    checar("Exame ficou com o médico correto", exame1.medico_id == medico1_id)

r = client_medico2.get(f"/grupos/{grupo_id}/exames")
checar("Exame do médico 1 aparece na lista de exames do grupo (visível a todo o grupo)", "Colonoscopia BBP" in r.get_data(as_text=True))

# ---------- Nome de exame duplicado no mesmo grupo é rejeitado ----------
r = client_medico2.post(f"/grupos/{grupo_id}/exames/novo", data={
    "nome": "Colonoscopia BBP", "preparo_modelo_id": str(modelo2_id),
}, follow_redirects=True)
checar("Nome de exame duplicado no grupo é rejeitado", "Já existe um exame chamado" in r.get_data(as_text=True))

# ---------- Médico sem modelo de preparo próprio é orientado a cadastrar um antes ----------
with app.app_context():
    u = Usuario.query.filter_by(email="medico5.bbp@teste.com").first()
    if u:
        GrupoMembro.query.filter_by(usuario_id=u.id).delete()
        db.session.delete(u)
        db.session.commit()
    medico3 = Usuario(nome="Dr. Bruno Tavares", email="medico5.bbp@teste.com", tipo="medico",
                       cpf="890.123.456-42", crm_numero="3333", crm_uf="ES")
    medico3.set_senha("123456")
    medico3.definir_permissoes_padrao()
    db.session.add(medico3)
    db.session.commit()
    medico3_id = medico3.id
    clinica_teste_local = Clinica.query.filter_by(nome="Clínica Teste BBP").first()
    if not ClinicaMembro.query.filter_by(clinica_id=clinica_teste_local.id, usuario_id=medico3_id).first():
        db.session.add(ClinicaMembro(clinica_id=clinica_teste_local.id, usuario_id=medico3_id))
        db.session.commit()

client_medico3 = app.test_client()
login(client_medico3, "890.123.456-42", "123456")
r = client_medico1.post(f"/grupos/{grupo_id}/convidar", data={"cpf": "890.123.456-42"}, follow_redirects=True)
with app.app_context():
    convite3 = GrupoConvite.query.filter_by(grupo_id=grupo_id, usuario_convidado_id=medico3_id).first()
    convite3_id = convite3.id
client_medico3.post(f"/grupos/convites/{convite3_id}/responder", data={"decisao": "aprovar"}, follow_redirects=True)

r = client_medico3.post(f"/grupos/{grupo_id}/exames/novo", data={"nome": "Exame Sem Modelo"}, follow_redirects=True)
checar("Médico sem modelo de preparo próprio é redirecionado para cadastrar um antes", "Cadastre ao menos um modelo de preparo seu" in r.get_data(as_text=True))
with app.app_context():
    checar("Nenhum exame 'Exame Sem Modelo' foi criado", Exame.query.filter_by(nome="Exame Sem Modelo").first() is None)

print("\nTodas as verificações do fluxo de modelo de preparo e exame por grupo passaram.")
