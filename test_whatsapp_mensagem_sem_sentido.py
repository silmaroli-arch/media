"""Testa a validação mínima de "isso parece um texto de verdade" em
app.whatsapp_conversa (pedido do Silvan, 2026-09-24, com print de um caso
real: mandou ":(&;" por engano de digitação, sem ser uma pergunta de
verdade nenhuma, e isso virou uma PerguntaPendente encaminhada pra
equipe). `_eh_mensagem_sem_sentido_minimo` filtra esse tipo de mensagem
(símbolo/emoji solto, número colado, pontuação repetida) ANTES de virar
pergunta - ver docstring de app.whatsapp_conversa.

No mesmo dia, o Silvan perguntou se um teclado travado/preso (ex.:
"eeeeeeeeeeee") também seria pego - a resposta era não na primeira versão
desta função (são só letras, sem símbolo nenhum), então ela ganhou mais
uma condição: uma letra sozinha não pode responder por quase todas as
letras da mensagem. Efeito colateral aceito dessa correção: uma sequência
de uma letra só repetida em geral (ex.: "kkkk"/"aaaaa" isolados, sem mais
nenhuma letra na mensagem) passou a ser tratada como sem sentido também -
não só o teclado travado em si.

Importante: NÃO é uma correção ortográfica nem um julgamento de "faz
sentido de verdade em português" - só filtra os casos mais óbvios (texto
sem quase nenhuma letra, ou só uma letra repetida). Erro de digitação
normal dentro de palavras de verdade continua passando direto, e uma
saudação de verdade como "oi" continua virando pergunta encaminhada pra
equipe (tradeoff aceito em 2026-09-14, quando o gatilho "1" foi removido -
esta mudança não resolve isso, só os casos mais extremos de mensagem sem
nenhuma cara de texto).

Duas partes: 1) `_eh_mensagem_sem_sentido_minimo` isolada (função pura de
texto, sem banco); 2) via `processar_mensagem`, confirmando que uma
mensagem sem sentido devolve o aviso pra reescrever SEM criar
PerguntaPendente/ChatMensagem nenhum, e que uma pergunta de verdade (até
com erro de digitação) continua funcionando normalmente depois disso, na
mesma conversa."""
from app.whatsapp_conversa import _eh_mensagem_sem_sentido_minimo, _normalizar_texto


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def sem_sentido(texto):
    return _eh_mensagem_sem_sentido_minimo(_normalizar_texto(texto))


# --- Parte 1: função pura, sem banco ---

checar("Caso real que motivou esta correção (\":(&;\") é sem sentido", sem_sentido(":(&;"))
checar("Só pontuação repetida (\"???\") é sem sentido", sem_sentido("???"))
checar("Só pontuação repetida (\"...\") é sem sentido", sem_sentido("..."))
checar("Emoji solto, sem nenhuma letra, é sem sentido", sem_sentido("😀👍"))
checar("Número colado (ex.: telefone digitado por engano) é sem sentido", sem_sentido("27999998888"))
checar("Uma letra só (\"k\") é sem sentido (não forma nem uma palavra mínima)", sem_sentido("k"))
checar(
    "Teclado travado/preso (\"eeeeeeeeeeee\") é sem sentido (pergunta do Silvan, 2026-09-24)",
    sem_sentido("eeeeeeeeeeee"),
)
checar("Só uma letra repetida (\"aaaaaaa\") é sem sentido, mesmo sem nenhum símbolo", sem_sentido("aaaaaaa"))
checar(
    "Risada (\"kkkk\"), sem mais nenhuma letra na mensagem, também é sem sentido "
    "(efeito colateral aceito da correção do teclado travado)",
    sem_sentido("kkkk"),
)

checar("Saudação de verdade (\"oi\") NÃO é sem sentido (tradeoff já aceito em 2026-09-14)", not sem_sentido("oi"))
checar(
    "Risada com duas letras alternadas (\"hahaha\") NÃO é sem sentido (tem variedade de letras)",
    not sem_sentido("hahaha"),
)
checar("Palavra de verdade com letra repetida (\"carro\") NÃO é sem sentido", not sem_sentido("carro"))
checar("Pergunta de verdade, sem erro nenhum, NÃO é sem sentido", not sem_sentido("Posso comer batata frita?"))
checar(
    "Pergunta de verdade COM erro de digitação comum continua passando",
    not sem_sentido("Posso tomar 2 comprimidos de Buscopan as 22h?"),
)
checar(
    "Pergunta de verdade com abreviação comum (\"vc\") continua passando",
    not sem_sentido("Vc pode midicar em jejum"),
)
checar(
    "Emoji + palavra de verdade (mais letra do que símbolo) NÃO é sem sentido",
    not sem_sentido("😊😊😊 obrigada"),
)
checar("Mensagem vazia não é tratada aqui (fica pra MENSAGEM_PERGUNTA_VAZIA)", not sem_sentido(""))


# --- Parte 2: via processar_mensagem, com banco ---
from app import create_app, db
from app.models import ChatMensagem, ConversaWhatsapp, Paciente, PerguntaPendente
from app.whatsapp_conversa import MENSAGEM_MENSAGEM_SEM_SENTIDO, processar_mensagem

app = create_app()

with app.app_context():
    joao = Paciente.query.filter_by(cpf="123.456.789-00").first()
    telefone = "+5527900008765"

    ConversaWhatsapp.query.filter_by(telefone=telefone).delete()
    db.session.commit()

    processar_mensagem(telefone, "123.456.789-00")
    processar_mensagem(telefone, "12/04/1985")  # identifica o João (exame único: colonoscopia)

    perguntas_antes = PerguntaPendente.query.filter_by(paciente_id=joao.id).count()
    mensagens_antes = ChatMensagem.query.filter_by(paciente_id=joao.id).count()

    resposta = processar_mensagem(telefone, ":(&;")
    checar(
        "Mensagem sem sentido devolve o aviso pra reescrever, não a resposta de uma pergunta",
        resposta == MENSAGEM_MENSAGEM_SEM_SENTIDO,
    )
    checar(
        "Mensagem sem sentido NÃO cria PerguntaPendente",
        PerguntaPendente.query.filter_by(paciente_id=joao.id).count() == perguntas_antes,
    )
    checar(
        "Mensagem sem sentido NÃO cria ChatMensagem (nem fica registrada como pergunta)",
        ChatMensagem.query.filter_by(paciente_id=joao.id).count() == mensagens_antes,
    )

    # Teclado travado/preso via processar_mensagem de verdade (pergunta do
    # Silvan que motivou a correção da variedade de letras) - mesmo
    # comportamento: só o aviso, sem criar nada.
    resposta = processar_mensagem(telefone, "eeeeeeeeeeee")
    checar(
        "Teclado travado (\"eeeeeeeeeeee\") também devolve o aviso pra reescrever",
        resposta == MENSAGEM_MENSAGEM_SEM_SENTIDO,
    )
    checar(
        "Teclado travado NÃO cria PerguntaPendente",
        PerguntaPendente.query.filter_by(paciente_id=joao.id).count() == perguntas_antes,
    )

    # Depois do aviso, uma pergunta de verdade (mesmo com erro de
    # digitação) continua funcionando normalmente, na mesma conversa.
    resposta = processar_mensagem(telefone, "Posso beber agua durante o jejum?")
    checar(
        "Pergunta de verdade depois do aviso funciona normalmente (bate com a FAQ do seed.py)",
        "água pura é permitida" in resposta,
    )

# --- Parte 3: julgamento pela IA (camada híbrida, pedido do Silvan,
# 2026-09-24) - a checagem por regras fixas (partes 1 e 2 acima) só pega
# os casos óbvios; palavras reais em ordem sem sentido passam por ela e
# só são pegas pela IA (ver app.ia_preparo.responder_com_ia, chave
# "sem_sentido"). Testado aqui mockando `responder_com_ia` diretamente
# (mesmo padrão já usado em test_whatsapp_pergunta.py para simular a IA
# sem precisar de nenhuma API key configurada) - confirma que o mesmo
# aviso é devolvido, sem PerguntaPendente/ChatMensagem, e sem colar o
# convite de próxima pergunta (diferente de uma pergunta respondida de
# verdade).
from unittest.mock import patch

with app.app_context():
    perguntas_antes3 = PerguntaPendente.query.filter_by(paciente_id=joao.id).count()
    mensagens_antes3 = ChatMensagem.query.filter_by(paciente_id=joao.id).count()

    with patch(
        "app.whatsapp_conversa.responder_com_ia",
        return_value={"final": None, "por_provedor": {"Claude": None, "ChatGPT": None, "Gemini": None}, "falhas": [], "sem_sentido": True},
    ) as ia_mock:
        resposta = processar_mensagem(telefone, "mesa amanhã vidro comprimido depois")

    checar("IA julgando sem sentido é consultada (passou pela checagem por regras fixas)", ia_mock.called)
    checar(
        "IA julgando sem sentido devolve o MESMO aviso da checagem por regras fixas",
        resposta == MENSAGEM_MENSAGEM_SEM_SENTIDO,
    )
    checar(
        "IA julgando sem sentido NÃO cria PerguntaPendente",
        PerguntaPendente.query.filter_by(paciente_id=joao.id).count() == perguntas_antes3,
    )
    checar(
        "IA julgando sem sentido NÃO cria ChatMensagem",
        ChatMensagem.query.filter_by(paciente_id=joao.id).count() == mensagens_antes3,
    )
    checar(
        "IA julgando sem sentido NÃO cola o convite de próxima pergunta (só o aviso puro)",
        "\n\n" not in resposta,
    )

    # Regressão: quando a IA responde normalmente (sem_sentido=False, com
    # "final" preenchido), o fluxo de pergunta de verdade continua igual -
    # não é afetado por esta camada nova.
    with patch(
        "app.whatsapp_conversa.responder_com_ia",
        return_value={
            "final": "Resposta de teste da IA.",
            "por_provedor": {"Claude": "Resposta de teste da IA.", "ChatGPT": None, "Gemini": None},
            "falhas": [],
            "sem_sentido": False,
        },
    ):
        resposta_normal = processar_mensagem(telefone, "[teste-sem-sentido-ia] Posso comer manga antes do exame?")
    checar(
        "Resposta normal da IA (sem_sentido=False) continua sendo encaminhada normalmente",
        "encaminhada" in resposta_normal.lower(),
    )

print("\nTodos os testes da validação mínima de 'isso parece um texto de verdade' passaram.")
