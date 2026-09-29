"""Testa a correção pedida pelo Silvan (2026-09-28, a partir de um bug
real: um paciente perguntou "Posso tomar nimesulida?" pelo WhatsApp e a
resposta - dizendo que a nimesulida não está cadastrada neste preparo e
dando uma orientação genérica sobre anti-inflamatórios - foi enviada
DIRETO ao paciente, sem passar pelo médico, porque a aprovação geral
estava desativada para aquele médico/Grupo (ver
app.routes_paciente.exige_aprovacao_pergunta). O Silvan corrigiu a
combinação errada que eu tinha proposto (reativar o parâmetro geral) com
a regra de verdade: "O que combinamos é que se não achar uma resposta
correta, deve ir ao médico para revisar a resposta" - ou seja, esse tipo
específico de resposta (medicamento identificado mas NÃO cadastrado no
preparo, complementado com conhecimento farmacológico genérico da IA -
ver a regra correspondente em app.ia_preparo.PROMPT_SISTEMA) precisa
SEMPRE passar pelo médico, mesmo com a aprovação geral desativada -
diferente de uma resposta tirada com confiança do preparo cadastrado,
essa é só um complemento de conhecimento geral, não uma certeza.

Mecanismo (ver app/ia_preparo.py): a IA é instruída a começar a resposta
com o marcador MARCADOR_MEDICAMENTO_NAO_CADASTRADO quando for esse o
caso. `_perguntar_claude`/`_perguntar_chatgpt`/`_perguntar_gemini`
detectam e removem o marcador (via
`_extrair_marcador_medicamento_nao_cadastrado`), devolvendo um 4º item na
tupla (`exige_revisao_medicamento_bool`). `responder_com_ia` agrega esse
sinal (True se a resposta que compõe o rascunho final veio de uma IA que
usou o marcador) e devolve em `resultado["exige_revisao_medicamento"]`.
`app.routes_paciente.chat()` e `app.whatsapp_conversa._responder_pergunta`
usam esse sinal para forçar `status="aguardando_aprovacao"` mesmo quando
`exige_aprovacao_pergunta()` diz que a aprovação geral está desativada.

Como este ambiente de teste não tem nenhuma API key de IA configurada
(ver app/ia_preparo.py e HANDOFF_CHAT.md), os testes usam clientes fake
(mesmo estilo de test_ia_sem_sentido.py) para a detecção/remoção do
marcador, e `unittest.mock.patch` em `responder_com_ia` (mesmo estilo de
test_conversa_contexto_ia.py) para os testes de ponta a ponta do chat web
e do WhatsApp."""
from types import SimpleNamespace
from unittest.mock import patch

from app import create_app, db
from app.ia_preparo import (
    MARCADOR_MEDICAMENTO_NAO_CADASTRADO,
    _extrair_marcador_medicamento_nao_cadastrado,
    _perguntar_claude,
    responder_com_ia,
)
from app.models import Agendamento, Exame, Grupo, Paciente, PerguntaPendente
from app.whatsapp_conversa import processar_mensagem

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login_paciente(cpf, dn):
    return client.post("/login-paciente", data={"cpf": cpf, "data_nascimento": dn}, follow_redirects=True)


# ---------------------------------------------------------------------
# 1) _extrair_marcador_medicamento_nao_cadastrado (função pura)
# ---------------------------------------------------------------------
texto_com_marcador = f"{MARCADOR_MEDICAMENTO_NAO_CADASTRADO}\nA nimesulida não está cadastrada neste preparo. Anti-inflamatórios geralmente precisam de atenção antes de exames com risco de sangramento."
resto, exige = _extrair_marcador_medicamento_nao_cadastrado(texto_com_marcador)
checar("Marcador é removido do início do texto", not resto.startswith(MARCADOR_MEDICAMENTO_NAO_CADASTRADO))
checar("Texto restante preserva a orientação de verdade", "nimesulida" in resto and "anti-inflamat" in resto.lower())
checar("Sinaliza exige_revisao=True quando o marcador está presente", exige is True)

texto_com_espaco = f"  {MARCADOR_MEDICAMENTO_NAO_CADASTRADO}  \nResposta normal aqui."
resto2, exige2 = _extrair_marcador_medicamento_nao_cadastrado(texto_com_espaco)
checar("Também reconhece o marcador com espaço/quebra de linha antes dele", exige2 is True)
checar("Remove o marcador mesmo com espaço extra em volta", "Resposta normal aqui." in resto2)

texto_sem_marcador = "Água pura é permitida durante o jejum."
resto3, exige3 = _extrair_marcador_medicamento_nao_cadastrado(texto_sem_marcador)
checar("Sem o marcador, o texto não é alterado", resto3 == texto_sem_marcador)
checar("Sem o marcador, exige_revisao=False", exige3 is False)


# ---------------------------------------------------------------------
# 2) _perguntar_claude detecta e remove o marcador (cliente fake, sem
#    nenhuma chamada de API de verdade - mesmo estilo de
#    test_ia_sem_sentido.py)
# ---------------------------------------------------------------------
with app.app_context():
    texto_resposta_ia = f"{MARCADOR_MEDICAMENTO_NAO_CADASTRADO}\nA nimesulida não está cadastrada neste preparo. Anti-inflamatórios geralmente precisam de avaliação antes de exames com risco de sangramento."
    cliente_claude_medicamento = SimpleNamespace(
        messages=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(
                content=[SimpleNamespace(text=texto_resposta_ia)],
                usage=SimpleNamespace(input_tokens=10, output_tokens=2),
                model="claude-sonnet-4-5",
            )
        )
    )
    texto, chamada, sem_sentido, exige_revisao = _perguntar_claude(
        cliente_claude_medicamento, "Posso tomar nimesulida?", "contexto",
    )
    checar("_perguntar_claude devolve o texto sem o marcador", texto is not None and MARCADOR_MEDICAMENTO_NAO_CADASTRADO not in texto)
    checar("_perguntar_claude preserva a orientação genérica de verdade", "nimesulida" in texto)
    checar("_perguntar_claude sinaliza exige_revisao=True", exige_revisao is True)
    checar("_perguntar_claude NãO sinaliza sem_sentido nesse caso", sem_sentido is False)
    checar("_perguntar_claude registra a ChamadaIA normalmente", chamada is not None)

    # Regressão: uma resposta normal (sem o marcador) continua com
    # exige_revisao=False - o comportamento de sempre não muda.
    cliente_claude_normal = SimpleNamespace(
        messages=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(
                content=[SimpleNamespace(text="Água pura é permitida durante o jejum.")],
                usage=SimpleNamespace(input_tokens=10, output_tokens=2),
                model="claude-sonnet-4-5",
            )
        )
    )
    texto_normal, _chamada_n, _sem_sentido_n, exige_revisao_normal = _perguntar_claude(
        cliente_claude_normal, "Posso beber água?", "contexto",
    )
    checar("Resposta normal (sem marcador) continua com exige_revisao=False", exige_revisao_normal is False)
    checar("Resposta normal (sem marcador) não é alterada", texto_normal == "Água pura é permitida durante o jejum.")


# ---------------------------------------------------------------------
# 3) responder_com_ia agrega o sinal (mockando _tentar_provedor, mesmo
#    estilo de test_ia_sem_sentido.py)
# ---------------------------------------------------------------------
with app.app_context():
    from app.models import PlataformaConfig

    config = PlataformaConfig.obter()
    config.ia_chat_provedor_1 = "Claude"
    config.ia_chat_provedor_2 = "ChatGPT"
    db.session.commit()

    exame_fake = SimpleNamespace(nome="Exame teste", descricao=None, agendamentos=[], preparo=None)

    resposta_medicamento_texto = "A nimesulida não está cadastrada neste preparo. Anti-inflamatórios geralmente precisam de avaliação antes de exames com risco de sangramento."

    # Caso 1: só a IA "provedor 1" respondeu, e usou o marcador -> o
    # rascunho final (dela) precisa de revisão.
    def side_effect_uma_ia_medicamento(nome_provedor, *args, **kwargs):
        if nome_provedor == "Claude":
            return (resposta_medicamento_texto, "chamada-fake", True, False, True)
        return (None, None, False, False, False)

    with patch("app.ia_preparo._tentar_provedor", side_effect=side_effect_uma_ia_medicamento):
        resultado1 = responder_com_ia("Posso tomar nimesulida?", exame_fake)
    checar("Uma IA respondeu com o marcador -> final é a resposta dela", resultado1["final"] == resposta_medicamento_texto)
    checar("Uma IA respondeu com o marcador -> exige_revisao_medicamento=True", resultado1["exige_revisao_medicamento"] is True)

    # Caso 2: as duas IAs concordam, mas nenhuma usou o marcador ->
    # exige_revisao_medicamento=False (regressão - comportamento normal
    # não muda).
    def side_effect_duas_ias_normal(nome_provedor, *args, **kwargs):
        if nome_provedor in ("Claude", "ChatGPT"):
            return ("Água pura é permitida durante o jejum.", "chamada-fake", True, False, False)
        return (None, None, False, False, False)

    with patch("app.ia_preparo._tentar_provedor", side_effect=side_effect_duas_ias_normal):
        resultado2 = responder_com_ia("Posso beber água?", exame_fake)
    checar("Duas IAs concordam sem o marcador -> exige_revisao_medicamento=False", resultado2["exige_revisao_medicamento"] is False)

    # Caso 3: nenhuma IA respondeu (final None) -> exige_revisao_medicamento
    # tem que ser False (não há nada pra revisar).
    def side_effect_nenhuma_responde(nome_provedor, *args, **kwargs):
        return (None, None, False, False, False)

    with patch("app.ia_preparo._tentar_provedor", side_effect=side_effect_nenhuma_responde):
        resultado3 = responder_com_ia("qualquer pergunta", exame_fake)
    checar("Nenhuma IA respondeu -> final None", resultado3["final"] is None)
    checar("Nenhuma IA respondeu -> exige_revisao_medicamento=False", resultado3["exige_revisao_medicamento"] is False)


# ---------------------------------------------------------------------
# 4) Ponta a ponta - chat web (app.routes_paciente.chat()): com a
#    aprovação geral DASATIVADA no Grupo, uma resposta com
#    exige_revisao_medicamento=True ainda assim fica aguardando
#    aprovação (não vai direto ao paciente) - e uma resposta normal
#    (exige_revisao_medicamento=False) continua indo direto, como já
#    era o comportamento de sempre (ver test_aprovacao_configuravel.py).
# ---------------------------------------------------------------------
with app.app_context():
    joao = Paciente.query.filter_by(cpf="123.456.789-00").first()
    clinica_vitoria = Grupo.query.filter_by(nome="Clínica Vitória").first()
    colonoscopia = Exame.query.filter_by(grupo_id=clinica_vitoria.id, nome="Colonoscopia").first()
    exame_id = colonoscopia.id

    valor_original_aprovacao = clinica_vitoria.aprovacao_perguntas_paciente
    clinica_vitoria.aprovacao_perguntas_paciente = False
    db.session.commit()

    try:
        login_paciente("123.456.789-00", "1985-04-12")

        pergunta_medicamento_web = "[teste-medicamento-web] Posso tomar nimesulida antes do exame?"
        resultado_ia_medicamento = {
            "final": resposta_medicamento_texto,
            "por_provedor": {"Claude": resposta_medicamento_texto, "ChatGPT": None, "Gemini": None},
            "falhas": [],
            "sem_sentido": False,
            "exige_revisao_medicamento": True,
        }
        with patch("app.routes_paciente.responder_com_ia", return_value=resultado_ia_medicamento):
            resposta_web = client.post(
                "/paciente/chat",
                data={"pergunta": pergunta_medicamento_web, "exame_id": str(exame_id)},
                follow_redirects=True,
            ).get_data(as_text=True)
        checar(
            "Web, aprovação geral desativada, resposta com marcador -> NÃO vai direto ao paciente",
            "nimesulida" not in resposta_web.lower(),
        )
        pendente_medicamento = (
            PerguntaPendente.query.filter_by(paciente_id=joao.id, pergunta=pergunta_medicamento_web)
            .order_by(PerguntaPendente.id.desc())
            .first()
        )
        checar("Web: PerguntaPendente foi criada", pendente_medicamento is not None)
        checar(
            'Web: fica com status "aguardando_aprovacao", mesmo com a aprovação geral desativada',
            pendente_medicamento.status == "aguardando_aprovacao",
        )
        pendente_medicamento.status = "respondida"
        db.session.commit()

        # Regressão: resposta normal (sem o marcador), mesma aprovação
        # geral desativada -> continua indo direto, como sempre.
        pergunta_normal_web = "[teste-medicamento-web] Esta pergunta não tem nada a ver com medicamento não cadastrado"
        resultado_ia_normal = {
            "final": "Resposta normal da IA, sem nenhum problema de cadastro.",
            "por_provedor": {"Claude": "Resposta normal da IA, sem nenhum problema de cadastro.", "ChatGPT": None, "Gemini": None},
            "falhas": [],
            "sem_sentido": False,
            "exige_revisao_medicamento": False,
        }
        with patch("app.routes_paciente.responder_com_ia", return_value=resultado_ia_normal):
            resposta_web_normal = client.post(
                "/paciente/chat",
                data={"pergunta": pergunta_normal_web, "exame_id": str(exame_id)},
                follow_redirects=True,
            ).get_data(as_text=True)
        checar(
            "Web, aprovação geral desativada, resposta SEM marcador -> vai direto ao paciente (regressão)",
            "resposta normal da ia" in resposta_web_normal.lower(),
        )

        client.get("/logout")
    finally:
        # Nunca deixa esse Grupo com aprovação desativada pros outros
        # arquivos de teste desta suíte que rodem depois.
        clinica_vitoria.aprovacao_perguntas_paciente = valor_original_aprovacao
        db.session.commit()


# ---------------------------------------------------------------------
# 5) Ponta a ponta - WhatsApp (app.whatsapp_conversa._responder_pergunta):
#    mesmo comportamento do item 4, mas pelo canal WhatsApp - usa um
#    paciente novo (mesmo motivo de test_conversa_contexto_ia.py: evitar
#    depender de agendamentos deixados por outros testes) e o próprio
#    Grupo "Clínica Vitória" com aprovação desativada de novo.
# ---------------------------------------------------------------------
with app.app_context():
    clinica_vitoria = Grupo.query.filter_by(nome="Clínica Vitória").first()
    colonoscopia = Exame.query.filter_by(grupo_id=clinica_vitoria.id, nome="Colonoscopia").first()
    exame_id = colonoscopia.id

    from datetime import datetime

    paciente_zap_medicamento = Paciente(
        nome="Paciente Teste Medicamento WhatsApp",
        cpf="000.222.333-44",
        data_nascimento=datetime(1988, 3, 20).date(),
    )
    db.session.add(paciente_zap_medicamento)
    db.session.commit()
    db.session.add(Agendamento(
        grupo_id=clinica_vitoria.id, paciente_id=paciente_zap_medicamento.id, exame_id=exame_id,
        medico_id=colonoscopia.medico_id, data_hora=datetime(2026, 10, 2, 8, 0),
    ))
    db.session.commit()

    valor_original_aprovacao_zap = clinica_vitoria.aprovacao_perguntas_paciente
    clinica_vitoria.aprovacao_perguntas_paciente = False
    db.session.commit()

    try:
        telefone_medicamento = "+5527900006666"
        processar_mensagem(telefone_medicamento, "000.222.333-44")
        processar_mensagem(telefone_medicamento, "20/03/1988")

        resultado_ia_medicamento_zap = {
            "final": resposta_medicamento_texto,
            "por_provedor": {"Claude": resposta_medicamento_texto, "ChatGPT": None, "Gemini": None},
            "falhas": [],
            "sem_sentido": False,
            "exige_revisao_medicamento": True,
        }
        with patch("app.whatsapp_conversa.responder_com_ia", return_value=resultado_ia_medicamento_zap):
            resposta_zap = processar_mensagem(telefone_medicamento, "Posso tomar nimesulida antes do exame?")
        checar(
            "WhatsApp, aprovação geral desativada, resposta com marcador -> NÃO vai direto ao paciente",
            "nimesulida" not in resposta_zap.lower(),
        )
        pendente_zap = (
            PerguntaPendente.query.filter_by(paciente_id=paciente_zap_medicamento.id)
            .order_by(PerguntaPendente.id.desc())
            .first()
        )
        checar("WhatsApp: PerguntaPendente foi criada e continua existindo (não foi aprovada automaticamente)", pendente_zap is not None)
        checar(
            'WhatsApp: fica com status "aguardando_aprovacao", mesmo com a aprovação geral desativada',
            pendente_zap.status == "aguardando_aprovacao",
        )
        pendente_zap.status = "respondida"
        db.session.commit()

        # Regressão: resposta normal (sem o marcador) continua indo
        # direto pelo WhatsApp, como sempre.
        resultado_ia_normal_zap = {
            "final": "Resposta normal da IA, sem nenhum problema de cadastro.",
            "por_provedor": {"Claude": "Resposta normal da IA, sem nenhum problema de cadastro.", "ChatGPT": None, "Gemini": None},
            "falhas": [],
            "sem_sentido": False,
            "exige_revisao_medicamento": False,
        }
        with patch("app.whatsapp_conversa.responder_com_ia", return_value=resultado_ia_normal_zap):
            resposta_zap_normal = processar_mensagem(telefone_medicamento, "Outra pergunta qualquer, sem relação com medicamento não cadastrado")
        checar(
            "WhatsApp, aprovação geral desativada, resposta SEM marcador -> vai direto ao paciente (regressão)",
            "resposta normal da ia" in resposta_zap_normal.lower(),
        )
    finally:
        clinica_vitoria.aprovacao_perguntas_paciente = valor_original_aprovacao_zap
        db.session.commit()

print("\nTodos os testes da revisão obrigatória para medicamento não cadastrado passaram.")
