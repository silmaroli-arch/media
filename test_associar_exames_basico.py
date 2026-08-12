"""Testa a tela "Associar exames" reformulada como cadastro BÁSICO (a
antiga matriz/grades com combobox de "tipo de associação" não era
intuitiva): uma lista simples (Exame, Filial, Médico, Preço) e um botão
"Adicionar" que abre um formulário com só esses 4 campos - preencheu,
salvou, pronto. Cada linha tem "Editar" pra trocar médico/preço.

O aviso de "médico não confirmado" (valor técnico do cadastro genérico do
exame, ver Exame.medico_confirmado) continua aparecendo na linha até
alguém escolher e salvar o médico pelo Editar."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Clinica, Exame, PreparoModelo

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


client.post("/login", data={"email": "secretaria@gruposaude.com", "senha": "123456"}, follow_redirects=True)

with app.app_context():
    centro = Clinica.query.filter_by(nome="Grupo Saúde Total - Centro").first()
    praia = Clinica.query.filter_by(nome="Grupo Saúde Total - Praia").first()
    centro_id, praia_id = centro.id, praia.id
    medico_grupo = Usuario.query.filter_by(email="medico@gruposaude.com").first()
    medico_grupo_id = medico_grupo.id
    # Um exame já existente na filial Centro, pra associar à Praia.
    modelo = PreparoModelo(clinica_id=centro_id, nome="Preparo Assoc Básica", instrucoes="Jejum.")
    db.session.add(modelo)
    db.session.flush()
    exame_centro = Exame(
        clinica_id=centro_id, medico_id=medico_grupo_id, nome="Endoscopia Assoc",
        descricao="", duracao_minutos=30, preparo_modelo_id=modelo.id, medico_confirmado=True,
    )
    # Um segundo exame (só no Centro) - usado no teste de trocar o EXAME de
    # uma associação para outro que ainda não existe naquela filial.
    exame_eco = Exame(
        clinica_id=centro_id, medico_id=medico_grupo_id, nome="Ecografia Assoc",
        descricao="", duracao_minutos=45, medico_confirmado=True,
    )
    db.session.add_all([exame_centro, exame_eco])
    db.session.commit()

# ---------- A tela é a lista simples + botão Adicionar ----------

r = client.get("/equipe/exames/por-filial")
html = r.get_data(as_text=True)
checar("Tela abre com o botão Adicionar", "Adicionar" in html)
checar("Formulário tem os 4 campos: Exame, Filial, Médico e Preço",
       'name="nome"' in html and 'name="clinica_destino_id"' in html
       and 'name="medico_id"' in html and 'name="preco"' in html)
checar("A antiga escolha de 'Tipo de associação' sumiu", "Tipo de associação" not in html)
checar("A lista mostra a associação existente (Endoscopia no Centro)",
       "Endoscopia Assoc" in html and "Grupo Saúde Total - Centro" in html)

# ---------- Adicionar: preencheu os 4 campos, salvou, pronto ----------

r = client.post("/equipe/exames/por-filial/associar", data={
    "nome": "Endoscopia Assoc",
    "clinica_destino_id": str(praia_id),
    "medico_id": str(medico_grupo_id),
    "preco": "300,00",
}, follow_redirects=True)
html2 = r.get_data(as_text=True)
checar("Associação criada com mensagem de sucesso", "associado à filial" in html2)
checar("A linha nova aparece na lista com a filial Praia", "Grupo Saúde Total - Praia" in html2)
checar("O preço aparece na linha", "R$ 300,00" in html2)
with app.app_context():
    exame_praia = Exame.query.filter_by(clinica_id=praia_id, nome="Endoscopia Assoc").first()
    checar("Exame foi criado na filial Praia", exame_praia is not None)
    checar("Médico escolhido no formulário vale como confirmado", exame_praia.medico_confirmado is True)
    exame_praia_id = exame_praia.id

# Duplicar a mesma associação é barrado com aviso.
r = client.post("/equipe/exames/por-filial/associar", data={
    "nome": "Endoscopia Assoc", "clinica_destino_id": str(praia_id),
    "medico_id": str(medico_grupo_id), "preco": "300,00",
}, follow_redirects=True)
checar("Associação repetida (mesmo exame + mesma filial) é barrada", "já está associado" in r.get_data(as_text=True))

# ---------- Editar: trocar o preço pela própria tela ----------

r = client.get(f"/equipe/exames/por-filial?editar={exame_praia_id}")
html3 = r.get_data(as_text=True)
checar("Editar abre o formulário pré-preenchido da associação", "Editar associação" in html3 and "300,00" in html3)

r = client.post(f"/equipe/exames/por-filial/{exame_praia_id}/atualizar", data={
    "medico_id": str(medico_grupo_id),
    "preco": "350,00",
}, follow_redirects=True)
checar("Salvar a edição atualiza a associação", "atualizado na filial" in r.get_data(as_text=True))
with app.app_context():
    checar("Novo preço foi salvo", float(Exame.query.get(exame_praia_id).preco) == 350.0)

# ---------- Aviso de médico não confirmado continua funcionando ----------

r = client.post("/equipe/exames/novo", data={
    "nome": "Exame Generico Basico", "descricao": "", "duracao_minutos": "20",
    "preparo_modelo_id": "nenhum",
}, follow_redirects=True)
r = client.get("/equipe/exames/por-filial")
html4 = r.get_data(as_text=True)
# O nome também aparece no <select> do formulário Adicionar - usa a
# ÚLTIMA ocorrência (a linha da tabela) pra checar o aviso.
checar("Exame do cadastro genérico aparece com aviso de 'não confirmado'",
       "não confirmado" in html4.rsplit("Exame Generico Basico", 1)[1].split("</tr>")[0])

with app.app_context():
    exame_gen = Exame.query.filter_by(nome="Exame Generico Basico").first()
    exame_gen_id = exame_gen.id
r = client.post(f"/equipe/exames/por-filial/{exame_gen_id}/atualizar", data={
    "medico_id": str(medico_grupo_id), "preco": "100,00",
}, follow_redirects=True)
with app.app_context():
    checar("Escolher e salvar o médico pelo Editar confirma a associação",
           Exame.query.get(exame_gen_id).medico_confirmado is True)

# ---------- Editar TODOS os campos: trocar a filial da associação ----------

# A associação "Exame Generico Basico" está no Centro - muda pra Praia.
r = client.post(f"/equipe/exames/por-filial/{exame_gen_id}/atualizar", data={
    "nome": "Exame Generico Basico",
    "clinica_destino_id": str(praia_id),
    "medico_id": str(medico_grupo_id),
    "preco": "120,00",
}, follow_redirects=True)
checar("Trocar a filial da associação funciona", "atualizado na filial" in r.get_data(as_text=True))
with app.app_context():
    exame_gen2 = Exame.query.get(exame_gen_id)
    checar("A associação agora é da filial Praia", exame_gen2.clinica_id == praia_id)
    checar("Preço novo salvo junto", float(exame_gen2.preco) == 120.0)
    checar("Modelo de preparo foi zerado (era da filial antiga)", exame_gen2.preparo_modelo_id is None)

# Trocar o EXAME da associação (aponta pra outro exame da empresa) -
# "Ecografia Assoc" ainda não existe na Praia, então pode.
r = client.post(f"/equipe/exames/por-filial/{exame_gen_id}/atualizar", data={
    "nome": "Ecografia Assoc",
    "clinica_destino_id": str(praia_id),
    "medico_id": str(medico_grupo_id),
    "preco": "130,00",
}, follow_redirects=True)
with app.app_context():
    exame_gen3 = Exame.query.get(exame_gen_id)
    checar("Trocar o exame da associação funciona (nome/dados vêm do exame escolhido)",
           exame_gen3.nome == "Ecografia Assoc" and exame_gen3.duracao_minutos == 45)

# Duplicidade também vale na edição: mudar pra uma combinação
# exame+filial que já existe é barrado.
r = client.post(f"/equipe/exames/por-filial/{exame_gen_id}/atualizar", data={
    "nome": "Endoscopia Assoc",  # já existe na Praia
    "clinica_destino_id": str(praia_id),
    "medico_id": str(medico_grupo_id),
    "preco": "130,00",
}, follow_redirects=True)
checar("Editar pra uma combinação exame+filial que já existe é barrado",
       "já está associado" in r.get_data(as_text=True))

# ---------- Excluir a associação ----------

r = client.get(f"/equipe/exames/por-filial?editar={exame_gen_id}")
checar("O Editar mostra o botão de excluir", "Excluir esta associação" in r.get_data(as_text=True))

r = client.post(f"/equipe/exames/por-filial/{exame_gen_id}/excluir", follow_redirects=True)
checar("Excluir remove a associação com mensagem de sucesso", "excluída" in r.get_data(as_text=True))
with app.app_context():
    checar("A associação sumiu do banco", Exame.query.get(exame_gen_id) is None)

# ---------- Com agendamento marcado: exame/filial travados e exclusão barrada ----------

from datetime import datetime, timedelta
from app.models import Paciente, Agendamento, normalizar_telefone

with app.app_context():
    tel = normalizar_telefone("(27) 94444-0111")
    u = Usuario(nome="Paciente Assoc", telefone=tel, tipo="paciente")
    db.session.add(u)
    db.session.flush()
    empresa_grupo_id = Clinica.query.get(centro_id).empresa_id
    pac = Paciente(empresa_id=empresa_grupo_id, usuario_id=u.id, nome="Paciente Assoc",
                   cpf="606.707.808-09", telefone=tel)
    db.session.add(pac)
    db.session.flush()
    ag = Agendamento(
        clinica_id=praia_id, paciente_id=pac.id, exame_id=exame_praia_id,
        medico_id=medico_grupo_id, data_hora=datetime.utcnow() + timedelta(days=10), status="agendado",
    )
    db.session.add(ag)
    db.session.commit()

r = client.post(f"/equipe/exames/por-filial/{exame_praia_id}/atualizar", data={
    "nome": "Endoscopia Assoc",
    "clinica_destino_id": str(centro_id),  # tenta mudar a filial com agendamento marcado
    "medico_id": str(medico_grupo_id),
    "preco": "350,00",
}, follow_redirects=True)
checar("Com agendamento marcado, trocar exame/filial é barrado",
       "já tem agendamentos" in r.get_data(as_text=True))
with app.app_context():
    checar("A filial não mudou", Exame.query.get(exame_praia_id).clinica_id == praia_id)

# Médico/preço continuam editáveis mesmo com agendamento.
r = client.post(f"/equipe/exames/por-filial/{exame_praia_id}/atualizar", data={
    "medico_id": str(medico_grupo_id), "preco": "375,00",
}, follow_redirects=True)
with app.app_context():
    checar("Médico/preço continuam editáveis com agendamento marcado",
           float(Exame.query.get(exame_praia_id).preco) == 375.0)

r = client.post(f"/equipe/exames/por-filial/{exame_praia_id}/excluir", follow_redirects=True)
checar("Excluir associação com agendamento marcado é barrado",
       "cancele/realize esses agendamentos" in r.get_data(as_text=True))
with app.app_context():
    checar("A associação com agendamento continua existindo", Exame.query.get(exame_praia_id) is not None)

client.get("/logout")
print("\nTodos os testes do cadastro básico de associações passaram.")
