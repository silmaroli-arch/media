"""Testa a reformulação da tela de modelo de preparo:

- Os botões "Importar de um PDF"/"Importar de um Excel" saíram da LISTA e
  foram pro FORMULÁRIO de novo modelo, abrindo um popup (modal) pra
  escolher o arquivo e extrair os dados.
- O formulário foi dividido em ABAS (uma por tópico: Dados gerais, Cortes
  de alimentação, Medicamentos, Informações gerais, Alimentos, Exames
  proibidos antes) - tudo dentro do MESMO <form>, então salvar continua
  enviando os campos de todas as abas juntos."""
import io

from openpyxl import Workbook

from app import create_app
from app.extensions import db
from app.models import PreparoModelo

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


client.post("/login", data={"email": "secretaria@gruposaude.com", "senha": "123456"}, follow_redirects=True)

# ---------- Lista: sem botões de importar; formulário: com eles (em modal) ----------

r = client.get("/equipe/preparo-modelos")
html_lista = r.get_data(as_text=True)
checar("Lista NÃO tem mais os botões de importar", "Importar de um PDF" not in html_lista)
checar("Lista continua com o botão de novo modelo", "Novo modelo" in html_lista)

r = client.get("/equipe/preparo-modelos/novo")
html_form = r.get_data(as_text=True)
checar("Formulário tem o botão de importar PDF", "Importar de um PDF" in html_form)
checar("Formulário tem o botão de importar Excel", "Importar de um Excel" in html_form)
checar("Os botões abrem popups (modais) com o campo de arquivo",
       'id="modal-importar-pdf"' in html_form and 'id="modal-importar-xlsx"' in html_form
       and 'name="arquivo_pdf"' in html_form and 'name="arquivo_xlsx"' in html_form)

# ---------- Abas por tópico ----------

for aba in ("aba-geral", "aba-cortes", "aba-medicamentos", "aba-infos", "aba-alimentos", "aba-exames-anteriores"):
    checar(f"Formulário tem a aba {aba}", f'id="{aba}"' in html_form)
checar("As abas ficam dentro do MESMO formulário (salvar envia tudo junto)",
       html_form.index('id="form-preparo"') < html_form.index('id="aba-exames-anteriores"'))

# Na tela de EDITAR os botões de importar não aparecem (importação é só pra novo).
with app.app_context():
    modelo_qualquer = PreparoModelo.query.first()
r = client.get(f"/equipe/preparo-modelos/{modelo_qualquer.id}/editar")
checar("Tela de editar não mostra os botões de importar", "Importar de um PDF" not in r.get_data(as_text=True))

# ---------- Salvar preenche campos de VÁRIAS abas de uma vez ----------

r = client.post("/equipe/preparo-modelos/novo", data={
    "nome": "Preparo Em Abas",
    "instrucoes": "Instruções gerais do preparo em abas.",       # aba Dados gerais
    "corte_descricao[]": ["Alimentos sólidos"],                   # aba Cortes
    "corte_horas[]": ["8"],
    "medicamento_nome[]": ["Xarelto"],                            # aba Medicamentos
    "medicamento_categoria[]": ["anticoagulante"],
    "medicamento_dias[]": ["3"],
    "medicamento_obs[]": [""],
    "observacoes_medicamentos": "",
    "info_geral[]": ["Não fumar no dia do exame"],                # aba Informações gerais
    "info_geral_horas[]": [""],
    "info_geral_dias[]": [""],
    "info_geral_hora_exata[]": [""],
    "alimento_nome[]": ["Leite e derivados"],                     # aba Alimentos
    "alimento_tipo[]": ["proibido"],
    "alimento_horas[]": ["12"],
    "alimento_dias[]": [""],
    "exame_anterior_nome[]": ["Colonoscopia"],                    # aba Exames proibidos
    "exame_anterior_dias[]": ["28"],
}, follow_redirects=True)
checar("Salvar com campos de todas as abas funciona", "cadastrado com sucesso" in r.get_data(as_text=True).lower())
with app.app_context():
    m = PreparoModelo.query.filter_by(nome="Preparo Em Abas").first()
    checar("Modelo salvo com os dados de cada aba",
           m is not None and len(m.cortes) == 1 and len(m.medicamentos_suspensos) == 1
           and len(m.informacoes_gerais) == 1 and len(m.alimentos) == 1
           and len(m.exames_anteriores_proibidos) == 1)

# ---------- Importar Excel pelo popup preenche o formulário ----------

wb = Workbook()
ws = wb.active
ws.append(["Tipo", "Ação", "Agrupador", "Nome", "Dias antes", "Horas antes", "Hora exata"])
ws.append(["alimento", "proibido", "", "Amendoim", "", "12", ""])
buf = io.BytesIO()
wb.save(buf)
buf.seek(0)

r = client.post("/equipe/preparo-modelos/importar-xlsx",
                data={"arquivo_xlsx": (buf, "preparo.xlsx")},
                content_type="multipart/form-data", follow_redirects=True)
html_import = r.get_data(as_text=True)
checar("Importar pelo popup leva ao formulário preenchido pra revisão",
       "Revise com cuidado" in html_import and "Amendoim" in html_import)

client.get("/logout")
print("\nTodos os testes de abas + importação no formulário de preparo passaram.")
