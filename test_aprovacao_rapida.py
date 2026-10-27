"""Aprovação rápida do médico em grade (pedido do Silvan, 2026-10-27)."""
from datetime import datetime
from unittest.mock import patch

from app import create_app, db
from app.models import Exame, Grupo, Paciente, PerguntaPendente

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False


def checar(nome, condicao):
    print(f"[{'OK' if condicao else 'FALHOU'}] {nome}")
    assert condicao, nome


with app.app_context():
    grupo = Grupo.query.filter_by(nome="Clínica Vitória").first()
    exame = Exame.query.filter_by(grupo_id=grupo.id).first()
    paciente = Paciente.query.filter_by(nome="Pedro Souza").first()
    grupo_id, exame_id, paciente_id = grupo.id, exame.id, paciente.id
    base = dict(grupo_id=grupo_id, paciente_id=paciente_id, exame_id=exame_id)
    com_rascunho = PerguntaPendente(**base, pergunta="Posso tomar café?", pergunta_faq="Posso tomar café antes do exame?",
                                    status="aguardando_aprovacao", resposta_sugerida_ia="Não tome café no jejum.",
                                    resposta_bruta_claude="Não tome café.", ias_situacao="Claude=respondeu_geral", pesquisa_internet="X = Y",
                                    criado_em=datetime(2026, 10, 2, 9, 0))
    sem_rascunho = PerguntaPendente(**base, pergunta="Qual o endereço do estacionamento?", status="pendente",
                                    ias_situacao="Claude=nao_sabe", criado_em=datetime(2026, 10, 1, 9, 0))
    db.session.add_all([com_rascunho, sem_rascunho])
    db.session.commit()
    id_a, id_b = com_rascunho.id, sem_rascunho.id

medico = app.test_client()
medico.post("/login", data={"identificador": "medica2@clinicavitoria.com", "senha": "123456"})
medico.post("/equipe/clinica", data={"clinica_id": str(grupo_id)}, follow_redirects=True)
html = medico.get("/equipe/aprovacao-rapida").get_data(as_text=True)
checar("Tela em grade lista as duas perguntas", "grade-aprovacao" in html and "Posso tomar café?" in html and "Qual o endereço do estacionamento?" in html)
checar("Mostra a frase corrigida, o rascunho editável e os selos",
       "Posso tomar café antes do exame?" in html and "Não tome café no jejum." in html
       and "conhecimento geral da IA" in html and "pesquisado na internet" in html and "a IA não soube responder" in html)
checar("Só o cartão com rascunho tem caixa de seleção em lote", html.count('class="form-check-input marcar"') == 1)
checar("Ordenado por data: a mais antiga primeiro (mesmo sem rascunho)", html.index("Qual o endereço") < html.index("Posso tomar café?"))
checar("Menu lateral tem 'Aprovação rápida'", "Aprovação rápida" in medico.get("/equipe/").get_data(as_text=True))

with patch("app.whatsapp_envio.enviar_mensagem_whatsapp"):
    r = medico.post(f"/equipe/perguntas/{id_a}/responder", headers={"X-Requested-With": "fetch"},
                    data={"resposta": "Não tome café durante o jejum.", "origem": "rapida", "opcao_faq": "1", "salvar_faq": "on"})
checar("Aprovar por fetch responde JSON ok", r.status_code == 200 and r.get_json() == {"ok": True, "erro": None})
with app.app_context():
    p = db.session.get(PerguntaPendente, id_a)
    checar("A pergunta ficou respondida pelo médico, com o texto editado", p.status == "respondida" and p.resposta == "Não tome café durante o jejum." and p.respondida_por)
html = medico.get("/equipe/aprovacao-rapida").get_data(as_text=True)
checar("Sai da grade e não sobra aviso (flash) para a próxima tela", "Posso tomar café?" not in html and "Resposta salva" not in html)

r = medico.post(f"/equipe/perguntas/{id_a}/responder", headers={"X-Requested-With": "fetch"}, data={"resposta": "outra"})
checar("Aprovar de novo é recusado", r.status_code == 400 and "já foi respondida" in r.get_json()["erro"])
r = medico.post(f"/equipe/perguntas/{id_b}/responder", headers={"X-Requested-With": "fetch"}, data={"resposta": "  "})
checar("Resposta vazia é recusada", r.status_code == 400 and "Digite uma resposta" in r.get_json()["erro"])
with patch("app.whatsapp_envio.enviar_mensagem_whatsapp"):
    r = medico.post(f"/equipe/perguntas/{id_b}/responder", headers={"X-Requested-With": "fetch"},
                    data={"resposta": "O estacionamento fica ao lado da clínica.", "origem": "rapida"})
checar("Pergunta sem rascunho: o médico digita e aprova", r.status_code == 200)
html = medico.get("/equipe/aprovacao-rapida").get_data(as_text=True)
checar("Fila vazia mostra 'Nada para aprovar'", "Nada para aprovar agora" in html)
# O aviso por WhatsApp (e o push) leva direto à Aprovação rápida.
from app.models import Usuario
from app.push_notificacoes import _notificar_whatsapp_medicos
with app.app_context():
    medica = Usuario.query.filter_by(email="medica2@clinicavitoria.com").first()
    medica.telefone = "27999990000"
    db.session.commit()
    nova = PerguntaPendente(grupo_id=grupo_id, paciente_id=paciente_id, exame_id=exame_id, pergunta="Posso fumar?", status="pendente")
    db.session.add(nova)
    db.session.commit()
    with patch("app.push_notificacoes.enviar_mensagem_whatsapp") as envio:
        _notificar_whatsapp_medicos(nova, [medica.id])
    texto = envio.call_args.kwargs["texto"]
    checar("Aviso por WhatsApp ao médico aponta para a Aprovação rápida", "Posso fumar?" in texto and texto.rstrip(".").endswith("/equipe/aprovacao-rapida"))
print("TUDO OK")
