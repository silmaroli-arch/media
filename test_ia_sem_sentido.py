"""Testa o julgamento de "isso faz sentido?" feito pela própria IA em
app.ia_preparo (pedido do Silvan, 2026-09-24): "vc não consegue ter uma
inteligencia para saber se alguma pergunta faz sentido ao invés de só
utilizar regras fixas...?" - o Silvan escolheu a opção híbrida (regex
primeiro, IA só em dúvida - ver docstring do módulo e de
`responder_com_ia`).

Como nenhuma ANTHROPIC_API_KEY/OPENAI_API_KEY/GEMINI_API_KEY está
configurada neste ambiente de teste, não dá pra chamar uma IA de verdade
(mesma limitação de sempre - ver HANDOFF_CHAT.md). Em vez de mockar os
clientes de cada provedor (Anthropic/OpenAI/Gemini SDK) um por um, estes
testes usam `unittest.mock.patch` direto em `app.ia_preparo._tentar_provedor`
- a função que já teria "tentou (True/False)" e "sem_sentido (bool)" prontos
por provedor, então o mock só precisa simular esse contrato de retorno
`(texto_ou_None, chamada_ou_None, tentou_bool, sem_sentido_bool)` -
suficiente para testar a lógica de AGREGAÇÃO em `responder_com_ia`
("sem_sentido" só True quando "final" é None E todas as IAs que de fato
responderam concordaram), sem precisar simular nenhuma biblioteca externa.

A detecção do MARCADOR_SEM_SENTIDO dentro de cada `_perguntar_*`
(_perguntar_claude/_perguntar_chatgpt/_perguntar_gemini) é testada
separadamente, também aqui, com um `cliente` fake mínimo (sem nenhuma
biblioteca de IA de verdade) - só precisa ter o formato de retorno que
cada função espera do SDK correspondente."""
from types import SimpleNamespace
from unittest.mock import patch

from app import create_app
from app.ia_preparo import MARCADOR_SEM_SENTIDO, _perguntar_chatgpt, _perguntar_claude, responder_com_ia
from app.models import PlataformaConfig

app = create_app()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


# --- Detecção do marcador dentro de _perguntar_claude/_perguntar_chatgpt
# (função pura de resposta - sem chamar nenhuma API de verdade, só um
# objeto fake com o formato que o código espera de volta do SDK) ---

with app.app_context():
    cliente_claude_fake = SimpleNamespace(
        messages=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(
                content=[SimpleNamespace(text=MARCADOR_SEM_SENTIDO)],
                usage=SimpleNamespace(input_tokens=10, output_tokens=2),
                model="claude-sonnet-4-5",
            )
        )
    )
    texto, chamada, sem_sentido = _perguntar_claude(cliente_claude_fake, "kljhsdf asdas mesa amanhã", "contexto")
    checar("_perguntar_claude reconhece MARCADOR_SEM_SENTIDO e devolve texto=None", texto is None)
    checar("_perguntar_claude registra a ChamadaIA mesmo quando é sem sentido (custo real houve)", chamada is not None)
    checar("_perguntar_claude sinaliza sem_sentido=True", sem_sentido is True)

    cliente_claude_normal = SimpleNamespace(
        messages=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(
                content=[SimpleNamespace(text="Água pura é permitida durante o jejum.")],
                usage=SimpleNamespace(input_tokens=10, output_tokens=2),
                model="claude-sonnet-4-5",
            )
        )
    )
    texto2, chamada2, sem_sentido2 = _perguntar_claude(cliente_claude_normal, "Posso beber água?", "contexto")
    checar("_perguntar_claude com resposta normal devolve o texto de verdade", texto2 == "Água pura é permitida durante o jejum.")
    checar("_perguntar_claude com resposta normal NÃO sinaliza sem_sentido", sem_sentido2 is False)

    cliente_chatgpt_fake = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=MARCADOR_SEM_SENTIDO))],
                    usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2),
                    model="gpt-4o-mini",
                )
            )
        )
    )
    texto3, chamada3, sem_sentido3 = _perguntar_chatgpt(cliente_chatgpt_fake, "asdkj qwe mesa vidro", "contexto")
    checar("_perguntar_chatgpt reconhece MARCADOR_SEM_SENTIDO", texto3 is None and sem_sentido3 is True)

    # NAO_SEI_ENCAMINHAR continua sendo um caminho diferente (não deve
    # disparar sem_sentido) - regressão simples pra garantir que os dois
    # marcadores não ficaram confundidos entre si.
    from app.ia_preparo import MARCADOR_NAO_SEI
    cliente_nao_sei = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=MARCADOR_NAO_SEI))],
                    usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2),
                    model="gpt-4o-mini",
                )
            )
        )
    )
    texto4, chamada4, sem_sentido4 = _perguntar_chatgpt(cliente_nao_sei, "Isso está fora do preparo", "contexto")
    checar("MARCADOR_NAO_SEI continua devolvendo texto=None mas sem_sentido=False (marcadores não se confundem)", texto4 is None and sem_sentido4 is False)


# --- Agregação em responder_com_ia (mockando _tentar_provedor direto) ---

with app.app_context():
    from app import db

    config = PlataformaConfig.obter()
    config.ia_chat_provedor_1 = "Claude"
    config.ia_chat_provedor_2 = "ChatGPT"
    db.session.commit()

    exame_fake = SimpleNamespace(nome="Exame teste", descricao=None, agendamentos=[], preparo=None)

    # Caso 1: as duas IAs escolhidas concordam que não faz sentido.
    def side_effect_ambas_sem_sentido(nome_provedor, *args, **kwargs):
        if nome_provedor in ("Claude", "ChatGPT"):
            return (None, "chamada-fake", True, True)
        return (None, None, False, False)

    with patch("app.ia_preparo._tentar_provedor", side_effect=side_effect_ambas_sem_sentido):
        resultado = responder_com_ia("asdkj qwe mesa vidro", exame_fake)
    checar("Duas IAs concordando em sem sentido -> final None", resultado["final"] is None)
    checar("Duas IAs concordando em sem sentido -> sem_sentido=True", resultado["sem_sentido"] is True)

    # Caso 2: uma acha sem sentido, a outra responde normalmente -> usa a
    # que respondeu, sem_sentido deve ser False (não descarta uma resposta
    # de verdade por causa da outra IA ter discordado).
    def side_effect_uma_discorda(nome_provedor, *args, **kwargs):
        if nome_provedor == "Claude":
            return (None, "chamada-fake", True, True)
        if nome_provedor == "ChatGPT":
            return ("Água pura é permitida durante o jejum.", "chamada-fake", True, False)
        return (None, None, False, False)

    with patch("app.ia_preparo._tentar_provedor", side_effect=side_effect_uma_discorda):
        resultado2 = responder_com_ia("Posso beber água?", exame_fake)
    checar("Uma IA discorda (responde normal) -> usa a resposta de verdade", resultado2["final"] == "Água pura é permitida durante o jejum.")
    checar("Uma IA discorda -> sem_sentido=False (não é unânime)", resultado2["sem_sentido"] is False)

    # Caso 3: nenhuma IA configurada -> sem_sentido deve ser False (só a
    # checagem por regras fixas continua valendo, ver
    # app.whatsapp_conversa._eh_mensagem_sem_sentido_minimo).
    def side_effect_nenhuma_configurada(nome_provedor, *args, **kwargs):
        return (None, None, False, False)

    with patch("app.ia_preparo._tentar_provedor", side_effect=side_effect_nenhuma_configurada):
        resultado3 = responder_com_ia("qualquer pergunta", exame_fake)
    checar("Nenhuma IA configurada -> final None", resultado3["final"] is None)
    checar("Nenhuma IA configurada -> sem_sentido=False (não é unânime, é ausência de dados)", resultado3["sem_sentido"] is False)

    # Caso 4: as duas escolhidas falham de verdade (erro de chamada) e a
    # reserva (a terceira IA) responde marcando sem sentido -> deve
    # propagar sem_sentido=True (a reserva "assume o lugar" da que falhou,
    # inclusive esse sinal).
    def side_effect_reserva_sem_sentido(nome_provedor, *args, **kwargs):
        if nome_provedor in ("Claude", "ChatGPT"):
            return (None, None, True, False)  # tentou, mas falhou de verdade (chamada=None)
        if nome_provedor == "Gemini":
            return (None, "chamada-fake", True, True)
        return (None, None, False, False)

    with patch("app.ia_preparo._tentar_provedor", side_effect=side_effect_reserva_sem_sentido):
        resultado4 = responder_com_ia("asdkj qwe mesa vidro", exame_fake)
    checar("Reserva respondeu sem sentido (as 2 escolhidas falharam) -> sem_sentido=True", resultado4["sem_sentido"] is True)
    checar("Reserva respondeu sem sentido -> pelo menos uma falha real registrada", len(resultado4["falhas"]) >= 1)

print("\nTodos os testes do julgamento de sentido pela IA passaram.")
