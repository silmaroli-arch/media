"""Atualizar um preparo com um PDF novo (pedido do Silvan, 2026-11-06)."""
import io
from unittest.mock import patch

from reportlab.pdfgen import canvas

from app import create_app, db
from app.models import Agendamento, PreparoModelo, PreparoPdfAnterior, PreparoPdfOriginal, PreparoPdfPagina

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False


def checar(nome, condicao):
    print(f"[{'OK' if condicao else 'FALHOU'}] {nome}")
    assert condicao, nome


def pdf_real(texto, paginas=1):
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for n in range(paginas):
        c.drawString(100, 750, f"{texto} - pagina {n + 1}")
        c.showPage()
    c.save()
    return buf.getvalue()


PDF_A, PDF_B = pdf_real("Preparo antigo", 2), pdf_real("Preparo novo", 3)

medico = app.test_client()
medico.post("/login", data={"identificador": "medica2@clinicavitoria.com", "senha": "123456"})
with app.app_context():
    ag = next(a for a in Agendamento.query.order_by(Agendamento.id).all()
              if a.exame and a.exame.preparo_modelo and a.exame.preparo_modelo.exames and len(a.exame.preparo_modelo.exames) == 1
              and a.medico and a.medico.email == "medica2@clinicavitoria.com")
    modelo = ag.exame.preparo_modelo
    modelo_id, nome, grupo_id, tipo_id = modelo.id, modelo.nome, ag.exame.grupo_id, modelo.tipo_exame_id
    instrucoes_originais = modelo.instrucoes
    ag_id = ag.id
    token = ag.obter_token_preparo_publico()
    db.session.add(PreparoPdfOriginal(preparo_modelo_id=modelo_id, nome_arquivo="antigo.pdf", tamanho=len(PDF_A), conteudo=PDF_A))
    db.session.commit()
medico.post("/equipe/clinica", data={"clinica_id": str(grupo_id)}, follow_redirects=True)
dados = {"nome": nome, "tipo_exame_id": str(tipo_id or ""), "instrucoes": instrucoes_originais}

html = medico.get(f"/equipe/preparo-modelos/{modelo_id}/editar").get_data(as_text=True)
checar("Editar tem o botão 'Importar preparo atualizado (PDF)' e o modal manda o id do modelo",
       "Importar preparo atualizado (PDF)" in html and f'name="modelo_id" value="{modelo_id}"' in html)

# ---------- importa o PDF atualizado (extração simulada) ----------
sugestao = {"nome_sugerido": nome, "instrucoes": "Jejum de 12 horas e sem café.",
            "cortes": [{"descricao": "Sólidos", "horas_antes": 12}], "alimentos": [{"nome": "Arroz", "permitido": False, "horas_antes": 24, "dias_antes": None}],
            "medicamentos": [], "medicamentos_mantidos": [], "informacoes_gerais": [], "exames_anteriores": []}
with patch("app.routes_medico._extrair_via_cadeia_de_ia", return_value=(sugestao, "Gemini")):
    r = medico.post("/equipe/preparo-modelos/importar-xlsx", content_type="multipart/form-data",
                    data={"modelo_id": str(modelo_id), "arquivo_xlsx": (io.BytesIO(PDF_B), "novo.pdf")})
html = r.get_data(as_text=True)
checar("Revisão: banner de atualização, conteúdo novo preenchido e PDF novo no lugar do atual",
       "Atualizando o preparo - revise e salve" in html and "Jejum de 12 horas e sem café." in html
       and "vai substituir o PDF atual" in html and "antigo.pdf" in html and 'name="atualizar_pdf"' in html)
checar("Revisão mostra o que muda (instruções e itens novos)",
       "Instruções gerais: o texto muda" in html and "Cortes de alimentação:" in html and "Alimentos: 1 novo(s)" in html)
with app.app_context():
    checar("Nada foi salvo ainda: o PDF atual continua o antigo e as instruções também",
           PreparoModelo.query.get(modelo_id).pdf_original.nome_arquivo == "antigo.pdf"
           and PreparoModelo.query.get(modelo_id).instrucoes == instrucoes_originais and PreparoPdfAnterior.query.count() == 0)

# ---------- salva: substitui o PDF e guarda o anterior ----------
r = medico.post(f"/equipe/preparo-modelos/{modelo_id}/editar", follow_redirects=True,
                data={**dados, "instrucoes": "Jejum de 12 horas e sem café.", "usar_pdf_importado": "1", "atualizar_pdf": "1"})
with app.app_context():
    m = PreparoModelo.query.get(modelo_id)
    checar("Ao salvar: instruções novas e PDF novo", m.instrucoes.startswith("Jejum de 12") and m.pdf_original.nome_arquivo == "novo.pdf"
           and bytes(m.pdf_original.conteudo) == PDF_B)
    anteriores = PreparoPdfAnterior.query.filter_by(preparo_modelo_id=modelo_id).all()
    checar("O PDF antigo ficou guardado como versão anterior", len(anteriores) == 1 and anteriores[0].nome_arquivo == "antigo.pdf"
           and bytes(anteriores[0].conteudo) == PDF_A and anteriores[0].substituido_por_id is not None)
    anterior_id = anteriores[0].id
    checar("As imagens das páginas foram refeitas com o PDF novo (3 páginas)",
           PreparoPdfPagina.query.filter_by(pdf_id=m.pdf_original.id).count() == 3)
    checar("O exame e o agendamento continuam ligados ao mesmo preparo", Agendamento.query.get(ag_id).exame.preparo_modelo_id == modelo_id)

html = medico.get(f"/equipe/preparo-modelos/{modelo_id}/editar").get_data(as_text=True)
checar("Editar lista a versão anterior com data", "Versões anteriores do PDF (1)" in html and "antigo.pdf" in html and "substituído em" in html)
r = medico.get(f"/equipe/preparo-modelos/{modelo_id}/pdf-anterior/{anterior_id}")
checar("Dá para baixar a versão anterior", r.status_code == 200 and r.data == PDF_A and r.headers["Content-Disposition"].startswith("attachment"))
paciente = app.test_client()
r = paciente.get(f"/paciente/preparo/{token}/original")
checar("O paciente que abre o link passa a ver o PDF novo", r.status_code == 200 and r.data == PDF_B)

# ---------- um PDF importado à toa não substitui nada numa edição comum ----------
with patch("app.routes_medico._extrair_via_cadeia_de_ia", return_value=(sugestao, "Gemini")):
    medico.post("/equipe/preparo-modelos/importar-xlsx", content_type="multipart/form-data",
                data={"arquivo_xlsx": (io.BytesIO(PDF_A), "outro.pdf")})  # importação de modelo NOVO, abandonada
medico.post(f"/equipe/preparo-modelos/{modelo_id}/editar", data={**dados, "instrucoes": "Só um ajuste de texto."}, follow_redirects=True)
with app.app_context():
    m = PreparoModelo.query.get(modelo_id)
    checar("Editar sem atualizar: PDF continua o mesmo e nenhuma versão nova é guardada",
           m.pdf_original.nome_arquivo == "novo.pdf" and PreparoPdfAnterior.query.filter_by(preparo_modelo_id=modelo_id).count() == 1)

# ---------- outra clínica não importa para este preparo ----------
outro = app.test_client()
outro.post("/login", data={"identificador": "secretaria@gruposaude.com", "senha": "123456"})
with patch("app.routes_medico._extrair_via_cadeia_de_ia", return_value=(sugestao, "Gemini")):
    r = outro.post("/equipe/preparo-modelos/importar-xlsx", content_type="multipart/form-data",
                   data={"modelo_id": str(modelo_id), "arquivo_xlsx": (io.BytesIO(PDF_B), "x.pdf")})
checar("Quem não é da clínica não atualiza o preparo", r.status_code in (302, 403, 404))
print("TUDO OK")
