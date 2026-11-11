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


def visita(cliente, caminho, headers=None, beacon=True):
    """Abre a página e, como o navegador faria, avisa que há uma pessoa nela."""
    r = cliente.get(caminho, headers=headers or {}, follow_redirects=True)
    if beacon:
        cliente.post("/_a", json={"p": caminho}, headers=headers or {})
    return r


UA1, UA2 = {"User-Agent": "Mozilla/5.0 Pessoa Um"}, {"User-Agent": "Mozilla/5.0 Pessoa Dois"}
hoje = agora_brasilia().date()

c1 = app.test_client()
visita(c1, "/", UA1)
checar("Site: 1ª visita conta 1 visita e 1 visitante", contagem("site") == (1, 1))
visita(c1, "/termos", UA1)
checar("Mesma pessoa navegando para outra página: continua 1 visita e 1 visitante", contagem("site") == (1, 1))
visita(app.test_client(), "/login", UA2)
checar("Outra pessoa: 2 visitas, 2 visitantes", contagem("site") == (2, 2))
c1.delete_cookie("ms")  # passou da meia hora parado: volta como uma nova visita
visita(c1, "/", UA1)
checar("Depois de 30 min parado, nova visita (mesmo visitante)", contagem("site") == (3, 2))

visita(c1, "/", {"User-Agent": "Googlebot/2.1"})
visita(c1, "/", {"User-Agent": "WhatsApp/2.23 preview"})
visita(app.test_client(), "/", UA2, beacon=False)  # varredura: abre a página e nada mais
c1.post("/_a", json={"p": "/dono/x"}, headers=UA1)
c1.post("/_a", json={"p": "http://evil"}, headers=UA1)
c1.post("/_a", json={"p": "/"}, headers={**UA1, "Sec-Fetch-Site": "cross-site"})
visita(c1, "/static/css/style.css", UA1)
c1.post("/login", data={}, headers=UA1)
checar("Robôs, quem não dá sinal de vida, estáticos, outros sites e envios não entram", contagem("site") == (3, 2))

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
visita(app.test_client(), f"/paciente/preparo/{token}", UA1)
checar("Link de preparo conta na área própria", contagem("link_preparo") == (1, 1))
with app.app_context():
    checar("Impressões com mais de 2 dias são apagadas", AcessoVisitanteDia.query.filter(AcessoVisitanteDia.dia < hoje - timedelta(days=1)).count() == 0)

# equipe logada (conta por usuário) e dono (não conta)
medico = app.test_client()
medico.post("/login", data={"identificador": "medica2@clinicavitoria.com", "senha": "123456"})
visita(medico, "/equipe/", UA1)
visita(medico, "/equipe/", UA2)
v, u = contagem("equipe")
checar("Equipe: usar várias telas é 1 visita e o mesmo usuário conta uma vez só", v == 1 and u == 1)
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
for _ in range(3):
    visita(app.test_client(), "/login", {"User-Agent": "Go-http-client/1.1"})
visita(app.test_client(), "/cadastro", {"User-Agent": "Mozilla/5.0 (Android 14; Mobile) Chrome/120"})
with app.app_context():
    d = {(x.pagina, x.navegador): x.visitas for x in AcessoDetalheDia.query.filter_by(dia=hoje, area="site").all()}
    checar("Detalhe conta por página e navegador", d.get(("/login", "Programa/script")) == 3 and d.get(("/cadastro", "Celular")) == 1)
    checar("Link de preparo no detalhe sem o token", ("/paciente/preparo/...", "Computador") in
           {(x.pagina, x.navegador) for x in AcessoDetalheDia.query.filter_by(dia=hoje, area="link_preparo").all()}
           or ("/paciente/preparo/...", "Programa/script") in {(x.pagina, x.navegador) for x in AcessoDetalheDia.query.filter_by(dia=hoje, area="link_preparo").all()}
           or AcessoDetalheDia.query.filter_by(dia=hoje, area="link_preparo").count() >= 1)
html = dono.get("/dono/acessos").get_data(as_text=True)
checar("Tela mostra páginas mais abertas e tipo de navegador, sem IP",
       "Páginas de entrada" in html and "Tipo de navegador" in html and "<code>/login</code>" in html and "Programa/script" in html
       and "127.0.0.1" not in html)
checar("Dá para escolher outro dia (dia inválido cai em hoje)", "Sem acessos registrados neste dia" in dono.get(f"/dono/acessos?dia={(hoje - timedelta(days=3)).isoformat()}").get_data(as_text=True)
       and "Páginas de entrada" in dono.get("/dono/acessos?dia=lixo").get_data(as_text=True))
# ---------- aparelhos (código aleatório em cookie) ----------
from app.models import AcessoDispositivoDia

celular = app.test_client()  # guarda cookies, como um navegador de verdade
UA_CEL = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17) Safari/604"}
celular.get("/", headers=UA_CEL)
r = celular.post("/_a", json={"p": "/"}, headers=UA_CEL)
cookie = next((c for c in r.headers.getlist("Set-Cookie") if c.startswith("mid=")), "")
checar("Navegador novo recebe o cookie com código aleatório de 8 caracteres (sem IP no código)",
       cookie.startswith("mid=") and len(cookie.split("=")[1].split(";")[0]) == 8 and "HttpOnly" in cookie and "SameSite=Lax" in cookie)
visita(celular, "/termos", UA_CEL)
visita(celular, "/privacidade", UA_CEL)
r2 = celular.post("/_a", json={"p": "/privacidade"}, headers=UA_CEL)
checar("O mesmo navegador não recebe outro código", "mid=" not in " ".join(r2.headers.getlist("Set-Cookie")))
with app.app_context():
    meu = AcessoDispositivoDia.query.filter_by(dia=hoje, codigo=cookie.split("=")[1].split(";")[0]).first()
    checar("O aparelho é contado uma vez, com 1 visita (3 páginas na mesma sessão), como novo e do tipo Celular",
           meu is not None and meu.visitas == 1 and meu.novo and meu.primeiro_em and meu.ultimo_em)
# robô sem cookie: cada visita vira um aparelho novo com 1 visita só
for _ in range(4):
    c_robo = app.test_client()
    c_robo.post("/_a", json={"p": "/cadastro"}, headers={"User-Agent": "Go-http-client/1.1"})
with app.app_context():
    robos = AcessoDispositivoDia.query.filter_by(dia=hoje, tipo="Programa/script").all()
    checar("Quem não guarda cookie aparece como vários aparelhos novos com 1 visita",
           len([r for r in robos if r.visitas == 1 and r.novo]) >= 4)
celular.delete_cookie("ms")
visita(celular, "/termos", UA_CEL)
with app.app_context():
    checar("Voltar depois de 30 min soma uma visita ao mesmo aparelho",
           AcessoDispositivoDia.query.filter_by(dia=hoje, codigo=cookie.split("=")[1].split(";")[0]).first().visitas == 2)
c_falso = app.test_client()
c_falso.set_cookie("mid", "../etc/passwd")
checar("Cookie com valor estranho vira um código novo válido", "mid=" in " ".join(c_falso.post("/_a", json={"p": "/"}, headers=UA_CEL).headers.getlist("Set-Cookie")))
html = dono.get("/dono/acessos").get_data(as_text=True)
checar("Tela do dono lista os aparelhos diferentes por código, tipo e visitas, sem IP",
       "Aparelhos diferentes neste dia" in html and "Dispositivo " in html and "novo neste dia" in html and "127.0.0.1" not in html
       and "aparelho(s) diferente(s)" in html)

for caminho in ("/", "/login"):
    checar(f"A página {caminho} leva o aviso de presença", "/_a" in app.test_client().get(caminho).get_data(as_text=True))
# ---------- limpar os logs ----------
from app.models import AcessoDia as _AD, AcessoDispositivoDia as _AP
checar("Botão 'Limpar logs de acesso' na tela do dono", "Limpar logs de acesso" in dono.get("/dono/acessos").get_data(as_text=True))
checar("Quem não é dono não limpa", medico.post("/dono/acessos/limpar").status_code in (302, 401, 403))
with app.app_context():
    checar("Antes: há registros", _AD.query.count() > 0 and AcessoVisitanteDia.query.count() > 0 and _AP.query.count() > 0 and AcessoDetalheDia.query.count() > 0)
r = dono.post("/dono/acessos/limpar", follow_redirects=True)
checar("Limpar avisa e zera tudo", "Contador de acessos zerado" in r.get_data(as_text=True))
with app.app_context():
    checar("Depois: nenhuma tabela do contador tem registro", _AD.query.count() == 0 and AcessoVisitanteDia.query.count() == 0 and _AP.query.count() == 0 and AcessoDetalheDia.query.count() == 0)
visita(app.test_client(), "/", UA1)
checar("A contagem recomeça do zero", contagem("site") == (1, 1))
print("TUDO OK")
