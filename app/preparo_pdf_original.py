"""PDF original do preparo (pedido do Silvan, 2026-10-24): o médico anexa o
PDF do preparo no modelo - o mesmo que ele importa para preencher os campos
passa a ficar guardado - e o paciente abre esse arquivo primeiro pelo link
do WhatsApp; a linha do tempo fica como segunda opção.

O PDF importado ainda não tem modelo (a tela de revisão vem antes de
salvar): fica "pendente" (sem preparo_modelo_id), com o id na sessão, e é
ligado ao modelo quando ele é salvo."""
from datetime import datetime, timedelta

from flask import session
from flask_login import current_user
from werkzeug.utils import secure_filename

from app.extensions import db

TAMANHO_MAXIMO = 10 * 1024 * 1024
CHAVE_SESSAO = "preparo_pdf_pendente_id"
VALIDADE_PENDENTE = timedelta(days=1)


def validar(conteudo, nome_arquivo):
    """Mensagem de erro, ou None se o arquivo serve."""
    if not (nome_arquivo or "").lower().endswith(".pdf") or not conteudo.startswith(b"%PDF"):
        return "O preparo original precisa ser um arquivo PDF."
    if len(conteudo) > TAMANHO_MAXIMO:
        return "O PDF do preparo pode ter no máximo 10 MB."
    return None


def _nome(nome_arquivo):
    return (secure_filename(nome_arquivo or "") or "preparo.pdf")[:255]


def guardar_pendente(conteudo, nome_arquivo):
    """Guarda o PDF que acabou de ser importado, para virar o preparo
    original quando o modelo for salvo. Não faz nada se o arquivo não serve
    (a importação segue normalmente). Apaga pendentes esquecidos."""
    from app.models import PreparoPdfOriginal

    if validar(conteudo, nome_arquivo):
        return None
    PreparoPdfOriginal.query.filter(
        PreparoPdfOriginal.preparo_modelo_id.is_(None),
        PreparoPdfOriginal.enviado_em < datetime.utcnow() - VALIDADE_PENDENTE,
    ).delete(synchronize_session=False)
    anterior = pendente_da_sessao()
    if anterior:
        db.session.delete(anterior)
    pdf = PreparoPdfOriginal(
        nome_arquivo=_nome(nome_arquivo), tamanho=len(conteudo), conteudo=conteudo,
        enviado_por_id=getattr(current_user, "id", None),
    )
    db.session.add(pdf)
    db.session.commit()
    session[CHAVE_SESSAO] = pdf.id
    return pdf


def pendente_da_sessao():
    """O PDF importado por este usuário e ainda não salvo num modelo."""
    from app.models import PreparoPdfOriginal

    pdf_id = session.get(CHAVE_SESSAO)
    if not pdf_id:
        return None
    pdf = PreparoPdfOriginal.query.get(pdf_id)
    if not pdf or pdf.preparo_modelo_id is not None:
        session.pop(CHAVE_SESSAO, None)
        return None
    return pdf


def aplicar_no_modelo(modelo, form, arquivos):
    """Depois de salvar o modelo: remove, troca ou liga o PDF importado,
    conforme o formulário. Devolve uma mensagem de erro (o modelo já foi
    salvo; só o PDF fica como estava) ou None."""
    from app.models import PreparoPdfOriginal

    arquivo = arquivos.get("pdf_original")
    pendente = pendente_da_sessao()
    erro = None
    novo = None
    if arquivo and arquivo.filename:
        conteudo = arquivo.read()
        erro = validar(conteudo, arquivo.filename)
        if not erro:
            novo = PreparoPdfOriginal(
                nome_arquivo=_nome(arquivo.filename), tamanho=len(conteudo), conteudo=conteudo,
                enviado_por_id=current_user.id,
            )
    elif pendente and form.get("usar_pdf_importado"):
        novo = pendente
    if pendente and novo is not pendente:
        db.session.delete(pendente)
    session.pop(CHAVE_SESSAO, None)

    if novo is not None or form.get("remover_pdf_original"):
        if modelo.pdf_original is not None:
            db.session.delete(modelo.pdf_original)
            db.session.flush()
        if novo is not None:
            novo.preparo_modelo_id = modelo.id
            db.session.add(novo)
    db.session.commit()
    return erro


def resposta_pdf_original(pdf, baixar=False):
    """O PDF para abrir no navegador (ou baixar)."""
    from flask import Response

    disposicao = "attachment" if baixar else "inline"
    return Response(
        pdf.conteudo, mimetype="application/pdf",
        headers={"Content-Disposition": f'{disposicao}; filename="{pdf.nome_arquivo}"', "Cache-Control": "private, max-age=300"},
    )
