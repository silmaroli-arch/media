"""Remover modelo de preparo (pedido do Silvan, 2026-10-26): só não pode se
houver agendamento ligado a ele; exames que apenas o usam ficam sem preparo."""
from app import create_app, db
from app.models import Agendamento, Exame, PreparoModelo

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False


def checar(nome, condicao):
    print(f"[{'OK' if condicao else 'FALHOU'}] {nome}")
    assert condicao, nome


medico = app.test_client()
medico.post("/login", data={"identificador": "medico@clinicavitoria.com", "senha": "123456"})

with app.app_context():
    ag = next(a for a in Agendamento.query.order_by(Agendamento.id).all()
              if a.exame and a.exame.preparo_modelo and a.medico and a.medico.email == "medico@clinicavitoria.com")
    com_agendamento_id = ag.exame.preparo_modelo_id
    grupo_id = ag.exame.grupo_id
    medico_id = ag.exame.medico_id
    criador_id = ag.exame.preparo_modelo.criado_por_id
    livre = PreparoModelo(grupo_id=grupo_id, nome="Preparo sem agendamento", instrucoes="x", criado_por_id=criador_id)
    db.session.add(livre)
    db.session.flush()
    exame = Exame(grupo_id=grupo_id, medico_id=medico_id, nome="Exame sem agendamento", descricao="",
                  duracao_minutos=30, preparo_modelo_id=livre.id, medico_confirmado=True)
    db.session.add(exame)
    db.session.commit()
    livre_id, exame_id = livre.id, exame.id

medico.post("/equipe/clinica", data={"clinica_id": str(grupo_id)}, follow_redirects=True)
html = medico.get("/equipe/preparo-modelos").get_data(as_text=True)
checar("Lista mostra por que o Remover está bloqueado (agendamento ligado)", "agendamento(s) ligado(s)" in html)

r = medico.post(f"/equipe/preparo-modelos/{com_agendamento_id}/remover", follow_redirects=True)
with app.app_context():
    checar("Com agendamento: não remove e explica",
           "agendamento(s) ligado(s)" in r.get_data(as_text=True) and db.session.get(PreparoModelo, com_agendamento_id) is not None)

r = medico.post(f"/equipe/preparo-modelos/{livre_id}/remover", follow_redirects=True)
with app.app_context():
    checar("Sem agendamento (mesmo com exame usando): remove",
           db.session.get(PreparoModelo, livre_id) is None and "removido" in r.get_data(as_text=True))
    checar("O exame continua, sem preparo", db.session.get(Exame, exame_id).preparo_modelo_id is None)
print("TUDO OK")
