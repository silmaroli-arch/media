"""Testa a correção do bug relatado: a tela "Dados Cadastrais" ficava
parecendo travada em "Buscando endereço..." quando a busca automática do
CEP (via ViaCEP, só no navegador) demorava ou falhava, porque os campos
de endereço (rua/bairro/cidade/UF) eram somente leitura e só podiam ser
preenchidos por aquela busca. Agora esses campos são editáveis
normalmente, então o cadastro nunca fica bloqueado esperando o CEP - a
pessoa pode digitar o endereço à mão a qualquer momento, e o POST salva
o que foi enviado independentemente de a busca de CEP ter funcionado."""
from app import create_app
from app.extensions import db
from app.models import Clinica

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


login("secretaria@clinicavitoria.com", "123456")

r0 = client.get("/equipe/clinica/configuracoes")
html0 = r0.get_data(as_text=True)
checar("Tela de Dados Cadastrais responde 200", r0.status_code == 200)
checar("Campo Rua NÃO é mais somente leitura", 'id="rua_clinica"' in html0 and 'readonly' not in html0.split('id="rua_clinica"')[1].split('>')[0])
checar("Campo Bairro NÃO é mais somente leitura", 'id="bairro_clinica"' in html0 and 'readonly' not in html0.split('id="bairro_clinica"')[1].split('>')[0])
checar("Campo Cidade NÃO é mais somente leitura", 'id="cidade_clinica"' in html0 and 'readonly' not in html0.split('id="cidade_clinica"')[1].split('>')[0])
checar("Campo UF NÃO é mais somente leitura", 'id="uf_clinica"' in html0 and 'readonly' not in html0.split('id="uf_clinica"')[1].split('>')[0])
checar("A busca do CEP tem um timeout (não fica esperando pra sempre)", "5000" in html0 and "AbortController" in html0)

# Salvar preenchendo o endereço manualmente (sem depender do ViaCEP) funciona normalmente.
r1 = client.post("/equipe/clinica/configuracoes", data={
    "nome": "Clínica Vitória", "telefone": "(27) 99999-0000", "email_contato": "contato@clinicavitoria.com",
    "cep": "29000-000", "rua": "Rua Digitada à Mão", "numero": "100", "bairro": "Centro",
    "cidade": "Vitória", "uf": "ES",
}, follow_redirects=True)
checar("Salvar com endereço digitado manualmente responde 200", r1.status_code == 200)
checar("Mensagem de sucesso aparece", "atualizados com sucesso" in r1.get_data(as_text=True))
with app.app_context():
    clinica = Clinica.query.filter_by(nome="Clínica Vitória").first()
    checar("Rua digitada manualmente foi salva", clinica.rua == "Rua Digitada à Mão")
    checar("Bairro digitado manualmente foi salvo", clinica.bairro == "Centro")
    checar("Cidade digitada manualmente foi salva", clinica.cidade == "Vitória")
    checar("UF digitada manualmente foi salva", clinica.uf == "ES")

client.get("/logout")
print("\nTodos os testes de Dados Cadastrais sem bloqueio pelo CEP passaram.")
