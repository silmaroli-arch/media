"""Testa a checagem de "conversa social" em app.whatsapp_conversa (pedido
do Silvan, 2026-09-24): "obrigado, oi, tchau, bom, dia, boa tarde, boa
noite, olá devem ser apenas considerados como conversa e não pergunta".

Contexto: desde a remoção do gatilho "1" (2026-09-14), qualquer texto
solto virava uma pergunta encaminhada pra equipe - inclusive uma saudação
de verdade como "oi", que TEM "cara de texto" o suficiente pra passar
pela checagem de sem sentido (`_eh_mensagem_sem_sentido_minimo`, ver
test_whatsapp_mensagem_sem_sentido.py). Isso era um tradeoff aceito
explicitamente naquela época (ver docstring do módulo). Esta correção
resolve esse tradeoff especificamente pras saudações/despedidas/
agradecimentos mais comuns: `_eh_apenas_conversa_social` reconhece quando
a mensagem inteira é só isso, e devolve uma resposta simpática (variando
por categoria) em vez de encaminhar como pergunta - sem criar
`PerguntaPendente` nem `ChatMensagem`.

Deliberadamente conservador: só entra em ação quando a mensagem é SÓ
saudação/despedida/agradecimento - combinada com qualquer outra palavra
(uma pergunta de verdade, por exemplo), continua sendo tratada como
pergunta normalmente.

Duas partes: 1) `_eh_apenas_conversa_social`/`_resposta_conversa_social`
isoladas (função pura de texto, sem banco); 2) via `processar_mensagem`,
confirmando que não cria `PerguntaPendente`/`ChatMensagem`, e que uma
saudação seguida de uma pergunta de verdade continua indo pro fluxo
normal."""
from app.whatsapp_conversa import (
    MENSAGEM_AGRADECIMENTO_SOCIAL,
    MENSAGEM_DESPEDIDA_SOCIAL,
    MENSAGEM_SAUDACAO_SOCIAL,
    _eh_apenas_conversa_social,
    _normalizar_texto,
    _resposta_conversa_social,
)


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def eh_social(texto):
    return _eh_apenas_conversa_social(_normalizar_texto(texto))


def resposta_social(texto):
    return _resposta_conversa_social(_normalizar_texto(texto))


# --- Parte 1: função pura, sem banco ---

checar('"oi" sozinho é conversa social', eh_social("oi"))
checar('"Oi!" com pontuação/caixa alta continua sendo conversa social', eh_social("Oi!"))
checar('"olá" (com acento) é conversa social', eh_social("olá"))
checar('"bom dia" é conversa social', eh_social("bom dia"))
checar('"Boa tarde!" é conversa social', eh_social("Boa tarde!"))
checar('"boa noite" é conversa social', eh_social("boa noite"))
checar('"tchau" é conversa social', eh_social("tchau"))
checar('"obrigado" é conversa social', eh_social("obrigado"))
checar('"obrigada" (feminino) também é conversa social', eh_social("obrigada"))
checar('"Oi, bom dia!" (combinação de saudações) é conversa social', eh_social("Oi, bom dia!"))
checar('"Obrigado, tchau!" (combinação) é conversa social', eh_social("Obrigado, tchau!"))

checar(
    '"Oi, posso comer batata?" NÃO é conversa social (tem pergunta de verdade junto)',
    not eh_social("Oi, posso comer batata?"),
)
checar(
    'Pergunta de verdade sem nenhuma saudação NÃO é conversa social',
    not eh_social("Posso beber água durante o jejum?"),
)
checar('Mensagem vazia não é conversa social (tratada à parte)', not eh_social(""))
checar('":(&;" (sem sentido) não é conversa social', not eh_social(":(&;"))

checar(
    'Resposta para "tchau" é a de despedida',
    resposta_social("tchau") == MENSAGEM_DESPEDIDA_SOCIAL,
)
checar(
    'Resposta para "obrigado" é a de agradecimento',
    resposta_social("obrigado") == MENSAGEM_AGRADECIMENTO_SOCIAL,
)
checar(
    'Resposta para "obrigada" (feminino) também é a de agradecimento',
    resposta_social("obrigada") == MENSAGEM_AGRADECIMENTO_SOCIAL,
)
checar(
    'Resposta para "oi"/"bom dia" é a de saudação',
    resposta_social("oi") == MENSAGEM_SAUDACAO_SOCIAL and resposta_social("bom dia") == MENSAGEM_SAUDACAO_SOCIAL,
)
checar(
    'Combinação "Obrigado, tchau!" prioriza a despedida (última palavra costuma ser a mais relevante)',
    resposta_social("Obrigado, tchau!") == MENSAGEM_DESPEDIDA_SOCIAL,
)


# --- Parte 2: via processar_mensagem, com banco ---
from app import create_app, db
from app.models import ChatMensagem, ConversaWhatsapp, Paciente, PerguntaPendente
from app.whatsapp_conversa import processar_mensagem

app = create_app()

with app.app_context():
    joao = Paciente.query.filter_by(cpf="123.456.789-00").first()
    telefone = "+5527900006543"

    ConversaWhatsapp.query.filter_by(telefone=telefone).delete()
    db.session.commit()

    processar_mensagem(telefone, "123.456.789-00")
    processar_mensagem(telefone, "12/04/1985")  # identifica o João (exame único: colonoscopia)

    perguntas_antes = PerguntaPendente.query.filter_by(paciente_id=joao.id).count()
    mensagens_antes = ChatMensagem.query.filter_by(paciente_id=joao.id).count()

    resposta = processar_mensagem(telefone, "Oi")
    # A partir de 2026-09-24 (link do preparo no WhatsApp, ver
    # app.preparo_publico), esta resposta passou a incluir o link público
    # do preparo depois da mensagem simpática de sempre - por isso
    # `startswith` em vez de igualdade exata (ver
    # `_resposta_conversa_social`/chamada em `processar_mensagem`).
    checar(
        "Saudação via processar_mensagem devolve a resposta simpática, não o aviso de encaminhamento",
        resposta.startswith(MENSAGEM_SAUDACAO_SOCIAL),
    )
    checar(
        "Saudação via processar_mensagem inclui o link público do preparo",
        "/paciente/preparo/" in resposta,
    )
    checar(
        "Saudação via processar_mensagem NÃO cria PerguntaPendente",
        PerguntaPendente.query.filter_by(paciente_id=joao.id).count() == perguntas_antes,
    )
    checar(
        "Saudação via processar_mensagem NÃO cria ChatMensagem",
        ChatMensagem.query.filter_by(paciente_id=joao.id).count() == mensagens_antes,
    )

    resposta = processar_mensagem(telefone, "Muito obrigado")
    checar(
        '"Muito obrigado" (com "muito" extra) continua sendo tratado como pergunta, não conversa social '
        "(escopo deliberadamente limitado às palavras exatas pedidas pelo Silvan - ver docstring)",
        resposta != MENSAGEM_AGRADECIMENTO_SOCIAL,
    )

    # Depois da saudação (que não criou nada), uma pergunta de verdade
    # continua funcionando normalmente, na mesma conversa.
    resposta = processar_mensagem(telefone, "Posso beber agua durante o jejum?")
    checar(
        "Pergunta de verdade depois da saudação funciona normalmente (bate com a FAQ do seed.py)",
        "água pura é permitida" in resposta,
    )

    # Saudação + pergunta de verdade na MESMA mensagem: tratada como
    # pergunta (não desviada pela conversa social) - ver docstring.
    perguntas_antes2 = PerguntaPendente.query.filter_by(paciente_id=joao.id).count()
    resposta = processar_mensagem(telefone, "Oi, posso comer amendoim antes do exame?")
    checar(
        '"Oi, posso comer amendoim...?" (saudação + pergunta de verdade) é tratada como pergunta, não conversa social',
        "encaminhada" in resposta.lower(),
    )
    checar(
        "Saudação + pergunta de verdade CRIA uma PerguntaPendente (não é desviada)",
        PerguntaPendente.query.filter_by(paciente_id=joao.id).count() == perguntas_antes2 + 1,
    )

print("\nTodos os testes de conversa social passaram.")
