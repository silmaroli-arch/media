"""Testa que o autocadastro público de paciente (/paciente/cadastro/<codigo>)
agora aceita e salva endereço e contato de emergência, igual ao formulário
que a equipe usa (medico.pacientes_novo) - antes só existiam os campos
básicos (nome, CPF, telefone, data de nascimento, e-mail)."""
from app import create_app
from app.extensions import db
from app.models import Paciente, Clinica

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


with app.app_context():
    clinica = Clinica.query.filter_by(nome="Clínica Vitória").first()
    codigo = clinica.codigo_cadastro_paciente
    if not codigo:
        import secrets
        codigo = secrets.token_urlsafe(8)
        clinica.codigo_cadastro_paciente = codigo
        db.session.commit()

r = client.post("/cadastro-paciente", data={
    "nome": "Paciente Endereco Teste", "cpf": "321.654.987-00",
    "telefone": "(27) 95555-4444", "data_nascimento": "05/05/1995",
    "cep": "29010-000", "rua": "Av. Jerônimo Monteiro", "numero": "123",
    "complemento": "Sala 2", "bairro": "Centro", "cidade": "Vitória", "uf": "ES",
    "contato_emergencia_nome": "Contato Emergencia", "contato_emergencia_telefone": "(27) 91111-2222",
}, follow_redirects=True)
checar("Cadastro com endereço/emergência responde 200", r.status_code == 200)

with app.app_context():
    p = Paciente.query.filter_by(cpf="321.654.987-00").first()
    checar("Paciente foi criado", p is not None)
    checar("CEP salvo", p.cep == "29010-000")
    checar("Rua salva", p.rua == "Av. Jerônimo Monteiro")
    checar("Número salvo", p.numero == "123")
    checar("Complemento salvo", p.complemento == "Sala 2")
    checar("Bairro salvo", p.bairro == "Centro")
    checar("Cidade salva", p.cidade == "Vitória")
    checar("UF salva", p.uf == "ES")
    checar("Contato de emergência (nome) salvo", p.contato_emergencia_nome == "Contato Emergencia")
    checar("Contato de emergência (telefone) salvo", p.contato_emergencia_telefone == "(27) 91111-2222")

client.get("/logout")

# ---------- Campos continuam opcionais: cadastro sem endereço/emergência ainda funciona ----------
r = client.post("/cadastro-paciente", data={
    "nome": "Paciente Sem Endereco", "cpf": "444.555.666-77",
    "telefone": "(27) 94444-3333", "data_nascimento": "10/10/2000",
}, follow_redirects=True)
with app.app_context():
    p2 = Paciente.query.filter_by(cpf="444.555.666-77").first()
    checar("Cadastro sem endereço/emergência (campos opcionais) continua funcionando", p2 is not None)
client.get("/logout")

print("\nTodos os testes de endereço/emergência no autocadastro de paciente passaram.")
