"""Médicos por preparo (pedido do Silvan, 2026-10-21): um mesmo exame, com o
mesmo preparo, pode ser feito por mais de um médico. Na tela "Médicos por
preparo" se escolhe quem faz cada preparo; o médico associado atende os
exames desse preparo e também pode editar o preparo."""
from app import create_app
from app.extensions import db
from app.models import Exame, Grupo, PreparoModelo, Usuario

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def entrar(email):
    c = app.test_client()
    c.post("/login", data={"identificador": email, "senha": "123456"})
    return c


with app.app_context():
    vitoria = Grupo.query.filter_by(nome="Clínica Vitória").first()
    carlos = Usuario.query.filter_by(email="medico@clinicavitoria.com").first()
    fernanda = Usuario.query.filter_by(email="medica2@clinicavitoria.com").first()
    modelo = PreparoModelo(grupo_id=vitoria.id, nome="Preparo Ecoendoscopia", instrucoes="Jejum de 8 horas.",
                           criado_por_id=carlos.id)
    db.session.add(modelo)
    db.session.flush()
    exame = Exame(grupo_id=vitoria.id, medico_id=carlos.id, nome="Ecoendoscopia", descricao="",
                  preparo_modelo_id=modelo.id, criado_por_id=carlos.id)
    db.session.add(exame)
    db.session.commit()
    vitoria_id, carlos_id, fernanda_id = vitoria.id, carlos.id, fernanda.id
    modelo_id, exame_id = modelo.id, exame.id
    checar("Antes: a Dra. Fernanda não pode editar o preparo do Dr. Carlos",
           not PreparoModelo.query.get(modelo_id).pode_ser_editado_por(Usuario.query.get(fernanda_id)))

fernanda_c = entrar("medica2@clinicavitoria.com")
fernanda_c.post("/equipe/clinica", data={"clinica_id": str(vitoria_id)}, follow_redirects=True)
checar("Antes: o preparo não aparece na lista dela", "Preparo Ecoendoscopia" not in
       fernanda_c.get("/equipe/preparo-modelos").get_data(as_text=True))

# ---------- Secretária associa a Dra. Fernanda ----------
sec = entrar("secretaria@clinicavitoria.com")
html = sec.get("/equipe/preparos/medicos", follow_redirects=True).get_data(as_text=True)
menu = html[html.find('<nav class="app-sidebar'):html.find('</nav>', html.find('<nav class="app-sidebar'))]
checar("Menu tem 'Médicos por preparo'", "Médicos por preparo" in menu)
checar("A tela lista o preparo com o médico principal e o botão da janela",
       "Preparo Ecoendoscopia" in html and "Dr. Carlos Andrade" in html and 'data-bs-target="#modalMedicosPreparo"' in html)
r = sec.post(f"/equipe/preparos/{modelo_id}/medicos", data={"medico_ids": [str(fernanda_id)]}, follow_redirects=True)
checar("Salvar mostra a confirmação", "Médicos do preparo" in r.get_data(as_text=True))
with app.app_context():
    ex = Exame.query.get(exame_id)
    checar("A Dra. Fernanda passa a atender o exame, e o Dr. Carlos continua o principal",
           ex.medico_id == carlos_id and ex.medico_pode_atender(fernanda_id))
    checar("... e pode editar o preparo",
           PreparoModelo.query.get(modelo_id).pode_ser_editado_por(Usuario.query.get(fernanda_id)))

# ---------- A Dra. Fernanda vê e edita ----------
checar("Agora o preparo aparece na lista dela", "Preparo Ecoendoscopia" in
       fernanda_c.get("/equipe/preparo-modelos").get_data(as_text=True))
r = fernanda_c.get(f"/equipe/preparo-modelos/{modelo_id}/editar")
checar("... e ela abre a edição (sem ser barrada)", r.status_code == 200 and "Preparo Ecoendoscopia" in r.get_data(as_text=True))
html = fernanda_c.get("/equipe/medico-agenda").get_data(as_text=True)
checar("... e o exame aparece para ela agendar", "Ecoendoscopia" in html)

# ---------- Desassociar ----------
r = sec.post(f"/equipe/preparos/{modelo_id}/medicos", data={}, follow_redirects=True)
with app.app_context():
    ex = Exame.query.get(exame_id)
    checar("Sem ninguém marcado: o principal fica e a associada sai",
           ex.medico_id == carlos_id and not ex.medico_pode_atender(fernanda_id))
checar("... e ela volta a não ver o preparo", "Preparo Ecoendoscopia" not in
       fernanda_c.get("/equipe/preparo-modelos").get_data(as_text=True))
# ---------- Permissão "Associar médicos aos exames" (2026-10-21) ----------
html = fernanda_c.get("/equipe/", follow_redirects=True).get_data(as_text=True)
menu = html[html.find('<nav class="app-sidebar'):html.find('</nav>', html.find('<nav class="app-sidebar'))]
checar("Médica sem a permissão: o menu não mostra 'Médicos por preparo'", "Médicos por preparo" not in menu)
r = fernanda_c.get("/equipe/preparos/medicos")
checar("... e a tela a manda de volta", r.status_code == 302)
html = sec.get(f"/equipe/equipe-membros/{fernanda_id}/permissoes", follow_redirects=True).get_data(as_text=True)
checar("A tela de permissões tem 'Associar médicos aos exames'",
       'name="perm_medicos_preparo"' in html and "Associar médicos aos exames" in html)
sec.post(f"/equipe/equipe-membros/{fernanda_id}/permissoes", data={"perm_medicos_preparo": "on"})
with app.app_context():
    checar("... e salvar liga a permissão", Usuario.query.get(fernanda_id).perm_medicos_preparo)
html = fernanda_c.get("/equipe/preparos/medicos").get_data(as_text=True)
checar("Com a permissão, ela abre a tela (só com os preparos dela - o do Dr. Carlos não)",
       "Médicos por preparo" in html and "Preparo Ecoendoscopia" not in html)
r = fernanda_c.post(f"/equipe/preparos/{modelo_id}/medicos", data={"medico_ids": [str(fernanda_id)]}, follow_redirects=True)
with app.app_context():
    checar("Médico sem acesso ao preparo não consegue se associar sozinho",
           not Exame.query.get(exame_id).medico_pode_atender(fernanda_id))

print("\nTodos os testes de médicos por preparo passaram.")
