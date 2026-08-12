"""Testa o ajuste: a tela "Exames por filial" (associações) abre SEM
nenhum tipo de associação pré-selecionado - o usuário escolhe de propósito
o que quer fazer ("Exame × Filial" ou "Exame × Médico") e só então a grade
correspondente aparece.

Exceção: empresa com UMA filial só não tem escolha a fazer (a grade por
filial teria uma coluna só), então continua indo direto para a grade
"Exame × Médico", sem combobox - comportamento que já existia."""
from app import create_app

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


# ---------- Empresa com 2 filiais: nada pré-selecionado ----------

login("secretaria@gruposaude.com", "123456")

r = client.get("/equipe/exames/por-filial")
html = r.get_data(as_text=True)
checar("Tela abre com o combobox em 'Selecione o que você quer fazer...'",
       "Selecione o que você quer fazer..." in html)
# O "selected" tem que estar na opção vazia, não nas outras duas.
checar("Nenhuma opção vem pré-selecionada",
       "selected" in html.split('<option value=""')[1][:30])
checar("A grade 'Exame × Filial' NÃO aparece antes da escolha", "Associação de exame por filial" not in html)
checar("A grade 'Exame × Médico' NÃO aparece antes da escolha", "Médico responsável" not in html)
checar("Aparece a orientação de escolher o tipo", "Escolha acima o tipo de associação" in html)

r = client.get("/equipe/exames/por-filial?tipo=filial")
html_f = r.get_data(as_text=True)
checar("Escolhendo 'Exame × Filial', a grade por filial aparece",
       "Associação de exame por filial" in html_f)
checar("Com a escolha feita, a orientação some", "Escolha acima o tipo de associação" not in html_f)

r = client.get("/equipe/exames/por-filial?tipo=medico")
html_m = r.get_data(as_text=True)
checar("Escolhendo 'Exame × Médico', a grade por médico aparece", "Médico responsável" in html_m)

r = client.get("/equipe/exames/por-filial?tipo=qualquercoisa")
checar("Tipo inválido na URL cai de volta em 'nenhuma seleção'",
       "Escolha acima o tipo de associação" in r.get_data(as_text=True))

client.get("/logout")

# ---------- Empresa com 1 filial: vai direto pra grade de médico ----------

login("secretaria@clinicavitoria.com", "123456")
r = client.get("/equipe/exames/por-filial")
html_v = r.get_data(as_text=True)
checar("Com uma filial só, a grade 'Exame × Médico' aparece direto (sem combobox)",
       "Médico responsável" in html_v and "Selecione o que você quer fazer..." not in html_v)
client.get("/logout")

print("\nTodos os testes da tela de associação sem seleção inicial passaram.")
