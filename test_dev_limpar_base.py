"""Testa o botão temporário "Limpar dados de teste" na tela de login
(auth.dev_limpar_base) - ferramenta de uso interno (Silvan) para apagar
rapidamente todos os dados de teste sem mexer no banco na mão. Como é
acessível sem estar logado, a única proteção é exigir a frase de
confirmação exata "APAGAR TUDO" antes de apagar qualquer coisa. A config
global da plataforma e o histórico de deploy NÃO são apagados (não são
"dados de teste").

Fatia 5: Empresa/Clinica foram substituídas por Grupo (unidade atômica) -
o teste passa a checar que todos os Grupos são apagados."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Grupo, PlataformaConfig, HistoricoDeploy, Paciente

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
    total_grupos_antes = Grupo.query.count()
    total_pacientes_antes = Paciente.query.count()
    checar("Seed populou usuários/grupos/pacientes (pré-condição)",
           total_usuarios_antes > 0 and total_grupos_antes > 0 and total_pacientes_antes > 0)

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
    # A conta do DONO da plataforma é preservada - senão ninguém
    # conseguiria mais entrar no painel do dono depois da limpeza.
    checar("Só a conta do dono sobrevive entre os usuários",
           Usuario.query.count() == Usuario.query.filter_by(tipo="dono").count()
           and Usuario.query.filter_by(tipo="dono").count() >= 1)
    checar("Todos os grupos foram apagados", Grupo.query.count() == 0)
    checar("Todos os pacientes foram apagados", Paciente.query.count() == 0)
    checar("A configuração global da plataforma NÃO foi apagada", PlataformaConfig.query.count() == 1)
    checar("O histórico de deploy NÃO foi apagado", HistoricoDeploy.query.count() == 1)

# A tela de login continua funcionando normalmente depois da limpeza.
r5 = client.get("/login")
checar("Login continua respondendo 200 depois de limpar a base", r5.status_code == 200)

# O dono continua conseguindo logar com as credenciais de antes.
r6 = client.post("/login", data={"email": "dono@plataforma.com", "senha": "123456"}, follow_redirects=True)
checar("Dono da plataforma continua logando depois da limpeza", r6.status_code == 200 and "inválidos" not in r6.get_data(as_text=True))

# ---------- Base que ficou SEM dono (limpeza por versão antiga) ----------
# Versões antigas do limpar-base apagavam o dono junto. Se isso já
# aconteceu, a PRÓXIMA limpeza recria a conta padrão do dono - e a
# migração de deploy (migrar_banco.py) faz o mesmo no banco publicado.
client.get("/logout")
with app.app_context():
    Usuario.query.filter_by(tipo="dono").delete()
    db.session.commit()
    checar("Cenário: base sem NENHUM dono", Usuario.query.filter_by(tipo="dono").count() == 0)

r7 = client.post("/dev/limpar-base", data={"confirmacao": "APAGAR TUDO"}, follow_redirects=True)
checar("Limpar de novo responde 200", r7.status_code == 200)
with app.app_context():
    checar("A conta do dono foi RECRIADA", Usuario.query.filter_by(tipo="dono").count() == 1)
r8 = client.post("/login", data={"email": "dono@plataforma.com", "senha": "123456"}, follow_redirects=True)
checar("Dono recriado consegue logar (dono@plataforma.com / 123456)",
       r8.status_code == 200 and "inválidos" not in r8.get_data(as_text=True))

print("\nTodos os testes de 'Limpar dados de teste' passaram.")
