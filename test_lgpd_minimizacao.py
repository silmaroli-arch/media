"""LGPD, minimização (pedido do Silvan, 2026-10-20): o sistema deixou de
pedir e de guardar dados que não usava para nada.

- Equipe (médico/secretária): endereço pessoal (CEP, rua, número,
  complemento, bairro, cidade, UF). O CPF continua: é login e é por ele que
  o administrador do grupo convida a pessoa (grupo.convidar).
- Paciente: e-mail, endereço e contato de emergência. Ficam nome, CPF,
  data de nascimento (identificação no WhatsApp e login) e telefone.

As telas não mostram mais os campos, as rotas ignoram o que vier no POST e
as colunas saíram dos modelos (os valores já salvos em produção são
apagados por migrar_banco.py)."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Paciente, Grupo

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False
client = app.test_client()

CAMPOS_ENDERECO = ["cep", "rua", "numero", "complemento", "bairro", "cidade", "uf"]
CAMPOS_PACIENTE_REMOVIDOS = CAMPOS_ENDERECO + ["email", "contato_emergencia_nome", "contato_emergencia_telefone"]
DADOS_EXTRAS = {
    "email": "nao-guardar@exemplo.com", "cep": "29010-000", "rua": "Rua Teste", "numero": "10",
    "complemento": "Ap 1", "bairro": "Centro", "cidade": "Vitória", "uf": "ES",
    "contato_emergencia_nome": "Alguem", "contato_emergencia_telefone": "(27) 91111-2222",
}


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def sem_campos(html, campos):
    return not any(f'name="{c}"' in html for c in campos)


# ---------- Modelos: as colunas não existem mais ----------
checar("Usuario não tem mais endereço pessoal", not any(hasattr(Usuario, c) for c in CAMPOS_ENDERECO))
checar("Usuario mantém CPF (login e convite para grupo)", hasattr(Usuario, "cpf"))
checar("Paciente não tem mais e-mail, endereço nem contato de emergência",
       not any(hasattr(Paciente, c) for c in CAMPOS_PACIENTE_REMOVIDOS))
checar("Paciente mantém o mínimo: nome, CPF, nascimento e telefone",
       all(hasattr(Paciente, c) for c in ["nome", "cpf", "data_nascimento", "telefone"]))

# ---------- Criar conta (equipe): sem endereço ----------
html = client.get("/cadastro").get_data(as_text=True)
checar("Criar conta não pede endereço", sem_campos(html, CAMPOS_ENDERECO))
r = client.post("/cadastro", data={
    "nome": "Medica Lgpd", "email": "medica.lgpd@teste.com", "senha": "123456", "senha_confirmacao": "123456",
    "cpf": "529.982.247-25", "telefone": "(27) 98888-1111", "papel": "medico",
    "crm_numero": "12345", "crm_uf": "ES", "data_nascimento": "01/01/1980",
}, follow_redirects=True)
with app.app_context():
    checar("Conta criada sem endereço", Usuario.query.filter_by(email="medica.lgpd@teste.com").first() is not None)
client.get("/logout")

# ---------- Meus dados (equipe): sem endereço ----------
client.post("/login", data={"identificador": "secretaria@clinicavitoria.com", "senha": "123456"})
client.post("/meus-dados", data={"acao": "confirmar_senha", "senha_atual": "123456"})
html = client.get("/meus-dados").get_data(as_text=True)
checar("Meus dados (equipe) não pede endereço", sem_campos(html, CAMPOS_ENDERECO))

# ---------- Cadastro do paciente pela equipe ----------
with app.app_context():
    vitoria_id = Grupo.query.filter_by(nome="Clínica Vitória").first().id
client.post("/equipe/clinica", data={"clinica_id": str(vitoria_id)}, follow_redirects=True)
html = client.get("/equipe/pacientes/novo").get_data(as_text=True)
checar("Novo paciente (equipe) não pede e-mail, endereço nem contato de emergência",
       sem_campos(html, CAMPOS_PACIENTE_REMOVIDOS))
r = client.post("/equipe/pacientes/novo", data=dict(
    DADOS_EXTRAS, nome="Paciente Lgpd Equipe", cpf="111.444.777-35",
    telefone="(27) 97777-1212", data_nascimento="10/05/1990",
), follow_redirects=True)
with app.app_context():
    p = Paciente.query.filter_by(cpf="111.444.777-35").first()
    checar("Paciente criado pela equipe (campos extras do POST são ignorados)", p is not None)
    checar("A conta do paciente não guarda e-mail", p.usuario.email is None)
    paciente_equipe_id = p.id
html = client.get(f"/equipe/pacientes/{paciente_equipe_id}/editar").get_data(as_text=True)
checar("Editar paciente não pede e-mail, endereço nem contato de emergência",
       sem_campos(html, CAMPOS_PACIENTE_REMOVIDOS))
checar("Observações avisam para não registrar dados de saúde", "Não registre diagnósticos" in html)
html = client.get(f"/equipe/pacientes/{paciente_equipe_id}").get_data(as_text=True)
checar("Ficha do paciente não mostra e-mail, endereço nem contato de emergência",
       "Contato de emergência" not in html and "<strong>Endereço:</strong>" not in html
       and "<strong>E-mail:</strong>" not in html)
client.get("/logout")

# ---------- Autocadastro do paciente ----------
html = client.get("/cadastro-paciente").get_data(as_text=True)
checar("Autocadastro do paciente não pede e-mail, endereço nem contato de emergência",
       sem_campos(html, CAMPOS_PACIENTE_REMOVIDOS))
r = client.post("/cadastro-paciente", data=dict(
    DADOS_EXTRAS, nome="Paciente Lgpd Auto", cpf="390.533.447-05",
    telefone="(27) 95555-4444", data_nascimento="05/05/1995",
), follow_redirects=True)
with app.app_context():
    p = Paciente.query.filter_by(cpf="390.533.447-05").first()
    checar("Autocadastro funciona só com nome, CPF, nascimento e telefone", p is not None)
    checar("... e a conta não guarda e-mail", p.usuario.email is None)

# ---------- Meus dados do paciente ----------
html = client.get("/paciente/meus-dados").get_data(as_text=True)
checar("Meus dados (paciente) não pede e-mail, endereço nem contato de emergência",
       sem_campos(html, CAMPOS_PACIENTE_REMOVIDOS))
r = client.post("/paciente/meus-dados", data=dict(DADOS_EXTRAS, telefone="(27) 95555-0000"), follow_redirects=True)
with app.app_context():
    p = Paciente.query.filter_by(cpf="390.533.447-05").first()
    checar("Meus dados (paciente) salva o telefone", p.telefone == "27955550000")
    checar("... e continua sem e-mail na conta", p.usuario.email is None)

# ---------- Migração: apaga os valores já salvos ----------
sql = open("migrar_banco.py", encoding="utf-8").read()
checar("migrar_banco.py apaga o endereço da equipe",
       "UPDATE usuarios SET cep = NULL, rua = NULL, numero = NULL, complemento = NULL, bairro = NULL, cidade = NULL, uf = NULL" in sql)
checar("migrar_banco.py apaga o e-mail das contas de paciente",
       "UPDATE usuarios SET email = NULL WHERE tipo = 'paciente'" in sql)
checar("migrar_banco.py apaga e-mail, endereço e contato de emergência dos pacientes",
       "UPDATE pacientes SET email = NULL" in sql and "contato_emergencia_telefone = NULL" in sql)

print("\nTodos os testes de minimização (LGPD) passaram.")
