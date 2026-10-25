"""Chamadas às IAs com os modelos novos (pedido do Silvan, 2026-10-25).

O Claude Sonnet 4.5 foi descontinuado; o padrão passa a ser o Claude Sonnet
5.5 (respostas) e o Claude Haiku 5.5 (validador). Os modelos novos sempre
"pensam" antes de responder, e esse raciocínio usa os tokens de saída - com
max_tokens pequeno (300, 120, 5...) a resposta sairia cortada ou vazia. Por
isso as chamadas passam por aqui:

- Claude: `max_tokens` com folga para o raciocínio, `output_config.effort`
  (quanto pensar) e o fallback do servidor quando o modelo recusa a pergunta
  por segurança (outro modelo responde no lugar, na mesma chamada).
- ChatGPT: a família GPT-5 (e os "o" de raciocínio) não aceita `max_tokens`
  - usa `max_completion_tokens` e `reasoning_effort`.

Qual modelo usar (pedido do Silvan, 2026-10-25): o que o dono informou no
painel (PlataformaConfig.modelo_*) vale mais que a variável de ambiente
(ANTHROPIC_MODEL, ANTHROPIC_MODEL_VALIDADOR, OPENAI_MODEL, GEMINI_MODEL), que
vale mais que o padrão daqui - ver modelo_claude() e companhia."""
import logging
import os
import re

logger = logging.getLogger(__name__)

# (campo do painel, variável de ambiente, padrão) de cada papel.
MODELOS = {
    "claude": ("modelo_claude", "ANTHROPIC_MODEL", "claude-sonnet-5-5"),
    "claude_validador": ("modelo_claude_validador", "ANTHROPIC_MODEL_VALIDADOR", "claude-haiku-5-5"),
    # Junção das respostas quando as 2 IAs divergem (2026-10-25): separada
    # das respostas para poder usar um modelo mais forte só nessa etapa.
    "claude_sintese": ("modelo_claude_sintese", "ANTHROPIC_MODEL_SINTESE", "claude-sonnet-5-5"),
    "openai": ("modelo_openai", "OPENAI_MODEL", "gpt-4o-mini"),
    "gemini": ("modelo_gemini", "GEMINI_MODEL", "gemini-flash-latest"),
}
NOME_MODELO_VALIDO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{1,59}$")


def modelo_sem_painel(papel):
    """Variável de ambiente ou padrão - o que vale com o campo do painel vazio."""
    _, variavel, padrao = MODELOS[papel]
    return os.environ.get(variavel) or padrao


def modelo(papel):
    """O modelo em uso para o papel: painel do dono > variável de ambiente >
    padrão. Fora de uma requisição (sem banco), ignora o painel."""
    campo = MODELOS[papel][0]
    try:
        from app.models import PlataformaConfig

        escolhido = getattr(PlataformaConfig.obter(), campo, None)
    except Exception:
        escolhido = None
    return escolhido or modelo_sem_painel(papel)


def modelo_claude():
    return modelo("claude")


def modelo_claude_validador():
    return modelo("claude_validador")


def modelo_claude_sintese():
    return modelo("claude_sintese")


def modelo_openai():
    return modelo("openai")


def modelo_gemini():
    return modelo("gemini")

# Folga para o raciocínio dos modelos novos, somada ao tamanho de resposta
# que cada chamada já pedia.
FOLGA_RACIOCINIO = 4000

# Fallback do servidor (Claude API): se o modelo recusar por segurança, o
# próprio servidor tenta outro modelo na mesma chamada.
BETA_FALLBACK = "server-side-fallback-2026-07-01"

_CLAUDE_NOVO = re.compile(r"^claude-(opus|sonnet|haiku|fable)-([5-9])")
_CLAUDE_COM_FALLBACK = re.compile(r"^claude-(opus-5|sonnet-5-5|fable-5-1)")
_OPENAI_RACIOCINIO = re.compile(r"^(gpt-5|o\d)")


def claude_novo(modelo):
    """Geração 5 em diante: raciocínio sempre ligado, regulado por `effort`."""
    return bool(_CLAUDE_NOVO.match(modelo or ""))


def criar_mensagem_claude(cliente, modelo, max_tokens, esforco="low", **kwargs):
    """`cliente.messages.create` adaptado ao modelo. `max_tokens` é o tamanho
    da RESPOSTA desejada; nos modelos novos soma-se a folga do raciocínio.
    `esforco`: low | medium | high."""
    if not claude_novo(modelo):
        return cliente.messages.create(model=modelo, max_tokens=max_tokens, **kwargs)
    parametros = dict(model=modelo, max_tokens=max_tokens + FOLGA_RACIOCINIO, output_config={"effort": esforco}, **kwargs)
    # Só no cliente de verdade do SDK (os testes usam clientes simulados).
    beta = getattr(getattr(cliente, "beta", None), "messages", None)
    if beta is not None and type(cliente).__module__.startswith("anthropic") and _CLAUDE_COM_FALLBACK.match(modelo):
        try:
            return beta.create(betas=[BETA_FALLBACK], fallbacks="default", **parametros)
        except Exception as erro:
            # Recurso indisponível (conta/plataforma/SDK) - segue sem fallback.
            if getattr(erro, "status_code", None) not in (400, 404):
                raise
            logger.warning("Fallback do servidor indisponível para %s: %s", modelo, erro)
    return cliente.messages.create(**parametros)


def texto_claude(resposta):
    """Só o texto da resposta (ignora os blocos de raciocínio e de fallback).
    Vazio quando o modelo recusou."""
    if getattr(resposta, "stop_reason", None) == "refusal":
        return ""
    partes = []
    for bloco in resposta.content:
        texto = getattr(bloco, "text", None)
        if isinstance(texto, str) and getattr(bloco, "type", None) not in ("thinking", "redacted_thinking", "fallback"):
            partes.append(texto)
    return "".join(partes).strip()


def criar_chat_openai(cliente, modelo, max_tokens, esforco="low", **kwargs):
    """`cliente.chat.completions.create` adaptado: a família GPT-5/"o" usa
    `max_completion_tokens` (com folga para o raciocínio) e `reasoning_effort`."""
    if _OPENAI_RACIOCINIO.match(modelo or ""):
        return cliente.chat.completions.create(
            model=modelo, max_completion_tokens=max_tokens + FOLGA_RACIOCINIO, reasoning_effort=esforco, **kwargs,
        )
    return cliente.chat.completions.create(model=modelo, max_tokens=max_tokens, **kwargs)
