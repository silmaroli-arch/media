"""PDF do preparo como imagens (pedido do Silvan, 2026-10-26): cada página do
PDF do médico vira imagem na página do link do WhatsApp - sem IA e sem
aprovação."""
import io

from reportlab.pdfgen import canvas

from app import create_app, db
from app.models import Agendamento, PreparoPdfOriginal, PreparoPdfPagina
from app.preparo_pdf_paginas import garantir_paginas

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False


def checar(nome, condicao):
    print(f"[{'OK' if condicao else 'FALHOU'}] {nome}")
    assert condicao, nome


def pdf_real(paginas=3):
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for n in range(paginas):
        c.drawString(100, 750, f"Preparo do médico - página {n + 1}")
        c.showPage()
    c.save()
    return buf.getvalue()


with app.app_context():
    agendamento = next(a for a in Agendamento.query.order_by(Agendamento.id).all()
                       if a.exame and a.exame.preparo_modelo)
    modelo = agendamento.exame.preparo_modelo
    token = agendamento.obter_token_preparo_publico()
    modelo_id = modelo.id
    conteudo = pdf_real(3)
    pdf = PreparoPdfOriginal(preparo_modelo_id=modelo.id, nome_arquivo="preparo.pdf", tamanho=len(conteudo), conteudo=conteudo)
    db.session.add(pdf)
    db.session.commit()
    pdf_id = pdf.id

cliente = app.test_client()
# PDF anexado sem imagens: gera na hora da primeira visita.
r = cliente.get(f"/paciente/preparo/{token}/pagina/1.jpg")
checar("Página 1 do link devolve JPEG", r.status_code == 200 and r.data[:3] == b"\xff\xd8\xff")
checar("Página 3 existe", cliente.get(f"/paciente/preparo/{token}/pagina/3.jpg").status_code == 200)
checar("Página 4 dá 404", cliente.get(f"/paciente/preparo/{token}/pagina/4.jpg").status_code == 404)
html = cliente.get(f"/paciente/preparo/{token}").get_data(as_text=True)
checar("Página do link mostra as imagens e a aba da linha do tempo",
       f"/pagina/1.jpg" in html and "Linha do tempo" in html and "Preparo do médico" in html)

with app.app_context():
    checar("3 páginas gravadas, status 'imagens'",
           PreparoPdfPagina.query.filter_by(pdf_id=pdf_id).count() == 3
           and db.session.get(PreparoPdfOriginal, pdf_id).pagina_status == "imagens")

# PDF corrompido: não quebra, fica marcado e não é tentado de novo.
with app.app_context():
    ruim = db.session.get(PreparoPdfOriginal, pdf_id)
    ruim.conteudo = b"lixo"
    ruim.pagina_status = None
    PreparoPdfPagina.query.filter_by(pdf_id=pdf_id).delete()
    db.session.commit()
    checar("PDF corrompido: sem páginas", garantir_paginas(ruim) == [])
    checar("... marcado como imagens_erro", ruim.pagina_status == "imagens_erro")
    db.session.delete(ruim)
    db.session.commit()
    checar("Apagar o PDF apaga as páginas", PreparoPdfPagina.query.filter_by(pdf_id=pdf_id).count() == 0)
print("TUDO OK")
