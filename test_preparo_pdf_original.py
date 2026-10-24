"""PDF original do preparo (pedido do Silvan, 2026-10-24): o médico anexa o
PDF do preparo no modelo (o PDF importado já vem marcado para isso) e o
paciente abre esse arquivo primeiro pelo link do WhatsApp, com a linha do
tempo como segunda opção."""
import io

from app import create_app, db
from app.models import Agendamento, PreparoModelo, PreparoPdfOriginal

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False
app.config["PAGINA_PDF_SINCRONO"] = True

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


medico = app.test_client()
medico.post("/login", data={"identificador": "medico@clinicavitoria.com", "senha": "123456"})

with app.app_context():
    agendamento = next(a for a in Agendamento.query.order_by(Agendamento.id).all()
                       if a.exame and a.exame.preparo_modelo and a.medico and a.medico.email == "medico@clinicavitoria.com")
    modelo = agendamento.exame.preparo_modelo
    grupo_id = agendamento.grupo_id or agendamento.exame.grupo_id
    modelo_id, nome, tipo_id = modelo.id, modelo.nome, modelo.tipo_exame_id
    token = agendamento.obter_token_preparo_publico()
    db.session.commit()

medico.post("/equipe/clinica", data={"clinica_id": str(grupo_id)}, follow_redirects=True)

# Sem PDF: a página do link continua como antes.
html = app.test_client().get(f"/paciente/preparo/{token}").get_data(as_text=True)
checar("Sem PDF original: página só com a linha do tempo", "Preparo do médico" not in html and "Baixar PDF" in html)
checar("... e o endereço do original dá 404", app.test_client().get(f"/paciente/preparo/{token}/original").status_code == 404)

html = medico.get(f"/equipe/preparo-modelos/{modelo_id}/editar").get_data(as_text=True)
checar("Formulário tem o campo do preparo original e aceita arquivo",
       'name="pdf_original"' in html and 'enctype="multipart/form-data"' in html)

dados = {"nome": nome, "especialidade": "Gastroenterologia", "tipo_exame_id": str(tipo_id or "")}
r = medico.post(f"/equipe/preparo-modelos/{modelo_id}/editar", content_type="multipart/form-data", follow_redirects=True,
                data={**dados, "pdf_original": (io.BytesIO(b"nao e pdf"), "preparo.pdf")})
with app.app_context():
    checar("Arquivo que não é PDF: avisa e não guarda",
           "precisa ser um arquivo PDF" in r.get_data(as_text=True) and PreparoPdfOriginal.query.count() == 0)

medico.post(f"/equipe/preparo-modelos/{modelo_id}/editar", content_type="multipart/form-data", follow_redirects=True,
            data={**dados, "pdf_original": (io.BytesIO(PDF), "Preparo Colonoscopia.pdf")})
with app.app_context():
    pdf = PreparoModelo.query.get(modelo_id).pdf_original
    checar("PDF anexado no modelo", pdf is not None and pdf.nome_arquivo == "Preparo_Colonoscopia.pdf" and pdf.tamanho == len(PDF))
r = medico.get(f"/equipe/preparo-modelos/{modelo_id}/pdf-original")
checar("Equipe abre o PDF anexado", r.status_code == 200 and r.data == PDF and r.mimetype == "application/pdf")

paciente = app.test_client()
html = paciente.get(f"/paciente/preparo/{token}").get_data(as_text=True)
checar("Link do WhatsApp: 'Preparo do médico' vem antes da linha do tempo",
       "Abrir o preparo (PDF)" in html and html.index("Preparo do médico") < html.index("Linha do tempo do preparo"))
checar("... e a linha do tempo continua, com o PDF dela", "Baixar a linha do tempo (PDF)" in html)
checar("'Baixar PDF' continua no topo e baixa o PDF do médico",
       f'href="/paciente/preparo/{token}/original?baixar=1">\n      Baixar PDF' in html)
r = paciente.get(f"/paciente/preparo/{token}/original")
checar("Paciente abre o PDF original sem login", r.status_code == 200 and r.data == PDF
       and r.headers["Content-Disposition"].startswith("inline"))
r = paciente.get(f"/paciente/preparo/{token}/original?baixar=1")
checar("... e pode baixar", r.headers["Content-Disposition"].startswith("attachment"))
checar("Token errado: 404", paciente.get("/paciente/preparo/xyz/original").status_code == 404)

medico.post(f"/equipe/preparo-modelos/{modelo_id}/editar", data={**dados, "remover_pdf_original": "1"}, follow_redirects=True)
with app.app_context():
    checar("Remover o PDF", PreparoModelo.query.get(modelo_id).pdf_original is None and PreparoPdfOriginal.query.count() == 0)

# Importar um PDF para criar o modelo: o mesmo arquivo vira o preparo original.
r = medico.post("/equipe/preparo-modelos/importar-xlsx", content_type="multipart/form-data", data={
    "arquivo_xlsx": (io.BytesIO(PDF), "importado.pdf"), "texto_extraido_cliente": "Jejum de 8 horas antes do exame."})
html = r.get_data(as_text=True)
checar("Revisão da importação já marca 'Usar o PDF importado'", 'name="usar_pdf_importado"' in html and "importado.pdf" in html)
checar("... e a equipe consegue abrir o PDF importado", medico.get("/equipe/preparo-modelos/pdf-importado").data == PDF)
medico.post("/equipe/preparo-modelos/novo", data={
    "nome": "Preparo com PDF importado", "instrucoes": "Jejum.", "tipo_exame_id": "outro",
    "tipo_exame_outro": "Exame do PDF", "especialidade": "Gastroenterologia", "usar_pdf_importado": "1"},
    follow_redirects=True)
with app.app_context():
    novo = PreparoModelo.query.filter_by(nome="Preparo com PDF importado").first()
    checar("Salvo o modelo, o PDF importado vira o preparo original",
           novo and novo.pdf_original and novo.pdf_original.nome_arquivo == "importado.pdf")
    checar("... sem sobrar PDF pendente", PreparoPdfOriginal.query.filter_by(preparo_modelo_id=None).count() == 0)
    novo_id = novo.id

with app.app_context():
    novo = PreparoModelo.query.get(novo_id)
    for exame in list(novo.exames):
        db.session.delete(exame)
    db.session.delete(novo)
    db.session.commit()
    checar("Remover o modelo apaga o PDF dele", PreparoPdfOriginal.query.count() == 0)

print("\nTodos os testes do PDF original do preparo passaram.")
