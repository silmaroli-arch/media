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


# ---------------------------------------------------------------------------
# Modelo por PONTO DE USO (pedido do Silvan, 2026-10-26): o dono escolhe, em
# /dono, o MODELO de cada lugar do sistema que chama IA - o nome do provedor
# (Claude, ChatGPT, Gemini) sai do próprio nome do modelo. Sem escolha, vale o
# que já valia antes (provedores e modelos por papel acima), então nada muda
# em produção até o dono mexer.
# ---------------------------------------------------------------------------
import json
from types import SimpleNamespace

CLAUDE, CHATGPT, GEMINI = "Claude", "ChatGPT", "Gemini"
_PAPEL_DO_PROVEDOR = {CLAUDE: "claude", CHATGPT: "openai", GEMINI: "gemini"}

# chave -> (rótulo, onde é usado, só_claude)
# `só_claude`: o ponto usa a ferramenta de busca da Anthropic.
PONTOS = {
    "resposta_1": ("Resposta ao paciente - 1ª IA", "Responde a pergunta do paciente no WhatsApp/link", False),
    "resposta_2": ("Resposta ao paciente - 2ª IA", "Responde a mesma pergunta, para conferir a 1ª (precisa ser de outra empresa)", False),
    "resposta_reserva": ("Resposta ao paciente - reserva", "Entra no lugar de uma das duas quando ela falha (3ª empresa)", False),
    "validador": ("Validador da pergunta", "Decide se a mensagem faz sentido e é sobre o exame, e corrige a digitação", False),
    "divergencia": ("Checagem de divergência", "Compara as duas respostas (e a da base) e diz se divergem", False),
    "sintese": ("Junção das respostas", "Escreve a resposta única quando as duas IAs divergem", False),
    "pdf_1": ("Importar PDF - 1ª tentativa", "Lê o PDF do preparo e preenche o modelo (a 1ª recebe o PDF inteiro)", False),
    "pdf_2": ("Importar PDF - 2ª tentativa", "Usada se a 1ª falhar", False),
    "pdf_3": ("Importar PDF - 3ª tentativa", "Usada se a 2ª também falhar", False),
    "malu": ("Malu (ajuda do app)", "Assistente de ajuda da equipe", False),
    "aprendizado": ("Base de aprendizado", "Generaliza a resposta do médico para a base compartilhada", False),
    "busca": ("Busca na internet", "Pesquisa nomes que a IA não reconhece (só modelos Claude, usa a busca da Anthropic)", True),
}


# Agrupamento por tema na tela do dono (2026-10-26).
GRUPOS_PONTOS = [
    ("Respostas ao paciente (WhatsApp e link)", ["resposta_1", "resposta_2", "resposta_reserva", "validador", "divergencia", "sintese"]),
    ("Importação do PDF do preparo", ["pdf_1", "pdf_2", "pdf_3"]),
    ("Ajuda e aprendizado", ["malu", "aprendizado"]),
    ("Pesquisa na internet", ["busca"]),
]


def provedor_do_modelo(nome):
    """Claude / ChatGPT / Gemini conforme o nome do modelo, ou None."""
    nome = (nome or "").strip().lower()
    if nome.startswith("claude"):
        return CLAUDE
    if nome.startswith(("gpt", "chatgpt", "o1", "o3", "o4")):
        return CHATGPT
    if nome.startswith("gemini"):
        return GEMINI
    return None


def _escolhas_do_painel(config=None):
    try:
        if config is None:
            from app.models import PlataformaConfig

            config = PlataformaConfig.obter()
        dados = json.loads(getattr(config, "modelos_pontos", None) or "{}")
        return {k: v for k, v in dados.items() if k in PONTOS and isinstance(v, str) and v}
    except Exception:
        return {}


def _provedores_antigos(config):
    """(1ª, 2ª, reserva, validador, malu) pelas escolhas antigas de provedor."""
    um = getattr(config, "ia_chat_provedor_1", None) or CLAUDE
    dois = getattr(config, "ia_chat_provedor_2", None) or CHATGPT
    tres = next(p for p in (CLAUDE, CHATGPT, GEMINI) if p not in (um, dois))
    return (um, dois, tres, getattr(config, "ia_validador_pergunta", None) or CLAUDE,
            getattr(config, "ia_ajuda_app", None) or CLAUDE)


def modelo_padrao_do_ponto(ponto, config=None):
    """O modelo que vale para o ponto quando o dono não escolheu nenhum."""
    try:
        if config is None:
            from app.models import PlataformaConfig

            config = PlataformaConfig.obter()
        um, dois, tres, valida, malu = _provedores_antigos(config)
    except Exception:
        um, dois, tres, valida, malu = CLAUDE, CHATGPT, GEMINI, CLAUDE, CLAUDE

    def do_provedor(provedor, validador=False):
        if provedor == CLAUDE and validador:
            return modelo("claude_validador")
        return modelo(_PAPEL_DO_PROVEDOR[provedor])

    return {
        "resposta_1": lambda: do_provedor(um),
        "resposta_2": lambda: do_provedor(dois),
        "resposta_reserva": lambda: do_provedor(tres),
        "validador": lambda: do_provedor(valida, validador=True),
        "divergencia": lambda: modelo("claude_validador"),
        "sintese": lambda: modelo("claude_sintese"),
        "pdf_1": lambda: modelo("gemini"),
        "pdf_2": lambda: modelo("openai"),
        "pdf_3": lambda: modelo("claude"),
        "malu": lambda: do_provedor(malu),
        "aprendizado": lambda: modelo("claude"),
        "busca": lambda: modelo("claude"),
    }[ponto]()


def modelo_do_ponto(ponto):
    """O modelo em uso no ponto: escolha do dono > padrão. Escolha que não
    dá para reconhecer (provedor desconhecido, ou busca fora do Claude) é
    ignorada."""
    escolhido = _escolhas_do_painel().get(ponto)
    if escolhido and provedor_do_modelo(escolhido) and (not PONTOS[ponto][2] or provedor_do_modelo(escolhido) == CLAUDE):
        return escolhido
    return modelo_padrao_do_ponto(ponto)


def chamar_texto(cliente, nome_modelo, system, mensagens, max_tokens, esforco="low", **kwargs):
    """Uma pergunta ao modelo, de qualquer empresa. `mensagens`: lista de
    {"role", "content"}. Devolve SimpleNamespace(texto, modelo, entrada,
    saida, provedor); levanta a exceção da chamada."""
    provedor = provedor_do_modelo(nome_modelo) or CLAUDE
    if provedor == CLAUDE:
        r = criar_mensagem_claude(cliente, nome_modelo, max_tokens, esforco=esforco, system=system, messages=mensagens, **kwargs)
        uso = getattr(r, "usage", None)
        return SimpleNamespace(texto=texto_claude(r), modelo=getattr(r, "model", nome_modelo) or nome_modelo,
                               entrada=getattr(uso, "input_tokens", None), saida=getattr(uso, "output_tokens", None), provedor=provedor)
    if provedor == CHATGPT:
        r = criar_chat_openai(cliente, nome_modelo, max_tokens, esforco=esforco,
                              messages=[{"role": "system", "content": system}] + list(mensagens), **kwargs)
        uso = getattr(r, "usage", None)
        return SimpleNamespace(texto=(r.choices[0].message.content or "").strip(), modelo=getattr(r, "model", nome_modelo) or nome_modelo,
                               entrada=getattr(uso, "prompt_tokens", None), saida=getattr(uso, "completion_tokens", None), provedor=provedor)
    from google.genai import types as genai_types

    conversa = mensagens[0]["content"] if len(mensagens) == 1 else "\n\n".join(
        ("Usuário: " if m["role"] == "user" else "Assistente: ") + m["content"] for m in mensagens)
    r = cliente.models.generate_content(
        model=nome_modelo, contents=conversa,
        config=genai_types.GenerateContentConfig(system_instruction=system, max_output_tokens=max_tokens + FOLGA_RACIOCINIO),
    )
    uso = getattr(r, "usage_metadata", None)
    return SimpleNamespace(texto=(getattr(r, "text", None) or "").strip(),
                           modelo=getattr(r, "model_version", None) or nome_modelo,
                           entrada=getattr(uso, "prompt_token_count", None), saida=getattr(uso, "candidates_token_count", None), provedor=provedor)
