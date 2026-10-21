"""Testa a mudança "o cliente é só cliente": o paciente é uma identidade
GLOBAL (por CPF), não pertence a uma filial nem nasce vinculado a nenhum
Grupo automaticamente.

- O cadastro de paciente (equipe) não pede/define filial nenhuma - cria o
  paciente GLOBAL e associa ao Grupo atual via GrupoPaciente.
- A filial só é escolhida na hora de marcar a consulta: pela equipe em
  "Agendar exame" (medico.agenda_novo).
- Um paciente pode ser agendado em qualquer Grupo ao qual esteja associado
  (importado pelo CPF - ver medico.pacientes_importar).
- Isolamento entre Grupos (tenants) diferentes continua valendo.

Fatia 5: "Grupo Saúde Total" (empresa com 2 filiais no modelo antigo) virou
2 Grupos independentes ("- Centro" e "- Praia"). Não existe mais um
fallback automático para cadastros antigos só com clinica_id/empresa_id
(esse trabalho de migração real foi feito uma vez por
migrar_paciente_para_grupo.py, que criou o GrupoPaciente que faltava para
cada cadastro legado) - por isso o cenário "paciente antigo sem
GrupoPaciente continua aparecendo" não existe mais para novos registros
sem essa associação, e foi removido deste teste."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Grupo, GrupoPaciente, Paciente, Exame, Agendamento, PreparoModelo

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


# Grupo Saúde Total tem 2 Grupos (Centro e Praia, compartilhando a mesma
# equipe) - o cenário certo pra provar que o paciente não fica preso a
# nenhum deles.
with app.app_context():
    centro = Grupo.query.filter_by(nome="Grupo Saúde Total - Centro").first()
    praia = Grupo.query.filter_by(nome="Grupo Saúde Total - Praia").first()
    centro_id, praia_id = centro.id, praia.id
    medico_grupo = Usuario.query.filter_by(email="medico@gruposaude.com").first()
    medico_grupo_id = medico_grupo.id

login("secretaria@gruposaude.com", "123456")
# Secretária tem vínculo ativo nos dois Grupos - escolhe o Centro primeiro.
client.post("/equipe/clinica", data={"clinica_id": str(centro_id)}, follow_redirects=True)

# ---------- Cadastro do paciente: sem filial, direto no Grupo atual ----------

r = client.get("/equipe/pacientes/novo")
html_form = r.get_data(as_text=True)
checar("Formulário de novo paciente NÃO pede filial", 'name="clinica_id"' not in html_form)

r = client.post("/equipe/pacientes/novo", data={
    "nome": "cliente da empresa", "cpf": "321.654.987-00", "email": "",
    "telefone": "(27) 97777-0001", "data_nascimento": "1992-05-10",
}, follow_redirects=True)
checar("Cadastro do paciente sem filial funciona", "cadastrado" in r.get_data(as_text=True).lower())

with app.app_context():
    cliente = Paciente.query.filter_by(nome="Cliente da Empresa").first()
    checar("Paciente foi criado", cliente is not None)
    checar(
        "Paciente associado ao Grupo (Centro) via GrupoPaciente",
        GrupoPaciente.query.filter_by(grupo_id=centro_id, paciente_id=cliente.id).first() is not None,
    )
    checar("Paciente NÃO tem filial legada nenhuma (clinica_id vazio)", cliente.clinica_id is None)
    cliente_id = cliente.id

r = client.get("/equipe/pacientes")
html_lista = r.get_data(as_text=True)
checar("Paciente aparece na lista (nome formatado como nome próprio)", "Cliente da Empresa" in html_lista)
checar("Lista de pacientes NÃO tem coluna de filial", "<th>Filial</th>" not in html_lista)
checar("Telefone aparece com máscara na lista (2026-10-21)", "(27) 97777-0001" in html_lista and "27977770001" not in html_lista)
checar("Coluna não diz mais 'Telefone (login)' (o login é CPF + nascimento)", "Telefone (login)" not in html_lista)
html_detalhe = client.get(f"/equipe/pacientes/{cliente_id}").get_data(as_text=True)
checar("Telefone com máscara no detalhe do paciente", "(27) 97777-0001" in html_detalhe and "login de acesso" not in html_detalhe)

# "Editar" fica na lista e abre numa janela (2026-10-21); salvar volta para a lista.
checar("Lista tem o botão 'Editar' que abre a janela de edição",
       'data-bs-target="#modalEditarPaciente"' in html_lista and f'/equipe/pacientes/{cliente_id}/editar' in html_lista
       and 'id="modalEditarPaciente"' in html_lista)
checar("O detalhe não tem mais o botão 'Editar cadastro'", "Editar cadastro" not in html_detalhe)
r = client.post(f"/equipe/pacientes/{cliente_id}/editar", data={
    "nome": "cliente da empresa editado", "observacoes": "Prefere contato à tarde", "origem": "lista",
})
checar("Salvar pela janela volta para a lista", r.status_code == 302 and r.headers["Location"].endswith("/equipe/pacientes"))
with app.app_context():
    editado = Paciente.query.get(cliente_id)
    checar("Nome e observações salvos pela janela",
           editado.nome == "Cliente da Empresa Editado" and editado.observacoes == "Prefere contato à tarde")
    editado.nome = "Cliente da Empresa"
    db.session.commit()

# CPF duplicado é bloqueado por GRUPO (não mais por filial).
r = client.post("/equipe/pacientes/novo", data={
    "nome": "Cliente Repetido", "cpf": "321.654.987-00", "email": "",
    "telefone": "(27) 97777-0002", "data_nascimento": "1993-06-11",
}, follow_redirects=True)
checar("CPF repetido no mesmo grupo é rejeitado", "já existe um paciente com esse cpf nesta empresa" in r.get_data(as_text=True).lower())

# ---------- Agendar o MESMO paciente em DOIS Grupos diferentes ----------

with app.app_context():
    # Um exame em cada Grupo, pro teste de agendamento.
    modelo_centro = PreparoModelo(grupo_id=centro_id, nome="Preparo Agenda Centro", instrucoes="Jejum.")
    db.session.add(modelo_centro)
    db.session.flush()
    exame_centro = Exame(
        grupo_id=centro_id, medico_id=medico_grupo_id, nome="Consulta no Centro",
        descricao="", duracao_minutos=30, preparo_modelo_id=modelo_centro.id,
    )
    exame_praia = Exame(
        grupo_id=praia_id, medico_id=medico_grupo_id, nome="Consulta na Praia",
        descricao="", duracao_minutos=30, preparo_modelo_id=modelo_centro.id,
    )
    db.session.add_all([exame_centro, exame_praia])
    db.session.commit()
    exame_centro_id, exame_praia_id = exame_centro.id, exame_praia.id

r = client.post("/equipe/agenda/novo", data={
    "filial_id": str(centro_id), "paciente_id": str(cliente_id), "exame_id": str(exame_centro_id),
    "medico_id": str(medico_grupo_id), "data_hora": "2026-09-01T09:00",
}, follow_redirects=True)
checar("Paciente pode ser agendado no Grupo Centro", "criado com sucesso" in r.get_data(as_text=True).lower())

# Pra agendar na Praia, a secretária troca o Grupo ativo e IMPORTA o
# mesmo paciente (identidade global) pra lá pelo CPF - a associação com um
# Grupo não é automática entre Grupos distintos.
client.post("/equipe/clinica", data={"clinica_id": str(praia_id)}, follow_redirects=True)
r = client.post("/equipe/pacientes/importar", data={"cpf": "321.654.987-00"}, follow_redirects=True)
checar("Paciente importado (pelo CPF) para o Grupo Praia", "importado" in r.get_data(as_text=True).lower())

r = client.post("/equipe/agenda/novo", data={
    "filial_id": str(praia_id), "paciente_id": str(cliente_id), "exame_id": str(exame_praia_id),
    "medico_id": str(medico_grupo_id), "data_hora": "2026-09-02T09:00",
}, follow_redirects=True)
checar("O MESMO paciente pode ser agendado no Grupo Praia", "criado com sucesso" in r.get_data(as_text=True).lower())

with app.app_context():
    ags = Agendamento.query.filter_by(paciente_id=cliente_id).order_by(Agendamento.data_hora).all()
    checar("Cada agendamento carrega o SEU Grupo (Centro e Praia)",
           len(ags) == 2 and ags[0].grupo_id == centro_id and ags[1].grupo_id == praia_id)

client.get("/logout")

# ---------- Isolamento entre Grupos continua valendo ----------

login("secretaria@clinicavitoria.com", "123456")
r = client.get("/equipe/pacientes")
checar("Paciente do Grupo Saúde Total NÃO aparece para a Clínica Vitória", "Cliente da Empresa" not in r.get_data(as_text=True))
client.get("/logout")

r = client.post("/login-paciente", data={"cpf": "321.654.987-00", "data_nascimento": "10/05/1992"}, follow_redirects=True)
checar("Paciente loga normalmente (CPF + nascimento)", r.status_code == 200)
client.get("/logout")

print("\nTodos os testes de paciente da empresa (sem filial no cadastro) passaram.")
