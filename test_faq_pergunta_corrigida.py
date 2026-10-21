"""FAQ com a pergunta corrigida (pedido do Silvan, 2026-10-21): "Posso ir
de carrp?" ia para o FAQ com o erro, e "Posso ir de carro?" depois não
achava a resposta aprovada. A IA validadora (que já roda em toda pergunta
do WhatsApp) devolve também a pergunta com a digitação corrigida; o FAQ
procura e grava essa versão. O médico e o paciente continuam vendo o texto
original."""
from unittest.mock import MagicMock, patch

from app import create_app, db
from app.ia_preparo import validar_pergunta
from app.models import Exame, FaqItem, Paciente, PerguntaPendente, PlataformaConfig, Usuario
from app.whatsapp_conversa import processar_mensagem

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def cliente_fake(texto):
    cliente, bloco, resposta = MagicMock(), MagicMock(), MagicMock()
    bloco.text = texto
    resposta.content, resposta.usage, resposta.model = [bloco], None, "claude"
    cliente.messages.create.return_value = resposta
    return cliente


def validador(texto):
    return patch.dict("app.ia_preparo._PROVEDORES_CHAT", {"Claude": (lambda: cliente_fake(texto), None)})


with app.app_context():
    config = PlataformaConfig.obter()
    config.ia_validador_pergunta = "Claude"
    db.session.commit()
    exame = Exame.query.first()

    # ---------- validar_pergunta devolve a pergunta corrigida ----------
    with validador("VALIDA\nPERGUNTA: Posso ir de carro?"):
        r = validar_pergunta("POsso ir de carrp", exame)
    checar("Validador devolve a pergunta corrigida", r["classificacao"] == "valida" and r["pergunta_corrigida"] == "Posso ir de carro?")
    with validador("VALIDA\nPERGUNTA: Posso comer batata frita no dia anterior ao exame de colonoscopia?"):
        r = validar_pergunta("e frita?", exame)
    checar("Correção que não é só correção (completou com o histórico) é descartada", r["pergunta_corrigida"] is None)
    with validador("VALIDA"):
        r = validar_pergunta("Posso ir de carro?", exame)
    checar("Sem a linha PERGUNTA, segue sem correção (como antes)", r["classificacao"] == "valida" and r["pergunta_corrigida"] is None)
    with validador("FORA_DO_EXAME_ENCAMINHAR"):
        r = validar_pergunta("Quem ganha o jogo?", exame)
    checar("Fora do exame continua reconhecido (e sem correção)", r["classificacao"] == "fora_do_exame" and not r["pergunta_corrigida"])

    # ---------- WhatsApp: grava e procura pela pergunta corrigida ----------
    telefone = "+5527900008888"
    processar_mensagem(telefone, "111.222.333-00")
    processar_mensagem(telefone, "25/12/1978")
    pedro = Paciente.query.filter_by(nome="Pedro Souza").first()
    resultado_ia = {"final": "Não é recomendado dirigir depois do exame.", "falhas": [], "sem_sentido": False,
                    "por_provedor": {"Claude": "Não dirija.", "ChatGPT": "Não dirija.", "Gemini": None}}
    with validador("VALIDA\nPERGUNTA: Posso ir de carro?"), \
            patch("app.whatsapp_conversa.responder_com_ia", return_value=resultado_ia):
        processar_mensagem(telefone, "Posso ir de carrp?")
    pendente = PerguntaPendente.query.filter_by(paciente_id=pedro.id).order_by(PerguntaPendente.id.desc()).first()
    checar("A pergunta pendente guarda o original e a versão corrigida",
           pendente.pergunta == "Posso ir de carrp?" and pendente.pergunta_faq == "Posso ir de carro?")
    pendente_id, exame_id = pendente.id, pendente.exame_id

medico = app.test_client()
medico.post("/login", data={"identificador": "medica2@clinicavitoria.com", "senha": "123456"})
with app.app_context():
    from app.models import Grupo
    vitoria_id = Grupo.query.filter_by(nome="Clínica Vitória").first().id
medico.post("/equipe/clinica", data={"clinica_id": str(vitoria_id)}, follow_redirects=True)
with patch("app.routes_medico.enviar_mensagem_whatsapp", return_value=None, create=True):
    medico.post(f"/equipe/perguntas/{pendente_id}/responder", data={"resposta": "Não dirija depois do exame."})

with app.app_context():
    faq = FaqItem.query.filter_by(exame_id=exame_id).order_by(FaqItem.id.desc()).first()
    checar("Ao aprovar, o FAQ grava a pergunta corrigida", faq.pergunta == "Posso ir de carro?" and "Não dirija" in faq.resposta)
    with validador("VALIDA\nPERGUNTA: Posso ir de carro?"), \
            patch("app.whatsapp_conversa.responder_com_ia") as ia:
        resposta = processar_mensagem(telefone, "POsso ir de carro")
    checar("Mesma pergunta escrita de outro jeito acha a resposta aprovada no FAQ, sem chamar as IAs",
           "Não dirija depois do exame." in resposta and not ia.called)
    with patch.dict("app.ia_preparo._PROVEDORES_CHAT", {"Claude": (lambda: None, None)}), \
            patch("app.whatsapp_conversa.responder_com_ia") as ia:
        resposta = processar_mensagem(telefone, "Posso ir de carro?")
    checar("Sem IA validadora, a pergunta escrita igual ao FAQ continua achando (texto original)",
           "Não dirija depois do exame." in resposta)

print("\nTodos os testes de FAQ com pergunta corrigida passaram.")
