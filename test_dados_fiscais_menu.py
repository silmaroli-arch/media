"""Testa que "Dados Fiscais" virou uma tela própria (submenu de "Cadastro
geral"), separada de "Dados Cadastrais": inscrição estadual, regime
tributário, CNAE e a configuração de emissão de NFS-e (ambiente, certificado,
provedor, dados da NFS-e) saíram de "Dados Cadastrais" e passaram para essa
nova tela. O código IBGE do município continua sendo preenchido a partir do
CEP em "Dados Cadastrais" (é derivado do endereço), só que agora é mostrado
como leitura em "Dados Fiscais"."""
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

# "Dados Cadastrais" não tem mais os campos fiscais nem a seção de NFS-e.
r1 = client.get("/equipe/clinica/configuracoes")
html1 = r1.get_data(as_text=True)
checar("Dados Cadastrais responde 200", r1.status_code == 200)
checar("Não tem mais 'Regime tributário'", "Regime tributário" not in html1)
checar("Não tem mais o campo de Inscrição estadual", 'name="inscricao_estadual"' not in html1)
checar("Não tem mais o campo de CNAE", 'name="cnae"' not in html1)
checar("Não tem mais a seção 'Emissão de NFS-e'", "Emissão de NFS-e" not in html1)
with app.app_context():
    _clinica_vitoria_id = Clinica.query.filter_by(nome="Clínica Vitória").first().id
checar(
    "Tem um link apontando para a nova tela de Dados Fiscais",
    f'href="/equipe/clinica/dados-fiscais/{_clinica_vitoria_id}"' in html1,
)

# A nova tela "Dados Fiscais" tem tudo isso.
r2 = client.get("/equipe/clinica/dados-fiscais")
html2 = r2.get_data(as_text=True)
checar("Dados Fiscais responde 200", r2.status_code == 200)
checar("Tem o campo de Inscrição estadual", 'name="inscricao_estadual"' in html2)
checar("Tem o campo de Regime tributário", "Regime tributário" in html2)
checar("Tem o campo de CNAE", 'name="cnae"' in html2)
checar("Tem a seção 'Emissão de NFS-e'", "Emissão de NFS-e" in html2)
checar("Tem o campo de Ambiente (homologação/produção)", "Homologação" in html2)
checar("Tem o upload de certificado digital", 'name="certificado_arquivo"' in html2)

# O menu lateral tem o item "Dados Fiscais" dentro de "Cadastro geral".
checar("Menu lateral mostra 'Dados Fiscais'", ">Dados Fiscais<" in html2)

# Salva os dados fiscais pela nova tela.
r3 = client.post("/equipe/clinica/dados-fiscais", data={
    "inscricao_estadual": "111.222.333", "regime_tributario": "Simples Nacional", "cnae": "8630-5/03",
}, follow_redirects=True)
checar("Salvar Dados Fiscais responde 200", r3.status_code == 200)
html3 = r3.get_data(as_text=True)
checar("Mensagem de sucesso de Dados Fiscais", "Dados Fiscais atualizados" in html3)
checar("Inscrição estadual salva aparece na tela", "111.222.333" in html3)

with app.app_context():
    clinica = Clinica.query.filter_by(nome="Clínica Vitória").first()
    checar("Inscrição estadual foi salva no banco", clinica.inscricao_estadual == "111.222.333")
    checar("Regime tributário foi salvo no banco", clinica.regime_tributario == "Simples Nacional")
    checar("CNAE foi salvo no banco", clinica.cnae == "8630-5/03")

# O código IBGE do município continua sendo preenchido via "Dados
# Cadastrais" (simulando o preenchimento automático do CEP) e aparece
# como leitura em "Dados Fiscais".
r4 = client.post("/equipe/clinica/configuracoes", data={
    "nome": "Clínica Vitória", "telefone": "(27) 99999-0000", "email_contato": "contato@clinicavitoria.com",
    "cep": "29000-000", "rua": "Rua Teste", "numero": "100", "bairro": "Centro",
    "cidade": "Vitória", "uf": "ES", "codigo_ibge_municipio": "3205309",
}, follow_redirects=True)
checar("Salvar Dados Cadastrais com CEP responde 200", r4.status_code == 200)

with app.app_context():
    clinica = Clinica.query.filter_by(nome="Clínica Vitória").first()
    checar("Código IBGE foi salvo a partir de Dados Cadastrais", clinica.codigo_ibge_municipio == "3205309")

r5 = client.get("/equipe/clinica/dados-fiscais")
html5 = r5.get_data(as_text=True)
checar("Código IBGE aparece (como leitura) em Dados Fiscais", "3205309" in html5)
checar("Campo do código IBGE não é mais editável em Dados Fiscais", 'name="codigo_ibge_municipio"' not in html5)

client.get("/logout")
print("\nTodos os testes do novo menu Dados Fiscais passaram.")
