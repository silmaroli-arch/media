"""Versão em página (HTML) do PDF original do preparo (pedido do Silvan,
2026-10-24, "opção 3"): a IA transcreve o PDF do médico para uma página
que o paciente lê confortavelmente no celular, no mesmo visual da linha do
tempo. O paciente só vê essa página depois que o médico revisa e aprova; até
lá (e sempre, como "Ver o PDF original") continua tendo o PDF.

Fidelidade (regra do Silvan: a IA nunca chuta): o prompt manda TRANSCREVER,
sem resumir, completar ou reescrever, e `conferir_fidelidade` compara, sem
IA, o texto do PDF com o da página - números e palavras do PDF que sumiram
aparecem para o médico na revisão.

A geração roda em segundo plano (thread, como em app.base_aprendizado): com
3 IAs em cadeia ela pode passar dos 120s do gunicorn. A tela de revisão se
atualiza sozinha enquanto o status é "gerando"."""
import base64
import html as html_lib
import io
import logging
import re
import threading
import unicodedata
from datetime import datetime
from html.parser import HTMLParser

from flask import current_app

from app.extensions import db
from app.ia_pdf_preparo import (
    MODELO_ANTHROPIC_PADRAO, MODELO_OPENAI_PADRAO, MODELO_PADRAO, _cliente_claude, _cliente_gemini,
    _cliente_openai,
)
from app.pdf_preparo import extrair_texto

logger = logging.getLogger(__name__)

TIPO_USO = "pagina_pdf_preparo"
MAXIMO_AVISOS = 20

PROMPT_SISTEMA = """Você transcreve o PDF de preparo de exame de um médico para uma página web que o paciente vai ler no celular.

Regras OBRIGATÓRIAS - é um documento médico, o paciente segue exatamente o que estiver escrito:
- TRANSCREVA todo o conteúdo do PDF, na mesma ordem, com as mesmas palavras. Não resuma, não omita nada, não complete, não acrescente explicações, não reescreva frases, não corrija o conteúdo e não troque termos (nomes de medicamentos, doses, horários, prazos, quantidades e números devem sair idênticos).
- Pode corrigir só a QUEBRA de linha/hifenização que vem do PDF (palavras partidas no fim da linha) e organizar o visual.
- Use apenas estas tags HTML: h2, h3, p, ul, ol, li, strong, em, br, table, thead, tbody, tr, th, td. Sem atributos (exceto colspan/rowspan em td/th), sem estilos, sem links, sem imagens, sem scripts.
- Títulos e seções do PDF viram h2/h3; listas viram ul/ol; tabelas (ex.: horários, cardápio) viram table; destaques do PDF (negrito, AVISOS, IMPORTANTE) viram strong.
- Não repita o nome da clínica, endereço, telefone ou logotipo se forem só cabeçalho/rodapé de página; todo o resto do conteúdo do preparo entra.
- Campos em branco para preencher à mão (ex.: "Data e hora do exame: ___/___/___ às ___h", "Nome: ______") não entram - o paciente já vê a data do exame dele na página.
- Responda SOMENTE com o HTML (sem ```, sem <html>, <head> ou <body>, sem comentários)."""


# ---------- Limpeza do HTML (allowlist) ----------

TAGS_PERMITIDAS = {"h2", "h3", "p", "ul", "ol", "li", "strong", "em", "br", "table", "thead", "tbody", "tr", "th", "td"}
TAGS_DESCARTAR_CONTEUDO = {"script", "style", "head", "title", "iframe", "object", "svg"}
# Títulos que a IA pode mandar em outro nível viram os permitidos.
TAGS_TROCAR = {"h1": "h2", "h4": "h3", "h5": "h3", "h6": "h3", "b": "strong", "i": "em", "div": "p"}


class _Limpador(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.partes = []
        self.descartando = 0

    def handle_starttag(self, tag, attrs):
        if tag in TAGS_DESCARTAR_CONTEUDO:
            self.descartando += 1
            return
        tag = TAGS_TROCAR.get(tag, tag)
        if self.descartando or tag not in TAGS_PERMITIDAS:
            return
        extras = "".join(
            f' {nome}="{int(valor)}"' for nome, valor in attrs
            if tag in ("td", "th") and nome in ("colspan", "rowspan") and (valor or "").isdigit()
        )
        self.partes.append(f"<{tag}{extras}>")

    def handle_startendtag(self, tag, attrs):
        if not self.descartando and tag == "br":
            self.partes.append("<br>")

    def handle_endtag(self, tag):
        if tag in TAGS_DESCARTAR_CONTEUDO:
            self.descartando = max(0, self.descartando - 1)
            return
        tag = TAGS_TROCAR.get(tag, tag)
        if not self.descartando and tag in TAGS_PERMITIDAS and tag != "br":
            self.partes.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.descartando:
            self.partes.append(html_lib.escape(data, quote=False))


def sanitizar_html(texto):
    """Só as tags da lista, sem atributos (fora colspan/rowspan) - vale para
    o que a IA devolve e para o que o médico edita na revisão."""
    texto = (texto or "").strip()
    texto = re.sub(r"^```(?:html)?\s*|\s*```$", "", texto)
    limpador = _Limpador()
    limpador.feed(texto)
    limpador.close()
    return "".join(limpador.partes).strip()


# ---------- Conferência de fidelidade (sem IA) ----------

def _normalizar(texto):
    sem_acento = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    return sem_acento.lower()


def _texto_do_html(texto_html):
    return html_lib.unescape(re.sub(r"<[^>]+>", " ", texto_html or ""))


def _palavras_e_numeros(texto):
    texto = re.sub(r"-\s*\n\s*", "", texto or "")  # hifenização do PDF
    normal = _normalizar(texto)
    numeros = set(re.findall(r"\d+(?:[.,:]\d+)*", normal))
    palavras = set(p for p in re.findall(r"[a-z]{4,}", normal))
    return palavras, numeros


def conferir_fidelidade(texto_pdf, texto_html):
    """Números e palavras (4+ letras) do PDF que não aparecem na página -
    lista curta para o médico conferir. Vazia quando não há texto no PDF
    (escaneado) para comparar."""
    if not (texto_pdf or "").strip():
        return []
    palavras_pdf, numeros_pdf = _palavras_e_numeros(texto_pdf)
    palavras_html, numeros_html = _palavras_e_numeros(_texto_do_html(texto_html))
    faltando = sorted(numeros_pdf - numeros_html) + sorted(palavras_pdf - palavras_html)
    return faltando[:MAXIMO_AVISOS]


# ---------- Chamada às IAs (mesma cadeia da importação de PDF) ----------

def _via_gemini(cliente, pdf_bytes, texto, uso):
    from google.genai import types as genai_types

    resposta = cliente.models.generate_content(
        model=MODELO_PADRAO,
        contents=[
            genai_types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
            "Transcreva este PDF de preparo para HTML seguindo as instruções.",
        ],
        config=genai_types.GenerateContentConfig(system_instruction=PROMPT_SISTEMA, max_output_tokens=16384),
    )
    meta = getattr(resposta, "usage_metadata", None)
    uso.update(modelo=getattr(resposta, "model_version", None) or MODELO_PADRAO,
               tokens_entrada=getattr(meta, "prompt_token_count", None),
               tokens_saida=getattr(meta, "candidates_token_count", None))
    return resposta.text


def _via_claude(cliente, pdf_bytes, texto, uso):
    resposta = cliente.messages.create(
        model=MODELO_ANTHROPIC_PADRAO, max_tokens=16384, system=PROMPT_SISTEMA,
        messages=[{"role": "user", "content": [
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                             "data": base64.standard_b64encode(pdf_bytes).decode("ascii")}},
            {"type": "text", "text": "Transcreva este PDF de preparo para HTML seguindo as instruções."},
        ]}],
    )
    meta = getattr(resposta, "usage", None)
    uso.update(modelo=getattr(resposta, "model", None) or MODELO_ANTHROPIC_PADRAO,
               tokens_entrada=getattr(meta, "input_tokens", None), tokens_saida=getattr(meta, "output_tokens", None))
    return "".join(getattr(bloco, "text", "") for bloco in resposta.content)


def _via_openai(cliente, pdf_bytes, texto, uso):
    # O ChatGPT recebe o texto extraído (sem o PDF) - não serve para PDF escaneado.
    if not texto.strip():
        raise ValueError("PDF sem texto selecionável")
    resposta = cliente.chat.completions.create(
        model=MODELO_OPENAI_PADRAO, max_tokens=16384,
        messages=[{"role": "system", "content": PROMPT_SISTEMA},
                  {"role": "user", "content": f"Texto extraído do PDF de preparo:\n\n---\n{texto}\n---"}],
    )
    meta = getattr(resposta, "usage", None)
    uso.update(modelo=getattr(resposta, "model", None) or MODELO_OPENAI_PADRAO,
               tokens_entrada=getattr(meta, "prompt_tokens", None), tokens_saida=getattr(meta, "completion_tokens", None))
    return resposta.choices[0].message.content or ""


PROVEDORES = (
    ("Gemini", _cliente_gemini, _via_gemini),
    ("Claude", _cliente_claude, _via_claude),
    ("ChatGPT", _cliente_openai, _via_openai),
)


def gerar_html(pdf_bytes, usuario_id=None):
    """(html_limpo, texto_extraido) ou (None, texto) se nenhuma IA conseguiu.
    Registra o custo de cada chamada que chegou a responder."""
    from app.custo_ia import registrar_chamada_ia

    try:
        texto = extrair_texto(io.BytesIO(pdf_bytes))
    except Exception:
        texto = ""
    for nome, obter_cliente, chamar in PROVEDORES:
        cliente = obter_cliente()
        if cliente is None:
            continue
        uso = {"modelo": None, "tokens_entrada": None, "tokens_saida": None}
        try:
            resultado = sanitizar_html(chamar(cliente, pdf_bytes, texto, uso))
            sucesso = bool(_texto_do_html(resultado).strip())
        except Exception:
            logger.exception("Falha ao gerar a página do PDF de preparo via %s", nome)
            resultado, sucesso = None, False
        if uso["modelo"] is not None:
            registrar_chamada_ia(TIPO_USO, nome, uso["modelo"], uso["tokens_entrada"], uso["tokens_saida"],
                                 sucesso=sucesso, usuario_id=usuario_id)
            db.session.commit()
        if sucesso:
            return resultado, texto
    return None, texto


# ---------- Ciclo da página no PreparoPdfOriginal ----------

def _executar(pdf_id, usuario_id):
    from app.models import PreparoPdfOriginal

    pdf = PreparoPdfOriginal.query.get(pdf_id)
    if not pdf:
        return
    try:
        resultado, texto = gerar_html(pdf.conteudo, usuario_id)
    except Exception:
        logger.exception("Falha ao gerar a página do PDF de preparo %s", pdf_id)
        resultado, texto = None, ""
    pdf = PreparoPdfOriginal.query.get(pdf_id)
    if not pdf or pdf.pagina_status != "gerando":
        return  # PDF trocado/removido no meio do caminho
    if resultado:
        pdf.pagina_html = resultado
        pdf.pagina_avisos = "\n".join(conferir_fidelidade(texto, resultado))
        pdf.pagina_status = "revisar"
    else:
        pdf.pagina_status = "erro"
    pdf.pagina_atualizada_em = datetime.utcnow()
    db.session.commit()


def agendar_geracao(pdf, usuario_id):
    """Marca como "gerando" e gera em segundo plano (na hora, com
    app.config["PAGINA_PDF_SINCRONO"], usado nos testes)."""
    pdf.pagina_status = "gerando"
    pdf.pagina_aprovada_em = None
    pdf.pagina_aprovada_por_id = None
    pdf.pagina_atualizada_em = datetime.utcnow()
    db.session.commit()
    pdf_id = pdf.id
    app = current_app._get_current_object()
    if app.config.get("PAGINA_PDF_SINCRONO"):
        _executar(pdf_id, usuario_id)
        return

    def _rodar():
        with app.app_context():
            try:
                _executar(pdf_id, usuario_id)
            finally:
                db.session.remove()

    threading.Thread(target=_rodar, daemon=True).start()


def texto_do_pdf(pdf):
    """Texto selecionável do PDF guardado (vazio se escaneado)."""
    try:
        return extrair_texto(io.BytesIO(pdf.conteudo))
    except Exception:
        return ""
