"""Versão em página (HTML) do PDF original do preparo (pedido do Silvan,
2026-10-24, "opção 3"): a IA transcreve o PDF, o médico revisa ao lado do
PDF e aprova; só então o paciente vê a página no link do WhatsApp."""
import io
from unittest.mock import patch

from app import create_app, db
from app.ia_pdf_html import conferir_fidelidade, sanitizar_html
from app.models import Agendamento, ChamadaIA, PreparoModelo

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False
app.config["PAGINA_PDF_SINCRONO"] = True

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


checar("Limpeza: tira script, estilo, atributos e link; troca h1/b",
       sanitizar_html('```html\n<h1 style="x">Preparo</h1><script>alert(1)</script><p onclick="x">Tome <b>4 L</b> <a href="http://x">aqui</a></p>\n```')
       == "<h2>Preparo</h2><p>Tome <strong>4 L</strong> aqui</p>")
checar("Limpeza: tabela mantém colspan", sanitizar_html('<table><tr><td colspan="2" class="a">x</td></tr></table>')
       == '<table><tr><td colspan="2">x</td></tr></table>')
checar("Conferência acha número e palavra do PDF que sumiram",
       conferir_fidelidade("Suspender Ozempic 14 dias antes. Tomar 2 litros de Manitol.",
                           "<p>Suspender Ozempic 14 dias antes.</p><p>Tomar litros.</p>") == ["2", "manitol"])
checar("Conferência junta palavra hifenizada do PDF", conferir_fidelidade("prepa-\nração", "<p>preparação</p>") == [])
checar("PDF sem texto (escaneado): nada a conferir", conferir_fidelidade("", "<p>x</p>") == [])

GERADO = "<h2>Preparo</h2><p>Suspender Ozempic 14 dias antes.</p><script>x</script>"


def fake(cliente, pdf_bytes, texto, uso):
    uso.update(modelo="fake-1", tokens_entrada=100, tokens_saida=50)
    return GERADO


medico = app.test_client()
medico.post("/login", data={"identificador": "medico@clinicavitoria.com", "senha": "123456"})
with app.app_context():
    agendamento = next(a for a in Agendamento.query.order_by(Agendamento.id).all()
                       if a.exame and a.exame.preparo_modelo and a.medico and a.medico.email == "medico@clinicavitoria.com")
    modelo = agendamento.exame.preparo_modelo
    modelo_id, nome, tipo_id = modelo.id, modelo.nome, modelo.tipo_exame_id
    grupo_id = agendamento.grupo_id or agendamento.exame.grupo_id
    token = agendamento.obter_token_preparo_publico()
    db.session.commit()
medico.post("/equipe/clinica", data={"clinica_id": str(grupo_id)}, follow_redirects=True)
dados = {"nome": nome, "especialidade": "Gastroenterologia", "tipo_exame_id": str(tipo_id or "")}
url_pagina = f"/equipe/preparo-modelos/{modelo_id}/pdf-original/pagina"

with patch("app.ia_pdf_html.PROVEDORES", (("Fake", lambda: object(), fake),)):
    r = medico.post(f"/equipe/preparo-modelos/{modelo_id}/editar", content_type="multipart/form-data",
                    follow_redirects=True, data={**dados, "pdf_original": (io.BytesIO(PDF), "preparo.pdf")})
checar("Anexar o PDF já gera a página e avisa onde revisar", "Revise e aprove aqui" in r.get_data(as_text=True))
with app.app_context():
    pdf = PreparoModelo.query.get(modelo_id).pdf_original
    checar("Página gerada, limpa, aguardando aprovação",
           pdf.pagina_status == "revisar" and "<script>" not in pdf.pagina_html and "Ozempic" in pdf.pagina_html)
    checar("Custo da chamada registrado", ChamadaIA.query.filter_by(tipo_uso="pagina_pdf_preparo").count() == 1)

paciente = app.test_client()
html = paciente.get(f"/paciente/preparo/{token}").get_data(as_text=True)
checar("Antes de aprovar, o paciente vê só o PDF", "Suspender Ozempic 14 dias antes." not in html and "Abrir o preparo (PDF)" in html)

html = medico.get(url_pagina).get_data(as_text=True)
checar("Tela de revisão: página editável ao lado do PDF",
       'contenteditable="true"' in html and "Aprovar e publicar para o paciente" in html and 'id="pdf-ao-lado"' in html and "pdf.min.js" in html)
checar("Formulário do modelo mostra 'aguardando sua aprovação'",
       "aguardando sua aprovação" in medico.get(f"/equipe/preparo-modelos/{modelo_id}/editar").get_data(as_text=True))

editado = '<h2>Preparo</h2><p>Suspender Ozempic 14 dias antes do exame.</p><img src=x onerror="alert(1)">'
medico.post(url_pagina, data={"acao": "aprovar", "pagina_html": editado}, follow_redirects=True)
with app.app_context():
    pdf = PreparoModelo.query.get(modelo_id).pdf_original
    checar("Aprovar grava a correção do médico, limpa", pdf.pagina_status == "aprovada"
           and pdf.pagina_html == "<h2>Preparo</h2><p>Suspender Ozempic 14 dias antes do exame.</p>")
html = paciente.get(f"/paciente/preparo/{token}").get_data(as_text=True)
checar("Aprovada: o paciente lê a página, com o PDF original como opção",
       "Suspender Ozempic 14 dias antes do exame." in html and "Ver o PDF original" in html
       and "Abrir o preparo (PDF)" not in html and "onerror" not in html)

medico.post(url_pagina, data={"acao": "tirar"}, follow_redirects=True)
html = paciente.get(f"/paciente/preparo/{token}").get_data(as_text=True)
checar("Tirar do ar: o paciente volta a ver o PDF", "Abrir o preparo (PDF)" in html and "do exame.</p>" not in html)


def falha(cliente, pdf_bytes, texto, uso):
    raise RuntimeError("IA fora")


with patch("app.ia_pdf_html.PROVEDORES", (("Fake", lambda: object(), falha),)):
    medico.post(url_pagina, data={"acao": "gerar"}, follow_redirects=True)
with app.app_context():
    checar("IA falhou: status de erro, dá para tentar de novo",
           PreparoModelo.query.get(modelo_id).pdf_original.situacao_pagina == "erro")
checar("... e a tela oferece gerar de novo", "Gerar a página com IA" in medico.get(url_pagina).get_data(as_text=True))

print("\nTodos os testes da versão em página do PDF passaram.")
