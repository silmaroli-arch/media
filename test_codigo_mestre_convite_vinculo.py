"""Testa a fase 1 do "código mestre do médico":

- Todo médico tem um código mestre (formato MED-XXXXX): contas novas nascem
  com ele; contas antigas ganham um na primeira visita ao painel (e o
  painel mostra o código, com botão de copiar e de regenerar).
- Secretária NÃO tem código (é coisa de médico).
- "Vincular médico por código" (tela Equipe) NÃO vincula na hora: cria um
  CONVITE pendente que o médico aceita ou recusa no painel dele -
  consentimento nas duas pontas.
- Validações: código inexistente, médico já vinculado, convite repetido.
- A clínica pode cancelar um convite pendente.
- Aceitar cria o ClinicaMembro (inclusive entre EMPRESAS diferentes - o
  caso central do redesenho); recusar não cria nada.
- Revinculação: se existia um vínculo desativado, aceitar REATIVA o mesmo
  registro (histórico preservado, sem duplicar).
- Regenerar o código invalida o antigo para novos convites.
"""
from app import create_app
from app.extensions import db
from app.models import Usuario, Clinica, ClinicaMembro, ConviteVinculo

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha="123456"):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


with app.app_context():
    vitoria = Clinica.query.filter_by(nome="Clínica Vitória").first()
    vitoria_id = vitoria.id
    centro = Clinica.query.filter_by(nome="Grupo Saúde Total - Centro").first()
    centro_id = centro.id
    eduardo = Usuario.query.filter_by(email="medico@gruposaude.com").first()
    eduardo_id = eduardo.id
    carlos = Usuario.query.filter_by(email="medico@clinicavitoria.com").first()
    checar("Médico do seed (antes da coluna) ainda não tem código", eduardo.codigo_mestre is None)

# ---------- O médico ganha/vê o código no painel ----------

login("medico@gruposaude.com")
r = client.get("/equipe/")
html = r.get_data(as_text=True)
with app.app_context():
    eduardo = Usuario.query.get(eduardo_id)
    codigo_eduardo = eduardo.codigo_mestre
checar("Conta antiga de médico ganha o código na primeira visita ao painel",
       codigo_eduardo is not None and codigo_eduardo.startswith("MED-") and len(codigo_eduardo) == 9)
checar("O painel mostra o código ('Meu código de médico')",
       "Meu código de médico" in html and codigo_eduardo in html)
client.get("/logout")

# Secretária não tem código nem vê o cartão.
login("secretaria@gruposaude.com")
r = client.get("/equipe/")
checar("Secretária não vê o cartão de código", "Meu código de médico" not in r.get_data(as_text=True))
with app.app_context():
    checar("Secretária não ganha código",
           Usuario.query.filter_by(email="secretaria@gruposaude.com").first().codigo_mestre is None)

# A clínica NÃO cria médico: o formulário de equipe barra e orienta o código.
r = client.post("/equipe/equipe-membros/novo", data={
    "nome": "Dra. Nova Com Codigo", "email": "novamedica@gruposaude.com",
    "papel": "medico", "senha": "123456", "filial_ids": [str(centro_id)],
}, follow_redirects=True)
checar("Equipe não cadastra médico (orienta o código)",
       "Vincular médico por código" in r.get_data(as_text=True))
with app.app_context():
    checar("Nenhuma conta de médico foi criada pela equipe",
           Usuario.query.filter_by(email="novamedica@gruposaude.com").first() is None)
client.get("/logout")

# Médico nasce com código quando cria a PRÓPRIA conta (cadastro público).
r = client.post("/cadastro", data={
    "modo": "independente", "nome": "Dra. Nova Com Codigo",
    "email": "novamedica@gruposaude.com", "senha": "123456",
}, follow_redirects=True)
with app.app_context():
    nova = Usuario.query.filter_by(email="novamedica@gruposaude.com").first()
    checar("Médico que cria a própria conta já nasce com código mestre",
           nova is not None and nova.tipo == "medico"
           and nova.codigo_mestre and nova.codigo_mestre.startswith("MED-"))
client.get("/logout")

# ---------- A Clínica Vitória convida o Eduardo pelo código ----------

login("secretaria@clinicavitoria.com")

# Código inexistente é recusado com mensagem clara.
r = client.post("/equipe/equipe-membros/vincular-por-codigo", data={
    "codigo_mestre": "MED-ZZZZZ", "filial_ids": [str(vitoria_id)],
}, follow_redirects=True)
checar("Código inexistente dá erro amigável",
       "Nenhum médico encontrado com esse código" in r.get_data(as_text=True))

# Médico que JÁ atende na filial não é convidado de novo.
with app.app_context():
    carlos = Usuario.query.filter_by(email="medico@clinicavitoria.com").first()
    if not carlos.codigo_mestre:
        from app.models import gerar_codigo_mestre_medico
        carlos.codigo_mestre = gerar_codigo_mestre_medico()
        db.session.commit()
    codigo_carlos = carlos.codigo_mestre
r = client.post("/equipe/equipe-membros/vincular-por-codigo", data={
    "codigo_mestre": codigo_carlos, "filial_ids": [str(vitoria_id)],
}, follow_redirects=True)
checar("Médico já vinculado: aviso 'já atende'", "já atende em" in r.get_data(as_text=True))
with app.app_context():
    checar("E nenhum convite é criado pra ele",
           ConviteVinculo.query.filter_by(medico_id=carlos.id).count() == 0)

# Convite de verdade: Eduardo (de OUTRA empresa) é convidado.
r = client.post("/equipe/equipe-membros/vincular-por-codigo", data={
    "codigo_mestre": codigo_eduardo.lower(),  # aceita minúsculas também
    "filial_ids": [str(vitoria_id)],
}, follow_redirects=True)
html = r.get_data(as_text=True)
checar("Convite enviado com mensagem explicando o aceite",
       "Convite enviado" in html and "depois que o médico aceitar" in html)
with app.app_context():
    convite = ConviteVinculo.query.filter_by(medico_id=eduardo_id, clinica_id=vitoria_id).first()
    checar("Convite criado como pendente", convite is not None and convite.status == "pendente")
    checar("NENHUM vínculo foi criado ainda (só o convite)",
           ClinicaMembro.query.filter_by(usuario_id=eduardo_id, clinica_id=vitoria_id).first() is None)
    convite_id = convite.id

# Repetir o convite não duplica.
r = client.post("/equipe/equipe-membros/vincular-por-codigo", data={
    "codigo_mestre": codigo_eduardo, "filial_ids": [str(vitoria_id)],
}, follow_redirects=True)
checar("Convite repetido: aviso de pendente, sem duplicar",
       "convite pendente" in r.get_data(as_text=True))
with app.app_context():
    checar("Continua existindo UM convite só",
           ConviteVinculo.query.filter_by(medico_id=eduardo_id, clinica_id=vitoria_id).count() == 1)

# A tela Equipe mostra o convite aguardando (e permite cancelar).
r = client.get("/equipe/equipe-membros")
html = r.get_data(as_text=True)
checar("Tela Equipe tem o formulário 'Vincular médico por código'",
       "Vincular médico por código" in html)
checar("Tela Equipe mostra o convite aguardando o médico",
       "Convites aguardando o médico aceitar" in html and "Dr. Eduardo Nunes" in html)

r = client.post(f"/equipe/equipe-membros/convites/{convite_id}/cancelar", follow_redirects=True)
checar("Cancelar convite funciona", "cancelado" in r.get_data(as_text=True))
with app.app_context():
    checar("Convite fica como 'cancelado' (histórico, não é apagado)",
           ConviteVinculo.query.get(convite_id).status == "cancelado")

# Cria de novo (dessa vez pro Eduardo decidir) + um segundo pra recusa.
client.post("/equipe/equipe-membros/vincular-por-codigo", data={
    "codigo_mestre": codigo_eduardo, "filial_ids": [str(vitoria_id)],
}, follow_redirects=True)
with app.app_context():
    convite2_id = ConviteVinculo.query.filter_by(
        medico_id=eduardo_id, clinica_id=vitoria_id, status="pendente").first().id
client.get("/logout")

# ---------- O Eduardo decide no painel dele ----------

login("medico@gruposaude.com")
r = client.get("/equipe/")
html = r.get_data(as_text=True)
checar("Painel do médico mostra o convite da Clínica Vitória",
       "Convites para atender em novas clínicas" in html and "Clínica Vitória" in html)

# Outro médico não consegue decidir o convite que não é dele.
client.get("/logout")
login("novamedica@gruposaude.com")
r = client.post(f"/equipe/convites/{convite2_id}/decidir", data={"acao": "aceitar"})
checar("Outro médico não decide convite alheio (404)", r.status_code == 404)
client.get("/logout")

# Recusar: nada de vínculo.
login("medico@gruposaude.com")
r = client.post(f"/equipe/convites/{convite2_id}/decidir", data={"acao": "recusar"}, follow_redirects=True)
checar("Recusar mostra confirmação", "recusado" in r.get_data(as_text=True))
with app.app_context():
    checar("Recusa não cria vínculo",
           ClinicaMembro.query.filter_by(usuario_id=eduardo_id, clinica_id=vitoria_id).first() is None)
    checar("Convite guarda a decisão ('recusado')",
           ConviteVinculo.query.get(convite2_id).status == "recusado")
client.get("/logout")

# Novo convite → ACEITAR cria o vínculo (entre empresas diferentes!).
login("secretaria@clinicavitoria.com")
client.post("/equipe/equipe-membros/vincular-por-codigo", data={
    "codigo_mestre": codigo_eduardo, "filial_ids": [str(vitoria_id)],
}, follow_redirects=True)
with app.app_context():
    convite3_id = ConviteVinculo.query.filter_by(
        medico_id=eduardo_id, clinica_id=vitoria_id, status="pendente").first().id
client.get("/logout")

login("medico@gruposaude.com")
r = client.post(f"/equipe/convites/{convite3_id}/decidir", data={"acao": "aceitar"}, follow_redirects=True)
checar("Aceitar mostra 'Você agora atende em...'", "Você agora atende em" in r.get_data(as_text=True))
with app.app_context():
    vinculo = ClinicaMembro.query.filter_by(usuario_id=eduardo_id, clinica_id=vitoria_id).first()
    checar("Aceite criou o vínculo (médico de uma empresa atendendo em outra)",
           vinculo is not None and vinculo.ativo)
    vinculo_id = vinculo.id
client.get("/logout")

# A Clínica Vitória agora vê o Eduardo na equipe dela.
login("secretaria@clinicavitoria.com")
r = client.get("/equipe/equipe-membros")
checar("Eduardo aparece na equipe da Clínica Vitória depois do aceite",
       "Dr. Eduardo Nunes" in r.get_data(as_text=True))
client.get("/logout")

# ---------- Revinculação reativa o MESMO vínculo (sem duplicar) ----------

with app.app_context():
    v = ClinicaMembro.query.get(vinculo_id)
    v.ativo = False  # simulando "parou de atender lá" (fase de desvinculação)
    db.session.commit()

login("secretaria@clinicavitoria.com")
client.post("/equipe/equipe-membros/vincular-por-codigo", data={
    "codigo_mestre": codigo_eduardo, "filial_ids": [str(vitoria_id)],
}, follow_redirects=True)
with app.app_context():
    convite4 = ConviteVinculo.query.filter_by(
        medico_id=eduardo_id, clinica_id=vitoria_id, status="pendente").first()
    checar("Vínculo desativado permite novo convite", convite4 is not None)
    convite4_id = convite4.id
client.get("/logout")

login("medico@gruposaude.com")
client.post(f"/equipe/convites/{convite4_id}/decidir", data={"acao": "aceitar"}, follow_redirects=True)
with app.app_context():
    vinculos = ClinicaMembro.query.filter_by(usuario_id=eduardo_id, clinica_id=vitoria_id).all()
    checar("Aceitar de novo REATIVA o mesmo vínculo (um registro só, ativo)",
           len(vinculos) == 1 and vinculos[0].ativo and vinculos[0].id == vinculo_id)

# ---------- Regenerar o código invalida o antigo ----------

r = client.post("/equipe/meu-codigo/regenerar", follow_redirects=True)
checar("Regenerar mostra o código novo", "Novo código gerado" in r.get_data(as_text=True))
with app.app_context():
    codigo_novo = Usuario.query.get(eduardo_id).codigo_mestre
    checar("O código realmente mudou", codigo_novo != codigo_eduardo and codigo_novo.startswith("MED-"))
client.get("/logout")

login("secretaria@clinicavitoria.com")
r = client.post("/equipe/equipe-membros/vincular-por-codigo", data={
    "codigo_mestre": codigo_eduardo, "filial_ids": [str(vitoria_id)],
}, follow_redirects=True)
checar("O código ANTIGO deixa de funcionar",
       "Nenhum médico encontrado com esse código" in r.get_data(as_text=True))
client.get("/logout")

# Secretária não regenera código (não é médico).
login("secretaria@gruposaude.com")
r = client.post("/equipe/meu-codigo/regenerar", follow_redirects=True)
checar("Secretária não tem código pra regenerar",
       "Só contas de médico" in r.get_data(as_text=True))
client.get("/logout")

print("\nTodos os testes de código mestre + convite de vínculo passaram.")
