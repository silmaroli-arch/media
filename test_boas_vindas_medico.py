"""Testa a boas-vindas do MEDICO recem-cadastrado (2026-09-30): popup no
primeiro acesso e as mesmas mensagens no sininho.

Sem rede. Banco recriado, sem seed:
    DATABASE_URL=sqlite:///teste_boas_vindas.db python test_boas_vindas_medico.py
"""
from app import create_app
from app.extensions import db
from app.db_utils import resetar_banco
from app.models import Usuario, Notificacao

app = create_app()
client = app.test_client()

with app.app_context():
    resetar_banco(db)
    db.session.commit()


def checar(nome, condicao):
    print(f"[{'OK' if condicao else 'FALHOU'}] {nome}")
    assert condicao, nome


dados = {
    "nome": "Dra. Boas Vindas", "papel": "medico", "cpf": "111.222.333-97", "crm_numero": "12345", "crm_uf": "ES",
    "data_nascimento": "10/05/1980", "email": "boasvindas@example.com", "senha": "123456", "senha_confirmacao": "123456",
    "telefone": "(27) 99999-1234", "cep": "29010-000", "rua": "Rua A", "numero": "1", "bairro": "Centro",
    "cidade": "Vitoria", "uf": "ES",
}
r = client.post("/cadastro", data=dados, follow_redirects=True)
html = r.get_data(as_text=True)
checar("Cadastro do medico funcionou", r.status_code == 200 and "modalBoasVindas" in html)
checar("Popup cita o sininho", "sininho" in html)
r2 = client.get("/equipe/", follow_redirects=True)
checar("Popup aparece so uma vez", "modalBoasVindas" not in r2.get_data(as_text=True))

with app.app_context():
    u = Usuario.query.filter_by(email="boasvindas@example.com").first()
    ns = Notificacao.query.filter_by(usuario_id=u.id, tipo="boas_vindas").order_by(Notificacao.criado_em.desc()).all()
    checar("Duas notificacoes criadas", len(ns) == 2)
    checar("Boas-vindas por cima e cita o sininho", "sininho" in ns[0].mensagem and ns[0].titulo.startswith("Bem-vindo"))
    checar("Passo a passo aponta para Primeiros passos", ns[1].link_endpoint == "medico.primeiros_passos")
checar("Notificacao aparece no sininho", "Primeiros passos no MedIA" in r2.get_data(as_text=True))

# Secretaria nao recebe o popup
client.get("/logout")
d2 = dict(dados, nome="Sec Teste", papel="secretaria", cpf="529.982.247-25", email="sec@example.com")
d2.pop("crm_numero"); d2.pop("crm_uf"); d2.pop("data_nascimento")
r3 = client.post("/cadastro", data=d2, follow_redirects=True)
checar("Secretaria nao ve o popup de medico", "modalBoasVindas" not in r3.get_data(as_text=True))
print("\nTodos os testes passaram.")
