"""Testa o novo perfil de usuário "configurador" - a pessoa que vai
configurar os dados no sistema (locais, equipe, exames, preparos) sem ser
médico nem secretária de atendimento.

- Aparece como opção no cadastro público ("Qual é o seu papel?") e no
  formulário de Equipe.
- É staff (entra nas telas da equipe) e nasce com todas as permissões
  administrativas por padrão (igual à secretária).
- NÃO é médico: não aparece em nenhuma lista de médicos (exames, agenda,
  horários) e não conta na cobrança por médico."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Empresa, Clinica, ClinicaMembro

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


# ---------- Cadastro público como configurador ----------

r = client.get("/cadastro")
html = r.get_data(as_text=True)
checar("Tela de cadastro oferece a opção Configurador(a)", 'value="configurador"' in html)

r = client.post("/cadastro", data={
    "modo": "empresa",
    "nome_empresa": "Clínica Config Teste",
    "nome": "Carla Configuradora",
    "email": "carla.config@example.com",
    "senha": "123456",
    "papel": "configurador",
}, follow_redirects=True)
checar("Cadastro público como configurador responde 200", r.status_code == 200)

with app.app_context():
    carla = Usuario.query.filter_by(email="carla.config@example.com").first()
    checar("Conta criada com tipo 'configurador'", carla is not None and carla.tipo == "configurador")
    checar("Configurador é staff (entra nas telas da equipe)", carla.is_staff)
    checar("Configurador não é médico", carla.tipo != "medico")
    checar(
        "Fundador configurador recebe todas as permissões administrativas",
        carla.perm_pacientes and carla.perm_equipe and carla.perm_filiais and carla.perm_dados_clinica,
    )
    empresa_config = Empresa.query.filter_by(nome="Clínica Config Teste").first()
    checar("Configurador fica ancorado à empresa (empresa_fundadora_id)", carla.empresa_fundadora_id == empresa_config.id)

# Consegue usar as telas administrativas normalmente (fluxo de configuração).
r = client.get("/equipe/")
checar("Painel responde 200 para o configurador", r.status_code == 200)
r = client.get("/equipe/filiais")
checar("'Meus locais de atendimento' responde 200 para o configurador", r.status_code == 200)
r = client.post("/equipe/filiais/nova", data={"nome": "Unidade Config Centro"}, follow_redirects=True)
checar("Configurador consegue cadastrar um local de atendimento", "cadastrado com sucesso" in r.get_data(as_text=True).lower())
r = client.get("/equipe/equipe-membros")
checar("Configurador acessa a tela de Equipe", r.status_code == 200)
client.get("/logout")

# ---------- Configurador adicionado pela tela de Equipe ----------

client.post("/login", data={"email": "secretaria@clinicavitoria.com", "senha": "123456"}, follow_redirects=True)
r = client.get("/equipe/equipe-membros/novo")
checar("Formulário de Equipe oferece a opção Configurador", 'value="configurador"' in r.get_data(as_text=True))

with app.app_context():
    vitoria_id = Clinica.query.filter_by(nome="Clínica Vitória").first().id

r = client.post("/equipe/equipe-membros/novo", data={
    "nome": "Paulo Configurador",
    "email": "paulo.config@clinicavitoria.com",
    "papel": "configurador",
    "senha": "123456",
    "filial_ids": [str(vitoria_id)],
    "perm_pacientes": "on", "perm_equipe": "on", "perm_filiais": "on", "perm_dados_clinica": "on",
}, follow_redirects=True)
checar("Equipe aceita cadastrar um configurador", "cadastrado" in r.get_data(as_text=True).lower())

with app.app_context():
    paulo = Usuario.query.filter_by(email="paulo.config@clinicavitoria.com").first()
    checar("Configurador da equipe criado com tipo certo", paulo is not None and paulo.tipo == "configurador")
    checar("Configurador ganhou vínculo com a filial", ClinicaMembro.query.filter_by(usuario_id=paulo.id, clinica_id=vitoria_id).count() == 1)
    vitoria = Clinica.query.get(vitoria_id)
    medico_ids = [m.id for m in vitoria.medicos_e_secretarias if m.tipo == "medico"]
    checar("Configurador NÃO aparece entre os médicos da filial", paulo.id not in medico_ids)
    empresa_vitoria = vitoria.empresa
    checar(
        "Configurador NÃO conta na cobrança por médico",
        paulo.id not in [m.id for m in empresa_vitoria.medicos_distintos],
    )
client.get("/logout")

# ---------- Configurador consegue logar e trabalhar ----------

r = client.post("/login", data={"email": "paulo.config@clinicavitoria.com", "senha": "123456"}, follow_redirects=True)
checar("Configurador loga normalmente", r.status_code == 200)
r = client.get("/equipe/")
checar("Painel responde 200 para o configurador da equipe", r.status_code == 200)
r = client.get("/equipe/exames/por-filial")
checar("Configurador acessa a tela de associação de exames", r.status_code == 200)
client.get("/logout")

print("\nTodos os testes do perfil configurador passaram.")
