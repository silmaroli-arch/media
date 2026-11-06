"""Contador de acessos ao site e ao sistema (pedido do Silvan, 2026-11-06)."""
from datetime import timedelta

from app import create_app, db
from app.fuso import agora_brasilia
from app.models import AcessoDia, AcessoVisitanteDia, Agendamento, Usuario

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False


def checar(nome, condicao):
    print(f"[{'OK' if condicao else 'FALHOU'}] {nome}")
    assert condicao, nome


def contagem(area):
    with app.app_context():
        r = AcessoDia.query.filter_by(dia=agora_brasilia().date(), area=area).first()
        return (r.visitas, r.visitantes) if r else (0, 0)


UA1, UA2 = {"User-Agent": "Mozilla/5.0 Pessoa Um"}, {"User-Agent": "Mozilla/5.0 Pessoa Dois"}
hoje = agora_brasilia().date()

c1 = app.test_client()
c1.get("/", headers=UA1)
checar("Site: 1ª visita conta 1 visita e 1 visitante", contagem("site") == (1, 1))
c1.get("/termos", headers=UA1)
checar("Mesma pessoa, outra página: 2 visitas, 1 visitante", contagem("site") == (2, 1))
app.test_client().get("/login", headers=UA2)
checar("Outra pessoa: 3 visitas, 2 visitantes", contagem("site") == (3, 2))

c1.get("/", headers={"User-Agent": "Googlebot/2.1"})
c1.get("/", headers={"User-Agent": "WhatsApp/2.23 preview"})
c1.get("/static/css/style.css", headers=UA1)
c1.post("/login", data={}, headers=UA1)
checar("Robôs, arquivos estáticos e envios (POST) não entram", contagem("site") == (3, 2))

with app.app_context():
    chaves = [v.chave for v in AcessoVisitanteDia.query.all()]
    checar("Só hash no banco: nenhum IP nem texto do navegador, 32 caracteres",
           chaves and all(len(c) == 32 and "127.0.0.1" not in c and "Mozilla" not in c for c in chaves))

# link de preparo do WhatsApp
with app.app_context():
    ag = next(a for a in Agendamento.query.order_by(Agendamento.id).all() if a.exame and a.exame.preparo_modelo)
    token = ag.obter_token_preparo_publico()
    db.session.commit()
    # visitantes antigos (5 dias) que devem ser apagados
    db.session.add(AcessoVisitanteDia(dia=hoje - timedelta(days=5), area="link_preparo", chave="x" * 32))
    db.session.commit()
app.test_client().get(f"/paciente/preparo/{token}", headers=UA1)
checar("Link de preparo conta na área própria", contagem("link_preparo") == (1, 1))
with app.app_context():
    checar("Impressões com mais de 2 dias são apagadas", AcessoVisitanteDia.query.filter(AcessoVisitanteDia.dia < hoje - timedelta(days=1)).count() == 0)

# equipe logada (conta por usuário) e dono (não conta)
medico = app.test_client()
medico.post("/login", data={"identificador": "medica2@clinicavitoria.com", "senha": "123456"})
medico.get("/equipe/", headers=UA1, follow_redirects=True)
medico.get("/equipe/", headers=UA2, follow_redirects=True)
v, u = contagem("equipe")
checar("Equipe: visitas contadas e o mesmo usuário conta uma vez só", v >= 2 and u == 1)
with app.app_context():
    dono_email = Usuario.query.filter_by(tipo="dono").first().email
dono = app.test_client()
dono.post("/login", data={"identificador": dono_email, "senha": "123456"})
html = dono.get("/dono/acessos").get_data(as_text=True)
checar("Tela do dono mostra as áreas e o menu 'Acessos'", "Acessos ao site e ao sistema" in html and "Site público" in html
       and "Link de preparo (WhatsApp)" in html and "Acessos" in dono.get("/dono/").get_data(as_text=True))
checar("O dono e a própria tela de acessos não entram na conta", contagem("site") == (3, 2) and "/dono" not in str(contagem("equipe")))
checar("A tela mostra os números de hoje", "visitas hoje" in html)
# ---------- detalhe por página e tipo de navegador (sem IP) ----------
from app.acessos import normalizar_pagina, tipo_navegador
from app.models import AcessoDetalheDia

checar("Tipo de navegador: celular, computador, programa e vazio",
       tipo_navegador("Mozilla/5.0 (iPhone; CPU iPhone OS 17) Safari") == "Celular"
       and tipo_navegador("Mozilla/5.0 (Windows NT 10.0) Chrome/120") == "Computador"
       and tipo_navegador("Go-http-client/1.1") == "Programa/script" and tipo_navegador("") == "Sem identificação")
checar("O token do link de preparo não vai para o detalhe", normalizar_pagina("/paciente/preparo/abc123XYZ") == "/paciente/preparo/...")
c2 = app.test_client()
for _ in range(3):
    c2.get("/login", headers={"User-Agent": "Go-http-client/1.1"})
c2.get("/cadastro", headers={"User-Agent": "Mozilla/5.0 (Android 14; Mobile) Chrome/120"})
with app.app_context():
    d = {(x.pagina, x.navegador): x.visitas for x in AcessoDetalheDia.query.filter_by(dia=hoje, area="site").all()}
    checar("Detalhe conta por página e navegador", d.get(("/login", "Programa/script")) == 3 and d.get(("/cadastro", "Celular")) == 1)
    checar("Link de preparo no detalhe sem o token", ("/paciente/preparo/...", "Computador") in
           {(x.pagina, x.navegador) for x in AcessoDetalheDia.query.filter_by(dia=hoje, area="link_preparo").all()}
           or ("/paciente/preparo/...", "Programa/script") in {(x.pagina, x.navegador) for x in AcessoDetalheDia.query.filter_by(dia=hoje, area="link_preparo").all()}
           or AcessoDetalheDia.query.filter_by(dia=hoje, area="link_preparo").count() >= 1)
html = dono.get("/dono/acessos").get_data(as_text=True)
checar("Tela mostra páginas mais abertas e tipo de navegador, sem IP",
       "Páginas mais abertas" in html and "Tipo de navegador" in html and "<code>/login</code>" in html and "Programa/script" in html
       and "127.0.0.1" not in html)
checar("Dá para escolher outro dia (dia inválido cai em hoje)", "Sem acessos registrados neste dia" in dono.get(f"/dono/acessos?dia={(hoje - timedelta(days=3)).isoformat()}").get_data(as_text=True)
       and "Páginas mais abertas" in dono.get("/dono/acessos?dia=lixo").get_data(as_text=True))
print("TUDO OK")
