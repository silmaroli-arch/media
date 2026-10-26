"""Monitor de perguntas na área do dono (pedido do Silvan, 2026-10-26)."""
from unittest.mock import MagicMock, patch

from app import create_app, db
from app.models import Exame, Grupo, MonitorPergunta, Paciente, PerguntaPendente, PlataformaConfig, Usuario
from app.whatsapp_conversa import processar_mensagem

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False


def checar(nome, condicao):
    print(f"[{'OK' if condicao else 'FALHOU'}] {nome}")
    assert condicao, nome


def cliente_fake(texto):
    cliente, bloco, resposta = MagicMock(), MagicMock(), MagicMock()
    bloco.text = texto
    resposta.content, resposta.usage, resposta.model = [bloco], None, "claude"
    cliente.messages.create.return_value = resposta
    return cliente


def validador(texto):
    return patch.dict("app.ia_preparo._PROVEDORES_CHAT", {"Claude": (lambda: cliente_fake(texto), None)})


telefone = "+5527900007777"
with app.app_context():
    PlataformaConfig.obter().ia_validador_pergunta = "Claude"
    db.session.commit()
    dono_email = Usuario.query.filter_by(tipo="dono").first().email
    processar_mensagem(telefone, "111.222.333-00")
    processar_mensagem(telefone, "25/12/1978")
    pedro = Paciente.query.filter_by(nome="Pedro Souza").first()
    pedro_id = pedro.id

    # Pergunta com frase corrigida, ANVISA consultada, IAs respondendo.
    ia = {"final": "Suspenda o Poviztra 14 dias antes.", "falhas": [], "sem_sentido": False, "pesquisa_internet": "Poviztra = semaglutida",
          "por_provedor": {"Claude": "Suspenda.", "ChatGPT": "Não tome.", "Gemini": None},
          "situacao": {"Claude": "respondeu", "ChatGPT": "respondeu"}}

    def com_anvisa(*a, **k):
        from app.monitor_perguntas import anotar
        anotar(anvisa="Poviztra = semaglutida (ANVISA)", modelos="Claude=claude-sonnet-5-5;ChatGPT=gpt-4o-mini", divergiu=True, sintese_usada=True)
        return ia

    with validador("VALIDA\nPERGUNTA: Posso tomar Poviztra?"), patch("app.whatsapp_conversa.responder_com_ia", side_effect=com_anvisa):
        processar_mensagem(telefone, "posso tomar poviztra?")
    db.session.commit()
    r = MonitorPergunta.query.filter_by(paciente_id=pedro_id).order_by(MonitorPergunta.id.desc()).first()
    checar("Pergunta de IA é registrada com desfecho, correção, ANVISA e internet",
           r.desfecho == "ia_aguardando" and r.corrigida and r.pergunta == "posso tomar poviztra?"
           and r.pergunta_corrigida == "Posso tomar Poviztra?" and r.consultou_anvisa and r.consultou_internet
           and r.divergiu and r.sintese_usada and r.canal == "whatsapp" and r.validacao == "valida")
    checar("... com as respostas de cada IA, os modelos e o rascunho",
           r.resposta_claude == "Suspenda." and r.resposta_chatgpt == "Não tome." and "claude-sonnet-5-5" in r.modelos
           and r.rascunho.startswith("Suspenda o Poviztra"))
    checar("... e ainda sem resposta final (aguardando o médico)", r.final == (None, "aguardando o médico") and r.pendente is not None)
    registro_id, pendente_id = r.id, r.pergunta_pendente_id

    # Sem sentido pelo validador.
    with validador("SEM_SENTIDO_ENCAMINHAR"):
        processar_mensagem(telefone, "Posso comer a lua amarela?")
    db.session.commit()
    checar("Pergunta barrada pelo validador também aparece", MonitorPergunta.query.filter_by(desfecho="sem_sentido").count() == 1)

dono = app.test_client()
dono.post("/login", data={"identificador": dono_email, "senha": "123456"})
html = dono.get("/dono/monitor-perguntas").get_data(as_text=True)
checar("Monitor lista a pergunta, a frase corrigida e os selos do caminho",
       "posso tomar poviztra?" in html and "Corrigida pela IA" in html and "ANVISA" in html and "internet" in html and "junção" in html)
checar("... com paciente, modelos e resposta de cada IA nos detalhes",
       "Pedro Souza" in html and "claude-sonnet-5-5" in html and "Sugestão do ChatGPT" in html and "aguardando o médico" in html)
checar("Menu do dono tem o Monitor de perguntas", "Monitor de perguntas" in dono.get("/dono/").get_data(as_text=True))
checar("Filtro por desfecho", "posso tomar poviztra" not in dono.get("/dono/monitor-perguntas?desfecho=sem_sentido").get_data(as_text=True))
checar("Filtro 'consultou ANVISA'", "posso tomar poviztra" in dono.get("/dono/monitor-perguntas?anvisa=1").get_data(as_text=True))
checar("Filtro 'frase corrigida' e busca por texto", "posso tomar poviztra" in dono.get("/dono/monitor-perguntas?corrigida=1&q=poviztra").get_data(as_text=True)
       and "posso tomar poviztra" not in dono.get("/dono/monitor-perguntas?q=outracoisa").get_data(as_text=True))

# Médico aprova: a resposta final aparece no monitor.
medico = app.test_client()
medico.post("/login", data={"identificador": "medica2@clinicavitoria.com", "senha": "123456"})
with app.app_context():
    vitoria_id = Grupo.query.filter_by(nome="Clínica Vitória").first().id
medico.post("/equipe/clinica", data={"clinica_id": str(vitoria_id)}, follow_redirects=True)
with patch("app.whatsapp_envio.enviar_mensagem_whatsapp"):
    medico.post(f"/equipe/perguntas/{pendente_id}/responder", data={"resposta": "Suspenda o Poviztra 14 dias antes do exame."})
html = dono.get("/dono/monitor-perguntas").get_data(as_text=True)
checar("Depois da aprovação, o monitor mostra a resposta final do médico",
       "Suspenda o Poviztra 14 dias antes do exame." in html and "aprovada pelo médico" in html)

# O FAQ grava quem respondeu (só aparece na área da equipe).
from app.models import FaqItem
with app.app_context():
    faq = FaqItem.query.order_by(FaqItem.id.desc()).first()
    medica = Usuario.query.filter_by(email="medica2@clinicavitoria.com").first()
    checar("FAQ guarda o id e a data de quem aprovou", faq.respondido_por_id == medica.id and faq.respondido_em is not None
           and faq.criado_por == medica.nome)
html = medico.get("/equipe/faq").get_data(as_text=True)
checar("Meu FAQ mostra quem respondeu", "Respondido por" in html and medica.nome in html)

# Mesma pergunta de novo: vem do FAQ.
with app.app_context():
    with validador("VALIDA\nPERGUNTA: Posso tomar Poviztra?"), patch("app.whatsapp_conversa.responder_com_ia") as chamada:
        processar_mensagem(telefone, "Posso tomar Poviztra?")
    db.session.commit()
    r = MonitorPergunta.query.order_by(MonitorPergunta.id.desc()).first()
    checar("Pergunta repetida: desfecho FAQ, com a resposta final gravada", r.desfecho == "faq" and r.faq_item_id and r.resposta_final
           and not chamada.called)

print("TUDO OK")
