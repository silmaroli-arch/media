"""Lista de medicamentos registrados na ANVISA (nome comercial -> princípio
ativo), importada à mão pelo dono a partir do arquivo de dados abertos
DADOS_ABERTOS_MEDICAMENTOS.csv (pedido do Silvan, 2026-10-23). Serve para
o sistema saber, por exemplo, que "Poviztra" é semaglutida - as IAs não
conhecem nomes comerciais recentes e chutavam (ver BACKLOG no HANDOFF).

A importação substitui a lista inteira. O arquivo vem com ponto e vírgula
e, em geral, em Latin-1; as colunas são achadas pelo nome do cabeçalho."""
import csv
import io
import unicodedata
from datetime import datetime

from app.extensions import db

LOTE = 1000

# Cabeçalhos aceitos (sem acento, maiúsculo) para cada campo.
COLUNAS = {
    "nome": ("NOME_PRODUTO", "NOME PRODUTO", "PRODUTO"),
    "principio_ativo": ("PRINCIPIO_ATIVO", "PRINCIPIO ATIVO"),
    "situacao": ("SITUACAO_REGISTRO", "SITUACAO REGISTRO"),
    "classe": ("CLASSE_TERAPEUTICA", "CLASSE TERAPEUTICA"),
    "empresa": ("EMPRESA_DETENTORA_REGISTRO", "EMPRESA DETENTORA REGISTRO", "EMPRESA"),
}


def normalizar_nome(texto):
    """Maiúsculo, sem acento e sem espaço sobrando - chave de busca."""
    sem_acento = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    return " ".join(sem_acento.upper().split())


def _codificacao(conteudo):
    """UTF-8 se o arquivo inteiro for UTF-8 válido, senão Latin-1 (o que a
    ANVISA costuma usar). Confere em pedaços, sem montar o texto inteiro."""
    import codecs

    decodificador = codecs.getincrementaldecoder("utf-8")()
    try:
        for inicio in range(0, len(conteudo), 1 << 20):
            decodificador.decode(conteudo[inicio:inicio + (1 << 20)])
        decodificador.decode(b"", final=True)
        return "utf-8-sig"
    except UnicodeDecodeError:
        return "latin-1"


def importar_csv(conteudo):
    """Lê o CSV (bytes) e substitui a lista. Devolve (quantidade, erro) - erro
    é um texto para o dono quando o arquivo não tem as colunas esperadas (aí
    a lista atual fica como está). Guarda um registro por par (nome
    comercial, princípio ativo). Lê e grava em lotes, sem montar o arquivo
    inteiro na memória - o servidor do plano Free tem pouca memória."""
    from app.models import MedicamentoAnvisa

    texto = io.TextIOWrapper(io.BytesIO(conteudo), encoding=_codificacao(conteudo), newline="")
    primeira_linha = texto.readline()
    if not primeira_linha.strip():
        return 0, "O arquivo está vazio."
    separador = ";" if primeira_linha.count(";") >= primeira_linha.count(",") else ","
    cabecalho = [normalizar_nome(c).replace(" ", "_") for c in next(csv.reader([primeira_linha], delimiter=separador))]
    indice = {}
    for campo, nomes in COLUNAS.items():
        for nome in nomes:
            chave = nome.replace(" ", "_")
            if chave in cabecalho:
                indice[campo] = cabecalho.index(chave)
                break
    if "nome" not in indice or "principio_ativo" not in indice:
        return 0, "Não achei as colunas NOME_PRODUTO e PRINCIPIO_ATIVO no cabeçalho. Confira se é o arquivo DADOS_ABERTOS_MEDICAMENTOS.csv da ANVISA."

    def campo(linha, nome, tamanho):
        i = indice.get(nome)
        valor = linha[i].strip() if i is not None and i < len(linha) else ""
        return valor[:tamanho] or None

    tabela = MedicamentoAnvisa.__table__
    vistos = set()
    lote = []
    total = 0
    agora = datetime.utcnow()

    def gravar():
        nonlocal total
        if total == 0:
            # Só apaga a lista atual quando já há o que pôr no lugar.
            MedicamentoAnvisa.query.delete(synchronize_session=False)
        db.session.execute(tabela.insert(), lote)
        db.session.commit()
        total += len(lote)
        lote.clear()

    for linha in csv.reader(texto, delimiter=separador):
        nome = campo(linha, "nome", 255)
        principio = campo(linha, "principio_ativo", 1000)
        if not nome or not principio:
            continue
        chave = (normalizar_nome(nome), normalizar_nome(principio))
        if chave in vistos:
            continue
        vistos.add(chave)
        lote.append({
            "nome_produto": nome, "nome_normalizado": chave[0], "principio_ativo": principio,
            "situacao": campo(linha, "situacao", 60), "classe_terapeutica": campo(linha, "classe", 255),
            "empresa": campo(linha, "empresa", 255), "importado_em": agora,
        })
        if len(lote) >= LOTE:
            gravar()
    if lote:
        gravar()
    if not total:
        return 0, "Nenhum medicamento com nome e princípio ativo foi encontrado no arquivo."
    return total, None


def buscar(nome):
    """Registros com este nome comercial (sem diferenciar acento/maiúscula),
    os válidos primeiro."""
    from app.models import MedicamentoAnvisa

    itens = MedicamentoAnvisa.query.filter_by(nome_normalizado=normalizar_nome(nome)).all()
    # No arquivo real a situação vem como "Ativo" (2026-10-23); "Válido" fica por garantia.
    return sorted(itens, key=lambda m: 0 if normalizar_nome(m.situacao or "").startswith(("ATIVO", "VALID")) else 1)


# ---------- Uso nas respostas das IAs (pedido do Silvan, 2026-10-23) ----------
# Antes de chamar as IAs, o sistema procura na lista os nomes comerciais
# citados na pergunta e manda junto no contexto "Poviztra = semaglutida" -
# assim as IAs comparam pelo princípio ativo em vez de chutar o que é um nome
# que elas não conhecem (o caso real: Poviztra virou varfarina).

MAXIMO_CITADOS = 5
MAXIMO_PRINCIPIOS_POR_NOME = 3
_TAMANHO_MINIMO_PALAVRA = 4


def _palavras(pergunta):
    return [p for p in normalizar_nome("".join(c if c.isalnum() else " " for c in (pergunta or ""))).split()]


def _candidatos(pergunta):
    """Nomes a procurar na lista: cada palavra fora do dicionário de
    português (uma palavra comum como "calma" ou "dieta" pode ser nome de
    produto e confundiria a IA) e cada par de palavras vizinhas em que ao
    menos uma é fora do dicionário (ex.: "AAS PROTECT"). Sem o dicionário,
    vale qualquer palavra de 5+ letras."""
    from app.whatsapp_conversa import _obter_verificador_ortografico

    palavras = _palavras(pergunta)
    verificador = _obter_verificador_ortografico()
    if verificador:
        desconhecidas = verificador.unknown([p.lower() for p in palavras])
        estranha = [p.lower() in desconhecidas for p in palavras]
    else:
        estranha = [len(p) >= 5 for p in palavras]
    candidatos = []
    for i, palavra in enumerate(palavras):
        if estranha[i] and len(palavra) >= _TAMANHO_MINIMO_PALAVRA:
            candidatos.append(palavra)
        if i + 1 < len(palavras) and (estranha[i] or estranha[i + 1]):
            candidatos.append(f"{palavra} {palavras[i + 1]}")
    return list(dict.fromkeys(candidatos))


def medicamentos_citados(pergunta):
    """[(nome comercial, [princípios ativos], classe ou None)] dos nomes da
    pergunta que estão na lista - no máximo MAXIMO_CITADOS, os registros
    ativos primeiro. Um par de palavras que bate esconde a palavra solta."""
    from app.models import MedicamentoAnvisa

    candidatos = _candidatos(pergunta)
    if not candidatos:
        return []
    itens = MedicamentoAnvisa.query.filter(MedicamentoAnvisa.nome_normalizado.in_(candidatos)).all()
    por_nome = {}
    for item in sorted(itens, key=lambda m: 0 if normalizar_nome(m.situacao or "").startswith(("ATIVO", "VALID")) else 1):
        por_nome.setdefault(item.nome_normalizado, []).append(item)
    achados = [c for c in candidatos if c in por_nome]
    achados = [c for c in achados if not any(c != o and c in o.split() for o in achados)]
    resultado = []
    for chave in achados[:MAXIMO_CITADOS]:
        registros = por_nome[chave]
        principios = list(dict.fromkeys(r.principio_ativo for r in registros))[:MAXIMO_PRINCIPIOS_POR_NOME]
        classe = next((r.classe_terapeutica for r in registros if r.classe_terapeutica), None)
        resultado.append((registros[0].nome_produto, principios, classe))
    return resultado


def contexto_medicamentos(pergunta):
    """Bloco para o contexto das IAs, ou "" quando a pergunta não cita nada
    da lista (ou a lista está vazia)."""
    citados = medicamentos_citados(pergunta)
    if not citados:
        return ""
    linhas = ["Medicamentos citados na pergunta, segundo a lista oficial da ANVISA (nome comercial = princípio ativo):"]
    for nome, principios, classe in citados:
        linha = f"- {nome} = {' / '.join(principios)}"
        if classe:
            linha += f" (classe: {classe})"
        linhas.append(linha)
    return "\n".join(linhas)
