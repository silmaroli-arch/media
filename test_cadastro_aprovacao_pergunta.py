"""Testa o novo campo do cadastro público (pedido do Silvan, 2026-09-25):
a pessoa já escolhe, no próprio cadastro, se as respostas de alimento/
medicamento/IA para o paciente exigem aprovação antes de ir pra ele
(equivalente a Usuario.aprovacao_perguntas_paciente, usado enquanto a
conta for solo) - antes só dava pra mudar depois, na tela "Perguntas
pendentes" (medico.perguntas_configuracao).

Cobre: (1) campo marcado (padrão do formulário) grava True; (2) campo
ausente do POST (equivalente a desmarcar o switch, já que checkbox
desmarcado não manda nada) grava False; (3) o texto explicativo aparece
na tela de cadastro.

Roda contra SQLite local, não toca no banco real. Para rodar:
    DATABASE_URL=sqlite:///teste_cadastro_aprovacao.db python test_cadastro_aprovacao_pergunta.py
"""
from app import create_app
from app.extensions import db
from app.db_utils import resetar_banco
from app.models import Usuario

app = create_app()
client = app.test_client()

with app.app_context():
    resetar_banco(db)


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


DADOS_BASE = {
    "papel": "medico",
    "crm_numero": "55555", "crm_uf": "ES",
    "data_nascimento": "10/05/1980",
    "telefone": "(27) 99999-0001",
    "cep": "29900-000",
    "rua": "Rua Teste", "numero": "100", "bairro": "Centro", "cidade": "Linhares", "uf": "ES",
    "senha": "123456", "senha_confirmacao": "123456",
}

# ---------- O texto explicativo aparece na tela de cadastro ----------
r_tela = client.get("/cadastro")
checar("Tela de cadastro responde 200", r_tela.status_code == 200)
html_tela = r_tela.get_data(as_text=True)
checar(
    "Explicação sobre aprovação de perguntas aparece no cadastro",
    "Exigir minha aprovação antes de responder o paciente" in html_tela,
)
checar(
    "O switch nasce marcado (padrão = exigir aprovação)",
    'name="aprovacao_perguntas_paciente" value="1" checked' in html_tela,
)

# ---------- 1. Campo marcado (padrão) grava True ----------
r1 = client.post("/cadastro", data={
    **DADOS_BASE,
    "nome": "Dr. Aprovação Padrão",
    "cpf": "111.222.333-99",
    "email": "aprovacao.padrao@example.com",
    "aprovacao_perguntas_paciente": "1",
}, follow_redirects=True)
checar("Cadastro com aprovação marcada responde 200", r1.status_code == 200)
with app.app_context():
    medico1 = Usuario.query.filter_by(email="aprovacao.padrao@example.com").first()
    checar("Médico foi criado", medico1 is not None)
    checar("aprovacao_perguntas_paciente nasceu True", medico1.aprovacao_perguntas_paciente is True)
client.get("/logout")

# ---------- 2. Campo ausente (switch desmarcado) grava False ----------
dados_sem_aprovacao = dict(DADOS_BASE)
r2 = client.post("/cadastro", data={
    **dados_sem_aprovacao,
    "nome": "Dr. Aprovação Desativada",
    "cpf": "111.222.333-27",
    "email": "aprovacao.desativada@example.com",
    # aprovacao_perguntas_paciente OMITIDO de propósito - simula o switch desmarcado
}, follow_redirects=True)
checar("Cadastro sem o campo (switch desmarcado) responde 200", r2.status_code == 200)
with app.app_context():
    medico2 = Usuario.query.filter_by(email="aprovacao.desativada@example.com").first()
    checar("Médico foi criado", medico2 is not None)
    checar("aprovacao_perguntas_paciente nasceu False", medico2.aprovacao_perguntas_paciente is False)
client.get("/logout")

print("\nTodos os testes do campo de aprovação no cadastro passaram.")
