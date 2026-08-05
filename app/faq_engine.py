"""
Motor simples de busca de perguntas e respostas (a "IA" do sistema).

Não usa modelos de linguagem externos: compara a pergunta do paciente com
as perguntas já cadastradas na base de FAQ (por exame) usando uma mistura
de similaridade textual (difflib) e sobreposição de palavras-chave.

Quando uma pergunta nova é respondida pela secretária/médico, ela é
adicionada à base de FAQ — é assim que o sistema "aprende".
"""
import re
import unicodedata
from difflib import SequenceMatcher

from app.models import FaqItem

# Confiança mínima para considerar que o paciente perguntou sobre um
# alimento específico da lista do preparo (comparando palavras-chave do
# nome do alimento com as palavras-chave da pergunta).
LIMIAR_CONFIANCA_ALIMENTO = 0.5

STOPWORDS = {
    "a", "o", "e", "é", "de", "da", "do", "das", "dos", "em", "um", "uma",
    "para", "por", "com", "sem", "que", "se", "eu", "posso", "pode", "podem",
    "no", "na", "nos", "nas", "meu", "minha", "os", "as", "ao", "aos", "às",
    "sobre", "antes", "depois", "vou", "estou", "tem", "ter", "seria",
}

LIMIAR_CONFIANCA = 0.45


def normalizar(texto: str) -> str:
    texto = texto.lower().strip()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^a-z0-9\s]", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def _radical(palavra: str) -> str:
    """Normalização leve de plural/singular para melhorar o casamento
    (ex.: 'batatas' -> 'batata'). Não é um stemmer completo, só o suficiente
    para o caso de uso de perguntas curtas em português."""
    if len(palavra) > 4 and palavra.endswith("oes"):
        return palavra[:-3] + "ao"
    if len(palavra) > 4 and palavra.endswith(("as", "os", "es")):
        return palavra[:-1]
    if len(palavra) > 3 and palavra.endswith("s"):
        return palavra[:-1]
    return palavra


def palavras_chave(texto: str) -> set:
    return {
        _radical(p)
        for p in normalizar(texto).split()
        if p not in STOPWORDS and len(p) > 1
    }


# Confiança mínima para considerar que duas palavras são "a mesma", mesmo
# com pequenos erros de digitação (ex.: 'amendoin' vs 'amendoim'). Abaixo
# disso, a diferença já muda demais a palavra pra confiar na correspondência.
LIMIAR_CONFIANCA_PALAVRA = 0.8


def _palavras_correspondem(palavra_a: str, palavra_b: str) -> bool:
    """Compara duas palavras já normalizadas, tolerando pequenos erros de
    digitação — assim 'amendoin' (com "n" no lugar do "m") ainda é
    reconhecido como 'amendoim'. Palavras muito curtas (até 3 letras) só
    casam se forem idênticas, pra não gerar falso positivo (ex.: 'sim' não
    deveria casar com qualquer outra palavra de 3 letras)."""
    if palavra_a == palavra_b:
        return True
    if len(palavra_a) <= 3 or len(palavra_b) <= 3:
        return False
    return SequenceMatcher(None, palavra_a, palavra_b).ratio() >= LIMIAR_CONFIANCA_PALAVRA


def _quantidade_correspondencias(palavras_a: set, palavras_b: set) -> int:
    """Quantas palavras de `palavras_a` têm alguma correspondente (exata ou
    por tolerância a erro de digitação) em `palavras_b`."""
    return sum(1 for pa in palavras_a if any(_palavras_correspondem(pa, pb) for pb in palavras_b))


# Muitos preparos cadastram categorias genéricas de alimento ("frutas",
# "leguminosas", "grãos"...) em vez de listar cada item específico. Sem essa
# tabela, uma pergunta sobre um item específico (ex.: "posso comer laranja?")
# não bate com a palavra-chave "frutas" cadastrada no preparo, e a pergunta
# cai como "pendente" mesmo já estando coberta pela categoria. As palavras
# aqui já estão no formato pós-`normalizar` (sem acento) e no singular (o
# jeito que `_radical` deixaria a categoria, ex.: "frutas" -> "fruta").
CATEGORIAS_ALIMENTOS = {
    "fruta": {
        "laranja", "banana", "maca", "uva", "manga", "mamao", "abacaxi", "melancia",
        "morango", "pera", "kiwi", "melao", "tangerina", "limao", "abacate", "goiaba",
        "caju", "acerola", "ameixa", "pessego", "figo", "caqui", "carambola", "graviola",
        "jaca", "coco", "framboesa", "amora", "cereja", "romatangerina", "mexerica",
    },
    "verdura": {
        "alface", "couve", "espinafre", "rucula", "agriao", "repolho", "brocolis",
        "acelga", "escarola",
    },
    "legume": {
        "cenoura", "batata", "beterraba", "abobora", "chuchu", "pepino", "abobrinha",
        "vagem", "quiabo", "pimentao", "berinjela",
    },
    "leguminosa": {
        "feijao", "lentilha", "soja", "ervilha", "fava",
    },
    "grao": {
        "arroz", "aveia", "trigo", "cevada", "centeio", "quinoa", "milho",
    },
    "carne": {
        "frango", "boi", "porco", "peixe", "carneiro", "peru", "bacon", "linguica",
        "salsicha", "presunto",
    },
    "laticinio": {
        "leite", "queijo", "iogurte", "manteiga", "requeijao", "nata",
    },
    "derivado": {
        "leite", "queijo", "iogurte", "manteiga", "requeijao", "nata",
    },
}

# Mapa inverso (item específico -> categoria) para busca rápida na hora de
# comparar a pergunta do paciente com o nome do alimento cadastrado.
_ITEM_PARA_CATEGORIA = {
    item: categoria for categoria, itens in CATEGORIAS_ALIMENTOS.items() for item in itens
}


def _correspondem_considerando_categoria(palavra_alimento: str, palavra_pergunta: str) -> bool:
    """Além da correspondência normal (exata ou com tolerância a erro de
    digitação), reconhece quando a palavra do alimento cadastrado é uma
    categoria genérica (ex.: 'fruta') e a palavra da pergunta é um item
    específico dessa categoria (ex.: 'laranja'), ou vice-versa."""
    if _palavras_correspondem(palavra_alimento, palavra_pergunta):
        return True
    if _ITEM_PARA_CATEGORIA.get(palavra_pergunta) == palavra_alimento:
        return True
    if _ITEM_PARA_CATEGORIA.get(palavra_alimento) == palavra_pergunta:
        return True
    return False


def _quantidade_correspondencias_alimento(kw_alimento: set, kw_pergunta: set) -> int:
    """Como `_quantidade_correspondencias`, mas também considera categorias
    genéricas de alimento (ver `CATEGORIAS_ALIMENTOS`)."""
    return sum(
        1 for pa in kw_alimento
        if any(_correspondem_considerando_categoria(pa, pp) for pp in kw_pergunta)
    )


def similaridade(pergunta_a: str, pergunta_b: str) -> float:
    a_norm, b_norm = normalizar(pergunta_a), normalizar(pergunta_b)
    ratio_texto = SequenceMatcher(None, a_norm, b_norm).ratio()

    kw_a, kw_b = palavras_chave(pergunta_a), palavras_chave(pergunta_b)
    if kw_a and kw_b:
        intersecao = len(kw_a & kw_b)
        uniao = len(kw_a | kw_b)
        ratio_kw = intersecao / uniao if uniao else 0
    else:
        ratio_kw = 0

    # média ponderada: palavras-chave pesam mais que a similaridade bruta de string
    return 0.4 * ratio_texto + 0.6 * ratio_kw


def buscar_resposta(pergunta_usuario: str, clinica_id, exame_id=None):
    """
    Procura a melhor resposta na base de FAQ, restrita à clínica do
    paciente (para não misturar conhecimento entre clínicas diferentes).
    Prioriza itens específicos do exame, mas também considera FAQs gerais.

    Retorna (faq_item, score) ou (None, melhor_score) se não houver
    confiança suficiente.
    """
    candidatos = FaqItem.query.filter(
        FaqItem.clinica_id == clinica_id,
        (FaqItem.exame_id == exame_id) | (FaqItem.exame_id.is_(None)),
    ).all()

    pergunta_normalizada = normalizar(pergunta_usuario)

    melhor_item = None
    melhor_score = 0.0

    for item in candidatos:
        if item.criado_por == "Assistente (IA)":
            # FAQs geradas pela IA (ver app.ia_preparo) NUNCA são
            # reaproveitadas por semelhança aproximada — só quando a
            # pergunta nova é essencialmente idêntica (após normalizar
            # acentuação/pontuação/maiúsculas). Uma resposta da IA tende a
            # depender de um detalhe bem específico da pergunta original
            # (ex.: um sabor, uma marca): "gatorade de uva" e "gatorade de
            # limão" compartilham quase todas as palavras, mas a resposta
            # certa pode ser diferente — similaridade "aproximada" não é
            # confiável o suficiente para diferenciar esse tipo de caso.
            if normalizar(item.pergunta) == pergunta_normalizada:
                return item, 1.0
            continue

        score = similaridade(pergunta_usuario, item.pergunta)
        # dá uma pequena vantagem para FAQs específicas do exame perguntado
        if exame_id is not None and item.exame_id == exame_id:
            score += 0.03
        if score > melhor_score:
            melhor_score = score
            melhor_item = item

    if melhor_item and melhor_score >= LIMIAR_CONFIANCA:
        return melhor_item, melhor_score
    return None, melhor_score


def buscar_resposta_alimento(pergunta_usuario: str, exame):
    """
    Verifica se a pergunta do paciente é sobre um alimento específico
    cadastrado no preparo do exame (lista de alimentos permitidos/sugeridos
    ou proibidos) e, se sim, monta uma resposta pronta na hora — sem
    depender de uma FAQ cadastrada manualmente para cada alimento.

    Retorna uma string com a resposta, ou None se não encontrou nenhum
    alimento correspondente com confiança suficiente.
    """
    preparo = exame.preparo if exame else None
    if not preparo or not preparo.alimentos:
        return None

    kw_pergunta = palavras_chave(pergunta_usuario)
    if not kw_pergunta:
        return None

    # Passo 1: correspondência DIRETA — o nome do alimento cadastrado (com
    # tolerância a erro de digitação) aparece na pergunta. Mais confiável
    # porque compara palavras do nome de verdade, não uma categoria inferida.
    melhor_alimento = None
    melhor_score = 0.0
    houve_correspondencia_direta = False
    for alimento in preparo.alimentos:
        kw_alimento = palavras_chave(alimento.nome)
        if not kw_alimento:
            continue
        correspondencias = _quantidade_correspondencias(kw_alimento, kw_pergunta)
        if correspondencias == 0:
            continue
        houve_correspondencia_direta = True
        # Proporção das palavras do nome do alimento que apareceram na
        # pergunta — um nome curto (ex.: "café") já casa com 1 palavra.
        score = correspondencias / len(kw_alimento)
        if score > melhor_score:
            melhor_score = score
            melhor_alimento = alimento

    # Passo 2: só recorre à categoria genérica (ex.: "laranja" bate com o
    # item cadastrado "frutas") quando NENHUM alimento bateu diretamente.
    # Se algum alimento específico já foi reconhecido na frase (ex.:
    # "gatorade"), uma palavra de categoria na mesma pergunta é tratada
    # como sabor/descrição do produto citado (ex.: "gatorade de uva" não
    # é a mesma coisa que comer uva) — sem essa checagem, o chat concluía
    # errado que a pergunta era sobre a fruta, e não sobre o produto.
    if not houve_correspondencia_direta:
        for alimento in preparo.alimentos:
            kw_alimento = palavras_chave(alimento.nome)
            if not kw_alimento:
                continue
            correspondencias = _quantidade_correspondencias_alimento(kw_alimento, kw_pergunta)
            if correspondencias == 0:
                continue
            score = correspondencias / len(kw_alimento)
            if score > melhor_score:
                melhor_score = score
                melhor_alimento = alimento

    if not melhor_alimento or melhor_score < LIMIAR_CONFIANCA_ALIMENTO:
        return None

    if melhor_alimento.permitido:
        return (
            f"Sim, {melhor_alimento.nome} está entre os alimentos sugeridos para consumo "
            "durante o preparo deste exame."
        )

    if (melhor_alimento.horas_antes is not None or melhor_alimento.dias_antes is not None) and exame.agendamentos:
        # Usa o agendamento mais recente para calcular o prazo (mesmo
        # critério usado na tela de preparo do paciente).
        agendamento = sorted(exame.agendamentos, key=lambda a: a.data_hora)[-1]
        prazo = melhor_alimento.horas_antes if melhor_alimento.horas_antes is not None else melhor_alimento.dias_antes
        unidade = "horas" if melhor_alimento.horas_antes is not None else "dias"
        return (
            f"Não, {melhor_alimento.nome} está na lista de alimentos proibidos deste preparo — "
            f"evite a partir de {melhor_alimento.limite_formatado(agendamento.data_hora)} "
            f"({prazo} {unidade} antes do exame)."
        )

    return (
        f"Não, {melhor_alimento.nome} está na lista de alimentos proibidos deste preparo."
    )


# Confiança mínima para considerar que o paciente perguntou sobre um
# medicamento específico do preparo (mesma ideia do limiar de alimento).
LIMIAR_CONFIANCA_MEDICAMENTO = 0.5

_SEPARADOR_SINONIMOS_MEDICAMENTO = re.compile(r"[,/]|\bou\b", re.IGNORECASE)
_PALAVRAS_IGNORADAS_SINONIMO = {"similares", "similar"}


def _sinonimos_medicamento(nome, categoria):
    """Quebra um nome de medicamento em sinônimos/marcas individuais (ex.:
    'Xarelto, Eliquis ou similares' -> ['Xarelto', 'Eliquis']), incluindo a
    categoria (quando houver) como mais uma opção de correspondência —
    assim "posso tomar meu anticoagulante?" também é reconhecido, sem
    precisar citar a marca."""
    pedacos = _SEPARADOR_SINONIMOS_MEDICAMENTO.split(nome)
    sinonimos = []
    for pedaco in pedacos:
        item = pedaco.strip(" .-")
        if item and len(item) >= 3 and item.lower() not in _PALAVRAS_IGNORADAS_SINONIMO:
            sinonimos.append(item)
    if not sinonimos:
        sinonimos = [nome]
    if categoria:
        sinonimos.append(categoria)
    return sinonimos


def buscar_resposta_medicamento(pergunta_usuario: str, exame):
    """
    Verifica se a pergunta do paciente é sobre um medicamento cadastrado no
    preparo do exame — tanto os que precisam ser suspensos quanto os que
    podem ser mantidos — e, se sim, monta uma resposta pronta na hora, sem
    depender de uma FAQ cadastrada manualmente para cada medicamento.

    Retorna uma string com a resposta, ou None se não encontrou nenhum
    medicamento correspondente com confiança suficiente.
    """
    preparo = exame.preparo if exame else None
    if not preparo or (not preparo.medicamentos_suspensos and not preparo.medicamentos_mantidos):
        return None

    kw_pergunta = palavras_chave(pergunta_usuario)
    if not kw_pergunta:
        return None

    # Um nome de medicamento costuma listar várias marcas/sinônimos juntos
    # (ex.: "Xarelto, Eliquis ou similares") — comparar a pergunta com a
    # frase inteira diluiria demais o placar de confiança (a pergunta
    # normalmente cita só UMA marca). Em vez disso, cada sinônimo (e a
    # categoria, quando houver) é comparado separadamente, e o melhor
    # entre eles é o que vale.
    candidatos = []
    for ms in preparo.medicamentos_suspensos:
        if not ms.medicamento:
            continue
        candidatos.append(("suspenso", ms, _sinonimos_medicamento(ms.medicamento.nome, ms.medicamento.categoria)))
    for mm in preparo.medicamentos_mantidos:
        candidatos.append(("mantido", mm, _sinonimos_medicamento(mm.nome, None)))

    melhor_tipo, melhor_item, melhor_score = None, None, 0.0
    for tipo, item, sinonimos in candidatos:
        for sinonimo in sinonimos:
            kw_sinonimo = palavras_chave(sinonimo)
            if not kw_sinonimo:
                continue
            correspondencias = _quantidade_correspondencias(kw_sinonimo, kw_pergunta)
            if correspondencias == 0:
                continue
            score = correspondencias / len(kw_sinonimo)
            if score > melhor_score:
                melhor_score = score
                melhor_tipo = tipo
                melhor_item = item

    if not melhor_item or melhor_score < LIMIAR_CONFIANCA_MEDICAMENTO:
        return None

    if melhor_tipo == "mantido":
        resposta = f"Sim, você pode continuar tomando {melhor_item.nome} normalmente."
        if melhor_item.observacao:
            resposta += f" {melhor_item.observacao}"
        return resposta

    nome_medicamento = melhor_item.medicamento.nome
    if exame.agendamentos:
        agendamento = sorted(exame.agendamentos, key=lambda a: a.data_hora)[-1]
        resposta = (
            f"Não, {nome_medicamento} precisa ser suspenso a partir de "
            f"{melhor_item.limite(agendamento.data_hora).strftime('%d/%m/%Y')} "
            f"({melhor_item.dias_antes} dias antes do exame)."
        )
    else:
        resposta = (
            f"Não, {nome_medicamento} precisa ser suspenso {melhor_item.dias_antes} dias antes do exame."
        )
    if melhor_item.observacao:
        resposta += f" {melhor_item.observacao}"
    resposta += " Em caso de dúvida, fale com o médico que prescreveu antes de suspender por conta própria."
    return resposta
