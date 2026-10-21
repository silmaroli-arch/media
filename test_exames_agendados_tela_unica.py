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

print("\nTodos os testes da tela única de exames agendados passaram.")
