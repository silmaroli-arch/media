"""Conhecimento geral quando o preparo não cobre a dúvida (pedido do
Silvan, 2026-10-21): "Que horas tenho que chegar na clínica?" recebia "não
sei" das 2 IAs, porque o preparo não fala do horário de chegada. Agora a IA
responde com orientação geral, começando com MARCADOR_CONHECIMENTO_GERAL;
a resposta sempre passa pelo médico (mesmo sinal do medicamento não
cadastrado) e a tela avisa que é conhecimento geral."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app import create_app, db
from app.ia_preparo import (
    MARCADOR_CONHECIMENTO_GERAL, PROMPT_SISTEMA, _perguntar_claude, _responder_com_duas_ias, formatar_situacao_ias,
)
from app.models import Exame, Grupo, Paciente, PerguntaPendente, PlataformaConfig
from app.whatsapp_conversa import processar_mensagem

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def cliente(texto):
    return SimpleNamespace(messages=SimpleNamespace(create=lambda **k: SimpleNamespace(
        content=[SimpleNamespace(text=texto)], usage=SimpleNamespace(input_tokens=10, output_tokens=2), model="claude")))


checar("O prompt tem a regra do conhecimento geral", MARCADOR_CONHECIMENTO_GERAL in PROMPT_SISTEMA)

with app.app_context():
    resposta = f"{MARCADOR_CONHECIMENTO_GERAL}\nEm geral, pede-se para chegar de 30 a 60 minutos antes da colonoscopia."
    texto, chamada, sem_sentido, exige_revisao = _perguntar_claude(cliente(resposta), "Que horas chego?", "contexto")
    checar("Marcador sai do texto e a resposta exige revisão",
           texto.startswith("Em geral") and MARCADOR_CONHECIMENTO_GERAL not in texto and exige_revisao)
    resposta = (f"{MARCADOR_CONHECIMENTO_GERAL}\nO preparo não traz essa informação, mas em geral pede-se chegar "
                "30 minutos antes. Confirme com a secretaria.")
    texto, *_ = _perguntar_claude(cliente(resposta), "Que horas chego?", "contexto")
    checar("Com o marcador, não é descartada como recusa disfarçada", texto is not None)

    config = PlataformaConfig.obter()
    config.ia_chat_provedor_1, config.ia_chat_provedor_2 = "Claude", "ChatGPT"
    db.session.commit()
    exame = Exame.query.first()
    chamada = MagicMock()
    respostas = {"Claude": ("Em geral, 30 a 60 minutos antes.", chamada, True, False, True),
                 "ChatGPT": (None, chamada, True, False, False)}
    with patch("app.ia_preparo._tentar_provedor", side_effect=lambda nome, *a, **k: respostas[nome]), \
            patch("app.ia_preparo._cliente_anthropic", return_value=None):
        r = _responder_com_duas_ias("Que horas tenho que chegar?", exame)
    checar("Situação: Claude respondeu com conhecimento geral, ChatGPT não soube",
           r["situacao"] == {"Claude": "respondeu_geral", "ChatGPT": "nao_sabe"} and r["exige_revisao_medicamento"])

    # WhatsApp, com a aprovação automática DESLIGADA: mesmo assim vai para o médico.
    vitoria = Grupo.query.filter_by(nome="Clínica Vitória").first()
    vitoria.aprovacao_perguntas_paciente = False
    db.session.commit()
    telefone = "+5527900009999"
    processar_mensagem(telefone, "111.222.333-00")
    processar_mensagem(telefone, "25121978")
    resultado = {"final": "Em geral, 30 a 60 minutos antes.", "falhas": [], "sem_sentido": False,
                 "exige_revisao_medicamento": True,
                 "por_provedor": {"Claude": "Em geral, 30 a 60 minutos antes.", "ChatGPT": None, "Gemini": None},
                 "situacao": {"Claude": "respondeu_geral", "ChatGPT": "nao_sabe"}}
    with patch("app.whatsapp_conversa.responder_com_ia", return_value=resultado):
        resposta = processar_mensagem(telefone, "Que horas tenho que chegar na clínica?")
    pedro = Paciente.query.filter_by(nome="Pedro Souza").first()
    pendente = PerguntaPendente.query.filter_by(paciente_id=pedro.id).order_by(PerguntaPendente.id.desc()).first()
    checar("Mesmo com a aprovação automática desligada, não vai direto ao paciente",
           "encaminhada" in resposta.lower() and pendente.status == "aguardando_aprovacao")
    checar("... e fica marcada como conhecimento geral", pendente.usou_conhecimento_geral)

medico = app.test_client()
medico.post("/login", data={"identificador": "medica2@clinicavitoria.com", "senha": "123456"})
with app.app_context():
    vitoria_id = Grupo.query.filter_by(nome="Clínica Vitória").first().id
medico.post("/equipe/clinica", data={"clinica_id": str(vitoria_id)}, follow_redirects=True)
html = medico.get("/equipe/portal").get_data(as_text=True)
checar("Portal avisa que é conhecimento geral e identifica a sugestão",
       "conhecimento geral</strong> da IA" in html and "Sugestão do Claude (conhecimento geral):" in html)

print("\nTodos os testes de conhecimento geral passaram.")
