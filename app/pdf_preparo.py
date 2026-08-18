"""Extração heurística (sem IA) de sugestões de preparo a partir de um PDF.

Não tenta ser perfeito: extrai o texto puro do PDF (sempre colocado como
base no campo de instruções, para não perder nada) e usa expressões
regulares simples para sugerir, além disso, cortes de alimentação/líquido,
alimentos permitidos/proibidos, medicamentos a suspender com seus prazos e
informações gerais avulsas — tudo isso é só uma sugestão pré-preenchida no
formulário de cadastro de um novo modelo de preparo; nada é salvo no banco
até a pessoa revisar e clicar em "Salvar".
"""
import io
import re

from openpyxl import Workbook
from pypdf import PdfReader


def extrair_texto(stream):
    leitor = PdfReader(stream)
    partes = []
    for pagina in leitor.pages:
        texto = pagina.extract_text() or ""
        partes.append(texto)
    return "\n".join(partes)


def _sugerir_nome(linhas):
    """Primeira linha "de título" (bem curta, maioria em caixa alta) entre
    as primeiras do documento — geralmente é o nome do exame/preparo."""
    for linha in linhas[:15]:
        linha = linha.strip()
        letras = [c for c in linha if c.isalpha()]
        if len(linha) < 8 or not letras:
            continue
        proporcao_maiusculas = sum(1 for c in letras if c.isupper()) / len(letras)
        if proporcao_maiusculas > 0.8:
            return linha
    return None


# Quando o preparo restringe algo "no dia do exame" sem informar um número
# de horas exato, é tratado como equivalente a 12 horas antes — mesma ideia
# do jejum típico da véspera/dia do exame.
HORAS_PADRAO_NO_DIA_DO_EXAME = 12


def _sugerir_cortes(texto):
    cortes = []

    for match in re.finditer(r"jejum\s+(?:total\s+)?de\s+(\d+)\s*horas", texto, re.IGNORECASE):
        cortes.append({"descricao": "Jejum total (sólidos e líquidos)", "horas_antes": int(match.group(1))})

    for match in re.finditer(r"l[ií]quidos?[^.\n]{0,60}?(\d+)\s*horas?\s*antes", texto, re.IGNORECASE):
        cortes.append({"descricao": "Líquidos claros", "horas_antes": int(match.group(1))})

    for match in re.finditer(r"s[oó]lidos?[^.\n]{0,60}?(\d+)\s*horas?\s*antes", texto, re.IGNORECASE):
        cortes.append({"descricao": "Alimentos sólidos", "horas_antes": int(match.group(1))})

    # "Jejum ... no dia do exame" sem um número de horas explícito — usa o
    # padrão de 12h em vez de deixar de sugerir nada.
    if re.search(r"jejum[^.\n]{0,60}?no dia do exame", texto, re.IGNORECASE) and not re.search(
        r"jejum\s+(?:total\s+)?de\s+\d+\s*horas", texto, re.IGNORECASE
    ):
        cortes.append({"descricao": "Jejum total (sólidos e líquidos)", "horas_antes": HORAS_PADRAO_NO_DIA_DO_EXAME})

    # Remove duplicados exatos (mesma descrição + mesmas horas).
    vistos = set()
    unicos = []
    for corte in cortes:
        chave = (corte["descricao"], corte["horas_antes"])
        if chave not in vistos:
            vistos.add(chave)
            unicos.append(corte)
    return unicos


# Palavras que costumam aparecer coladas em títulos de seção como "3 DIAS
# ANTES DO EXAME" (sem serem, de fato, o nome de um medicamento) — filtradas
# para reduzir falsos positivos da extração automática.
_PALAVRAS_IGNORADAS = {"do", "da", "de", "exame", "do exame", "da consulta"}


def _converter_para_dias(quantidade_str, unidade_str):
    """1 semana = 7 dias (ex.: '2 semanas antes' -> 14 dias antes)."""
    quantidade = int(quantidade_str)
    if unidade_str.lower().startswith("semana"):
        return quantidade * 7
    return quantidade


_SEPARADOR_MEDICAMENTOS = re.compile(r"[,/]")


def _dividir_lista_medicamentos(texto_lista):
    """Quebra uma lista de medicamentos separados por "/" ou "," em itens
    individuais (ex.: 'Ozempic, Mounjaro, Trulicity' -> 3 medicamentos
    separados) — o PDF costuma listar várias marcas/classes juntas numa
    linha só, e cada uma deve virar sua própria linha no cadastro."""
    pedacos = _SEPARADOR_MEDICAMENTOS.split(texto_lista)
    itens = []
    vistos = set()
    for pedaco in pedacos:
        # Normaliza espaços (inclusive quebras de linha, que sobram quando a
        # lista atravessa mais de uma linha do PDF original) antes de tirar
        # os separadores nas pontas - sem isso, itens como "sal\nou maisena"
        # ficavam com a quebra de linha "crua" no meio do nome.
        item = re.sub(r"\s+", " ", pedaco).strip(" .-")
        if item and len(item) >= 3 and item.lower() not in vistos and item.lower() not in _PALAVRAS_IGNORADAS:
            vistos.add(item.lower())
            itens.append(item)
    return itens


def _sugerir_medicamentos(texto):
    medicamentos = []

    # Estilo "N dias/semanas antes: LISTA." (comum em tabelas de prazos).
    # O ":" ou "-" é exigido aqui para não colidir com o estilo em prosa
    # tratado abaixo ("Suspender N semanas antes do exame os medicamentos
    # ... (LISTA)"), que não tem esse separador logo depois de "antes".
    padrao_tabela = re.compile(r"(\d+)\s*(dias?|semanas?)\s*antes\s*[:\-]\s*([^.\n(]+)", re.IGNORECASE)
    for match in padrao_tabela.finditer(texto):
        dias = _converter_para_dias(match.group(1), match.group(2))
        nomes = match.group(3).strip().rstrip(".").strip()
        for nome in _dividir_lista_medicamentos(nomes):
            medicamentos.append({"nome": nome, "dias_antes": dias})

    # Estilo em prosa: "Suspender N dias/semanas antes do exame os
    # medicamentos ... (LISTA)" — comum em preparos de teste respiratório.
    padrao_prosa = re.compile(
        r"suspender\s+(\d+)\s*(dias?|semanas?)\s*antes[^()\n]*\(([^)]+)\)", re.IGNORECASE
    )
    for match in padrao_prosa.finditer(texto):
        dias = _converter_para_dias(match.group(1), match.group(2))
        nomes = match.group(3).strip().rstrip(".").strip()
        for nome in _dividir_lista_medicamentos(nomes):
            medicamentos.append({"nome": nome, "dias_antes": dias})

    return medicamentos


def _sugerir_observacoes_medicamentos(linhas):
    # "e"/"eh" além de "é": a extração de texto de PDF às vezes perde
    # acentos (comum quando o documento foi gerado sem os caracteres
    # devidamente codificados), então "não é necessário" pode chegar como
    # "nao e necessario" - sem aceitar o "e" solto aqui, essa observação
    # (que é toda a razão de ser desta função) passava despercebida.
    for linha in linhas:
        if re.search(r"n[aã]o\s+(?:é|eh|e)?\s*necess[aá]rio\s+suspender", linha, re.IGNORECASE):
            return linha.strip()
    return None


def _sugerir_medicamentos_mantidos(linhas):
    """Complementa `_sugerir_observacoes_medicamentos` (que devolve a frase
    inteira, ex.: 'OBS: NÃO É NECESSÁRIO SUSPENDER O AAS, SOMALGIN,
    ASPIRINA.') separando os nomes num item por medicamento — mesmo formato
    estruturado (PreparoMedicamentoMantido) que a importação de Excel já
    produz para a Ação "Não suspender", em vez de um só item com a frase
    toda."""
    frase = _sugerir_observacoes_medicamentos(linhas)
    if not frase:
        return []
    match = re.search(r"necess[aá]rio\s+suspender\s+o?\s*(.+?)\.?\s*$", frase, re.IGNORECASE)
    if not match:
        return []
    return [{"nome": nome, "observacao": None} for nome in _dividir_lista_medicamentos(match.group(1))]


# Marcadores de item de lista usados nos PDFs de preparo reais — quando uma
# linha começa com um destes, é tratada como o início de um item; linhas
# seguintes sem marcador são "continuação" do mesmo item (o texto do PDF
# costuma quebrar a frase em mais de uma linha).
_BULLET_REGRA = ("➢", "▪", "▶", "→")
_BULLET_LISTA_LONGA = ("•", "*", "-")
_TODOS_BULLETS = _BULLET_REGRA + _BULLET_LISTA_LONGA

# Além dos símbolos acima, muitos PDFs de preparo organizam instruções (ex.:
# modo de preparo de um laxante, cuidados do dia do exame) em listas
# numeradas ("1.", "2.") ou letradas ("a)", "b)") em vez de símbolos - sem
# reconhecer esse estilo também, seções inteiras (receituário, orientações
# do dia do exame) ficavam de fora da sugestão.
_PADRAO_ITEM_NUMERADO = re.compile(r"^(?:\d+[.)]|[a-zA-Z][.)])\s+")


def _parece_titulo_secao(linha):
    """Uma linha curta e majoritariamente em maiúsculas (ex.: "SUSPENDER",
    "DIA DO EXAME", "VÉSPERA DO EXAME") é provavelmente um título de seção,
    não a continuação de um item de lista anterior. Sem essa checagem, o
    título inteiro era "engolido" como parte do último item de lista aberto
    (nenhum marcador novo pra fechá-lo), e a linha real do próximo item -
    colada ao título por engano - podia passar do limite de tamanho de uma
    informação geral e ser descartada por inteiro."""
    letras = [c for c in linha if c.isalpha()]
    if not letras or len(linha) > 40:
        return False
    return sum(1 for c in letras if c.isupper()) / len(letras) > 0.8


def _juntar_por_marcador(linhas, marcadores_alvo, aceitar_numerado=False):
    """Junta um item de lista marcado por um dos `marcadores_alvo` (ou, se
    `aceitar_numerado`, por um item numerado/letrado tipo "1." ou "a)") com
    as linhas de continuação seguintes (sem marcador nenhum). Qualquer outro
    marcador reconhecido encerra o item atual sem iniciar um novo (só os
    marcadores em `marcadores_alvo`/numerados iniciam itens desta lista); um
    título de seção (ver `_parece_titulo_secao`) faz o mesmo, mesmo sem ser
    um marcador de lista."""
    itens = []
    atual = None
    ativo = False
    for linha in linhas:
        s = linha.strip()
        primeiro = s[0] if s else ""
        marcador_numerado = aceitar_numerado and _PADRAO_ITEM_NUMERADO.match(s)
        if primeiro in marcadores_alvo or marcador_numerado:
            if atual is not None:
                itens.append(atual.strip())
            atual = _PADRAO_ITEM_NUMERADO.sub("", s, count=1) if marcador_numerado else s[1:].strip()
            ativo = True
        elif primeiro in _TODOS_BULLETS or (ativo and _parece_titulo_secao(s)):
            if atual is not None:
                itens.append(atual.strip())
            atual = None
            ativo = False
        elif ativo:
            atual += " " + s
    if atual is not None:
        itens.append(atual.strip())
    return itens


# Regras já capturadas de forma estruturada (jejum/corte/medicamento/exame
# anterior proibido) não entram de novo como "informação geral", pra não
# aparecer duplicado.
_PADROES_JA_ESTRUTURADOS = [
    re.compile(r"jejum\s+(?:total\s+)?de\s+\d+\s*horas", re.IGNORECASE),
    re.compile(r"\d+\s*horas?\s*antes", re.IGNORECASE),
    re.compile(r"\d+\s*dias?\s*antes", re.IGNORECASE),
    re.compile(r"\d+\s*semanas?\s*antes", re.IGNORECASE),
    re.compile(r"n[aã]o\s+deve\s+ter\s+(?:realizado|feito|se\s+submetido\s+a)", re.IGNORECASE),
]


def _sugerir_informacoes_gerais(linhas):
    """Regras avulsas do preparo (ex.: 'não utilizar enxaguante bucal com
    álcool no dia do exame', passos de um receituário tipo 'a) dissolver o
    sachê em água', orientações do dia do exame numeradas) que não são nem
    um corte de tempo nem um medicamento a suspender — ficam numa lista
    própria, separada do texto livre de instruções. Reconhece tanto os
    marcadores de símbolo (➢▪▶→) quanto listas numeradas/letradas ("1.",
    "a)") - comuns em receituários e orientações do dia do exame."""
    # O "*" também é usado, em alguns preparos, como marcador de observação
    # avulsa no meio de uma seção de orientações do dia do exame (ex.: "*Em
    # caso de náuseas..."). Sem incluí-lo aqui como marcador de INÍCIO de
    # item (e não só de encerramento, que é o papel padrão de qualquer
    # marcador reconhecido fora de `marcadores_alvo` - ver `_TODOS_BULLETS`
    # em `_juntar_por_marcador`), essas linhas eram descartadas por inteiro
    # em vez de aparecerem como informação geral.
    itens = _juntar_por_marcador(linhas, _BULLET_REGRA + ("*",), aceitar_numerado=True)

    sugestoes = []
    for item in itens:
        if not (10 <= len(item) <= 300):
            continue
        if any(padrao.search(item) for padrao in _PADROES_JA_ESTRUTURADOS):
            continue
        sugestoes.append(item)
    return sugestoes


# "Pode comer até às 20:00 da véspera do exame" / "... do dia anterior" é um
# horário FIXO do relógio num dia relativo ao exame - diferente de "X horas
# antes" (ver _sugerir_cortes) - vira uma informação geral com um prazo
# calculável (dias_antes=1 + hora_exata), no mesmo estilo já suportado por
# PreparoInfoGeral e pela importação de Excel.
_PADRAO_PRAZO_VESPERA = re.compile(
    r"pode\s+comer\s+at[ée]\s+(?:as|às)\s+(\d{1,2})[:h](\d{2})\s*(?:horas?)?\s*"
    r"(?:d[ae]\s+v[eé]spera|do\s+dia\s+anterior)",
    re.IGNORECASE,
)


def _sugerir_prazo_vespera(texto):
    match = _PADRAO_PRAZO_VESPERA.search(texto)
    if not match:
        return []
    hora = f"{int(match.group(1)):02d}:{match.group(2)}"
    return [{
        "texto": f"Pode comer até às {hora} da véspera do exame (depois disso, só líquidos)",
        "horas_antes": None, "dias_antes": 1, "hora_exata": hora,
    }]


_SEPARADOR_ALIMENTOS = re.compile(r"[,/]| e ", re.IGNORECASE)


def _dividir_lista_alimentos(texto_lista):
    """Quebra uma lista em prosa (ex.: 'leite e derivados, suco, água com
    gás') em itens individuais. Parênteses são tratados como mais um
    separador, pra também aproveitar os exemplos citados dentro deles
    (ex.: 'leguminosas (feijão, lentilha)' -> 'leguminosas', 'feijão',
    'lentilha') — útil porque o paciente tende a perguntar pelo nome
    específico, não pela categoria."""
    texto_lista = re.sub(r"[()]", ",", texto_lista)
    pedacos = _SEPARADOR_ALIMENTOS.split(texto_lista)
    itens = []
    vistos = set()
    for pedaco in pedacos:
        # Normaliza espaços (inclusive quebras de linha) antes de tirar os
        # separadores nas pontas - sem isso, itens como "alimentos com
        # sementes" (quebrado em duas linhas no PDF original) ficavam com um
        # "\n" cru no meio do nome (ex.: "alimentos com\nsementes").
        item = re.sub(r"\s+", " ", pedaco).strip(" .-")
        if item and 2 <= len(item) <= 60 and item.lower() not in vistos:
            vistos.add(item.lower())
            itens.append(item)
    return itens


# Rótulos alternativos de alimento proibido/permitido, direto no meio de um
# parágrafo (sem marcador de item nenhum) - estilo bem comum em preparos de
# colonoscopia organizados em tabela ("NÃO COMER: ... PODE COMER: ..."),
# diferente do estilo com marcador de lista tratado em _sugerir_alimentos
# ("Alimentos proibidos:" / "Sugestão para consumo:").
_PADRAO_NAO_COMER = re.compile(r"n[aã]o\s+comer\s*[:\-]\s*(.+?)\.(?:\s|$)", re.IGNORECASE | re.DOTALL)
_PADRAO_PODE_COMER = re.compile(r"pode\s+comer\s*[:\-]\s*(.+?)\.(?:\s|$)", re.IGNORECASE | re.DOTALL)


def _sugerir_alimentos_rotulo_direto(texto):
    """Mesma ideia de _sugerir_alimentos, mas para o estilo "NÃO COMER: .../
    PODE COMER: ..." sem marcador de lista — como o rótulo já é bem
    específico (ao contrário de um "-" ou "•" solto, que apareceria em
    qualquer lista), não precisa de marcador pra ser reconhecido com
    segurança. Repete o mesmo prazo padrão de 12h pros proibidos."""
    alimentos = []
    m = _PADRAO_NAO_COMER.search(texto)
    if m:
        for nome in _dividir_lista_alimentos(m.group(1)):
            alimentos.append({"nome": nome, "permitido": False, "horas_antes": HORAS_PADRAO_NO_DIA_DO_EXAME})
    m = _PADRAO_PODE_COMER.search(texto)
    if m:
        for nome in _dividir_lista_alimentos(m.group(1)):
            alimentos.append({"nome": nome, "permitido": True, "horas_antes": None})
    return alimentos


def _sugerir_alimentos(linhas, texto):
    """Alimentos proibidos e sugestões de consumo (permitidos), extraídos
    tanto das listas com marcador ('Alimentos proibidos:' / 'Sugestão para
    consumo:', uma linha por item) quanto de rótulos diretos em parágrafo
    ('NÃO COMER: .../PODE COMER: ...', ver _sugerir_alimentos_rotulo_direto)
    — ficam disponíveis tanto para mostrar ao paciente quanto para o chat
    responder automaticamente perguntas do tipo 'posso comer X?'.

    Alimentos proibidos sem um horário específico mencionado recebem o
    prazo padrão de 12 horas antes do exame (mesma lógica do jejum da
    véspera/dia do exame)."""
    itens = _juntar_por_marcador(linhas, _BULLET_LISTA_LONGA)
    alimentos = []
    for item in itens:
        m = re.match(r"alimentos?\s+proibidos?\s*[:\-]\s*(.+)", item, re.IGNORECASE)
        if m:
            for nome in _dividir_lista_alimentos(m.group(1)):
                alimentos.append({"nome": nome, "permitido": False, "horas_antes": HORAS_PADRAO_NO_DIA_DO_EXAME})
            continue
        m = re.match(r"sugest(?:[aã]o|[oõ]es)\s+(?:para|de)\s+consumo\s*[:\-]\s*(.+)", item, re.IGNORECASE)
        if m:
            for nome in _dividir_lista_alimentos(m.group(1)):
                alimentos.append({"nome": nome, "permitido": True, "horas_antes": None})
    alimentos.extend(_sugerir_alimentos_rotulo_direto(texto))
    return alimentos


_SEPARADOR_EXAMES_ANTERIORES = re.compile(r"[,/]| ou ", re.IGNORECASE)


def _dividir_lista_exames_anteriores(texto_lista):
    """Quebra uma lista de exames/procedimentos em prosa (ex.: 'colonoscopia
    ou lavagens intestinais') em itens individuais — nesse tipo de frase os
    itens costumam vir ligados por "ou", não por "e"."""
    texto_lista = re.sub(r"[()]", ",", texto_lista)
    pedacos = _SEPARADOR_EXAMES_ANTERIORES.split(texto_lista)
    itens = []
    vistos = set()
    for pedaco in pedacos:
        item = re.sub(r"\s+", " ", pedaco).strip(" .-")
        if item and 2 <= len(item) <= 80 and item.lower() not in vistos:
            vistos.add(item.lower())
            itens.append(item)
    return itens


# Frases do tipo "Não deve ter realizado colonoscopia ou lavagens
# intestinais nas 4 semanas anteriores ao exame." — diferente de um
# medicamento a suspender, aqui a restrição é sobre um procedimento que já
# pode ter acontecido no passado e invalidaria o preparo/resultado.
_PADRAO_EXAME_ANTERIOR = re.compile(
    r"n[aã]o\s+deve\s+ter\s+(?:realizado|feito|se\s+submetido\s+a)\s+(.+?)\s+"
    r"nas?\s+(\d+)\s*(dias?|semanas?)\s*anteriores?",
    re.IGNORECASE,
)


def _sugerir_exames_anteriores(texto):
    """Exames/procedimentos que o paciente não deve ter feito num período
    antes deste exame (ex.: colonoscopia, lavagens intestinais), com o
    prazo em dias já convertido (1 semana = 7 dias)."""
    exames = []
    for match in _PADRAO_EXAME_ANTERIOR.finditer(texto):
        dias = _converter_para_dias(match.group(2), match.group(3))
        for nome in _dividir_lista_exames_anteriores(match.group(1)):
            exames.append({"nome": nome, "dias_antes": dias})
    return exames


def extrair_sugestao_de_pdf(stream):
    """Retorna um dicionário com uma sugestão pré-preenchida a partir do
    texto extraído do PDF: nome sugerido, instruções (texto completo,
    servindo de base para revisão manual), cortes de alimentação sugeridos,
    alimentos permitidos/proibidos sugeridos, medicamentos a suspender
    sugeridos (e os que NÃO precisam ser suspensos, individualmente),
    exames/procedimentos anteriores proibidos sugeridos e informações
    gerais avulsas sugeridas (incluindo, quando reconhecido, um prazo do
    tipo "até às HH:MM da véspera do exame")."""
    texto = extrair_texto(stream)
    linhas = [l for l in texto.splitlines() if l.strip()]

    return {
        "nome_sugerido": _sugerir_nome(linhas),
        "instrucoes": texto.strip(),
        "cortes": _sugerir_cortes(texto),
        "medicamentos": _sugerir_medicamentos(texto),
        "observacoes_medicamentos": _sugerir_observacoes_medicamentos(linhas),
        "medicamentos_mantidos": _sugerir_medicamentos_mantidos(linhas),
        "informacoes_gerais": _sugerir_informacoes_gerais(linhas) + _sugerir_prazo_vespera(texto),
        "alimentos": _sugerir_alimentos(linhas, texto),
        "exames_anteriores": _sugerir_exames_anteriores(texto),
    }


_CARACTERES_INVALIDOS_NOME_ABA = set('[]:*?/\\')


def _nome_aba_valido(nome_sugerido):
    """Nome de aba do Excel tem limite de 31 caracteres e não pode conter
    alguns símbolos - normaliza a sugestão de nome (ou usa um genérico) pra
    caber nessa regra."""
    nome = "".join(c for c in (nome_sugerido or "") if c not in _CARACTERES_INVALIDOS_NOME_ABA).strip()
    return (nome or "Preparo")[:31]


def gerar_xlsx_da_sugestao(sugestao):
    """Gera uma planilha .xlsx no MESMO formato aceito pela importação de
    Excel do cadastro de modelo de preparo (ver `app.xlsx_preparo` — colunas
    Tipo/Ação/Agrupador/Nome/Dias antes/Horas antes/Hora exata), a partir de
    uma sugestão extraída de um PDF (ver `extrair_sugestao_de_pdf`).

    A ideia é dar pra pessoa uma planilha já preenchida pra revisar e
    ajustar com calma no Excel — depois é só importar essa mesma planilha
    pelo botão "Importar de um Excel" na tela de novo modelo de preparo.
    Não tenta ser perfeito (mesma heurística da extração de PDF): algumas
    regras em prosa livre viram um aviso solto em vez de uma regra
    estruturada — por isso vale sempre revisar antes de importar de volta.
    """
    workbook = Workbook()
    planilha = workbook.active
    planilha.title = _nome_aba_valido(sugestao.get("nome_sugerido"))
    planilha.append(["Tipo", "Ação", "Agrupador", "Nome", "Dias antes", "Horas antes", "Hora exata"])

    for corte in sugestao.get("cortes") or []:
        planilha.append(["Aviso", "", "", corte.get("descricao", ""), None, corte.get("horas_antes"), None])

    for medicamento in sugestao.get("medicamentos") or []:
        planilha.append([
            "Medicamento", "Suspender", medicamento.get("categoria") or "",
            medicamento.get("nome", ""), medicamento.get("dias_antes"), None, None,
        ])

    mantidos = sugestao.get("medicamentos_mantidos") or []
    if mantidos:
        # Um item por medicamento (ex.: AAS, Somalgin, Aspirina em linhas
        # separadas), igual ao que a importação de Excel já produz pra
        # "Não suspender" - melhor que uma frase só, que viraria um "nome"
        # gigante em vez de itens individuais reconhecíveis pelo chat.
        for item in mantidos:
            planilha.append(["Medicamento", "Não suspender", "", item.get("nome", ""), None, None, None])
    elif sugestao.get("observacoes_medicamentos"):
        # Não deu pra separar por medicamento - melhor a frase inteira numa
        # linha só do que perder a informação.
        planilha.append(["Medicamento", "Não suspender", "", sugestao["observacoes_medicamentos"], None, None, None])

    for alimento in sugestao.get("alimentos") or []:
        acao = "Permitido (Sugestão de consumo)" if alimento.get("permitido") else "Suspender"
        planilha.append([
            "Alimento", acao, "", alimento.get("nome", ""),
            alimento.get("dias_antes"), alimento.get("horas_antes"), None,
        ])

    for exame in sugestao.get("exames_anteriores") or []:
        planilha.append([
            "Exames / Procedimentos", "Proibido", "", exame.get("nome", ""),
            exame.get("dias_antes"), None, None,
        ])

    for info in sugestao.get("informacoes_gerais") or []:
        # A maioria vem como texto solto (sem prazo estruturado) - a
        # extração de Excel, ao reimportar, vai tratar como um aviso sem
        # prazo (ou como corte, se mencionar jejum com horas). Alguns itens
        # (ex.: "até às 20:00 da véspera", ver _sugerir_prazo_vespera) já
        # vêm com um prazo calculável (dias_antes + hora_exata) - esses
        # preenchem as colunas certas em vez de virar só texto solto.
        if isinstance(info, str):
            texto, horas_antes, dias_antes, hora_exata = info, None, None, None
        else:
            texto = info.get("texto", "")
            horas_antes, dias_antes, hora_exata = info.get("horas_antes"), info.get("dias_antes"), info.get("hora_exata")
        if texto:
            planilha.append(["Aviso", "", "", texto, dias_antes, horas_antes, hora_exata])

    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return buffer
