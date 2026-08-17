"""Testa a tela "Associar exames" reformulada como cadastro BÁSICO (a
antiga matriz/grades com combobox de "tipo de associação" não era
intuitiva): uma lista simples (Exame, Médico, Preço) e um botão
"Adicionar" que abre um formulário com só esses 3 campos - preencheu,
salvou, pronto. Cada linha tem "Editar" pra trocar exame/médico/preço.

Fatia 5: a tela era "Exame x Filial" porque uma empresa podia ter várias
filiais e o mesmo exame precisava ser associado filial a filial. Isso não
existe mais - cada Grupo já é a própria unidade (1 Grupo = 1 antiga
filial), então "associar" não escolhe mais UMA FILIAL DE DESTINO: só
falta médico e preço, direto no Grupo atual (ver
app/routes_medico.py:exames_por_filial). Este teste usa só o Grupo
"Grupo Saúde Total - Centro" do seed.

O aviso de "médico não confirmado" (valor técnico do cadastro genérico do
exame, ver Exame.medico_confirmado) continua aparecendo na linha até
alguém escolher e salvar o médico pelo Editar."""
from datetime import datetime, timedelta

from app import create_app
from app.extensions import db
from app.models import Usuario, Grupo, GrupoMembro, Exame, PreparoModelo, Paciente, GrupoPaciente, Agendamento, normalizar_telefone

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


client.post("/login", data={"email": "secretaria@gruposaude.com", "senha": "123456"}, follow_redirects=True)

with app.app_context():
    centro = Grupo.query.filter_by(nome="Grupo Saúde Total - Centro").first()
    centro_id = centro.id
    medico_grupo = Usuario.query.filter_by(email="medico@gruposaude.com").first()
    medico_grupo_id = medico_grupo.id
    # Um exame já existente no Grupo, pra reutilizar como associação-base.
    modelo = PreparoModelo(grupo_id=centro_id, nome="Preparo Assoc Básica", instrucoes="Jejum.")
    db.session.add(modelo)
    db.session.flush()
    exame_centro = Exame(
        grupo_id=centro_id, medico_id=medico_grupo_id, nome="Endoscopia Assoc",
        descricao="", duracao_minutos=30, preparo_modelo_id=modelo.id, medico_confirmado=True,
        associado=True,
    )
    # Um segundo exame (só catálogo, sem associação) - usado no teste de
    # trocar o EXAME de uma associação para outro que ainda não é associado.
    exame_eco = Exame(
        grupo_id=centro_id, medico_id=medico_grupo_id, nome="Ecografia Assoc",
        descricao="", duracao_minutos=45, medico_confirmado=True, associado=False,
    )
    db.session.add_all([exame_centro, exame_eco])
    db.session.commit()
    exame_centro_id = exame_centro.id

# A secretária atua nos dois grupos "Saúde Total" - precisa escolher qual
# está usando agora (Grupo é atômico - ver clinica_utils.py).
client.post("/equipe/clinica", data={"clinica_id": str(centro_id)}, follow_redirects=True)

# ---------- A tela é a lista simples + botão Adicionar ----------

r = client.get("/equipe/exames/por-filial")
html = r.get_data(as_text=True)
checar("Tela abre com o botão Adicionar", "Adicionar" in html)
checar("Formulário tem os 2 campos: Exame e Médico",
       'name="nome"' in html and 'name="medico_id"' in html)
checar("Não pede mais filial de destino (Grupo já é a unidade)", 'name="clinica_destino_id"' not in html)
checar("A antiga escolha de 'Tipo de associação' sumiu", "Tipo de associação" not in html)
checar("A lista mostra a associação existente (Endoscopia)", "Endoscopia Assoc" in html)

# ---------- Adicionar um exame de catálogo NOVO (cadastro genérico não cria associação) ----------

r = client.post("/equipe/exames/novo", data={
    "nome": "Exame Generico Basico", "descricao": "", "duracao_minutos": "20",
    "preparo_modelo_id": "nenhum",
}, follow_redirects=True)
r = client.get("/equipe/exames/por-filial")
html4 = r.get_data(as_text=True)
with app.app_context():
    exame_gen = Exame.query.filter_by(nome="Exame Generico Basico").first()
    exame_gen_id = exame_gen.id
    checar("Exame genérico nasce como catálogo (associado=False)", exame_gen.associado is False)
# O nome aparece SÓ no <select> do formulário Adicionar - não como linha
# da tabela de associações (não há <td> com o nome).
checar("Exame do cadastro genérico NÃO aparece como associação na lista",
       "<td>Exame Generico Basico</td>" not in html4)
checar("Mas o exame aparece como opção no formulário Adicionar",
       'value="Exame Generico Basico"' in html4)

# Associar o exame genérico PROMOVE o mesmo registro (não duplica).
r = client.post("/equipe/exames/por-filial/associar", data={
    "nome": "Exame Generico Basico",
    "medico_id": str(medico_grupo_id),
}, follow_redirects=True)
checar("Associar o exame de catálogo funciona", "associado com" in r.get_data(as_text=True))
with app.app_context():
    checar("O MESMO registro foi promovido a associação (sem duplicar)",
           Exame.query.filter_by(nome="Exame Generico Basico").count() == 1)
    exame_gen2 = Exame.query.get(exame_gen_id)
    checar("Registro promovido: associado=True e médico confirmado",
           exame_gen2.associado is True and exame_gen2.medico_confirmado is True)
r = client.get("/equipe/exames/por-filial")
checar("Agora SIM o exame aparece na lista de associações",
       "<td>Exame Generico Basico</td>" in r.get_data(as_text=True))

# Repetir a MESMA associação com o MESMO médico é barrada com aviso.
r = client.post("/equipe/exames/por-filial/associar", data={
    "nome": "Exame Generico Basico",
    "medico_id": str(medico_grupo_id),
}, follow_redirects=True)
checar("Associação repetida (mesmo exame + médico) é barrada", "já está associado" in r.get_data(as_text=True))

# Mas um MÉDICO NOVO na mesma associação é ADICIONADO como médico que
# também atende (era o caso do usuário: criou um médico novo e quis
# associá-lo a um exame que já existia).
with app.app_context():
    medico2 = Usuario(nome="Medico Dois Assoc", email="medico2.assoc@gruposaude.com", tipo="medico")
    medico2.set_senha("123456")
    db.session.add(medico2)
    db.session.flush()
    db.session.add(GrupoMembro(grupo_id=centro_id, usuario_id=medico2.id, papel="membro", ativo=True))
    db.session.commit()
    medico2_id = medico2.id

r = client.post("/equipe/exames/por-filial/associar", data={
    "nome": "Exame Generico Basico",
    "medico_id": str(medico2_id),
}, follow_redirects=True)
html_extra = r.get_data(as_text=True)
checar("Médico NOVO na mesma associação é adicionado (não é rejeitado)",
       "foi adicionado(a) como médico que também atende" in html_extra)
with app.app_context():
    e_gen = Exame.query.get(exame_gen_id)
    checar("O médico novo entrou como médico extra da associação",
           medico2_id in [m.id for m in e_gen.medicos_extra])
    checar("O responsável original continua o mesmo", e_gen.medico_id == medico_grupo_id)
    checar("Continua UMA associação só (não duplicou a linha)",
           Exame.query.filter_by(grupo_id=centro_id, nome="Exame Generico Basico").count() == 1)
checar("O médico extra aparece na linha da lista", "+ Medico Dois Assoc" in html_extra)

# Repetir o mesmo médico extra também é barrado.
r = client.post("/equipe/exames/por-filial/associar", data={
    "nome": "Exame Generico Basico",
    "medico_id": str(medico2_id),
}, follow_redirects=True)
checar("Repetir o médico extra é barrado com aviso", "já está associado" in r.get_data(as_text=True))

# ---------- Editar a associação pela própria tela ----------

r = client.get(f"/equipe/exames/por-filial?editar={exame_centro_id}")
html3 = r.get_data(as_text=True)
checar("Editar abre o formulário pré-preenchido da associação", "Editar associação" in html3)

r = client.post(f"/equipe/exames/por-filial/{exame_centro_id}/atualizar", data={
    "medico_id": str(medico_grupo_id),
}, follow_redirects=True)
checar("Salvar a edição atualiza a associação", "atualizado" in r.get_data(as_text=True))

# ---------- Editar TODOS os campos: trocar o EXAME de uma associação ----------

# Trocar o EXAME da associação (aponta pra outro exame do Grupo) -
# "Ecografia Assoc" ainda não está associada, então pode.
r = client.post(f"/equipe/exames/por-filial/{exame_centro_id}/atualizar", data={
    "nome": "Ecografia Assoc",
    "medico_id": str(medico_grupo_id),
}, follow_redirects=True)
with app.app_context():
    exame_atualizado = Exame.query.get(exame_centro_id)
    checar("Trocar o exame da associação funciona (nome/dados vêm do exame escolhido)",
           exame_atualizado.nome == "Ecografia Assoc" and exame_atualizado.duracao_minutos == 45)

# Duplicidade também vale na edição: mudar pra um nome que já é associação
# existente é barrado.
r = client.post(f"/equipe/exames/por-filial/{exame_centro_id}/atualizar", data={
    "nome": "Exame Generico Basico",  # já é uma associação
    "medico_id": str(medico_grupo_id),
}, follow_redirects=True)
checar("Editar pra um nome que já é associação é barrado",
       "já está associado" in r.get_data(as_text=True))

# ---------- Excluir a associação ----------

r = client.get(f"/equipe/exames/por-filial?editar={exame_centro_id}")
checar("O Editar mostra o botão de excluir", "Excluir esta associação" in r.get_data(as_text=True))

r = client.post(f"/equipe/exames/por-filial/{exame_centro_id}/excluir", follow_redirects=True)
checar("Excluir remove a associação com mensagem de sucesso", "excluída" in r.get_data(as_text=True))
with app.app_context():
    checar("A associação sumiu do banco", Exame.query.get(exame_centro_id) is None)

# ---------- Com agendamento marcado: exame travado e exclusão barrada ----------

with app.app_context():
    tel = normalizar_telefone("(27) 94444-0111")
    u = Usuario(nome="Paciente Assoc", telefone=tel, tipo="paciente")
    db.session.add(u)
    db.session.flush()
    pac = Paciente(usuario_id=u.id, nome="Paciente Assoc", cpf="606.707.808-09", telefone=tel)
    db.session.add(pac)
    db.session.flush()
    db.session.add(GrupoPaciente(grupo_id=centro_id, paciente_id=pac.id))
    exame_gen_final = Exame.query.get(exame_gen_id)
    ag = Agendamento(
        grupo_id=centro_id, paciente_id=pac.id, exame_id=exame_gen_id,
        medico_id=medico_grupo_id, data_hora=datetime.utcnow() + timedelta(days=10),
    )
    db.session.add(ag)
    db.session.commit()

r = client.post(f"/equipe/exames/por-filial/{exame_gen_id}/atualizar", data={
    "nome": "Ecografia Assoc",  # tenta trocar o exame da associação com agendamento marcado
    "medico_id": str(medico_grupo_id),
}, follow_redirects=True)
checar("Com agendamento marcado, trocar o exame é barrado",
       "já tem agendamentos" in r.get_data(as_text=True))
with app.app_context():
    checar("O exame da associação não mudou", Exame.query.get(exame_gen_id).nome == "Exame Generico Basico")

# Médico continua editável mesmo com agendamento.
r = client.post(f"/equipe/exames/por-filial/{exame_gen_id}/atualizar", data={
    "medico_id": str(medico_grupo_id),
}, follow_redirects=True)
checar("Médico continua editável com agendamento marcado",
       "atualizado" in r.get_data(as_text=True))

r = client.post(f"/equipe/exames/por-filial/{exame_gen_id}/excluir", follow_redirects=True)
checar("Excluir associação com agendamento marcado é barrado",
       "cancele/realize esses agendamentos" in r.get_data(as_text=True))
with app.app_context():
    checar("A associação com agendamento continua existindo", Exame.query.get(exame_gen_id) is not None)

client.get("/logout")
print("\nTodos os testes do cadastro básico de associações passaram.")
