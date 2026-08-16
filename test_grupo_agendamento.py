"""Teste de ponta a ponta do agendamento de consulta vinculado a um grupo de
trabalho, com cálculo automático do cronograma de preparo (BBP MedIA, telas
5.1.17-5.1.19): qualquer membro ativo do grupo pode agendar uma consulta
para um paciente do grupo usando um dos exames do grupo; o cronograma
(cortes de alimentação/líquido) é calculado automaticamente a partir do
horário escolhido, na visão do próprio paciente (paciente.preparo_exame, já
existente e não alterada).

Fatia 5 (passo 5): as telas de dados de um grupo específico (pacientes,
modelos de preparo, exames, agenda) NÃO vivem mais em routes_grupo.py por
URL (`/grupos/<id>/...`) - isso foi consolidado em routes_medico.py
(`/equipe/...`, escopado pelo Grupo ativo da sessão, ver
app/clinica_utils.py). routes_grupo.py ficou só com a ADMINISTRAÇÃO do
grupo em si (criar/listar/entrar/sair/convidar) - por isso este teste usa
`/grupos/...` só para montar a equipe (criar o grupo, convidar a
secretária) e `/equipe/...` para tudo o mais (modelo de preparo, exame,
associação exame×médico×preço, importar paciente pelo CPF, agendar).

Também não existe mais "grupo.agenda_detalhe" (débito técnico conhecido,
não migrado - ver docstring de routes_grupo.py) para a equipe ver o
cronograma calculado sem editar notas; a verificação do cálculo automático
do cronograma é feita pela visão do paciente (paciente.preparo_exame), que
é exatamente a mesma tela citada no objetivo original deste teste."""
from datetime import datetime, timedelta

from app import create_app
from app.extensions import db
from app.models import (
    Usuario, Grupo, GrupoMembro, GrupoConvite, Paciente, PreparoModelo,
    PreparoCorte, Exame, Agendamento, normalizar_telefone,
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
        for p in Paciente.query.filter_by(cpf=cpf).all():
            Agendamento.query.filter_by(paciente_id=p.id).delete()
            if p.usuario_id:
                Usuario.query.filter_by(id=p.usuario_id).delete()
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

    # Paciente é uma identidade GLOBAL (Fatia 5) - nasce sem grupo nenhum;
    # a associação com o grupo do teste é feita depois, importando pelo CPF
    # (medico.pacientes_importar), o mesmo fluxo real da equipe.
    telefone_paciente = normalizar_telefone("(27) 98888-0000")
    usuario_paciente = Usuario(nome="Paciente Agenda BBP", telefone=telefone_paciente, tipo="paciente")
    db.session.add(usuario_paciente)
    db.session.flush()
    paciente = Paciente(
        usuario_id=usuario_paciente.id, nome="Paciente Agenda BBP", cpf="901.234.567-70",
        telefone=telefone_paciente, data_nascimento=datetime(1988, 3, 3).date(),
        cep="29000-000", rua="Rua Agenda", numero="1", bairro="Centro", cidade="Vitória", uf="ES",
    )
    db.session.add(paciente)
    db.session.commit()
    paciente_id = paciente.id


client_medico = app.test_client()
client_secretaria = app.test_client()
client_paciente = app.test_client()


def login(client, cpf, senha):
    # Sem seguir redirect: logo após o login, quem ainda não tem vínculo
    # com nenhum Grupo seria deslogado de novo ao cair em /equipe/ (ver
    # staff_required em routes_medico.py) - o médico e a secretária deste
    # teste só ganham vínculo depois (criando/aceitando o Grupo abaixo).
    return client.post("/login", data={"identificador": cpf, "senha": senha}, follow_redirects=False)


login(client_medico, "123.987.456-19", "123456")
login(client_secretaria, "234.098.567-65", "123456")

# ---------- Montagem da equipe (rotas de administração do grupo) ----------

r = client_medico.post("/grupos/novo", data={"nome": "Grupo Agenda BBP"}, follow_redirects=True)
with app.app_context():
    grupo = Grupo.query.filter_by(nome="Grupo Agenda BBP").order_by(Grupo.id.desc()).first()
    grupo_id = grupo.id

r = client_medico.post(f"/grupos/{grupo_id}/convidar", data={"cpf": "234.098.567-65"}, follow_redirects=True)
with app.app_context():
    convite = GrupoConvite.query.filter_by(grupo_id=grupo_id, usuario_convidado_id=secretaria_id).first()
    convite_id = convite.id
client_secretaria.post(f"/grupos/convites/{convite_id}/responder", data={"decisao": "aprovar"}, follow_redirects=True)

# ---------- Sem exame ainda, a tela orienta o usuário ----------
r = client_medico.get("/equipe/agenda/novo")
checar(
    "Sem exame associado, a tela de agendar avisa que não há exame disponível",
    "Nenhum exame encontrado" in r.get_data(as_text=True),
)

# ---------- Modelo de preparo com cortes (cronograma) ----------
r = client_medico.post("/equipe/preparo-modelos/novo", data={
    "nome": "Preparo Agenda BBP",
    "instrucoes": "Chegar com 30 minutos de antecedência.",
    "corte_descricao[]": ["Jejum de líquidos", "Jejum de sólidos"],
    "corte_horas[]": ["2", "8"],
}, follow_redirects=True)
checar("Modelo de preparo com cortes cadastrado com sucesso", "cadastrado com sucesso" in r.get_data(as_text=True))

with app.app_context():
    modelo = PreparoModelo.query.filter_by(grupo_id=grupo_id, nome="Preparo Agenda BBP").first()
    checar("Modelo de preparo foi criado", modelo is not None)
    checar("Os dois cortes foram salvos", PreparoCorte.query.filter_by(preparo_modelo_id=modelo.id).count() == 2)
    modelo_id = modelo.id

# ---------- Exame vinculado ao modelo (nasce só como catálogo) ----------
r = client_medico.post("/equipe/exames/novo", data={
    "nome": "Consulta Agenda BBP", "descricao": "", "duracao_minutos": "30",
    "preparo_modelo_id": str(modelo_id),
}, follow_redirects=True)
checar("Exame cadastrado com sucesso", "cadastrado com sucesso" in r.get_data(as_text=True))
with app.app_context():
    exame = Exame.query.filter_by(grupo_id=grupo_id, nome="Consulta Agenda BBP").first()
    exame_id = exame.id
    checar("Exame nasce como catálogo, sem associação", exame.associado is False)

# A associação (exame + médico + preço) é o que faz o exame aparecer como
# opção de agendamento (ver medico.exames_por_filial_associar).
r = client_medico.post("/equipe/exames/por-filial/associar", data={
    "nome": "Consulta Agenda BBP", "medico_id": str(medico_id), "preco": "150,00",
}, follow_redirects=True)
checar("Exame associado com sucesso", "associado" in r.get_data(as_text=True).lower())

# ---------- Paciente importado da plataforma pelo CPF ----------
r = client_secretaria.post("/equipe/pacientes/importar", data={"cpf": "901.234.567-70"}, follow_redirects=True)
checar("Paciente importado para o grupo com sucesso", "importado" in r.get_data(as_text=True).lower())

# ---------- Agendamento pela secretária (membro do grupo, não é o médico) ----------
data_hora_consulta = datetime(2027, 3, 10, 14, 0, 0)
r = client_secretaria.post("/equipe/agenda/novo", data={
    "filial_id": str(grupo_id), "paciente_id": str(paciente_id), "exame_id": str(exame_id),
    "data_hora": data_hora_consulta.strftime("%Y-%m-%dT%H:%M"),
}, follow_redirects=True)
checar("Consulta agendada com sucesso pela secretária do grupo", "criado com sucesso" in r.get_data(as_text=True).lower())

with app.app_context():
    agendamento = Agendamento.query.filter_by(
        grupo_id=grupo_id, paciente_id=paciente_id, exame_id=exame_id,
    ).first()
    checar("Agendamento foi criado com o grupo_id do grupo", agendamento is not None)
    checar("Agendamento ficou com o médico do exame", agendamento.medico_id == medico_id)
    agendamento_id = agendamento.id

# ---------- Cronograma calculado automaticamente na visão do paciente ----------
client_paciente.post("/login-paciente", data={"cpf": "901.234.567-70", "data_nascimento": "03/03/1988"}, follow_redirects=True)
r = client_paciente.get(f"/paciente/exame/{agendamento_id}")
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
client_paciente.get("/logout")

# ---------- Validações de formulário ----------
r = client_medico.post("/equipe/agenda/novo", data={
    "filial_id": str(grupo_id), "paciente_id": str(paciente_id), "exame_id": str(exame_id),
    "data_hora": "data-invalida",
}, follow_redirects=True)
checar("Data/hora inválida é rejeitada", "Data/hora inválida" in r.get_data(as_text=True))

r = client_medico.post("/equipe/agenda/novo", data={
    "filial_id": str(grupo_id), "paciente_id": "999999", "exame_id": str(exame_id),
    "data_hora": "2027-03-11T10:00",
}, follow_redirects=True)
checar("Paciente inválido/fora do grupo é rejeitado", "Paciente ou exame inválido" in r.get_data(as_text=True))

client_medico.get("/logout")
client_secretaria.get("/logout")

print("\nTodas as verificações do fluxo de agendamento por grupo (com cronograma automático) passaram.")
