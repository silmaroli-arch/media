"""Opção "Salvar no Meu FAQ" na aprovação (pedido do Silvan, 2026-10-21):
o médico escolhe se a resposta entra no FAQ (e responde sozinha quem
perguntar o mesmo) ou vale só para este paciente."""
from unittest.mock import patch

from app import create_app, db
from app.models import Exame, FaqItem, Grupo, Paciente, PerguntaPendente

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


with app.app_context():
    vitoria = Grupo.query.filter_by(nome="Clínica Vitória").first()
    hemograma = Exame.query.filter_by(nome="Hemograma completo").first()
    pedro = Paciente.query.filter_by(nome="Pedro Souza").first()
    ids = []
    for texto in ("Pergunta só dele", "Pergunta para todos", "Pergunta de outra tela"):
        p = PerguntaPendente(grupo_id=vitoria.id, paciente_id=pedro.id, exame_id=hemograma.id, pergunta=texto,
                             status="aguardando_aprovacao", resposta_sugerida_ia="Resposta.")
        db.session.add(p)
        db.session.flush()
        ids.append(p.id)
    db.session.commit()
    vitoria_id = vitoria.id

medico = app.test_client()
medico.post("/login", data={"identificador": "medica2@clinicavitoria.com", "senha": "123456"})
medico.post("/equipe/clinica", data={"clinica_id": str(vitoria_id)}, follow_redirects=True)
html = medico.get("/equipe/portal").get_data(as_text=True)
checar("A janela tem 'Salvar no Meu FAQ', marcada por padrão",
       f'name="salvar_faq" id="salvar_faq_{ids[0]}a" checked' in html)
checar("... também em Perguntas pendentes", "Salvar no Meu FAQ" in medico.get("/equipe/perguntas").get_data(as_text=True))

with patch("app.base_aprendizado.agendar_aprendizado") as aprendizado:
    r = medico.post(f"/equipe/perguntas/{ids[0]}/responder",
                    data={"resposta": "Só para você.", "opcao_faq": "1", "origem": "portal"}, follow_redirects=True)
with app.app_context():
    checar("Desmarcada: responde o paciente e NÃO grava no FAQ",
           PerguntaPendente.query.get(ids[0]).status == "respondida"
           and FaqItem.query.filter_by(pergunta="Pergunta só dele").count() == 0
           and "não foi salva no Meu FAQ" in r.get_data(as_text=True))
    checar("... nem ensina o FAQ do MedIA", not aprendizado.called)
medico.post(f"/equipe/perguntas/{ids[1]}/responder", data={"resposta": "Para todos.", "opcao_faq": "1", "salvar_faq": "on"})
medico.post(f"/equipe/perguntas/{ids[2]}/responder", data={"resposta": "Padrão."})
with app.app_context():
    checar("Marcada: grava no FAQ", FaqItem.query.filter_by(pergunta="Pergunta para todos").count() == 1)
    checar("Form sem a opção mantém o padrão (grava)", FaqItem.query.filter_by(pergunta="Pergunta de outra tela").count() == 1)

print("\nTodos os testes da opção Salvar no Meu FAQ passaram.")
