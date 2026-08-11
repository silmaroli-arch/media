"""Testa a correção do bug relatado: "Está dando erro ao salvar o modelo
de preparo". Cenário real do usuário: fundador criou a empresa e cadastrou
2 locais de atendimento SEM se vincular a nenhum (agora que cadastrar
local não vincula ninguém automaticamente). Ao salvar um modelo de
preparo, a rota usava os locais EM QUE O USUÁRIO ATUA (filiais_atuais,
por vínculo) como âncora - com zero vínculos, respondia "Você não tem
nenhum local de atendimento cadastrado ainda" e DESCARTAVA o formulário
inteiro, mesmo com a empresa tendo 2 locais.

Regra corrigida: dados de CONFIGURAÇÃO (modelos de preparo, exames,
etapas do assistente inicial) são da EMPRESA - quem configura não precisa
estar vinculado a local nenhum. O vínculo ("você atua aqui") só diz
respeito a atuação/agenda."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Empresa, Clinica, ClinicaMembro, PreparoModelo, Exame

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


# Fundador cria a empresa e 2 locais, sem se vincular a nenhum -
# exatamente o estado das telas do usuário ("Marcar que você também atua
# aqui" aparecendo nos dois locais).
r = client.post("/cadastro", data={
    "modo": "empresa",
    "nome_empresa": "Medical Gastro Config",
    "nome": "Bruno Config",
    "email": "bruno.config@example.com",
    "senha": "123456",
    "papel": "medico",
}, follow_redirects=True)
checar("Cadastro da empresa responde 200", r.status_code == 200)
client.post("/equipe/filiais/nova", data={"nome": "Medical Gastro Centro"}, follow_redirects=True)
client.post("/equipe/filiais/nova", data={"nome": "Medical Gastro Santa Lucia"}, follow_redirects=True)

with app.app_context():
    empresa = Empresa.query.filter_by(nome="Medical Gastro Config").first()
    bruno = Usuario.query.filter_by(email="bruno.config@example.com").first()
    checar("Empresa tem 2 locais", Clinica.query.filter_by(empresa_id=empresa.id).count() == 2)
    checar("Fundador NÃO está vinculado a nenhum local (cenário do bug)",
           ClinicaMembro.query.filter_by(usuario_id=bruno.id).count() == 0)
    empresa_id = empresa.id

# ---------- Salvar modelo de preparo SEM vínculo nenhum ----------

r = client.post("/equipe/preparo-modelos/novo", data={
    "nome": "Hidro",
    "instrucoes": "- Deverá comparecer ao local do exame trazendo o pedido médico.\n- A dieta no dia anterior deve ser não fermentativa.",
    "observacoes_medicamentos": "",
    "corte_descricao[]": ["JEJUM de 12 horas, sendo permitido apenas água"],
    "corte_horas[]": ["12"],
}, follow_redirects=True)
html = r.get_data(as_text=True)
checar("Salvar o modelo de preparo FUNCIONA mesmo sem vínculo", "cadastrado com sucesso" in html.lower())
checar("NÃO aparece mais o aviso de 'nenhum local cadastrado'", "não tem nenhum local de atendimento" not in html)

with app.app_context():
    modelo = PreparoModelo.query.filter_by(nome="Hidro").first()
    checar("Modelo foi salvo de verdade", modelo is not None)
    checar("Modelo ficou ancorado numa filial DA EMPRESA",
           modelo.clinica.empresa_id == empresa_id)
    modelo_id = modelo.id

r = client.get("/equipe/preparo-modelos")
checar("Modelo aparece na lista de modelos (sem vínculo)", "Hidro" in r.get_data(as_text=True))

# Editar o modelo também funciona sem vínculo.
r = client.post(f"/equipe/preparo-modelos/{modelo_id}/editar", data={
    "nome": "Hidro (Teste de Hidrogênio)",
    "instrucoes": "Instruções revisadas.",
    "observacoes_medicamentos": "",
}, follow_redirects=True)
checar("Editar o modelo funciona sem vínculo", r.status_code == 200)
with app.app_context():
    checar("Edição foi salva", PreparoModelo.query.get(modelo_id).nome == "Hidro (Teste de Hidrogênio)")

# ---------- Cadastrar exame genérico também é configuração da empresa ----------

# Ainda sem vínculo - mas também sem nenhum médico na empresa: o cadastro
# de exame avisa que precisa de um médico (o valor técnico do banco).
# Vincula o próprio fundador (que é médico) a um local e tenta de novo.
r = client.post("/equipe/exames/novo", data={
    "nome": "Teste de Hidrogênio Expirado", "descricao": "", "duracao_minutos": "60",
    "preparo_modelo_id": str(modelo_id),
}, follow_redirects=True)
with app.app_context():
    exame_criado = Exame.query.filter_by(nome="Teste de Hidrogênio Expirado").first()
if exame_criado is None:
    # Sem nenhum médico vinculado na empresa ainda - vincula o fundador
    # (ação explícita) e repete.
    with app.app_context():
        centro_id = Clinica.query.filter_by(empresa_id=empresa_id, nome="Medical Gastro Centro").first().id
    client.post(f"/equipe/filiais/{centro_id}/vincular-me", follow_redirects=True)
    r = client.post("/equipe/exames/novo", data={
        "nome": "Teste de Hidrogênio Expirado", "descricao": "", "duracao_minutos": "60",
        "preparo_modelo_id": str(modelo_id),
    }, follow_redirects=True)
    with app.app_context():
        exame_criado = Exame.query.filter_by(nome="Teste de Hidrogênio Expirado").first()
checar("Exame genérico foi criado usando o modelo salvo", exame_criado is not None)

# ---------- Assistente de configuração enxerga o progresso da EMPRESA ----------

r = client.get("/equipe/configuracao-inicial")
html_onb = r.get_data(as_text=True)
checar("Assistente de configuração responde 200", r.status_code == 200)

client.get("/logout")
print("\nTodos os testes de configuração sem vínculo de local passaram.")
