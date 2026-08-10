"""Testa o botão temporário "Limpar dados de teste" na tela de login
(auth.dev_limpar_base) - ferramenta de uso interno (Silvan) para apagar
rapidamente todos os dados de teste sem mexer no banco na mão. Como é
acessível sem estar logado, a única proteção é exigir a frase de
confirmação exata "APAGAR TUDO" antes de apagar qualquer coisa. A config
global da plataforma e o histórico de deploy NÃO são apagados (não são
"dados de teste")."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Clinica, Empresa, PlataformaConfig, HistoricoDeploy, Paciente

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


with app.app_context():
    PlataformaConfig.obter()
    db.session.add(HistoricoDeploy(commit="0123456789abcdef", commit_curto="0123456", branch="dev", mensagem="teste"))
    db.session.commit()
    total_usuarios_antes = Usuario.query.count()
    total_clinicas_antes = Clinica.query.count()
    total_empresas_antes = Empresa.query.count()
    total_pacientes_antes = Paciente.query.count()
    checar("Seed populou usuários/clínicas/empresas/pacientes (pré-condição)",
           total_usuarios_antes > 0 and total_clinicas_antes > 0 and total_empresas_antes > 0 and total_pacientes_antes > 0)

# O link aparece na tela de login, mesmo sem estar logado.
r0 = client.get("/login")
html0 = r0.get_data(as_text=True)
checar("Tela de login responde 200", r0.status_code == 200)
checar("Tela de login tem o link 'Limpar dados de teste'", "Limpar dados de teste" in html0)
checar("Link aponta para a rota de limpeza", 'href="/dev/limpar-base"' in html0)

# A tela de confirmação é acessível sem estar logado.
r1 = client.get("/dev/limpar-base")
checar("Tela de confirmação responde 200 (sem precisar login)", r1.status_code == 200)
checar("Tela pede a frase de confirmação", 'name="confirmacao"' in r1.get_data(as_text=True))

# Frase errada não apaga nada.
r2 = client.post("/dev/limpar-base", data={"confirmacao": "apagar tudo"}, follow_redirects=True)
checar("Frase errada (minúscula) mostra erro", "Frase incorreta" in r2.get_data(as_text=True))
r3 = client.post("/dev/limpar-base", data={"confirmacao": "qualquer coisa"}, follow_redirects=True)
checar("Frase completamente diferente mostra erro", "Frase incorreta" in r3.get_data(as_text=True))
with app.app_context():
    checar("Nada foi apagado com frase errada", Usuario.query.count() == total_usuarios_antes)

# Frase certa apaga tudo, exceto plataforma_config e historico_deploy.
r4 = client.post("/dev/limpar-base", data={"confirmacao": "APAGAR TUDO"}, follow_redirects=True)
checar("Frase certa responde 200", r4.status_code == 200)
checar("Mensagem de sucesso aparece", "limpa com sucesso" in r4.get_data(as_text=True))

with app.app_context():
    checar("Todos os usuários foram apagados", Usuario.query.count() == 0)
    checar("Todas as clínicas foram apagadas", Clinica.query.count() == 0)
    checar("Todas as empresas foram apagadas", Empresa.query.count() == 0)
    checar("Todos os pacientes foram apagados", Paciente.query.count() == 0)
    checar("A configuração global da plataforma NÃO foi apagada", PlataformaConfig.query.count() == 1)
    checar("O histórico de deploy NÃO foi apagado", HistoricoDeploy.query.count() == 1)

# A tela de login continua funcionando normalmente depois da limpeza.
r5 = client.get("/login")
checar("Login continua respondendo 200 depois de limpar a base", r5.status_code == 200)

print("\nTodos os testes de 'Limpar dados de teste' passaram.")
