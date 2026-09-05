"""Smoke test manual (não faz parte da suíte oficial) da nova tela
medico.testar_ia - roda sem nenhuma API key de IA configurada, então
exercita o caminho de fallback (FAQ/alimento/medicamento/pendente)."""
from app import create_app
from app.models import Usuario, Exame, Paciente, PreparoModelo

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


login("medica2@clinicavitoria.com", "123456")

r = client.get("/equipe/testar-ia")
checar("GET /equipe/testar-ia responde 200 para médico", r.status_code == 200)
html = r.get_data(as_text=True)
checar("Tela lista pelo menos um exame/preparo do médico", '<option value="' in html)

with app.app_context():
    medico = Usuario.query.filter_by(email="medica2@clinicavitoria.com").first()
    exame = Exame.query.filter(Exame.medico_id == medico.id, Exame.preparo_modelo_id.isnot(None)).first()
    checar("Existe um exame com preparo para testar", exame is not None)
    exame_id = exame.id

r2 = client.post("/equipe/testar-ia", data={
    "exame_id": str(exame_id),
    "pergunta": "Posso comer batata antes do exame?",
}, follow_redirects=True)
checar("POST /equipe/testar-ia responde 200", r2.status_code == 200)
html2 = r2.get_data(as_text=True)
checar("Pergunta enviada aparece na tela", "Posso comer batata antes do exame?" in html2)

with app.app_context():
    paciente_teste = Paciente.query.filter_by(cpf=f"TESTE-IA-{medico.id}", eh_teste=True).first()
    checar("Paciente de teste sintético foi criado com eh_teste=True", paciente_teste is not None)

# O paciente de teste NUNCA deve aparecer na lista normal de pacientes do médico.
r3 = client.get("/equipe/pacientes")
html3 = r3.get_data(as_text=True)
checar("Paciente de teste NÃO aparece na lista normal de pacientes", "Paciente de teste" not in html3)

client.get("/logout")

# Secretária não vê o link "Testar IA" no menu (só médico).
login("secretaria@clinicavitoria.com", "123456")
r4 = client.get("/equipe/")
html4 = r4.get_data(as_text=True)
checar("Secretária NÃO vê o link 'Testar IA' no menu", "Testar IA nos meus preparos" not in html4)
# Mas a rota em si segue existindo (secretária não é bloqueada por engano
# se acessar a URL direta) - ver decisão de restringir só na tela
# (eh_medico()), não no menu.
r5 = client.get("/equipe/testar-ia", follow_redirects=True)
html5 = r5.get_data(as_text=True)
checar("Secretária acessando a URL direta é redirecionada (mensagem de aviso)", "só para contas de médico" in html5)

client.get("/logout")
print("\nTodos os testes de fumaça de testar_ia passaram.")
