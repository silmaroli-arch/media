"""Estimativa de custo das chamadas de IA (Gemini/ChatGPT/Claude), usada
tanto pela importação de PDF de preparo (`app.ia_pdf_preparo`) quanto
pelo chat de dúvidas do paciente (`app.ia_preparo`) - alimenta o painel
de custo por usuário na área do dono da plataforma (ver
`app.routes_dono`), persistindo um `app.models.ChamadaIA` por chamada.

IMPORTANTE - nenhum provedor devolve o valor em dólares na resposta da
API, só a contagem de tokens; o valor REAL só aparece no painel de
faturamento de cada um (Google AI Studio / OpenAI / Anthropic Console).
O que este módulo calcula é uma ESTIMATIVA, a partir dessa contagem de
tokens e de uma tabela de preços mantida à mão abaixo - precisa ser
revisada periodicamente, preços mudam com frequência.

Outro ponto confirmado numa pesquisa em 2026-08-20: os apelidos
configurados no `.env` (`GEMINI_MODEL=gemini-flash-latest`,
`OPENAI_MODEL=gpt-4o-mini`, `ANTHROPIC_MODEL=claude-sonnet-4-5`) não são
necessariamente a versão exata que respondeu - "gemini-flash-latest" em
particular é um apelido que a própria Google "troca por baixo" a cada
novo lançamento de Flash (hoje já existem Gemini 2.5, 3, 3.1, 3.5, 3.6 e
3.7 Flash coexistindo, com preços bem diferentes entre si). Por isso a
tabela abaixo é indexada pelo nome EXATO do modelo devolvido por CADA
resposta (ex.: `resposta.model_version` no Gemini, `resposta.model` na
OpenAI/Anthropic) e não pelo apelido - e um modelo que respondeu mas não
está cadastrado aqui vira custo desconhecido (None), nunca um valor
inventado."""

# (dólares por 1 milhão de tokens) - (entrada, saída). Preços conferidos
# em 2026-08-20 diretamente nas páginas oficiais de cada provedor
# (ai.google.dev/gemini-api/docs/pricing, developers.openai.com/api/docs/pricing,
# anthropic.com/pricing) - revisar esta tabela de tempos em tempos, e
# sempre que um provedor novo/versão nova aparecer nos logs com
# "preco_desconhecido".
PRECOS_POR_MILHAO_TOKENS = {
    # Gemini
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-3-flash-preview": (0.50, 3.00),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3.6-flash": (0.75, 3.75),
    "gemini-3.7-flash": (0.75, 3.75),
    # OpenAI
    "gpt-4o-mini": (0.15, 0.60),
    # Anthropic (preço estável entre Sonnet 3.7 e 4.6, incluindo o 4.5
    # usado hoje como padrão - ver app.ia_preparo.MODELO_PADRAO)
    "claude-sonnet-4-5": (3.00, 15.00),
}


# Cotação fixa USD -> BRL, mantida à mão (nenhum provedor de IA cobra em
# reais, e não vale a pena chamar uma API de cotação em tempo real só pra
# uma exibição estimada) - revisar de tempos em tempos junto com a tabela
# de preços acima. Ver ptax do Banco Central ou cotação comercial de
# qualquer banco/corretora para atualizar.
COTACAO_USD_PARA_BRL = 5.40


def calcular_custo_brl(modelo, tokens_entrada, tokens_saida):
    """Mesma estimativa de `calcular_custo_usd`, convertida pra reais pela
    cotação fixa acima - devolve None nos mesmos casos (modelo não
    cadastrado ou tokens ausentes)."""
    custo_usd = calcular_custo_usd(modelo, tokens_entrada, tokens_saida)
    return custo_usd * COTACAO_USD_PARA_BRL if custo_usd is not None else None


def calcular_custo_usd(modelo, tokens_entrada, tokens_saida):
    """Devolve o custo estimado em dólares pra uma chamada, ou None se o
    modelo não estiver na tabela de preços acima (ex.: uma versão nova
    que ainda não foi cadastrada) ou os tokens não vieram na resposta da
    API. A correspondência é por PREFIXO (não só nome exato) porque as
    APIs costumam devolver a versão completa com data/sufixo (ex.:
    "gpt-4o-mini-2024-07-18", "claude-sonnet-4-5-20250929") em vez do
    nome curto usado como chave aqui."""
    if not modelo or tokens_entrada is None or tokens_saida is None:
        return None
    chave = next((k for k in PRECOS_POR_MILHAO_TOKENS if modelo.startswith(k)), None)
    if chave is None:
        return None
    preco_entrada, preco_saida = PRECOS_POR_MILHAO_TOKENS[chave]
    return (tokens_entrada * preco_entrada + tokens_saida * preco_saida) / 1_000_000


def registrar_chamada_ia(
    tipo_uso, provedor, modelo, tokens_entrada, tokens_saida, sucesso,
    usuario_id=None, paciente_id=None, resposta_final_usada=None,
):
    """Persiste um `ChamadaIA` com o custo estimado já calculado - chamar
    logo depois de CADA tentativa a um provedor que chegou a receber uma
    resposta da API (mesmo que a chamada tenha falhado depois, ex.: JSON
    inválido - a chamada em si já gerou custo real no provedor). Não
    faz `db.session.commit()` sozinho - quem chama decide quando
    persistir, junto com o resto da transação da requisição.

    Devolve o objeto `ChamadaIA` criado (já adicionado à sessão, mas ainda
    não commitado) - usado por app.ia_preparo.responder_com_ia para
    marcar `resposta_final_usada` DEPOIS de decidir qual IA "venceu",
    já que isso só é sabido depois que as duas IAs já responderam (a
    própria chamada de registro acontece antes dessa decisão)."""
    from app.extensions import db
    from app.models import ChamadaIA

    custo = calcular_custo_usd(modelo, tokens_entrada, tokens_saida)
    chamada = ChamadaIA(
        usuario_id=usuario_id,
        paciente_id=paciente_id,
        tipo_uso=tipo_uso,
        provedor=provedor,
        modelo=modelo,
        tokens_entrada=tokens_entrada,
        tokens_saida=tokens_saida,
        custo_estimado_usd=custo,
        preco_desconhecido=modelo is not None and custo is None,
        sucesso=sucesso,
        resposta_final_usada=resposta_final_usada,
    )
    db.session.add(chamada)
    return chamada
