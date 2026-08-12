"""Testa o ajuste: ao salvar o "Horário de atendimento do médico", a tela
CONTINUA no horário (mesmo médico/local) - inclusive quando a pessoa
chegou ali pelo assistente de configuração inicial (antes, vindo do
assistente, salvar voltava pro wizard, interrompendo quem ia continuar
ajustando horários). A etapa do assistente é marcada como concluída de
qualquer forma, porque o status é calculado a partir dos dados reais."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Clinica, MedicoHorario

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


client.post("/login", data={"email": "secretaria@gruposaude.com", "senha": "123456"}, follow_redirects=True)

with app.app_context():
    centro = Clinica.query.filter_by(nome="Grupo Saúde Total - Centro").first()
    centro_id = centro.id
    medico = Usuario.query.filter_by(email="medico@gruposaude.com").first()
    medico_id = medico.id

dados = {
    "medico_id": str(medico_id),
    "clinica_id": str(centro_id),
    # Segunda a sexta 08:00-13:00 (dias 0-4 ativos)
}
for dia in range(7):
    ativo = dia <= 4
    if ativo:
        dados[f"dia_{dia}_ativo"] = "on"
        dados[f"dia_{dia}_inicio"] = "08:00"
        dados[f"dia_{dia}_fim"] = "13:00"

# ---------- Vindo do assistente (voltar_onboarding=1): CONTINUA na tela ----------

dados_wizard = dict(dados)
dados_wizard["voltar_onboarding"] = "1"
r = client.post("/equipe/medico-horarios", data=dados_wizard, follow_redirects=False)
checar("Salvar redireciona", r.status_code in (301, 302))
checar("Mesmo vindo do assistente, o destino é a PRÓPRIA tela de horário (não o wizard)",
       "medico-horarios" in r.headers["Location"] and "configuracao-inicial" not in r.headers["Location"])

r2 = client.post("/equipe/medico-horarios", data=dados_wizard, follow_redirects=True)
html = r2.get_data(as_text=True)
checar("A tela de horário reabre com a mensagem de sucesso",
       "Horário de atendimento" in html and "atualizado" in html)
checar("Continua no mesmo médico/local", "08:00" in html and "13:00" in html)

with app.app_context():
    salvos = MedicoHorario.query.filter_by(clinica_id=centro_id, medico_id=medico_id, ativo=True).count()
    checar("Os horários foram salvos de verdade (5 dias ativos)", salvos == 5)

# O assistente marca a etapa como concluída sozinho (status vem dos dados).
r3 = client.get("/equipe/configuracao-inicial")
html3 = r3.get_data(as_text=True)
idx = html3.find("Horário de atendimento do médico")
checar("No assistente, a etapa de horário aparece como concluída",
       "bi-check-circle-fill" in html3[max(0, idx - 300):idx] or "check" in html3[max(0, idx - 300):idx].lower())

client.get("/logout")
print("\nTodos os testes de salvar horário continuando na tela passaram.")
