"""Meu FAQ: "Adicionar manualmente" numa janela (pedido do Silvan,
2026-10-21). Salvar volta para a lista; com erro, a lista volta com a
janela aberta, o motivo e os campos preenchidos."""
from app import create_app
from app.models import Exame, FaqItem, Grupo, Usuario

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


with app.app_context():
    vitoria_id = Grupo.query.filter_by(nome="Clínica Vitória").first().id
    carlos_id = Usuario.query.filter_by(email="medico@clinicavitoria.com").first().id
    exame_id = Exame.query.filter_by(grupo_id=vitoria_id, medico_id=carlos_id, nome="Colonoscopia").first().id

med = app.test_client()
med.post("/login", data={"identificador": "medico@clinicavitoria.com", "senha": "123456"})
med.post("/equipe/clinica", data={"clinica_id": str(vitoria_id)}, follow_redirects=True)

html = med.get("/equipe/faq").get_data(as_text=True)
checar("'Adicionar manualmente' abre a janela", 'data-bs-target="#modalNovoFaq"' in html and 'id="modalNovoFaq"' in html)
checar("A janela lista os exames do médico", f'value="{exame_id}"' in html)

r = med.post("/equipe/faq/novo", data={"origem": "lista", "exame_id": str(exame_id),
                                       "pergunta": "Posso tomar café?", "resposta": ""})
checar("Erro na janela volta com ela aberta", r.status_code == 302 and "novo=1" in r.headers["Location"])
html = med.get(r.headers["Location"]).get_data(as_text=True)
checar("... com o motivo e a pergunta preenchida",
       "Pergunta e resposta são obrigatórias." in html and 'value="Posso tomar café?"' in html
       and "new bootstrap.Modal" in html)

r = med.post("/equipe/faq/novo", data={"origem": "lista", "exame_id": str(exame_id),
                                       "pergunta": "Posso tomar café?", "resposta": "Só café preto, sem leite."},
             follow_redirects=True)
html = r.get_data(as_text=True)
checar("Salvar pela janela volta para a lista com o item", "Item adicionado" in html and "Só café preto, sem leite." in html)
with app.app_context():
    checar("Item gravado no exame certo", FaqItem.query.filter_by(pergunta="Posso tomar café?", exame_id=exame_id).count() == 1)

print("\nTodos os testes do FAQ em janela passaram.")
