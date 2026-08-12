"""O link de auto-cadastro POR CLÍNICA foi desativado: o paciente agora
se cadastra na plataforma, independente de clínica (ver
auth.cadastro_paciente_global e test_cadastro_global_importar_cpf.py), e
a clínica o importa pelo CPF. Este teste garante a transição:

- O Painel da clínica NÃO mostra mais o card do link de cadastro.
- Links antigos divulgados pelas clínicas (por empresa E por filial)
  continuam funcionando: redirecionam pro cadastro global.
"""
from app import create_app
from app.extensions import db
from app.models import Empresa, Clinica

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


with app.app_context():
    grupo = Empresa.query.filter_by(nome="Grupo Saúde Total").first()
    if not grupo.codigo_cadastro_paciente:
        grupo.codigo_cadastro_paciente = "TESTEGRP"
    centro = Clinica.query.filter_by(nome="Grupo Saúde Total - Centro").first()
    centro.codigo_cadastro_paciente = "legadoctr"[:8]
    db.session.commit()
    codigo_empresa = grupo.codigo_cadastro_paciente
    codigo_legado = centro.codigo_cadastro_paciente

# O Painel não divulga mais o link por clínica.
client.post("/login", data={"email": "secretaria@gruposaude.com", "senha": "123456"}, follow_redirects=True)
r = client.get("/equipe/")
checar("Painel NÃO mostra mais o card do link de cadastro",
       "Cadastro de pacientes pelo app" not in r.get_data(as_text=True))
client.get("/logout")

# Links antigos redirecionam pro cadastro global (nada quebra).
for rotulo, codigo in (("empresa", codigo_empresa), ("filial legada", codigo_legado)):
    r = client.get(f"/paciente/cadastro/{codigo}", follow_redirects=False)
    checar(f"Link antigo ({rotulo}) redireciona", r.status_code in (301, 302)
           and "/cadastro-paciente" in r.headers["Location"])

r = client.get(f"/paciente/cadastro/{codigo_empresa}", follow_redirects=True)
checar("O destino é o cadastro global (sem nome de clínica no título)",
       "Cadastro de paciente" in r.get_data(as_text=True)
       and "qualquer clínica" in r.get_data(as_text=True))

print("\nTodos os testes da transição do link de cadastro passaram.")
