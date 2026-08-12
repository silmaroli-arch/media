"""Testa a correção do bug relatado: "Está dando erro e limpando os dados
antes inseridos" - no formulário "Adicionar médico/secretária", quando uma
validação falhava no servidor (ex.: nenhuma filial marcada), a tela era
re-renderizada VAZIA, jogando fora nome, e-mail, papel, senha e permissões
já preenchidos.

Agora, em qualquer erro de validação, a tela volta com todos os valores
digitados preenchidos (reaproveitados de request.form) - a pessoa só
corrige o que faltou. Também há uma barreira no navegador (JS) que impede
o envio sem filial marcada, mas a validação do servidor continua."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Clinica

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


client.post("/login", data={"email": "secretaria@gruposaude.com", "senha": "123456"}, follow_redirects=True)

# POST sem nenhuma filial marcada - validação falha, MAS os dados voltam preenchidos.
r = client.post("/equipe/equipe-membros/novo", data={
    "nome": "Fernanda Nova Secretaria",
    "email": "fernanda.nova@gruposaude.com",
    "papel": "secretaria",
    "senha": "segredo123",
    "perm_pacientes": "on",
    # perm_equipe/perm_filiais/perm_dados_clinica desmarcadas de propósito
}, follow_redirects=True)
html = r.get_data(as_text=True)
checar("Validação continua barrando (nenhuma filial marcada)", "Escolha em qual(is) filial(is)" in html)
checar("Nome digitado NÃO foi perdido", 'value="Fernanda Nova Secretaria"' in html)
checar("E-mail digitado NÃO foi perdido", 'value="fernanda.nova@gruposaude.com"' in html)
checar("Senha digitada NÃO foi perdida", 'value="segredo123"' in html)
# (o campo de papel sumiu do formulário: a equipe só cadastra SECRETÁRIA -
# médico entra pelo código, ver equipe_vincular_por_codigo)
checar("O formulário não tem mais a opção de papel Médico", 'value="medico"' not in html)
# Olha só o trecho logo depois do id de cada checkbox (antes do ">" que
# fecha o input) pra saber se ela voltou marcada ou não.
checar("Permissão marcada (pacientes) continua marcada",
       "checked" in html.split('id="perm_pacientes"')[1][:40])
checar("Permissão desmarcada (equipe) continua desmarcada",
       "checked" not in html.split('id="perm_equipe"')[1][:40])

# Marca as filiais e completa o cadastro - agora vai.
with app.app_context():
    centro_id = Clinica.query.filter_by(nome="Grupo Saúde Total - Centro").first().id
r = client.post("/equipe/equipe-membros/novo", data={
    "nome": "Fernanda Nova Secretaria",
    "email": "fernanda.nova@gruposaude.com",
    "papel": "secretaria",
    "senha": "segredo123",
    "filial_ids": [str(centro_id)],
    "perm_pacientes": "on",
}, follow_redirects=True)
checar("Com a filial marcada, o cadastro completa normalmente", "cadastrado" in r.get_data(as_text=True).lower())
with app.app_context():
    fernanda = Usuario.query.filter_by(email="fernanda.nova@gruposaude.com").first()
    checar("Conta criada com o papel certo (secretária)", fernanda is not None and fernanda.tipo == "secretaria")
    checar("Permissões respeitam o que foi marcado (só pacientes)",
           fernanda.perm_pacientes and not fernanda.perm_equipe and not fernanda.perm_filiais and not fernanda.perm_dados_clinica)

client.get("/logout")
print("\nTodos os testes de preservação de dados no formulário de equipe passaram.")
