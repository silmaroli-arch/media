"""Programa de testadores: cadastro limitado e 1º mês grátis (2026-11-10)."""
from datetime import date, timedelta

from app import create_app
from app.extensions import db
from app.models import PlataformaConfig, Usuario

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False


def checar(nome, condicao):
    print(f"[{'OK' if condicao else 'FALHOU'}] {nome}")
    assert condicao, nome


def cadastrar(i, cpf, papel="medico"):
    c = app.test_client()
    dados = {"senha_confirmacao": "123456", "telefone": "(27) 99999-12%02d" % i, "nome": f"Tester {i}",
             "papel": papel, "cpf": cpf, "email": f"tester{i}@example.com", "senha": "123456"}
    if papel == "medico":
        dados.update({"crm_numero": str(1000 + i), "crm_uf": "ES", "data_nascimento": "10/05/1980"})
    return c.post("/cadastro", data=dados, follow_redirects=True)


CPFS = ["111.444.777-35", "529.982.247-25", "168.995.350-09"]

with app.app_context():
    config = PlataformaConfig.obter()
    checar("Padrão: programa desligado", not config.tester_ativo and config.tester_vagas == 10 and config.tester_dias == 30)

# Desligado: cadastro normal, sem vaga nem banner
checar("Desligado: sem banner", "Programa de testadores" not in app.test_client().get("/cadastro?metodo=senha").get_data(as_text=True))
cadastrar(0, CPFS[0])
with app.app_context():
    u = Usuario.query.filter_by(email="tester0@example.com").first()
    checar("Desligado: não conta vaga", u and not u.tester)

# Dono liga: 2 vagas, 30 dias
with app.app_context():
    dono = Usuario(nome="Dono T", email="donot@example.com", tipo="dono"); dono.set_senha("123456")
    db.session.add(dono); db.session.commit()
d = app.test_client(); d.post("/login", data={"identificador": "donot@example.com", "senha": "123456"})
r = d.post("/dono/configuracoes/tester", data={"tester_ativo": "on", "tester_vagas": "2", "tester_dias": "30"}, follow_redirects=True)
checar("Dono liga o programa", "Programa de testadores ligado" in r.get_data(as_text=True))
checar("Painel do dono mostra vagas", "Vagas usadas: <strong>0 de 2</strong>" in r.get_data(as_text=True))
comum = app.test_client()
checar("Quem não é dono não configura", comum.post("/dono/configuracoes/tester", data={"tester_vagas": "5", "tester_dias": "5"}).status_code in (302, 401, 403))

html = app.test_client().get("/cadastro?metodo=senha").get_data(as_text=True)
checar("Banner com vagas restantes", "Programa de testadores" in html and "30 dias grátis</strong>" in html and "Restam <strong>2</strong>" in html)

cadastrar(1, CPFS[1])
with app.app_context():
    u = Usuario.query.filter_by(email="tester1@example.com").first()
    checar("Médico entra como tester com 30 dias grátis", u.tester and u.licenca_vencimento == date.today() + timedelta(days=30))
cadastrar(2, CPFS[2], papel="secretaria")
with app.app_context():
    checar("Secretária também ocupa vaga", Usuario.query.filter_by(email="tester2@example.com").first().tester)

# Esgotado
html = app.test_client().get("/cadastro").get_data(as_text=True)
checar("Esgotado: mostra aviso de vagas esgotadas", "Vagas de testadores esgotadas" in html)
r = cadastrar(3, "390.533.447-05")
with app.app_context():
    checar("Esgotado: não cria conta", Usuario.query.filter_by(email="tester3@example.com").first() is None)

# Desligar reabre
d.post("/dono/configuracoes/tester", data={"tester_vagas": "2", "tester_dias": "30"})
checar("Desligado de novo: cadastro reabre", "Vagas de testadores esgotadas" not in app.test_client().get("/cadastro?metodo=senha").get_data(as_text=True))
print("\nTodos os testes do programa de testadores passaram.")
