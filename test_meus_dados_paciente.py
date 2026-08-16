"""Testa "Meus dados": o paciente edita os PRÓPRIOS dados ("Meus dados")
e a mudança vale em TODOS os grupos/clínicas que ele frequenta.

- Telefone, e-mail, endereço e contato de emergência são atualizados no
  cadastro da conta de uma vez (são dados da pessoa).
- Nome/CPF/data de nascimento não são editáveis pela tela (identidade
  verificada pela clínica).
- O login é por CPF + data de nascimento (o CPF não muda): trocar o
  telefone é livre e NÃO afeta o acesso.

Fatia 5: o cadastro (Paciente) é único e GLOBAL por CPF
(`uq_pacientes_cpf`) - uma mesma pessoa frequentando duas clínicas tem UM
só registro Paciente, associado aos dois Grupos via GrupoPaciente (não
mais dois registros com o mesmo CPF, como no modelo anterior de "conta
única"). Como só existe UM registro para atualizar, "vale em todas as
clínicas" passou a ser garantido automaticamente por essa unificação."""
from datetime import date

from app import create_app
from app.extensions import db
from app.models import Usuario, Grupo, GrupoPaciente, Paciente, normalizar_telefone

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


# Cenário: Carla com UM cadastro associado a DUAS clínicas (conta/cadastro
# único) + uma outra conta ("Rival") pra testar a colisão de telefone.
with app.app_context():
    centro = Grupo.query.filter_by(nome="Grupo Saúde Total - Centro").first()
    vitoria = Grupo.query.filter_by(nome="Clínica Vitória").first()

    tel = normalizar_telefone("(27) 96060-0001")
    conta = Usuario(nome="Carla Dados", telefone=tel, tipo="paciente")
    db.session.add(conta)
    db.session.flush()
    carla = Paciente(usuario_id=conta.id, nome="Carla Dados",
                      cpf="930.140.250-06", data_nascimento=date(1992, 2, 2), telefone=tel,
                      status_cadastro="aprovado")
    db.session.add(carla)
    db.session.flush()
    db.session.add(GrupoPaciente(grupo_id=centro.id, paciente_id=carla.id))
    db.session.add(GrupoPaciente(grupo_id=vitoria.id, paciente_id=carla.id))

    tel_rival = normalizar_telefone("(27) 96060-0999")
    rival = Usuario(nome="Rival Mesma Data", telefone=tel_rival, tipo="paciente")
    db.session.add(rival)
    db.session.flush()
    p_rival = Paciente(usuario_id=rival.id, nome="Rival Mesma Data",
                       cpf="930.140.250-17", data_nascimento=date(1992, 2, 2), telefone=tel_rival,
                       status_cadastro="aprovado")
    db.session.add(p_rival)
    db.session.commit()
    conta_id, carla_id = conta.id, carla.id

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

# ---------- Salvar atualiza o cadastro (vale nos dois grupos) ----------

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
    carla = Paciente.query.get(carla_id)
    tel_novo = normalizar_telefone("(27) 96060-0002")
    checar("A conta (login) ficou com o telefone novo", conta.telefone == tel_novo)
    checar("O cadastro foi atualizado (telefone, e-mail, endereço)",
           carla.telefone == tel_novo and carla.email == "carla@exemplo.com" and carla.rua == "Rua Nova")
    checar("A cidade/UF do cadastro também foram atualizadas",
           carla.cidade == "Vitória" and carla.uf == "ES")
    checar("Contato de emergência salvo", carla.contato_emergencia_nome == "Mario Dados")
    checar("O único cadastro continua associado aos DOIS grupos (não duplicou nada)",
           len(carla.grupos) == 2)

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
    checar("O endereço não foi salvo pela metade", Paciente.query.get(carla_id).rua == "Rua Nova")

client.get("/logout")
print("\nTodos os testes de 'Meus dados' do paciente passaram.")
