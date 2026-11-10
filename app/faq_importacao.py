"""Importar o próprio FAQ do médico (pedido do Silvan, 2026-11-10): uma
planilha (Excel .xlsx ou CSV) com as colunas Pergunta, Resposta e, se
quiser, Exame. Aqui só se LÊ o arquivo; quem decide o que entra é a tela de
conferência (app/routes_medico.py)."""
import csv
import io

from app.faq_engine import normalizar

MAX_LINHAS = 500
MAX_BYTES = 2 * 1024 * 1024

_NOMES_COLUNA = {
    "pergunta": ("pergunta", "perguntas", "duvida"),
    "resposta": ("resposta", "respostas"),
    "exame": ("exame", "exames"),
}


class ErroImportacao(Exception):
    pass


def _linhas_do_arquivo(conteudo, nome):
    nome = (nome or "").lower()
    if nome.endswith(".xlsx"):
        from openpyxl import load_workbook

        try:
            planilha = load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True).worksheets[0]
        except Exception:
            raise ErroImportacao("Não consegui abrir o arquivo Excel. Use o modelo da tela.")
        return [["" if c is None else str(c) for c in linha] for linha in planilha.iter_rows(values_only=True)]
    if nome.endswith(".csv"):
        try:
            texto = conteudo.decode("utf-8-sig")
        except UnicodeDecodeError:
            texto = conteudo.decode("latin-1")
        separador = ";" if texto.split("\n", 1)[0].count(";") >= texto.split("\n", 1)[0].count(",") else ","
        return [list(l) for l in csv.reader(io.StringIO(texto), delimiter=separador)]
    raise ErroImportacao("Envie um arquivo Excel (.xlsx) ou CSV (.csv).")


def ler_planilha(conteudo, nome):
    """Devolve [{linha, exame, pergunta, resposta}]. A primeira linha tem de
    ser o cabeçalho com 'Pergunta' e 'Resposta'."""
    if len(conteudo) > MAX_BYTES:
        raise ErroImportacao("Arquivo grande demais (máximo 2 MB).")
    linhas = _linhas_do_arquivo(conteudo, nome)
    if not linhas:
        raise ErroImportacao("O arquivo está vazio.")
    cabecalho = [normalizar(c) for c in linhas[0]]
    colunas = {}
    for campo, apelidos in _NOMES_COLUNA.items():
        for i, c in enumerate(cabecalho):
            if c in apelidos:
                colunas[campo] = i
                break
    if "pergunta" not in colunas or "resposta" not in colunas:
        raise ErroImportacao("A primeira linha precisa ter as colunas 'Pergunta' e 'Resposta' (e, se quiser, 'Exame').")

    def celula(linha, campo):
        i = colunas.get(campo)
        return linha[i].strip() if i is not None and i < len(linha) else ""

    itens = []
    for n, linha in enumerate(linhas[1:], start=2):
        item = {"linha": n, "exame": celula(linha, "exame"),
                "pergunta": celula(linha, "pergunta"), "resposta": celula(linha, "resposta")}
        if not (item["pergunta"] or item["resposta"] or item["exame"]):
            continue
        itens.append(item)
    if not itens:
        raise ErroImportacao("Nenhuma linha preenchida abaixo do cabeçalho.")
    if len(itens) > MAX_LINHAS:
        raise ErroImportacao(f"Máximo de {MAX_LINHAS} linhas por importação (o arquivo tem {len(itens)}).")
    return itens


def modelo_xlsx():
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Meu FAQ"
    ws.append(["Exame", "Pergunta", "Resposta"])
    ws.append(["Colonoscopia", "Posso tomar café antes do exame?",
               "Só café preto, sem leite nem açúcar, até [3 horas antes do exame]."])
    ws.append(["", "Preciso de acompanhante?", "Sim, é obrigatório vir com acompanhante."])
    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 50
    ws.column_dimensions["C"].width = 80
    saida = io.BytesIO()
    wb.save(saida)
    return saida.getvalue()
