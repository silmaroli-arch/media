"""Testa o choque de horário do médico multi-clínica e a agenda
consolidada:

A agenda do médico é UMA SÓ, mesmo atendendo em clínicas de empresas
diferentes (que não se enxergam entre si):

- Criar agendamento num horário em que o médico já atende em OUTRO local
  é bloqueado - citando a filial (mesma empresa) ou só "outro local em
  que ele atende" (outra empresa, sem expor dados dela).
- Confirmar uma solicitação de paciente também respeita o choque.
- As sugestões de horário (pro paciente e pra equipe) pulam os horários
  tomados em qualquer clínica do médico.
- "Minha agenda em todos os locais": o médico vê os atendimentos dele de
  TODAS as empresas numa tela só; secretária não tem essa tela.
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


# Cenário: Dr. Eduardo (Grupo Saúde Total) também atende na Clínica
# Vitória (OUTRA empresa) - o caso central do médico por código mestre.
with app.app_context():
    centro = Clinica.query.filter_by(nome="Grupo Saúde Total - Centro").first()
    praia = Clinica.query.filter_by(nome="Grupo Saúde Total - Praia").first()
    vitoria = Clinica.query.filter_by(nome="Clínica Vitória").first()
    centro_id, praia_id, vitoria_id = centro.id, praia.id, vitoria.id
    eduardo = Usuario.query.filter_by(email="medico@gruposaude.com").first()
    eduardo_id = eduardo.id

    db.session.add(ClinicaMembro(clinica_id=vitoria_id, usuario_id=eduardo_id, ativo=True))

    # Expediente do Eduardo no Centro (seg-sex 8h-12h) - necessário pro
    # otimizador de sugestões ter de onde partir.
    from datetime import time
    from app.models import MedicoHorario
    for dia_idx in range(5):
        db.session.add(MedicoHorario(
            clinica_id=centro_id, medico_id=eduardo_id, dia_semana=dia_idx, ativo=True,
            hora_inicio=time(8, 0), hora_fim=time(12, 0),
        ))

    exame_vit = Exame(clinica_id=vitoria_id, medico_id=eduardo_id, nome="Consulta Vitoria Edu",
                      descricao="", duracao_minutos=30, medico_confirmado=True, associado=True)
    exame_centro = Exame(clinica_id=centro_id, medico_id=eduardo_id, nome="Consulta Centro Edu",
                         descricao="", duracao_minutos=30, medico_confirmado=True, associado=True)
    exame_praia = Exame(clinica_id=praia_id, medico_id=eduardo_id, nome="Consulta Praia Edu",
                        descricao="", duracao_minutos=30, medico_confirmado=True, associado=True)
    db.session.add_all([exame_vit, exame_centro, exame_praia])

    # Paciente da Clínica Vitória e paciente do Grupo.
    def novo_paciente(nome, cpf, tel, empresa_id):
        t = normalizar_telefone(tel)
        u = Usuario(nome=nome, telefone=t, tipo="paciente")
        db.session.add(u)
        db.session.flush()
        p = Paciente(empresa_id=empresa_id, usuario_id=u.id, nome=nome, cpf=cpf,
                     data_nascimento=date(1990, 1, 1), telefone=t, status_cadastro="aprovado")
        db.session.add(p)
        db.session.flush()
        return p

    pac_vit = novo_paciente("Paciente Da Vitoria", "111.222.333-96", "(27) 96666-1001", vitoria.empresa_id)
    pac_grupo = novo_paciente("Paciente Do Grupo", "111.222.333-45", "(27) 96666-1002", centro.empresa_id)
    db.session.flush()

    # Eduardo já tem consulta na Clínica Vitória em 15/09 às 09:00.
    ag_vit = Agendamento(clinica_id=vitoria_id, paciente_id=pac_vit.id, exame_id=exame_vit.id,
                         medico_id=eduardo_id, data_hora=datetime(2026, 9, 15, 9, 0), status="agendado")
    db.session.add(ag_vit)
    db.session.commit()
    exame_centro_id, exame_praia_id = exame_centro.id, exame_praia.id
    pac_grupo_id = pac_grupo.id

# ---------- Choque ENTRE EMPRESAS ao criar agendamento ----------

login("secretaria@gruposaude.com")

r = client.post("/equipe/agenda/novo", data={
    "filial_id": str(centro_id), "paciente_id": str(pac_grupo_id),
    "exame_id": str(exame_centro_id), "medico_id": str(eduardo_id),
    "data_hora": "2026-09-15T09:00",
}, follow_redirects=True)
html = r.get_data(as_text=True)
checar("Agendar no horário tomado em OUTRA empresa é bloqueado",
       "já tem um agendamento nesse horário" in html)
checar("A mensagem NÃO expõe a outra empresa (só 'outro local em que ele atende')",
       "fora desta empresa" in html and "Clínica Vitória" not in html.split("já tem um agendamento")[1][:200])

# Sobreposição parcial também conta (09:15 colide com 09:00-09:30).
r = client.post("/equipe/agenda/novo", data={
    "filial_id": str(centro_id), "paciente_id": str(pac_grupo_id),
    "exame_id": str(exame_centro_id), "medico_id": str(eduardo_id),
    "data_hora": "2026-09-15T09:15",
}, follow_redirects=True)
checar("Sobreposição parcial (09:15 x 09:00-09:30) também é bloqueada",
       "já tem um agendamento nesse horário" in r.get_data(as_text=True))

# Horário livre funciona normalmente.
r = client.post("/equipe/agenda/novo", data={
    "filial_id": str(centro_id), "paciente_id": str(pac_grupo_id),
    "exame_id": str(exame_centro_id), "medico_id": str(eduardo_id),
    "data_hora": "2026-09-15T10:00",
}, follow_redirects=True)
checar("Horário livre continua agendando normalmente",
       "criado com sucesso" in r.get_data(as_text=True).lower())

# ---------- Choque entre FILIAIS da mesma empresa cita a filial ----------

r = client.post("/equipe/agenda/novo", data={
    "filial_id": str(praia_id), "paciente_id": str(pac_grupo_id),
    "exame_id": str(exame_praia_id), "medico_id": str(eduardo_id),
    "data_hora": "2026-09-15T10:00",
}, follow_redirects=True)
html = r.get_data(as_text=True)
checar("Choque entre filiais da MESMA empresa é bloqueado citando a filial",
       "já tem um agendamento nesse horário" in html and "Grupo Saúde Total - Centro" in html)

# ---------- Sugestões de horário pulam o que está tomado em outra clínica ----------

with app.app_context():
    from app.agendamento_otimizador import sugerir_horarios
    exame = Exame.query.get(exame_centro_id)
    medico = Usuario.query.get(eduardo_id)
    clinica = Clinica.query.get(centro_id)
    sugestoes = sugerir_horarios(exame, medico, clinica,
                                 data_inicio=date(2026, 9, 15), quantidade=10)
    checar("Sugestões existem para o dia (médico tem expediente)", len(sugestoes) > 0)
    checar("09:00 (tomado na Clínica Vitória) NÃO é sugerido no Centro",
           datetime(2026, 9, 15, 9, 0) not in sugestoes)
    checar("10:00 (tomado no próprio Centro) também não é sugerido",
           datetime(2026, 9, 15, 10, 0) not in sugestoes)
    checar("Horários vizinhos livres continuam sendo sugeridos",
           datetime(2026, 9, 15, 9, 30) in sugestoes)

# ---------- Confirmar solicitação também respeita o choque ----------

with app.app_context():
    solicitacao = Agendamento(clinica_id=centro_id, paciente_id=pac_grupo_id,
                              exame_id=exame_centro_id, medico_id=eduardo_id,
                              data_hora=datetime(2026, 9, 15, 9, 0), status="solicitado")
    db.session.add(solicitacao)
    db.session.commit()
    solicitacao_id = solicitacao.id

r = client.post(f"/equipe/agenda/{solicitacao_id}/confirmar-solicitacao",
                data={"acao": "confirmar"}, follow_redirects=True)
checar("Confirmar solicitação em horário tomado em outro local é bloqueado",
       "já tem um agendamento nesse horário" in r.get_data(as_text=True))
with app.app_context():
    checar("A solicitação continua pendente (não virou agendamento)",
           Agendamento.query.get(solicitacao_id).status == "solicitado")

# Secretária não tem a tela consolidada (é pessoal do médico).
r = client.get("/equipe/minha-agenda-completa", follow_redirects=True)
checar("Secretária não vê a agenda consolidada",
       "agenda pessoal consolidada" in r.get_data(as_text=True))
client.get("/logout")

# ---------- "Minha agenda em todos os locais" do médico ----------

login("medico@gruposaude.com")
# Eduardo agora atua em DUAS empresas - escolhe o Grupo pra entrar (a
# tela consolidada mostra as duas de qualquer forma).
with app.app_context():
    grupo_empresa_id = Clinica.query.get(centro_id).empresa_id
client.post("/equipe/clinica", data={"empresa_id": str(grupo_empresa_id)}, follow_redirects=True)
r = client.get("/equipe/minha-agenda-completa")
html = r.get_data(as_text=True)
checar("A tela consolidada abre para o médico", "Minha agenda em todos os locais" in html)
checar("Mostra o atendimento na Clínica Vitória (outra empresa)",
       "Clínica Vitória" in html and "Paciente Da Vitoria" in html)
checar("Mostra o atendimento no Grupo (Centro) na mesma tela",
       "Grupo Saúde Total - Centro" in html and "Paciente Do Grupo" in html)
client.get("/logout")

print("\nTodos os testes de choque de horário + agenda consolidada passaram.")
