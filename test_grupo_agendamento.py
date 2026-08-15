"""Teste de ponta a ponta do agendamento de consulta vinculado a um grupo de
trabalho, com cálculo automático do cronograma de preparo (BBP MedIA, telas
5.1.17-5.1.19): qualquer membro ativo do grupo pode agendar uma consulta
para um paciente do grupo usando um dos exames do grupo; o cronograma
(cortes de alimentação/líquido) é calculado automaticamente a partir do
horário escolhido, tanto na visão da equipe (grupo.agenda_detalhe) quanto na
visão do próprio paciente (paciente.preparo_exame, já existente e não
alterada)."""
from datetime import datetime, timedelta

from app import create_app
from app.extensions import db
from app.models import (
    Usuario, Grupo, GrupoMembro, GrupoPaciente, Paciente,
    Empresa, Clinica, ClinicaMembro, PreparoModelo, PreparoCorte, Exame, Agendamento,
)

app = create_app()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


with app.app_context():
    for email in ("medico6.bbp@teste.com", "secretaria6.bbp@teste.com"):
        u = Usuario.query.filter_by(email=email).first()
        if u:
            GrupoMembro.query.filter_by(usuario_id=u.id).delete()
            db.session.delete(u)
    for cpf in ("901.234.567-70",):
        for p in Paciente.query.filter_by(cpf=cpf, empresa_id=None).all():
            GrupoPaciente.query.filter_by(paciente_id=p.id).delete()
            Agendamento.query.filter_by(paciente_id=p.id).delete()
            db.session.delete(p)
    db.session.commit()

    medico = Usuario(nome="Dr. Otávio Nunes", email="medico6.bbp@teste.com", tipo="medico",
                      cpf="123.987.456-19", crm_numero="4444", crm_uf="ES")
    medico.set_senha("123456")
    medico.definir_permissoes_padrao()
    secretaria = Usuario(nome="Marina Costa", email="secretaria6.bbp@teste.com", tipo="secretaria",
                          cpf="234.098.567-65")
    secretaria.set_senha("123456")
    secretaria.definir_permissoes_padrao()
    db.session.add_all([medico, secretaria])
    db.session.commit()
    medico_id, secretaria_id = medico.id, secretaria.id

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
    for uid in (medico_id, secretaria_id):
        if not ClinicaMembro.query.filter_by(clinica_id=clinica_teste.id, usuario_id=uid).first():
            db.session.add(ClinicaMembro(clinica_id=clinica_teste.id, usuario_id=uid))
    db.session.commit()

    paciente = Paciente(
        empresa_id=None, nome="Paciente Agenda BBP", cpf="901.234.567-70",
        telefone="(27) 98888-0000", cep="29000-000", rua="Rua Agenda", numero="1",
        bairro="Centro", cidade="Vitória", uf="ES",
    )
    db.session.add(paciente)
    db.session.commit()
    paciente_id = paciente.id


client_medico = app.test_client()
client_secretaria = app.test_client()


def login(client, cpf, senha):
    return client.post("/login", data={"identificador": cpf, "senha": senha}, follow_redirects=True)


login(client_medico, "123.987.456-19", "123456")
login(client_secretaria, "234.098.567-65", "123456")

r = client_medico.post("/grupos/novo", data={"nome": "Grupo Agenda BBP"}, follow_redirects=True)
with app.app_context():
    grupo = Grupo.query.filter_by(nome="Grupo Agenda BBP").order_by(Grupo.id.desc()).first()
    grupo_id = grupo.id

r = client_medico.post(f"/grupos/{grupo_id}/convidar", data={"cpf": "234.098.567-65"}, follow_redirects=True)
with app.app_context():
    from app.models import GrupoConvite
    convite = GrupoConvite.query.filter_by(grupo_id=grupo_id, usuario_convidado_id=secretaria_id).first()
    convite_id = convite.id
client_secretaria.post(f"/grupos/convites/{convite_id}/responder", data={"decisao": "aprovar"}, follow_redirects=True)

# Associa o paciente ao grupo.
client_medico.post(f"/grupos/{grupo_id}/pacientes/novo", data={
    "etapa": "salvar", "paciente_id": str(paciente_id), "grupos_ids": str(grupo_id),
}, follow_redirects=True)

# ---------- Sem paciente/exame ainda, a tela orienta o usuário ----------
r = client_medico.get(f"/grupos/{grupo_id}/agenda/novo")
checar("Sem exame cadastrado, a tela de agendar orienta a cadastrar um exame primeiro", "Nenhum exame cadastrado" in r.get_data(as_text=True))

# ---------- Modelo de preparo com cortes (cronograma) ----------
r = client_medico.post(f"/grupos/{grupo_id}/preparo-modelos/novo", data={
    "nome": "Preparo Agenda BBP",
    "instrucoes": "Chegar com 30 minutos de antecedência.",
    "corte_descricao[]": ["Jejum de líquidos", "Jejum de sólidos"],
    "corte_horas[]": ["2", "8"],
}, follow_redirects=True)
checar("Modelo de preparo com cortes cadastrado com sucesso", "cadastrado com sucesso" in r.get_data(as_text=True))

with app.app_context():
    grupo_local = Grupo.query.get(grupo_id)
    modelo = PreparoModelo.query.filter_by(clinica_id=grupo_local.clinica_interna_id, nome="Preparo Agenda BBP").first()
    checar("Modelo de preparo foi criado", modelo is not None)
    checar("Os dois cortes foram salvos", PreparoCorte.query.filter_by(preparo_modelo_id=modelo.id).count() == 2)
    modelo_id = modelo.id

# ---------- Exame vinculado ao modelo ----------
r = client_medico.post(f"/grupos/{grupo_id}/exames/novo", data={
    "nome": "Consulta Agenda BBP", "preparo_modelo_id": str(modelo_id),
}, follow_redirects=True)
checar("Exame cadastrado com sucesso", "cadastrado com sucesso" in r.get_data(as_text=True))
with app.app_context():
    grupo_local2 = Grupo.query.filter_by(nome="Grupo Agenda BBP").first()
    exame = Exame.query.filter_by(clinica_id=grupo_local2.clinica_interna_id, nome="Consulta Agenda BBP").first()
    exame_id = exame.id


# ---------- Agendamento pela secretária (membro do grupo, não é o médico) ----------
data_hora_consulta = datetime(2027, 3, 10, 14, 0, 0)
r = client_secretaria.post(f"/grupos/{grupo_id}/agenda/novo", data={
    "paciente_id": str(paciente_id), "exame_id": str(exame_id),
    "data_hora": data_hora_consulta.strftime("%Y-%m-%dT%H:%M"),
}, follow_redirects=True)
checar("Consulta agendada com sucesso pela secretária do grupo", "agendada" in r.get_data(as_text=True))

with app.app_context():
    grupo_local3 = Grupo.query.filter_by(nome="Grupo Agenda BBP").first()
    agendamento = Agendamento.query.filter_by(
        clinica_id=grupo_local3.clinica_interna_id, paciente_id=paciente_id, exame_id=exame_id,
    ).first()
    checar("Agendamento foi criado com a clínica interna do grupo", agendamento is not None)
    checar("Agendamento ficou com o médico do exame", agendamento.medico_id == medico_id)
    agendamento_id = agendamento.id

r = client_secretaria.get(f"/grupos/{grupo_id}/agenda")
corpo = r.get_data(as_text=True)
checar("Consulta aparece na agenda do grupo", "Paciente Agenda BBP" in corpo)
# A lista mostra o corte de MAIS horas de antecedência primeiro (ordenado por
# horas_antes desc, ver PreparoModelo.cortes) — "Jejum de sólidos" (8h antes
# de 14:00 = 06:00), não "Jejum de líquidos" (2h antes = 12:00).
checar("Próximo corte de preparo aparece calculado na lista (8h antes de 14:00 = 06:00)", "06:00" in corpo)

# ---------- Cronograma calculado automaticamente no detalhe ----------
r = client_secretaria.get(f"/grupos/{grupo_id}/agenda/{agendamento_id}")
corpo = r.get_data(as_text=True)
checar("Corte 'Jejum de líquidos' aparece com o horário limite calculado (2h antes -> 12:00)", "12:00" in corpo)
checar("Corte 'Jejum de sólidos' aparece com o horário limite calculado (8h antes -> 06:00)", "06:00" in corpo)

with app.app_context():
    corte_liquidos = PreparoCorte.query.filter_by(preparo_modelo_id=modelo_id, descricao="Jejum de líquidos").first()
    corte_solidos = PreparoCorte.query.filter_by(preparo_modelo_id=modelo_id, descricao="Jejum de sólidos").first()
    checar("Cálculo do corte de líquidos bate com a fórmula (data_hora - horas_antes)",
           corte_liquidos.limite(data_hora_consulta) == data_hora_consulta - timedelta(hours=2))
    checar("Cálculo do corte de sólidos bate com a fórmula (data_hora - horas_antes)",
           corte_solidos.limite(data_hora_consulta) == data_hora_consulta - timedelta(hours=8))

# ---------- Validações de formulário ----------
r = client_medico.post(f"/grupos/{grupo_id}/agenda/novo", data={
    "paciente_id": str(paciente_id), "exame_id": str(exame_id), "data_hora": "data-invalida",
}, follow_redirects=True)
checar("Data/hora inválida é rejeitada", "Escolha uma data/hora válida" in r.get_data(as_text=True))

r = client_medico.post(f"/grupos/{grupo_id}/agenda/novo", data={
    "paciente_id": "999999", "exame_id": str(exame_id),
    "data_hora": "2027-03-11T10:00",
}, follow_redirects=True)
checar("Paciente inválido/fora do grupo é rejeitado", "Escolha um paciente e um exame válidos" in r.get_data(as_text=True))

print("\nTodas as verificações do fluxo de agendamento por grupo (com cronograma automático) passaram.")
