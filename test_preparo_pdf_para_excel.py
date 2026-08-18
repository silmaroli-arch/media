"""Testa a importação de modelo de preparo a partir de um PDF, unificada na
mesma tela/rota do Excel (medico.preparo_modelos_importar_xlsx): o PDF é lido
diretamente por IA (app.ia_pdf_preparo), com fallback automático e silencioso
para a extração heurística por regex (app.pdf_preparo) quando a IA não está
configurada — como não há ANTHROPIC_API_KEY no ambiente de teste, este arquivo
sempre exercita o caminho de fallback. Substitui o antigo fluxo em duas
etapas (gerar Excel a partir do PDF, revisar/ajustar no Excel, reimportar)."""
import io

from reportlab.pdfgen import canvas

from app import create_app

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha="123456"):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


login("secretaria@clinicavitoria.com")

# ---------- Menu e tela ----------

r = client.get("/equipe/")
checar("O menu lateral NÃO tem mais o item separado 'Gerar Excel a partir de PDF'",
       "Gerar Excel a partir de PDF" not in r.get_data(as_text=True))

html_novo = client.get("/equipe/preparo-modelos/novo").get_data(as_text=True)
checar("A tela de novo modelo tem o botão unificado de importar Excel/PDF",
       "Importar de um Excel ou PDF" in html_novo)

html_importar = client.get("/equipe/preparo-modelos/importar-xlsx").get_data(as_text=True)
checar("A tela de importar aceita PDF no campo de arquivo", 'accept=".xlsx' in html_importar and ".pdf" in html_importar)

# ---------- Importar um PDF de teste direto (via fallback de regex) ----------

buffer_pdf = io.BytesIO()
c = canvas.Canvas(buffer_pdf)
c.drawString(50, 800, "PREPARO TESTE IMPORTACAO DE PDF")
c.drawString(50, 780, "JEJUM de 10 horas, sendo permitido apenas agua.")
c.drawString(50, 760, "7 dias antes: VARFARINA, XARELTO.")
c.showPage()
c.save()
buffer_pdf.seek(0)

r = client.post(
    "/equipe/preparo-modelos/importar-xlsx",
    data={"arquivo_xlsx": (buffer_pdf, "preparo.pdf")},
    content_type="multipart/form-data",
    follow_redirects=True,
)
checar("Importar o PDF responde 200", r.status_code == 200)
html = r.get_data(as_text=True)
checar("Leva direto pra tela de revisão do formulário (nada de download de Excel)",
       "Revise com cuidado" in html)
checar("O nome sugerido do exame foi extraído do PDF",
       'value="PREPARO TESTE IMPORTACAO DE PDF"' in html)
checar("As instruções extraídas aparecem no formulário", "JEJUM de 10 horas" in html)
checar("Os medicamentos extraídos aparecem no formulário", "VARFARINA" in html and "XARELTO" in html)

# ---------- Erro: sem arquivo ----------

r_sem_arquivo = client.post("/equipe/preparo-modelos/importar-xlsx", data={}, content_type="multipart/form-data")
checar("Sem arquivo, mostra aviso e não quebra",
       "Selecione um arquivo Excel" in r_sem_arquivo.get_data(as_text=True))

# ---------- PDF ilegível cai no aviso amigável (sem IA e sem texto extraível) ----------

r_invalido = client.post(
    "/equipe/preparo-modelos/importar-xlsx",
    data={"arquivo_xlsx": (io.BytesIO(b"isso nao e um pdf de verdade"), "invalido.pdf")},
    content_type="multipart/form-data",
)
checar(
    "PDF inválido mostra aviso amigável, não quebra",
    "Não foi possível ler esse PDF" in r_invalido.get_data(as_text=True),
)

client.get("/logout")
print("\nTodos os testes de importação de PDF de preparo passaram.")
