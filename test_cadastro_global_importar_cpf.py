"""Testa o novo fluxo de entrada do paciente:

1) O paciente se cadastra na PLATAFORMA, independente de clínica
   (/cadastro-paciente) - formulário completo (com endereço e contato de
   emergência, que faltavam no cadastro antigo pelo link).
2) Na clínica, a secretária IMPORTA o paciente digitando o CPF: se o CPF
   existe na base, os dados vêm na hora; se não existe, ela cadastra do
   zero pelo formulário completo.
3) O link antigo de cadastro por clínica saiu do Painel (links já
   divulgados redirecionam pro cadastro global - ver
   test_link_cadastro_por_empresa.py).
"""
from datetime import date

from app import create_app
from app.extensions import db
from app.models import Usuario, Empresa, Clinica, Paciente, normalizar_telefone

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login_equipe(email, senha="123456"):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


CPF = "852.963.741-00"  # CPF VÁLIDO (dígitos verificadores conferem)

# ---------- 1) Cadastro global do paciente (independente de clínica) ----------

r = client.get("/cadastro-paciente")
html = r.get_data(as_text=True)
checar("A tela de cadastro global abre (sem clínica no título)",
       "Cadastro de paciente" in html and "qualquer clínica" in html)
checar("Tem os campos de endereço (faltavam no cadastro antigo)",
       'name="cep"' in html and 'name="rua"' in html and 'name="cidade"' in html)
checar("Tem os campos de contato de emergência",
       'name="contato_emergencia_nome"' in html and 'name="contato_emergencia_telefone"' in html)
checar("O CEP busca o endereço automaticamente (ViaCEP), com rua/bairro/cidade/UF travados",
       "viacep.com.br" in html and 'id="cep_status"' in html
       and 'name="rua" id="rua"' in html and 'readonly' in html)

# CPF INVÁLIDO (número inventado) é barrado antes de qualquer coisa.
r = client.post("/cadastro-paciente", data={
    "nome": "Cpf Invalido", "cpf": "123.456.789-99", "telefone": "(27) 93030-0000",
    "data_nascimento": "01/01/1990",
}, follow_redirects=True)
checar("CPF inexistente (dígitos verificadores errados) é barrado",
       "CPF inválido" in r.get_data(as_text=True))
r = client.post("/cadastro-paciente", data={
    "nome": "Cpf Repetido", "cpf": "111.111.111-11", "telefone": "(27) 93030-0000",
    "data_nascimento": "01/01/1990",
}, follow_redirects=True)
checar("Sequência repetida (111.111.111-11) também é barrada",
       "CPF inválido" in r.get_data(as_text=True))

# Data de nascimento incompleta (mascara deixando apagar não pode virar
# "deixa enviar pela metade") é barrada - "10" não é uma data.
r = client.post("/cadastro-paciente", data={
    "nome": "Data Incompleta", "cpf": "998.887.776-53", "telefone": "(27) 93030-0010",
    "data_nascimento": "10",
}, follow_redirects=True)
checar("Data de nascimento incompleta ('10') é barrada",
       "Data de nascimento inválida" in r.get_data(as_text=True))
with app.app_context():
    checar("Ninguém foi criado com a data incompleta",
           Paciente.query.filter_by(cpf="998.887.776-53").first() is None)

# CEP incompleto (ex.: "29055", faltando os 3 últimos números) é barrado -
# sem isso, o cadastro salvava com rua/bairro/cidade/UF sempre em branco.
r = client.post("/cadastro-paciente", data={
    "nome": "Cep Incompleto", "cpf": "998.887.776-53", "telefone": "(27) 93030-0011",
    "data_nascimento": "01/01/1990", "cep": "29055",
}, follow_redirects=True)
checar("CEP incompleto ('29055') é barrado",
       "CEP incompleto" in r.get_data(as_text=True))
with app.app_context():
    checar("Ninguém foi criado com o CEP incompleto",
           Paciente.query.filter_by(cpf="998.887.776-53").first() is None)

r = client.post("/cadastro-paciente", data={
    "nome": "Diego Plataforma", "cpf": CPF, "telefone": "(27) 93030-0001",
    "data_nascimento": "12/12/1990", "email": "diego@exemplo.com",
    "cep": "29000-111", "rua": "Rua Global", "numero": "10", "complemento": "",
    "bairro": "Centro", "cidade": "Vitória", "uf": "ES",
    "contato_emergencia_nome": "Irma Plataforma", "contato_emergencia_telefone": "(27) 93030-0002",
}, follow_redirects=True)
checar("Cadastro global cria a conta e já loga", "Diego Plataforma" in r.get_data(as_text=True))

# Nome próprio digitado em minúsculas entra formatado (Primeira Letra
# Maiúscula, com conectivos em minúsculas).
r_nome = client.get("/logout")
r_nome = client.post("/cadastro-paciente", data={
    "nome": "  maria das graças de   oliveira", "cpf": "222.333.444-05",
    "telefone": "(27) 93030-0055", "data_nascimento": "05/05/1995",
    "contato_emergencia_nome": "josé d'ávila", "contato_emergencia_telefone": "",
}, follow_redirects=True)
with app.app_context():
    p_nome = Paciente.query.filter_by(cpf="222.333.444-05").first()
    checar("Nome digitado minúsculo vira Nome Próprio ('Maria das Graças de Oliveira')",
           p_nome is not None and p_nome.nome == "Maria das Graças de Oliveira")
    checar("Contato de emergência também é formatado (José D'Ávila)",
           p_nome.contato_emergencia_nome == "José D'Ávila")
client.get("/logout")
checar("A mensagem orienta informar o CPF na clínica", "CPF" in r.get_data(as_text=True))

with app.app_context():
    perfil = Paciente.query.filter_by(cpf=CPF).first()
    checar("O cadastro global NÃO pertence a nenhuma empresa",
           perfil is not None and perfil.empresa_id is None and perfil.clinica_id is None)
    checar("Endereço e emergência salvos no cadastro global",
           perfil.rua == "Rua Global" and perfil.contato_emergencia_nome == "Irma Plataforma")
client.get("/logout")

# CPF repetido no cadastro global é barrado.
r = client.post("/cadastro-paciente", data={
    "nome": "Impostor", "cpf": CPF, "telefone": "(27) 93030-0099",
    "data_nascimento": "01/01/1980",
}, follow_redirects=True)
checar("CPF já cadastrado na plataforma é barrado",
       "já está cadastrado na plataforma" in r.get_data(as_text=True))

# Mesma pessoa (telefone+nascimento) tentando de novo → orienta o login.
r = client.post("/cadastro-paciente", data={
    "nome": "Diego Plataforma", "cpf": "998.887.776-53", "telefone": "(27) 93030-0001",
    "data_nascimento": "12/12/1990",
}, follow_redirects=True)
checar("Quem já tem conta é orientado a entrar pelo login",
       "já tem cadastro na plataforma" in r.get_data(as_text=True))

# ---------- 2) A clínica importa pelo CPF ----------

login_equipe("secretaria@gruposaude.com")
with app.app_context():
    grupo_id = Empresa.query.filter_by(nome="Grupo Saúde Total").first().id

r = client.get("/equipe/pacientes/novo")
html = r.get_data(as_text=True)
checar("A tela de novo paciente tem a busca por CPF", "Importar paciente pelo CPF" in html)

# CPF que existe → mostra o encontrado com botão de importar.
r = client.get(f"/equipe/pacientes/novo?cpf_busca={CPF}")
html = r.get_data(as_text=True)
checar("Busca acha o Diego na plataforma",
       "Diego Plataforma" in html and "Importar para esta clínica" in html)

r = client.post("/equipe/pacientes/importar", data={"cpf": CPF}, follow_redirects=True)
checar("Importar cria o cadastro nesta empresa", "importado(a) da plataforma" in r.get_data(as_text=True))
with app.app_context():
    importado = Paciente.query.filter_by(cpf=CPF, empresa_id=grupo_id).first()
    perfil = Paciente.query.filter_by(cpf=CPF, empresa_id=None).first()
    checar("O cadastro da empresa existe e é separado do global",
           importado is not None and perfil is not None and importado.id != perfil.id)
    checar("Dados vieram junto (endereço/emergência/nascimento)",
           importado.rua == "Rua Global" and importado.contato_emergencia_nome == "Irma Plataforma"
           and importado.data_nascimento == date(1990, 12, 12))
    checar("MESMA conta de login (conta única)", importado.usuario_id == perfil.usuario_id)
    checar("Importado pela equipe entra APROVADO (sem fila de pendentes)",
           importado.status_cadastro == "aprovado")

r = client.get("/equipe/pacientes")
checar("O Diego aparece na lista de pacientes da empresa", "Diego Plataforma" in r.get_data(as_text=True))

# Importar de novo → aviso.
r = client.post("/equipe/pacientes/importar", data={"cpf": CPF}, follow_redirects=True)
checar("Importar repetido avisa que já é paciente daqui",
       "já é paciente desta empresa" in r.get_data(as_text=True))

# Buscar CPF que já é da empresa → aviso direto.
r = client.get(f"/equipe/pacientes/novo?cpf_busca={CPF}", follow_redirects=True)
checar("Buscar CPF que já é da empresa avisa e leva pra lista",
       "já é paciente desta empresa" in r.get_data(as_text=True))

# CPF inexistente → orienta cadastrar do zero (formulário com o CPF preenchido).
r = client.get("/equipe/pacientes/novo?cpf_busca=111.111.111-11")
html = r.get_data(as_text=True)
checar("CPF não encontrado orienta o cadastro completo",
       "não encontrado na plataforma" in html)
checar("O CPF buscado já vem preenchido no formulário",
       'value="111.111.111-11"' in html)
client.get("/logout")

# ---------- O paciente vê a clínica no app dele depois da importação ----------

r = client.post("/login-paciente", data={"cpf": CPF, "data_nascimento": "12/12/1990"},
                follow_redirects=True)
html = r.get_data(as_text=True)
checar("O Diego loga direto (conta única)", "Diego Plataforma" in html)

# Importação em OUTRA clínica também funciona (paciente da empresa → Vitória).
client.get("/logout")
login_equipe("secretaria@clinicavitoria.com")
r = client.post("/equipe/pacientes/importar", data={"cpf": CPF}, follow_redirects=True)
checar("Outra clínica também importa o mesmo CPF",
       "importado(a) da plataforma" in r.get_data(as_text=True))
with app.app_context():
    checar("O Diego agora tem 3 cadastros (global + 2 empresas), uma conta só",
           Paciente.query.filter_by(cpf=CPF).count() == 3
           and len({p.usuario_id for p in Paciente.query.filter_by(cpf=CPF).all()}) == 1)
client.get("/logout")

print("\nTodos os testes de cadastro global + importação por CPF passaram.")
