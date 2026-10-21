"""Filtros nas telas de FAQ (pedido do Silvan, 2026-10-21):
- FAQ do MedIA (médico): o filtro só lista exames com itens; busca por texto livre.
- Meu FAQ: filtro pelos exames do médico (só os que têm item) e busca por texto livre."""
from app import create_app, db
from app.base_conhecimento_padrao import semear_base_conhecimento
from app.models import BaseConhecimentoItem, Exame, FaqItem, Grupo, TipoExame, Usuario
from app.routes_medico import _contem_busca
from app.tipos_exame_padrao import semear_tipos_exame

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


checar("Busca ignora acento, maiúscula e ordem das palavras", _contem_busca("cafe POSSO", "Posso tomar café?", ""))
checar("... e exige todas as palavras", not _contem_busca("café dirigir", "Posso tomar café?", ""))
checar("Busca vazia aceita tudo", _contem_busca("", "x"))

with app.app_context():
    semear_tipos_exame(db, TipoExame)
    semear_base_conhecimento(db)
    fernanda = Usuario.query.filter_by(email="medica2@clinicavitoria.com").first()
    fernanda.especialidade = "Gastroenterologia"
    vitoria = Grupo.query.filter_by(nome="Clínica Vitória").first()
    hemograma = Exame.query.filter_by(nome="Hemograma completo").first()
    db.session.add_all([
        FaqItem(grupo_id=vitoria.id, exame_id=hemograma.id, pergunta="Posso tomar café?", resposta="Só água.", criado_por="t"),
        FaqItem(grupo_id=vitoria.id, exame_id=hemograma.id, pergunta="Que horas chego?",
                resposta="Chegue às {hora_exame}.", criado_por="t"),
    ])
    db.session.commit()
    vitoria_id, hemograma_id = vitoria.id, hemograma.id
    from app.routes_medico import _tipos_exame_da_especialidade  # noqa: F401
    colono = TipoExame.query.filter_by(nome="Colonoscopia").first()
    colono_id = colono.id
    tipos_com_item = {t for (t,) in db.session.query(BaseConhecimentoItem.tipo_exame_id).distinct()}

c = app.test_client()
c.post("/login", data={"identificador": "medica2@clinicavitoria.com", "senha": "123456"})
c.post("/equipe/clinica", data={"clinica_id": str(vitoria_id)}, follow_redirects=True)

html = c.get("/equipe/base-compartilhada").get_data(as_text=True)
filtro = html[html.find('name="tipo"'):html.find("</select>", html.find('name="tipo"'))]
with app.app_context():
    sem_item = [t for t in TipoExame.query.all() if t.id not in tipos_com_item]
checar("FAQ do MedIA: o filtro lista exame com itens (Colonoscopia)", f'value="{colono_id}"' in filtro)
checar("... e não lista exame sem itens", all(f'value="{t.id}"' not in filtro for t in sem_item))
html = c.get(f"/equipe/base-compartilhada?tipo={colono_id}&q=dirigir").get_data(as_text=True)
checar("FAQ do MedIA: busca por texto livre filtra os itens",
       "Posso dirigir depois da colonoscopia?" in html and "A colonoscopia dói?" not in html)

html = c.get("/equipe/faq").get_data(as_text=True)
checar("Meu FAQ: filtro por exame com os exames que têm item", f'<option value="{hemograma_id}"' in html and 'name="q"' in html)
html = c.get(f"/equipe/faq?exame={hemograma_id}&q=cafe").get_data(as_text=True)
checar("Meu FAQ: busca 'cafe' acha 'café' e esconde o resto", "Posso tomar café?" in html and "Que horas chego?" not in html)
html = c.get("/equipe/faq?q=horario do exame").get_data(as_text=True)
checar("Meu FAQ: busca também no texto legível dos marcadores", "Que horas chego?" in html)
html = c.get("/equipe/faq?q=nada-disso").get_data(as_text=True)
checar("Meu FAQ: sem resultado mostra 'Nenhum item encontrado'", "Nenhum item encontrado." in html)

print("\nTodos os testes de filtros do FAQ passaram.")
