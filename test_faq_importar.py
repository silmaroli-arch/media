"""Meu FAQ: importar a base pessoal do médico (planilha), com conferência."""
import io

from app import create_app
from app.faq_importacao import modelo_xlsx
from app.models import Exame, FaqItem, Grupo, Usuario

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False


def checar(nome, condicao):
    print(f"[{'OK' if condicao else 'FALHOU'}] {nome}")
    assert condicao, nome


with app.app_context():
    vitoria_id = Grupo.query.filter_by(nome="Clínica Vitória").first().id
    carlos_id = Usuario.query.filter_by(email="medico@clinicavitoria.com").first().id
    exame = Exame.query.filter_by(grupo_id=vitoria_id, medico_id=carlos_id, nome="Colonoscopia").first()
    exame_id, exame_nome = exame.id, exame.nome

med = app.test_client()
med.post("/login", data={"identificador": "medico@clinicavitoria.com", "senha": "123456"})
med.post("/equipe/clinica", data={"clinica_id": str(vitoria_id)}, follow_redirects=True)

html = med.get("/equipe/faq").get_data(as_text=True)
checar("Botão Importar meu FAQ na lista", "Importar meu FAQ" in html and 'id="modalImportarFaq"' in html)
checar("Modelo para baixar", med.get("/equipe/faq/importar/modelo").status_code == 200)

csv = (f"Exame;Pergunta;Resposta\n"
       f"{exame_nome};Importado pode água?;Pode beber água até [2 horas antes do exame].\n"
       f"{exame_nome};Ligar quando?;Ligue para 11 98888-7777\n"
       f";Sem exame;Falta exame\n"
       f"Exame Inexistente;Outra?;Resposta\n").encode()
r = med.post("/equipe/faq/importar", data={"arquivo": (io.BytesIO(csv), "faq.csv")},
             content_type="multipart/form-data")
html = r.get_data(as_text=True)
checar("Conferência mostra 1 válida de 4", "1 de 4 linha(s)" in html)
checar("Barra telefone, exame inexistente e sem exame",
       "não entra no FAQ" in html and "não encontrado" in html and "Sem exame" in html)
with app.app_context():
    checar("Nada gravado antes de confirmar", FaqItem.query.filter_by(pergunta="Importado pode água?").count() == 0)

r = med.post("/equipe/faq/importar/confirmar", follow_redirects=True)
checar("Confirmar grava", "1 item(ns) importado(s)" in r.get_data(as_text=True))
with app.app_context():
    item = FaqItem.query.filter_by(pergunta="Importado pode água?").first()
    checar("Item no exame certo, com autor", item and item.exame_id == exame_id and item.respondido_por_id == carlos_id)
    checar("Marcador guardado", "{" in item.resposta)

r = med.post("/equipe/faq/importar", data={"arquivo": (io.BytesIO(csv), "faq.csv")},
             content_type="multipart/form-data")
checar("Reimportar: duplicada ignorada", "Já existe" in r.get_data(as_text=True))

r = med.post("/equipe/faq/importar", data={"arquivo": (io.BytesIO(modelo_xlsx()), "m.xlsx")},
             content_type="multipart/form-data")
checar("Lê o .xlsx do modelo", "Posso tomar café antes do exame?" in r.get_data(as_text=True))
r = med.post("/equipe/faq/importar", data={"arquivo": (io.BytesIO(b"x"), "a.txt")},
             content_type="multipart/form-data", follow_redirects=True)
checar("Formato inválido recusado", "Excel (.xlsx) ou CSV" in r.get_data(as_text=True))
print("\nTodos os testes de importar FAQ passaram.")
