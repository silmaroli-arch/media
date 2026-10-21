"""Tela única de exames agendados (pedido do Silvan, 2026-10-21).

- "Agendar exame" saiu do menu e virou botão (com janela) nesta tela.
- A lista mostra o nome do médico.
- Médico vê só os exames dele; secretária vê os de todos os médicos do
  grupo (com filtro opcional por médico).
- A janela agenda de verdade e volta para a tela; com erro, volta com a
  janela aberta e o motivo."""
from app import create_app
from app.models import Agendamento, Grupo, Usuario

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def tabela(html):
    """Só os agendamentos do calendário (os eventos; a janela de agendar
    tem a lista de pacientes)."""
    inicio = html.find("var eventos = [")
    return html[inicio:html.find("];", inicio)]


def entrar(email):
    c = app.test_client()
    c.post("/login", data={"identificador": email, "senha": "123456"})
    return c


with app.app_context():
    vitoria_id = Grupo.query.filter_by(nome="Clínica Vitória").first().id
    carlos_id = Usuario.query.filter_by(email="medico@clinicavitoria.com").first().id
    fernanda_id = Usuario.query.filter_by(email="medica2@clinicavitoria.com").first().id
    pedro_ag = Agendamento.query.filter_by(medico_id=fernanda_id).first()
    pedro_id, hemograma_id = pedro_ag.paciente_id, pedro_ag.exame_id

# ---------- Secretária: todos os médicos do grupo ----------
sec = entrar("secretaria@clinicavitoria.com")
html = sec.get("/equipe/medico-agenda", follow_redirects=True).get_data(as_text=True)
checar("Secretária vê o título 'Exames agendados'", "<h3 class=\"mb-1\">Exames agendados</h3>" in html)
checar("... os exames dos dois médicos do grupo", "Dr. Carlos Andrade" in html and "Dra. Fernanda Lima" in html
       and "Pedro Souza" in html and "João Pereira" in html)
checar("... em calendário, com o médico de cada exame",
       'id="calendario"' in html and "FullCalendar.Calendar" in html and '"medico": "Dra. Fernanda Lima"' not in html
       and "medico: \"Dra. Fernanda Lima\"" in tabela(html))
checar("... e não os de outro grupo (Maria, Clínica São Paulo)", "Maria Silva" not in tabela(html))
checar("... e o botão 'Agendar exame' com a janela", 'data-bs-target="#modalAgendarExame"' in html and 'id="modalAgendarExame"' in html)
menu = html[html.find('<nav class="app-sidebar'):html.find('</nav>', html.find('<nav class="app-sidebar'))]
checar("Menu não tem mais 'Agendar exame'", "Agendar exame" not in menu)
html = tabela(sec.get(f"/equipe/medico-agenda/{fernanda_id}").get_data(as_text=True))
checar("Filtro por médico mostra só os dele", "Pedro Souza" in html and "João Pereira" not in html)

# ---------- Médico: só os dele ----------
med = entrar("medica2@clinicavitoria.com")
med.post("/equipe/clinica", data={"clinica_id": str(vitoria_id)}, follow_redirects=True)
pagina = med.get("/equipe/medico-agenda", follow_redirects=True).get_data(as_text=True)
html = tabela(pagina)
checar("Médica vê 'Meus exames agendados' só com os dela", "Meus exames agendados" in pagina
       and "Pedro Souza" in html and "João Pereira" not in html)
html = tabela(med.get(f"/equipe/medico-agenda/{carlos_id}").get_data(as_text=True))
checar("Médica não vê os do colega nem pelo endereço", "João Pereira" not in html)

# ---------- Agendar pela janela ----------
r = sec.post("/equipe/agenda/novo", data={
    "origem": "agenda", "filial_id": str(vitoria_id), "paciente_id": str(pedro_id),
    "exame_id": str(hemograma_id), "medico_id": str(fernanda_id), "data_hora": "2026-12-01T09:30",
})
checar("Agendar pela janela volta para a tela de exames agendados",
       r.status_code == 302 and r.headers["Location"].endswith("/equipe/medico-agenda"))
with app.app_context():
    checar("... e o agendamento foi criado", Agendamento.query.filter_by(
        paciente_id=pedro_id, medico_id=fernanda_id).count() == 2)
r = sec.post("/equipe/agenda/novo", data={
    "origem": "agenda", "filial_id": str(vitoria_id), "paciente_id": str(pedro_id),
    "exame_id": str(hemograma_id), "medico_id": str(fernanda_id), "data_hora": "data errada",
    "observacoes": "trazer exames antigos",
})
checar("Erro na janela volta com ela aberta", r.status_code == 302 and "agendar=1" in r.headers["Location"])
html = sec.get(r.headers["Location"]).get_data(as_text=True)
checar("... mostrando o motivo e o que foi preenchido",
       "Data/hora inválida." in html and "trazer exames antigos" in html and "new bootstrap.Modal" in html)

# ---------- Conflito de horário do mesmo médico (2026-10-21) ----------
from app.extensions import db
from app.models import Exame
with app.app_context():
    Exame.query.get(hemograma_id).duracao_minutos = 30
    db.session.commit()


def agendar(data_hora, medico_id=fernanda_id):
    return sec.post("/equipe/agenda/novo", data={
        "origem": "agenda", "filial_id": str(vitoria_id), "paciente_id": str(pedro_id),
        "exame_id": str(hemograma_id), "medico_id": str(medico_id), "data_hora": data_hora,
    }, follow_redirects=True).get_data(as_text=True)


with app.app_context():
    antes = Agendamento.query.filter_by(medico_id=fernanda_id).count()
html = agendar("2026-12-01T09:30")
checar("Mesmo médico, mesmo dia e hora: recusado", "já tem um exame nesse horário" in html and "01/12/2026 às 09:30" in html)
html = agendar("2026-12-01T09:45")
checar("Mesmo médico, dentro da duração do outro exame (30 min): recusado", "já tem um exame nesse horário" in html)
html = agendar("2026-12-01T09:10")
checar("Mesmo médico, terminaria depois que o outro começa: recusado", "já tem um exame nesse horário" in html)
with app.app_context():
    checar("... e nenhum desses foi criado", Agendamento.query.filter_by(medico_id=fernanda_id).count() == antes)
html = agendar("2026-12-01T10:00")
checar("Logo depois do fim do outro exame (10:00): aceito", "Agendamento criado com sucesso." in html)
with app.app_context():
    exame_carlos = Agendamento.query.filter_by(medico_id=carlos_id).first().exame_id
r = sec.post("/equipe/agenda/novo", data={
    "origem": "agenda", "filial_id": str(vitoria_id), "paciente_id": str(pedro_id),
    "exame_id": str(exame_carlos), "medico_id": str(carlos_id), "data_hora": "2026-12-01T09:30",
}, follow_redirects=True)
checar("Outro médico no mesmo horário: aceito", "Agendamento criado com sucesso." in r.get_data(as_text=True))

# ---------- Remarcar e cancelar (2026-10-21) ----------
with app.app_context():
    ag = Agendamento.query.filter_by(medico_id=fernanda_id).order_by(Agendamento.data_hora.desc()).first()
    ag_id = ag.id
html = sec.get("/equipe/medico-agenda").get_data(as_text=True)
checar("A janela do exame tem Remarcar e Cancelar", f"/equipe/agenda/{ag_id}/remarcar" in html and f"/equipe/agenda/{ag_id}/cancelar" in html)
r = sec.post(f"/equipe/agenda/{ag_id}/remarcar", data={"data_hora": "2026-12-01T09:40"}, follow_redirects=True)
checar("Remarcar para um horário ocupado: recusado", "já tem um exame nesse horário" in r.get_data(as_text=True))
r = sec.post(f"/equipe/agenda/{ag_id}/remarcar", data={"data_hora": "2026-12-02T15:00"}, follow_redirects=True)
with app.app_context():
    checar("Remarcar para um horário livre muda a data", "remarcado para 02/12/2026 às 15:00" in r.get_data(as_text=True)
           and Agendamento.query.get(ag_id).data_hora.strftime("%d/%m %H:%M") == "02/12 15:00")
r = sec.post(f"/equipe/agenda/{ag_id}/remarcar", data={"data_hora": "2026-12-02T15:00"}, follow_redirects=True)
checar("Remarcar para o mesmo horário dele não conflita com ele mesmo", "remarcado" in r.get_data(as_text=True))
r = med.post(f"/equipe/agenda/{ag_id}/cancelar")
checar("Médica pode cancelar o próprio exame", r.status_code == 302)
with app.app_context():
    checar("... e o agendamento sumiu", Agendamento.query.get(ag_id) is None)
    outro = Agendamento.query.filter_by(medico_id=carlos_id).first()
    outro_id = outro.id
r = med.post(f"/equipe/agenda/{outro_id}/cancelar")
with app.app_context():
    checar("Médica não cancela exame do colega", r.status_code == 404 and Agendamento.query.get(outro_id) is not None)

print("\nTodos os testes da tela única de exames agendados passaram.")
