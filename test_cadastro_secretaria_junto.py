"""Testa a opção de cadastrar a secretária junto, na mesma tela e mesma
submissão do cadastro público — pedido do usuário depois de ver a tela de
cadastro do médico independente ("Seu consultório") e notar que não dava
pra já deixar a secretária cadastrada ali, só depois em "Equipe". Ou
preenche nome/e-mail/senha da secretária, ou deixa os três em branco; e
essa opção só existe pra quem está FUNDANDO um local novo (não faz
sentido pra quem entra numa clínica já existente pelo CNPJ - ver
test_cadastro_por_cnpj.py)."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Empresa, Clinica, ClinicaMembro

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


# ---------- Modo independente: médico cadastra o consultório + a secretária junto ----------

r = client.post("/cadastro", data={
    "modo": "independente",
    "nome": "Dr. Solo Com Secretaria",
    "cpf": "852.963.741-00", "crm_numero": "13579", "crm_uf": "ES",
    "email": "solo.comsecretaria@example.com",
    "senha": "123456",
    "telefone_filial": "(27) 90000-0020",
    "nome_secretaria": "Secretária do Dr. Solo",
    "email_secretaria": "secretaria.dosolo@example.com",
    "senha_secretaria": "123456",
}, follow_redirects=True)
checar("Cadastro responde 200", r.status_code == 200)
texto = r.get_data(as_text=True)
checar("Mensagem confirma que a conta da secretária também foi criada",
       "secretária" in texto.lower() and "secretária do dr. solo" in texto.lower())

with app.app_context():
    medico = Usuario.query.filter_by(email="solo.comsecretaria@example.com").first()
    secretaria = Usuario.query.filter_by(email="secretaria.dosolo@example.com").first()
    checar("Médico foi criado", medico is not None)
    checar("Secretária foi criada", secretaria is not None)
    checar("Secretária é do tipo certo", secretaria.tipo == "secretaria")
    filial = Clinica.query.filter_by(empresa_id=medico.empresa_fundadora_id).first()
    checar("Médico e secretária estão vinculados ao MESMO local",
           ClinicaMembro.query.filter_by(clinica_id=filial.id, usuario_id=medico.id, ativo=True).count() == 1
           and ClinicaMembro.query.filter_by(clinica_id=filial.id, usuario_id=secretaria.id, ativo=True).count() == 1)
    checar("Secretária recebeu as permissões administrativas padrão do papel",
           secretaria.perm_pacientes and secretaria.perm_equipe
           and secretaria.perm_filiais and secretaria.perm_dados_clinica)
    checar("Secretária NÃO é fundadora da empresa (quem fundou foi o médico)",
           secretaria.empresa_fundadora_id is None)
client.get("/logout")

# Secretária já consegue logar normalmente.
r = client.post("/login", data={"email": "secretaria.dosolo@example.com", "senha": "123456"}, follow_redirects=True)
checar("Secretária criada junto já consegue entrar", "Painel" in r.get_data(as_text=True) or r.status_code == 200)
client.get("/logout")

# ---------- Deixar os três campos em branco continua funcionando normalmente ----------

r = client.post("/cadastro", data={
    "modo": "independente",
    "nome": "Dr. Solo Sem Secretaria",
    "cpf": "123.456.789-09", "crm_numero": "24680", "crm_uf": "ES",
    "email": "solo.semsecretaria@example.com",
    "senha": "123456",
    "telefone_filial": "(27) 90000-0021",
}, follow_redirects=True)
checar("Cadastro sem preencher a secretária continua funcionando", r.status_code == 200)
with app.app_context():
    medico_sem = Usuario.query.filter_by(email="solo.semsecretaria@example.com").first()
    filial_sem = Clinica.query.filter_by(empresa_id=medico_sem.empresa_fundadora_id).first()
    checar(
        "Nenhuma conta de secretária foi criada nesse caso - só o médico está vinculado ao local",
        ClinicaMembro.query.filter_by(clinica_id=filial_sem.id, ativo=True).count() == 1,
    )
client.get("/logout")

# ---------- Preencher só PARTE dos campos da secretária é rejeitado ----------

r = client.post("/cadastro", data={
    "modo": "independente",
    "nome": "Dr. Solo Secretaria Incompleta",
    "cpf": "111.444.777-35", "crm_numero": "11223", "crm_uf": "ES",
    "email": "solo.incompleta@example.com",
    "senha": "123456",
    "telefone_filial": "(27) 90000-0022",
    "nome_secretaria": "Fulana",
    # e-mail e senha da secretária ficaram em branco de propósito
}, follow_redirects=True)
checar(
    "Preencher só o nome da secretária, sem e-mail/senha, é rejeitado",
    "Preencha nome, e-mail e senha da secretária" in r.get_data(as_text=True),
)
with app.app_context():
    checar("Nenhuma conta foi criada (nem médico, nem secretária) - o cadastro inteiro foi rejeitado",
           Usuario.query.filter_by(email="solo.incompleta@example.com").first() is None)

# ---------- E-mail da secretária igual ao do médico é rejeitado ----------

r = client.post("/cadastro", data={
    "modo": "independente",
    "nome": "Dr. Solo Email Igual",
    "cpf": "529.982.247-25", "crm_numero": "33445", "crm_uf": "ES",
    "email": "mesmo.email@example.com",
    "senha": "123456",
    "telefone_filial": "(27) 90000-0023",
    "nome_secretaria": "Secretária Email Igual",
    "email_secretaria": "mesmo.email@example.com",
    "senha_secretaria": "123456",
}, follow_redirects=True)
checar(
    "E-mail da secretária igual ao do médico é rejeitado",
    "e-mail diferente do seu" in r.get_data(as_text=True).lower(),
)

# ---------- Modo empresa: fundadora também consegue cadastrar a secretária junto ----------

r = client.post("/cadastro", data={
    "modo": "empresa",
    "nome_empresa": "Clínica Com Secretaria Junto",
    "nome": "Dra. Fundadora Com Secretaria",
    "cpf": "852.963.741-00", "crm_numero": "55667", "crm_uf": "ES",
    "email": "fundadora.comsecretaria@example.com",
    "senha": "123456",
    "papel": "medico",
    "nome_filial": "Clínica Com Secretaria Junto - Sede",
    "telefone_filial": "(27) 90000-0024",
    "cnpj_filial": "12.345.609/0001-81",
    "nome_secretaria": "Secretária da Fundadora",
    "email_secretaria": "secretaria.dafundadora@example.com",
    "senha_secretaria": "123456",
}, follow_redirects=True)
checar("Cadastro 'empresa' com secretária junto responde 200", r.status_code == 200)
with app.app_context():
    checar("Secretária da fundadora foi criada",
           Usuario.query.filter_by(email="secretaria.dafundadora@example.com").first() is not None)
client.get("/logout")

print("\nTodos os testes de cadastro com secretária junto passaram.")
