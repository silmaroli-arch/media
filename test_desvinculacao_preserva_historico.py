"""Testa a desvinculação com histórico preservado:

- Remover alguém da equipe (ou desmarcar filiais no Editar, ou
  "desmarcar" a própria atuação em Meus Locais) NÃO apaga mais o vínculo:
  ele é ENCERRADO (ativo=False + encerrado_em) - o registro continua no
  banco, e os agendamentos/histórico da pessoa ficam intactos.
- A pessoa encerrada some da tela Equipe e perde o acesso àquela clínica,
  mas a conta dela continua funcionando nos outros locais.
- TRAVA: médico com agendamentos FUTUROS na filial não pode ser encerrado
  ali - a equipe cancela/transfere as consultas primeiro.
- Revinculação REATIVA o mesmo registro (nunca duplica).
"""
from datetime import datetime, date

from app import create_app
from app.extensions import db
from app.models import (
    Usuario, Clinica, ClinicaMembro, Exame, Paciente, Agendamento, normalizar_telefone,
)

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha="123456"):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


with app.app_context():
    centro = Clinica.query.filter_by(nome="Grupo Saúde Total - Centro").first()
    praia = Clinica.query.filter_by(nome="Grupo Saúde Total - Praia").first()
    centro_id, praia_id = centro.id, praia.id
    empresa_id = centro.empresa_id
    eduardo = Usuario.query.filter_by(email="medico@gruposaude.com").first()
    eduardo_id = eduardo.id

    # Um exame + um paciente + um agendamento FUTURO do Eduardo na Praia
    # (pra testar a trava) e um PASSADO no Centro (pra provar que o
    # histórico fica).
    exame_praia = Exame(clinica_id=praia_id, medico_id=eduardo_id, nome="Exame Trava Praia",
                        descricao="", duracao_minutos=30, medico_confirmado=True, associado=True)
    exame_centro = Exame(clinica_id=centro_id, medico_id=eduardo_id, nome="Exame Historico Centro",
                         descricao="", duracao_minutos=30, medico_confirmado=True, associado=True)
    db.session.add_all([exame_praia, exame_centro])
    tel = normalizar_telefone("(27) 95555-0001")
    u = Usuario(nome="Paciente Historico", telefone=tel, tipo="paciente")
    db.session.add(u)
    db.session.flush()
    pac = Paciente(empresa_id=empresa_id, usuario_id=u.id, nome="Paciente Historico",
                   cpf="606.707.808-09", data_nascimento=date(1985, 5, 5), telefone=tel,
                   status_cadastro="aprovado")
    db.session.add(pac)
    db.session.flush()
    ag_futuro = Agendamento(clinica_id=praia_id, paciente_id=pac.id, exame_id=exame_praia.id,
                            medico_id=eduardo_id, data_hora=datetime(2026, 9, 20, 9, 0))
    ag_passado = Agendamento(clinica_id=centro_id, paciente_id=pac.id, exame_id=exame_centro.id,
                             medico_id=eduardo_id, data_hora=datetime(2026, 5, 10, 9, 0))
    db.session.add_all([ag_futuro, ag_passado])
    db.session.commit()
    ag_futuro_id, ag_passado_id = ag_futuro.id, ag_passado.id
    vinculo_praia = ClinicaMembro.query.filter_by(usuario_id=eduardo_id, clinica_id=praia_id).first()
    vinculo_centro = ClinicaMembro.query.filter_by(usuario_id=eduardo_id, clinica_id=centro_id).first()
    vinculo_praia_id, vinculo_centro_id = vinculo_praia.id, vinculo_centro.id

login("secretaria@gruposaude.com")

# ---------- TRAVA: agendamento futuro impede o encerramento ----------

r = client.post(f"/equipe/equipe-membros/{vinculo_praia_id}/remover", follow_redirects=True)
checar("Remover médico com agendamento FUTURO na filial é bloqueado",
       "agendamento(s) futuro(s)" in r.get_data(as_text=True))
with app.app_context():
    checar("O vínculo continua ativo",
           ClinicaMembro.query.get(vinculo_praia_id).ativo)

# Move o agendamento futuro pro passado (sem status pra "cancelar", a
# trava olha só data_hora) e tenta de novo.
with app.app_context():
    Agendamento.query.get(ag_futuro_id).data_hora = datetime(2020, 1, 1, 9, 0)
    db.session.commit()

r = client.post(f"/equipe/equipe-membros/{vinculo_praia_id}/remover", follow_redirects=True)
checar("Sem agendamentos futuros, o encerramento funciona (com aviso de histórico)",
       "não atua mais" in r.get_data(as_text=True) and "histórico foi preservado" in r.get_data(as_text=True))

# ---------- O vínculo foi ENCERRADO, não apagado ----------

with app.app_context():
    v = ClinicaMembro.query.get(vinculo_praia_id)
    checar("O registro do vínculo CONTINUA no banco", v is not None)
    checar("Encerrado: ativo=False e encerrado_em preenchido",
           v.ativo is False and v.encerrado_em is not None)
    ag_hist = Agendamento.query.get(ag_passado_id)
    checar("O agendamento histórico continua intacto",
           ag_hist is not None and ag_hist.data_hora == datetime(2026, 5, 10, 9, 0)
           and ag_hist.clinica_id == centro_id)

r = client.get("/equipe/equipe-membros")
html = r.get_data(as_text=True)
linha_eduardo = html.split("Dr. Eduardo Nunes", 1)[1].split("</tr>")[0]
checar("Na tela Equipe, o Eduardo não aparece mais na Praia",
       "Grupo Saúde Total - Praia</option>" in linha_eduardo or "Praia" not in linha_eduardo.split("+ Associar")[0])
checar("Mas continua na equipe (ainda atua no Centro)", "Dr. Eduardo Nunes" in html)

# ---------- Revinculação REATIVA o mesmo registro ----------

r = client.post(f"/equipe/equipe-membros/{eduardo_id}/associar-filial",
                data={"filial_id": str(praia_id)}, follow_redirects=True)
checar("Reassociar à filial funciona", "vinculado" in r.get_data(as_text=True))
with app.app_context():
    vinculos = ClinicaMembro.query.filter_by(usuario_id=eduardo_id, clinica_id=praia_id).all()
    checar("Um registro só (reativado, não duplicado)",
           len(vinculos) == 1 and vinculos[0].id == vinculo_praia_id and vinculos[0].ativo)
    checar("encerrado_em foi limpo na reativação", vinculos[0].encerrado_em is None)

# ---------- Editar equipe: desmarcar encerra, remarcar reativa ----------

r = client.post(f"/equipe/equipe-membros/{eduardo_id}/editar", data={
    "nome": "Dr. Eduardo Nunes", "filial_ids": [str(centro_id)],
}, follow_redirects=True)
checar("Desmarcar a Praia no Editar funciona", "atualizados" in r.get_data(as_text=True))
with app.app_context():
    v = ClinicaMembro.query.get(vinculo_praia_id)
    checar("Vínculo da Praia encerrado de novo (mesmo registro)",
           v.ativo is False and v.encerrado_em is not None)

r = client.post(f"/equipe/equipe-membros/{eduardo_id}/editar", data={
    "nome": "Dr. Eduardo Nunes", "filial_ids": [str(centro_id), str(praia_id)],
}, follow_redirects=True)
with app.app_context():
    vinculos = ClinicaMembro.query.filter_by(usuario_id=eduardo_id, clinica_id=praia_id).all()
    checar("Remarcar no Editar reativa o MESMO vínculo",
           len(vinculos) == 1 and vinculos[0].ativo)

# Editar também respeita a trava de agendamentos futuros.
with app.app_context():
    Agendamento.query.get(ag_futuro_id).data_hora = datetime(2026, 9, 20, 9, 0)
    db.session.commit()
r = client.post(f"/equipe/equipe-membros/{eduardo_id}/editar", data={
    "nome": "Dr. Eduardo Nunes", "filial_ids": [str(centro_id)],
}, follow_redirects=True)
checar("Editar bloqueia o encerramento com agendamento futuro",
       "agendamento(s) futuro(s)" in r.get_data(as_text=True))
with app.app_context():
    checar("Vínculo da Praia segue ativo",
           ClinicaMembro.query.get(vinculo_praia_id).ativo)
    Agendamento.query.get(ag_futuro_id).data_hora = datetime(2020, 1, 1, 9, 0)
    db.session.commit()

client.get("/logout")

# ---------- "Desmarcar que atuo aqui" (Meus Locais) também encerra ----------

login("medico@gruposaude.com")
r = client.post(f"/equipe/filiais/{praia_id}/desvincular-me", follow_redirects=True)
checar("Médico se desvincula da Praia (sem agendamentos futuros)",
       "não está mais marcado" in r.get_data(as_text=True))
with app.app_context():
    v = ClinicaMembro.query.get(vinculo_praia_id)
    checar("Auto-desvinculação também ENCERRA (não apaga)",
           v is not None and v.ativo is False)
checar("Médico continua logado e com acesso (ainda atua no Centro)",
       client.get("/equipe/").status_code == 200)

# Marcar de novo reativa o mesmo registro.
r = client.post(f"/equipe/filiais/{praia_id}/vincular-me", follow_redirects=True)
with app.app_context():
    vinculos = ClinicaMembro.query.filter_by(usuario_id=eduardo_id, clinica_id=praia_id).all()
    checar("'Marcar que atuo aqui' de novo reativa o MESMO vínculo",
           len(vinculos) == 1 and vinculos[0].ativo and vinculos[0].id == vinculo_praia_id)

client.get("/logout")
print("\nTodos os testes de desvinculação com histórico preservado passaram.")
