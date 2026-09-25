"""Testa o botão "Encerrar" de um clique na tela "Meus exames agendados"
(pedido do Silvan, 2026-09-25 - ver medico.atendimento_encerrar_rapido em
app/routes_medico.py): encerra o agendamento sem abrir a tela de
Atendimento e sem exigir nenhuma anotação.

- O botão aparece na lista pra um agendamento ainda não encerrado.
- O POST na rota marca `encerrado_em` e NÃO toca em `notas_atendimento`
  (essa é a razão de existir uma rota separada da `atendimento` - ver
  comentário na função: reaproveitar a rota de anotações zeraria uma
  nota já existente, porque o campo do formulário não vem no POST).
- Depois de encerrado, o botão não aparece mais (o `{% if not
  a.encerrada %}` no template escondeu) e um segundo POST não muda nada.
- Um médico de outra clínica não consegue encerrar um agendamento que
  não é do escopo dele (404)."""
from app import create_app
from app.extensions import db
from app.models import Agendamento, Exame

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login(email, senha):
    return client.post("/login", data={"email": email, "senha": senha}, follow_redirects=True)


with app.app_context():
    colonoscopia = Exame.query.filter_by(nome="Colonoscopia").first()
    agendamento = Agendamento.query.filter_by(exame_id=colonoscopia.id).first()
    checar("Agendamento de colonoscopia do seed existe e não está encerrado", agendamento is not None and agendamento.encerrado_em is None)
    agendamento_id = agendamento.id
    nota_original = agendamento.notas_atendimento

login("medico@clinicavitoria.com", "123456")

r = client.get("/equipe/medico-agenda")
html = r.get_data(as_text=True)
checar("Botão 'Encerrar' aparece pro agendamento ainda aberto", "Encerrar" in html)

r = client.post(f"/equipe/agenda/{agendamento_id}/encerrar-rapido", follow_redirects=True)
checar("POST de encerrar-rápido responde 200", r.status_code == 200)

with app.app_context():
    agendamento = Agendamento.query.get(agendamento_id)
    checar("encerrado_em foi preenchido", agendamento.encerrado_em is not None)
    checar("notas_atendimento NÃO foi tocado", agendamento.notas_atendimento == nota_original)

# Segundo clique (ex.: usuário deu refresh e clicou de novo) não quebra
# nem sobrescreve o encerrado_em já salvo.
with app.app_context():
    encerrado_em_primeiro = Agendamento.query.get(agendamento_id).encerrado_em

r = client.post(f"/equipe/agenda/{agendamento_id}/encerrar-rapido", follow_redirects=True)
checar("Segundo POST (idempotente) ainda responde 200", r.status_code == 200)
with app.app_context():
    checar(
        "encerrado_em não muda num segundo clique",
        Agendamento.query.get(agendamento_id).encerrado_em == encerrado_em_primeiro,
    )

r = client.get("/equipe/medico-agenda")
html = r.get_data(as_text=True)
checar("Botão 'Encerrar' some da lista depois de encerrado", "encerrar-rapido" not in html)
client.get("/logout")

# Médico de outra clínica (grupo diferente) não consegue encerrar um
# agendamento fora do próprio escopo.
with app.app_context():
    hemograma = Exame.query.filter_by(nome="Hemograma").first()
    agendamento_sp = Agendamento.query.filter(Agendamento.exame_id != colonoscopia.id).first()

login("medico@gruposaude.com", "123456")
r = client.post(f"/equipe/agenda/{agendamento_id}/encerrar-rapido")
checar("Médico de outro grupo recebe 404 ao tentar encerrar agendamento fora do escopo", r.status_code == 404)
client.get("/logout")

print("\nTodos os testes de test_atendimento_encerrar_rapido.py passaram.")
