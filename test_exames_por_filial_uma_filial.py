"""Testa a correção do bug relatado: a tela "Exames por filial" (menu
"Associar exames entre filiais") redirecionava para a lista de exames
sempre que a empresa tinha só UMA filial - o caso mais comum de médico
independente, que não tem secretária e faz todo o próprio cadastro. Como
não havia nenhum link visível de volta pra essa tela na lista de exames, a
pessoa só via o botão "Novo exame" e acabava recadastrando o exame do
zero, achando que "associação" não funcionava.

Agora a tela nunca redireciona: com uma filial só, ela mostra direto a
grade "Exame × Médico" (a grade "Exame × Filial" não faria sentido com
uma coluna só, então nem aparece no combobox - que também some, já que só
resta uma opção)."""
from app import create_app
from app.models import Clinica

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


with app.app_context():
    checar(
        "Pré-condição: Clínica Vitória é uma empresa de UMA filial só (o cenário do bug)",
        Clinica.query.filter_by(nome="Clínica Vitória").count() == 1,
    )

login("secretaria@clinicavitoria.com", "123456")

# A tela responde 200 direto - NÃO redireciona mais pra exames_lista.
r = client.get("/equipe/exames/por-filial", follow_redirects=False)
checar("Tela responde 200 (não redireciona)", r.status_code == 200)
html = r.get_data(as_text=True)
checar("Mostra o título da tela", "Associar exames" in html)
# A tela virou um cadastro básico (lista + Adicionar) - igual pra
# empresa de 1 ou várias filiais.
checar("Mostra o botão Adicionar e a lista de associações", "Adicionar" in html and "<th>Exame</th>" in html)
checar("NÃO mostra o combobox de tipo (só teria uma opção útil)", "Tipo de associação" not in html)
checar("NÃO mostra a mensagem antiga de bloqueio por filial única", "só faz sentido para empresas com mais de uma filial" not in html)

# Também não teve nenhum redirect: se eu tivesse seguido follow_redirects, o
# destino não deveria ser a lista de exames.
r2 = client.get("/equipe/exames/por-filial", follow_redirects=True)
checar("Seguindo redirect (se houvesse) ainda mostra a própria tela de associação", "Associar exames" in r2.get_data(as_text=True))

client.get("/logout")
print("\nTodos os testes de 'Exames por filial' com uma filial só (sem redirecionar) passaram.")
