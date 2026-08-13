"""Testa a fase 3 da conta única: o paciente edita os PRÓPRIOS dados
("Meus dados") e a mudança vale em TODAS as clínicas que ele frequenta.

- Telefone, e-mail, endereço e contato de emergência são atualizados em
  todos os cadastros da conta de uma vez (são dados da pessoa).
- Nome/CPF/data de nascimento não são editáveis pela tela (identidade
  verificada pela clínica).
- O login é por CPF + data de nascimento (o CPF não muda): trocar o
  telefone é livre e NÃO afeta o acesso.
"""
from datetime import date

from app import create_app
from app.extensions import db
from app.models import Usuario, Clinica, Paciente, normalizar_telefone

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


# Cenário: Carla com cadastros em DUAS empresas (conta única) + uma outra
# conta ("Rival") pra testar a colisão de telefone.
with app.app_context():
    centro = Clinica.query.filter_by(nome="Grupo Saúde Total - Centro").first()
    vitoria = Clinica.query.filter_by(nome="Clínica Vitória").first()

    tel = normalizar_telefone("(27) 96060-0001")
    conta = Usuario(nome="Carla Dados", telefone=tel, tipo="paciente")
    db.session.add(conta)
    db.session.flush()
    p1 = Paciente(empresa_id=centro.empresa_id, usuario_id=conta.id, nome="Carla Dados",
                  cpf="930.140.250-06", data_nascimento=date(1992, 2, 2), telefone=tel,
                  status_cadastro="aprovado")
    p2 = Paciente(empresa_id=vitoria.empresa_id, usuario_id=conta.id, nome="Carla Dados",
                  cpf="930.140.250-06", data_nascimento=date(1992, 2, 2), telefone=tel,
                  status_cadastro="aprovado")
    db.session.add_all([p1, p2])

    tel_rival = normalizar_telefone("(27) 96060-0999")
    rival = Usuario(nome="Rival Mesma Data", telefone=tel_rival, tipo="paciente")
    db.session.add(rival)
    db.session.flush()
    p_rival = Paciente(empresa_id=centro.empresa_id, usuario_id=rival.id, nome="Rival Mesma Data",
                       cpf="930.140.250-17", data_nascimento=date(1992, 2, 2), telefone=tel_rival,
                       status_cadastro="aprovado")
    db.session.add(p_rival)
    db.session.commit()
    conta_id, p1_id, p2_id = conta.id, p1.id, p2.id

client.post("/login-paciente", data={"cpf": "930.140.250-06", "data_nascimento": "02/02/1992"},
            follow_redirects=True)

# ---------- A tela existe e não deixa editar a identidade ----------

r = client.get("/paciente/meus-dados")
html = r.get_data(as_text=True)
checar("A tela 'Meus dados' abre", r.status_code == 200 and "Meus dados" in html)
checar("Explica que vale em todas as clínicas", "todas as clínicas" in html)
checar("Nome não é campo editável (identidade)", 'name="nome"' not in html)
checar("CPF e nascimento não são campos editáveis",
       'name="cpf"' not in html and 'name="data_nascimento"' not in html)
checar("O link 'Meus dados' está no menu do paciente", "Meus dados" in html)

# ---------- Salvar atualiza TODOS os cadastros ----------

r = client.post("/paciente/meus-dados", data={
    "telefone_original": normalizar_telefone("(27) 96060-0001"),
    "telefone": "(27) 96060-0002", "email": "carla@exemplo.com",
    "cep": "29000-000", "rua": "Rua Nova", "numero": "42", "complemento": "",
    "bairro": "Centro", "cidade": "Vitória", "uf": "es",
    "contato_emergencia_nome": "Mario Dados", "contato_emergencia_telefone": "(27) 96060-0003",
}, follow_redirects=True)
html = r.get_data(as_text=True)
checar("Salvar confirma a atualização em todas as clínicas",
       "atualizados em todas as clínicas" in html)

with app.app_context():
    conta = Usuario.query.get(conta_id)
    p1 = Paciente.query.get(p1_id)
    p2 = Paciente.query.get(p2_id)
    tel_novo = normalizar_telefone("(27) 96060-0002")
    checar("A conta (login) ficou com o telefone novo", conta.telefone == tel_novo)
    checar("O cadastro da 1ª clínica foi atualizado (telefone, e-mail, endereço)",
           p1.telefone == tel_novo and p1.email == "carla@exemplo.com" and p1.rua == "Rua Nova")
    checar("O cadastro da 2ª clínica TAMBÉM foi atualizado",
           p2.telefone == tel_novo and p2.email == "carla@exemplo.com"
           and p2.cidade == "Vitória" and p2.uf == "ES")
    checar("Contato de emergência salvo nos dois",
           p1.contato_emergencia_nome == "Mario Dados" and p2.contato_emergencia_nome == "Mario Dados")

# ---------- O login é por CPF: trocar telefone não afeta o acesso ----------

client.get("/logout")
r = client.post("/login-paciente", data={"cpf": "930.140.250-06", "data_nascimento": "02/02/1992"},
                follow_redirects=True)
checar("Depois de trocar o telefone, o login por CPF continua funcionando",
       "Olá, Carla Dados" in r.get_data(as_text=True))
with app.app_context():
    checar("O telefone novo ficou salvo na conta",
           Usuario.query.get(conta_id).telefone == normalizar_telefone("(27) 96060-0002"))

# Telefone vazio também é bloqueado (é a credencial).
r = client.post("/paciente/meus-dados", data={
    "telefone_original": normalizar_telefone("(27) 96060-0002"),
    "telefone": "", "email": "",
    "cep": "", "rua": "", "numero": "", "complemento": "", "bairro": "", "cidade": "", "uf": "",
    "contato_emergencia_nome": "", "contato_emergencia_telefone": "",
}, follow_redirects=True)
checar("Telefone vazio é rejeitado", "obrigatório" in r.get_data(as_text=True))

# CEP incompleto (ex.: "29055") é rejeitado - sem isso, salvava com
# rua/bairro/cidade/UF sempre em branco.
r = client.post("/paciente/meus-dados", data={
    "telefone_original": normalizar_telefone("(27) 96060-0002"),
    "telefone": "(27) 96060-0002", "email": "carla@exemplo.com",
    "cep": "29055", "rua": "", "numero": "", "complemento": "", "bairro": "", "cidade": "", "uf": "",
    "contato_emergencia_nome": "", "contato_emergencia_telefone": "",
}, follow_redirects=True)
checar("CEP incompleto é rejeitado", "CEP incompleto" in r.get_data(as_text=True))
with app.app_context():
    checar("O endereço não foi salvo pela metade", Paciente.query.get(p1_id).rua == "Rua Nova")

client.get("/logout")
print("\nTodos os testes de 'Meus dados' do paciente passaram.")
