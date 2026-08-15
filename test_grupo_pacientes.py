"""Teste de ponta a ponta do cadastro/busca de paciente associado a
grupo(s) de trabalho (BBP MedIA, telas 5.1.8/5.1.9, seção 7): busca por
CPF -> se não existir, cadastra (com endereço obrigatório) -> associa ao
grupo escolhido (ou a todos, se o usuário participa de mais de um) ->
paciente sem consulta agendada pode ser removido do grupo; depois de uma
consulta agendada, a associação vira definitiva."""
from datetime import datetime, timedelta

from app import create_app
from app.extensions import db
from app.models import (
    Usuario, Grupo, GrupoMembro, GrupoPaciente, Paciente,
    Empresa, Clinica, ClinicaMembro, Exame, Agendamento,
)

app = create_app()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


with app.app_context():
    for email in ("dono2.bbp@teste.com", "membro2.bbp@teste.com"):
        u = Usuario.query.filter_by(email=email).first()
        if u:
            GrupoMembro.query.filter_by(usuario_id=u.id).delete()
            db.session.delete(u)
    for cpf in ("345.678.912-28", "456.789.123-64"):
        for p in Paciente.query.filter_by(cpf=cpf, empresa_id=None).all():
            GrupoPaciente.query.filter_by(paciente_id=p.id).delete()
            Agendamento.query.filter_by(paciente_id=p.id).delete()
            db.session.delete(p)
    db.session.commit()

    dono = Usuario(nome="Dra. Ana Ferreira 2", email="dono2.bbp@teste.com", tipo="medico",
                    cpf="123.456.789-09", crm_numero="9999", crm_uf="ES")
    dono.set_senha("123456")
    dono.definir_permissoes_padrao()
    membro = Usuario(nome="Carlos Souza 2", email="membro2.bbp@teste.com", tipo="secretaria",
                      cpf="234.567.891-73")
    membro.set_senha("123456")
    membro.definir_permissoes_padrao()
    db.session.add_all([dono, membro])
    db.session.commit()
    dono_id, membro_id = dono.id, membro.id

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
    for uid in (dono_id, membro_id):
        if not ClinicaMembro.query.filter_by(clinica_id=clinica_teste.id, usuario_id=uid).first():
            db.session.add(ClinicaMembro(clinica_id=clinica_teste.id, usuario_id=uid))
    db.session.commit()

    exame_teste = Exame.query.filter_by(nome="Exame Teste BBP", clinica_id=clinica_teste.id).first()
    if not exame_teste:
        exame_teste = Exame(clinica_id=clinica_teste.id, nome="Exame Teste BBP", medico_id=dono_id, criado_por_id=dono_id)
        db.session.add(exame_teste)
        db.session.commit()
    exame_id = exame_teste.id


client_dono = app.test_client()
client_membro = app.test_client()


def login(client, cpf, senha):
    return client.post("/login", data={"identificador": cpf, "senha": senha}, follow_redirects=True)


login(client_dono, "123.456.789-09", "123456")
login(client_membro, "234.567.891-73", "123456")

r = client_dono.post("/grupos/novo", data={"nome": "Grupo Pacientes BBP"}, follow_redirects=True)
with app.app_context():
    grupo1 = Grupo.query.filter_by(nome="Grupo Pacientes BBP").order_by(Grupo.id.desc()).first()
    grupo1_id = grupo1.id

r = client_dono.post(f"/grupos/{grupo1_id}/convidar", data={"cpf": "234.567.891-73"}, follow_redirects=True)
with app.app_context():
    from app.models import GrupoConvite
    convite = GrupoConvite.query.filter_by(grupo_id=grupo1_id, usuario_convidado_id=membro_id).first()
    convite_id = convite.id
client_membro.post(f"/grupos/convites/{convite_id}/responder", data={"decisao": "aprovar"}, follow_redirects=True)

# Um segundo grupo, criado pelo médico "membro" desta vez, para testar
# associação simultânea a mais de um grupo.
client_membro.post("/grupos/novo", data={"nome": "Grupo Pacientes BBP 2"}, follow_redirects=True)
with app.app_context():
    grupo2 = Grupo.query.filter_by(nome="Grupo Pacientes BBP 2").order_by(Grupo.id.desc()).first()
    grupo2_id = grupo2.id


# ---------- 5.1.8 — Buscar CPF que ainda não existe ----------
r = client_dono.post(f"/grupos/{grupo1_id}/pacientes/novo", data={"etapa": "buscar", "cpf": "345.678.912-28"}, follow_redirects=True)
checar("CPF novo aciona o formulário de cadastro completo", "Nenhum cadastro encontrado" in r.get_data(as_text=True))
checar("Formulário de cadastro pede endereço", "CEP *" in r.get_data(as_text=True))

# Tenta salvar sem endereço -> deve falhar.
r = client_dono.post(f"/grupos/{grupo1_id}/pacientes/novo", data={
    "etapa": "salvar", "cpf": "345.678.912-28", "nome": "Paciente Teste BBP",
}, follow_redirects=True)
checar("Cadastro sem endereço é rejeitado (endereço obrigatório, BBP seção 7)", "Endereço completo é obrigatório" in r.get_data(as_text=True))

# Cadastra com endereço completo.
r = client_dono.post(f"/grupos/{grupo1_id}/pacientes/novo", data={
    "etapa": "salvar", "cpf": "345.678.912-28", "nome": "Paciente Teste BBP",
    "data_nascimento": "10/05/1990", "telefone": "(27) 99999-0000",
    "cep": "29000-000", "rua": "Rua Teste", "numero": "100", "bairro": "Centro", "cidade": "Vitória", "uf": "ES",
    "grupos_ids": str(grupo1_id),
}, follow_redirects=True)
checar("Paciente novo cadastrado e associado ao grupo", "associado" in r.get_data(as_text=True))

with app.app_context():
    paciente1 = Paciente.query.filter_by(cpf="345.678.912-28", empresa_id=None).first()
    checar("Paciente foi criado sem empresa_id (cadastro global do novo modelo)", paciente1 is not None and paciente1.empresa_id is None)
    checar("Paciente ficou associado ao grupo 1", GrupoPaciente.query.filter_by(grupo_id=grupo1_id, paciente_id=paciente1.id).first() is not None)
    paciente1_id = paciente1.id

r = client_dono.get(f"/grupos/{grupo1_id}/pacientes")
checar("Paciente aparece na lista de pacientes do grupo", "Paciente Teste BBP" in r.get_data(as_text=True))


# ---------- Buscar o MESMO CPF por outro usuário (sem duplicar cadastro) ----------
r = client_membro.post(f"/grupos/{grupo2_id}/pacientes/novo", data={"etapa": "buscar", "cpf": "345.678.912-28"}, follow_redirects=True)
checar("Segundo usuário encontra o paciente já cadastrado pelo CPF (sem duplicar)", "já cadastrado" in r.get_data(as_text=True))

r = client_membro.post(f"/grupos/{grupo2_id}/pacientes/novo", data={
    "etapa": "salvar", "paciente_id": str(paciente1_id), "grupos_ids": str(grupo2_id),
}, follow_redirects=True)
checar("Paciente associado ao segundo grupo sem duplicar cadastro", "associado" in r.get_data(as_text=True))

with app.app_context():
    checar("Não existe um segundo registro de Paciente com o mesmo CPF (global)",
           Paciente.query.filter_by(cpf="345.678.912-28", empresa_id=None).count() == 1)
    checar("Paciente agora está associado a AMBOS os grupos", GrupoPaciente.query.filter_by(paciente_id=paciente1_id).count() == 2)


# ---------- Remoção antes de qualquer consulta agendada ----------
r = client_dono.post(f"/grupos/{grupo1_id}/pacientes/{paciente1_id}/remover", follow_redirects=True)
checar("Paciente sem consulta agendada pode ser removido do grupo", "Paciente removido do grupo" in r.get_data(as_text=True))
with app.app_context():
    checar("Vínculo com o grupo 1 foi removido", GrupoPaciente.query.filter_by(grupo_id=grupo1_id, paciente_id=paciente1_id).first() is None)
    checar("Vínculo com o grupo 2 continua intacto", GrupoPaciente.query.filter_by(grupo_id=grupo2_id, paciente_id=paciente1_id).first() is not None)

# Re-associa ao grupo 1 para o próximo teste (agora com consulta agendada).
client_dono.post(f"/grupos/{grupo1_id}/pacientes/novo", data={
    "etapa": "salvar", "paciente_id": str(paciente1_id), "grupos_ids": str(grupo1_id),
}, follow_redirects=True)


# ---------- Vínculo definitivo após consulta agendada ----------
with app.app_context():
    clinica_teste_local = Clinica.query.filter_by(nome="Clínica Teste BBP").first()
    ag = Agendamento(
        clinica_id=clinica_teste_local.id, paciente_id=paciente1_id, exame_id=exame_id,
        medico_id=dono_id, data_hora=datetime.utcnow() + timedelta(days=1),
    )
    db.session.add(ag)
    db.session.commit()

r = client_dono.get(f"/grupos/{grupo1_id}/pacientes")
checar("Paciente com consulta agendada aparece como vínculo definitivo", "Vínculo definitivo" in r.get_data(as_text=True))

r = client_dono.post(f"/grupos/{grupo1_id}/pacientes/{paciente1_id}/remover", follow_redirects=True)
checar("Remoção é bloqueada depois de consulta agendada neste grupo", "associação é definitiva" in r.get_data(as_text=True))
with app.app_context():
    checar("Vínculo com o grupo 1 continua existindo (não foi removido)", GrupoPaciente.query.filter_by(grupo_id=grupo1_id, paciente_id=paciente1_id).first() is not None)

# O vínculo com o grupo 2 (sem consulta agendada NESSE grupo) continua removível.
r = client_membro.post(f"/grupos/{grupo2_id}/pacientes/{paciente1_id}/remover", follow_redirects=True)
checar("Vínculo com outro grupo (sem consulta lá) continua removível normalmente", "Paciente removido do grupo" in r.get_data(as_text=True))

print("\nTodas as verificações do fluxo de paciente por grupo passaram.")
