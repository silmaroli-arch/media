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
