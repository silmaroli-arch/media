"""Páginas do PDF original do preparo como imagem (pedido do Silvan,
2026-10-26): em vez de a IA transcrever o PDF para texto (e o médico ter de
revisar e aprovar), cada página vira uma imagem e o paciente vê o documento do
médico exatamente como ele escreveu. Sem IA, sem custo, sem etapa de
aprovação, e funciona com PDF escaneado.

Usa o pypdfium2 (PDFium, licença BSD/Apache) - não precisa de programa externo
no servidor. As imagens ficam no banco (o disco do Render não é permanente).
`PreparoPdfOriginal.pagina_status` (coluna que já existia) guarda "imagens"
quando deu certo e "imagens_erro" quando o PDF não pôde ser lido - assim um PDF
corrompido não é tentado de novo a cada visita."""
import io
import logging

from app.extensions import db

logger = logging.getLogger(__name__)

MAXIMO_PAGINAS = 30
ESCALA = 2.0          # 72 dpi x 2: ~1190 px de largura numa página A4
QUALIDADE_JPEG = 82
STATUS_OK = "imagens"
STATUS_ERRO = "imagens_erro"  # (valores antigos da versão em texto da IA são ignorados)


def gerar_paginas(pdf):
    """Gera (de novo) as imagens das páginas de um `PreparoPdfOriginal` e
    grava no banco. Devolve a quantidade de páginas, ou 0 se o PDF não pôde
    ser lido (nunca levanta exceção)."""
    from app.models import PreparoPdfPagina

    try:
        import pypdfium2 as pdfium

        documento = pdfium.PdfDocument(bytes(pdf.conteudo))
        paginas = []
        for indice in range(min(len(documento), MAXIMO_PAGINAS)):
            imagem = documento[indice].render(scale=ESCALA).to_pil().convert("RGB")
            saida = io.BytesIO()
            imagem.save(saida, "JPEG", quality=QUALIDADE_JPEG, optimize=True)
            paginas.append((indice + 1, imagem.width, imagem.height, saida.getvalue()))
        if not paginas:
            raise ValueError("PDF sem páginas")
    except Exception:
        logger.exception("Não foi possível transformar o PDF do preparo %s em imagens", getattr(pdf, "id", None))
        db.session.rollback()
        pdf.pagina_status = STATUS_ERRO
        db.session.commit()
        return 0

    PreparoPdfPagina.query.filter_by(pdf_id=pdf.id).delete(synchronize_session=False)
    for numero, largura, altura, dados in paginas:
        db.session.add(PreparoPdfPagina(pdf_id=pdf.id, numero=numero, largura=largura, altura=altura, imagem=dados))
    pdf.pagina_status = STATUS_OK
    db.session.commit()
    return len(paginas)


def garantir_paginas(pdf):
    """Páginas prontas deste PDF: gera na hora se ainda não existem (PDFs
    anexados antes desta função) e se não falhou antes."""
    if pdf is None:
        return []
    if pdf.pagina_status == STATUS_ERRO:
        return []
    if not pdf.paginas:
        gerar_paginas(pdf)
    return list(pdf.paginas)
