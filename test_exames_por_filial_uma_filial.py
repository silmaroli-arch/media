"""Testa a tela "Exames por filial" ("Associar exames entre filiais").

Histórico do bug original (pré-Fatia 5): a tela redirecionava para a lista
de exames sempre que a empresa tinha só UMA filial - o caso mais comum de
médico independente, que não tem secretária e faz todo o próprio
cadastro. Como não havia nenhum link visível de volta pra essa tela na
lista de exames, a pessoa só via o botão "Novo exame" e acabava
recadastrando o exame do zero, achando que "associação" não funcionava.

Fatia 5: agora não existe mais "várias filiais dentro de uma empresa" -
cada Grupo já É a própria unidade (1 Grupo = 1 antiga filial), então a
distinção "uma filial vs. várias filiais" que causava o bug nem existe
mais como conceito. A tela (medico.exames_por_filial) sempre mostra
direto a grade "Exame × Médico" (nunca existiu - e nunca vai existir - uma
grade "Exame × Filial" dentro de um Grupo, já que não há mais "filial" por
baixo dele). Este teste passa a validar esse comportamento único e
universal (que também cobre o caso do médico independente, sem precisar
de um cenário dedicado de "uma filial só")."""
from app import create_app
from app.models import Grupo

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
        "Pré-condição: Clínica Vitória é um Grupo (unidade atômica, sem conceito de filial)",
        Grupo.query.filter_by(nome="Clínica Vitória").count() == 1,
    )

login("secretaria@clinicavitoria.com", "123456")

# A tela responde 200 direto - NÃO redireciona nunca (não existe mais o
# cenário "empresa com só uma filial" que causava o bug original).
r = client.get("/equipe/exames/por-filial", follow_redirects=False)
checar("Tela responde 200 (não redireciona)", r.status_code == 200)
html = r.get_data(as_text=True)
checar("Mostra o título da tela", "Associar exames" in html)
# A tela é um cadastro básico (lista + Adicionar) - igual para qualquer Grupo.
checar("Mostra o botão Adicionar e a lista de associações", "Adicionar" in html and "<th>Exame</th>" in html)
checar("NÃO mostra o combobox de tipo (não existe mais 'filial' dentro do Grupo)", "Tipo de associação" not in html)
checar("NÃO mostra a mensagem antiga de bloqueio por filial única", "só faz sentido para empresas com mais de uma filial" not in html)

# Também não teve nenhum redirect: se eu tivesse seguido follow_redirects, o
# destino não deveria ser a lista de exames.
r2 = client.get("/equipe/exames/por-filial", follow_redirects=True)
checar("Seguindo redirect (se houvesse) ainda mostra a própria tela de associação", "Associar exames" in r2.get_data(as_text=True))

client.get("/logout")
print("\nTodos os testes de 'Exames por filial' (comportamento único, sem conceito de filial) passaram.")
