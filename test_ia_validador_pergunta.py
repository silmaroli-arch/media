"""Testa app.ia_preparo.validar_pergunta (pedido do Silvan, 2026-09-24 -
ver docstring do módulo, "Validador de pergunta dedicado"): uma IA ÚNICA,
escolhida pelo dono (PlataformaConfig.ia_validador_pergunta), classifica
a mensagem do paciente em sem_sentido / fora_do_exame / valida, numa
chamada dedicada e SEPARADA da que responde de verdade
(responder_com_ia) - roda ANTES dela, e evita a chamada de resposta por
completo quando já rejeita a pergunta (ver app.whatsapp_conversa.
_responder_pergunta).

Testado com app_context + banco de verdade (create_app/seed.py), mas SEM
chamar nenhuma IA de verdade: mocka o par (fábrica_de_cliente, função_de_
pergunta) de "Claude" dentro de `app.ia_preparo._PROVEDORES_CHAT` (via
`unittest.mock.patch.dict` - IMPORTANTE usar patch.dict no dicionário, e
NÃO `patch("app.ia_preparo._cliente_anthropic", ...)`: `_PROVEDORES_CHAT`
já guarda uma referência direta à função no momento em que o módulo é
carregado, então trocar o atributo do módulo depois não afeta o que já
está dentro do dicionário - só patch.dict no próprio dicionário funciona,
porque a busca dentro de `validar_pergunta` é feita em tempo de
execução). Fixa `PlataformaConfig.ia_validador_pergunta = "Claude"` antes
de cada caso, pra não depender do que outro teste deixou configurado."""
from unittest.mock import MagicMock, patch

from app import create_app, db
from app.ia_preparo import validar_pergunta
from app.models import Exame, PlataformaConfig


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def _cliente_fake(texto_resposta):
    """Simula o cliente Anthropic respondendo `texto_resposta` (mesmo
    formato usado de verdade: `resposta.content[0].text`)."""
    cliente = MagicMock()
    bloco = MagicMock()
    bloco.text = texto_resposta
    resposta = MagicMock()
    resposta.content = [bloco]
    resposta.usage = None
    resposta.model = "claude-sonnet-4-5"
    cliente.messages.create.return_value = resposta
    return cliente


app = create_app()

with app.app_context():
    config = PlataformaConfig.obter()
    config.ia_validador_pergunta = "Claude"
    db.session.commit()

    exame = Exame.query.first()
    checar("Existe pelo menos um Exame cadastrado (seed.py) para testar com contexto real", exame is not None)

    with patch.dict("app.ia_preparo._PROVEDORES_CHAT", {"Claude": (lambda: _cliente_fake("SEM_SENTIDO_ENCAMINHAR"), None)}):
        resultado = validar_pergunta("asdkjf qwerty lorem", exame)
        checar(
            "Classifica como sem_sentido quando a IA responde o marcador SEM_SENTIDO_ENCAMINHAR",
            resultado["classificacao"] == "sem_sentido",
        )

    with patch.dict("app.ia_preparo._PROVEDORES_CHAT", {"Claude": (lambda: _cliente_fake("FORA_DO_EXAME_ENCAMINHAR"), None)}):
        resultado = validar_pergunta("Quem vai ganhar o jogo hoje?", exame)
        checar(
            "Classifica como fora_do_exame quando a IA responde o marcador FORA_DO_EXAME_ENCAMINHAR",
            resultado["classificacao"] == "fora_do_exame",
        )

    with patch.dict("app.ia_preparo._PROVEDORES_CHAT", {"Claude": (lambda: _cliente_fake("VALIDA"), None)}):
        resultado = validar_pergunta("Posso comer batata frita?", exame)
        checar("Classifica como valida quando a IA responde VALIDA", resultado["classificacao"] == "valida")

    with patch.dict("app.ia_preparo._PROVEDORES_CHAT", {"Claude": (lambda: _cliente_fake("blablabla, isso não é nenhum marcador"), None)}):
        resultado = validar_pergunta("Qualquer coisa", exame)
        checar(
            "Resposta que não bate com nenhum marcador esperado é tratada como valida (mais seguro que bloquear por engano)",
            resultado["classificacao"] == "valida",
        )

    checar(
        "Sem exame em foco, não chama IA nenhuma - classificacao vem None (fluxo normal segue, ver _responder_pergunta)",
        validar_pergunta("oi", None)["classificacao"] is None,
    )

    with patch.dict("app.ia_preparo._PROVEDORES_CHAT", {"Claude": (lambda: None, None)}):
        resultado = validar_pergunta("Posso comer batata?", exame)
        checar(
            "Sem API key configurada (fábrica de cliente devolve None), classificacao vem None - segue fluxo normal",
            resultado["classificacao"] is None,
        )

print("\nTodos os testes do validador de pergunta dedicado passaram.")
