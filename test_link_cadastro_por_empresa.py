"""O link de auto-cadastro POR CLÍNICA foi desativado: o paciente agora
se cadastra na plataforma, independente de clínica (ver
auth.cadastro_paciente_global e test_cadastro_global_importar_cpf.py), e
a clínica o importa pelo CPF. Este teste garante a transição:

- O Painel da clínica NÃO mostra mais o card do link de cadastro.
- Links antigos divulgados pelas clínicas continuam funcionando:
  redirecionam pro cadastro global.

Fatia 5: "Grupo Saúde Total" (empresa com 2 filiais no modelo antigo) virou
2 Grupos independentes ("- Centro" e "- Praia", cada um sua própria
unidade) - não existe mais a distinção "código da empresa" vs. "código da
filial legada", já que não há mais uma empresa por cima agrupando os dois;
o teste usa o código de cada Grupo diretamente."""
from app import create_app
from app.extensions import db
from app.models import Grupo

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


with app.app_context():
    centro = Grupo.query.filter_by(nome="Grupo Saúde Total - Centro").first()
    if not centro.codigo_cadastro_paciente:
        centro.codigo_cadastro_paciente = "TESTEGRP"
    praia = Grupo.query.filter_by(nome="Grupo Saúde Total - Praia").first()
    praia.codigo_cadastro_paciente = "legadopra"[:8]
    db.session.commit()
    codigo_centro = centro.codigo_cadastro_paciente
    codigo_praia = praia.codigo_cadastro_paciente

# O Painel não divulga mais o link por clínica.
client.post("/login", data={"email": "secretaria@gruposaude.com", "senha": "123456"}, follow_redirects=True)
r = client.get("/equipe/")
checar("Painel NÃO mostra mais o card do link de cadastro",
       "Cadastro de pacientes pelo app" not in r.get_data(as_text=True))
client.get("/logout")

# Links antigos redirecionam pro cadastro global (nada quebra).
for rotulo, codigo in (("Centro", codigo_centro), ("Praia", codigo_praia)):
    r = client.get(f"/paciente/cadastro/{codigo}", follow_redirects=False)
    checar(f"Link antigo ({rotulo}) redireciona", r.status_code in (301, 302)
           and "/cadastro-paciente" in r.headers["Location"])

r = client.get(f"/paciente/cadastro/{codigo_centro}", follow_redirects=True)
checar("O destino é o cadastro global (sem nome de clínica no título)",
       "Cadastro de paciente" in r.get_data(as_text=True)
       and "qualquer clínica" in r.get_data(as_text=True))

print("\nTodos os testes da transição do link de cadastro passaram.")
