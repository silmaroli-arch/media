"""Testa o login com Google da equipe (pedido do Silvan, 2026-10-19 - ver
app/login_google.py): entrar com conta existente (pelo e-mail e depois
pelo "sub"), e-mail não verificado recusado, cadastro novo pelo Google sem
senha, confirmação pelo Google para "Meus dados"/"Criar senha", ligar e
desligar a conta Google em Meus dados, conta desativada.

As respostas do Google são simuladas (unittest.mock) - nenhuma chamada
de rede. Usa os dados do seed.py (Dr. Carlos, Ana Secretária)."""
import os
from unittest.mock import patch

os.environ["GOOGLE_CLIENT_ID"] = "id-de-teste.apps.googleusercontent.com"
os.environ["GOOGLE_CLIENT_SECRET"] = "segredo-de-teste"

from flask import redirect

from app import create_app
from app.extensions import db
from app.login_google import oauth
from app.models import Usuario

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def ida_e_volta_google(info, intencao="login", proximo=None):
    """Simula: clicar no botão (ida ao Google) e o Google devolver `info`."""
    url = f"/login/google?intencao={intencao}" + (f"&next={proximo}" if proximo else "")
    with patch.object(oauth.google, "authorize_redirect", return_value=redirect("/fake-google")):
        client.get(url)
    with patch.object(oauth.google, "authorize_access_token", return_value={"userinfo": info}):
        return client.get("/login/google/retorno", follow_redirects=True)


def logado_como():
    with client.session_transaction() as s:
        return s.get("_user_id")


def usuario(email):
    with app.app_context():
        return Usuario.query.filter_by(email=email).first()


# ---------- Botões aparecem ----------
html = client.get("/login").get_data(as_text=True)
checar("Login mostra 'Continuar com Google' (serve para entrar e para criar conta)",
       "Continuar com Google" in html and "Primeiro acesso?" in html and "Criar conta com e-mail e senha" in html)
html = client.get("/cadastro").get_data(as_text=True)
checar("Cadastro mostra 'Continuar com Google'", "Continuar com Google" in html)
checar("Cadastro com senha: o CPF continua indicado como login", "CPF (será seu login)" in html)

# ---------- 1. Conta existente, achada pelo e-mail ----------
carlos = usuario("medico@clinicavitoria.com")
r = ida_e_volta_google({"sub": "g-carlos", "email": "medico@clinicavitoria.com", "email_verified": True, "name": "Carlos"})
checar("Conta existente: entra pelo e-mail do Google", logado_como() == str(carlos.id))
checar("... e a conta fica ligada ao Google (sub gravado)", usuario("medico@clinicavitoria.com").google_sub == "g-carlos")
client.get("/logout")

# ---------- 2. Próxima vez entra pelo sub, mesmo com outro e-mail no Google ----------
ida_e_volta_google({"sub": "g-carlos", "email": "carlos.novo@gmail.com", "email_verified": True})
checar("Entra pelo sub mesmo se o e-mail do Google mudou", logado_como() == str(carlos.id))
client.get("/logout")

# ---------- 3. E-mail não verificado é recusado ----------
r = ida_e_volta_google({"sub": "g-x", "email": "medico@clinicavitoria.com", "email_verified": False})
checar("E-mail não verificado no Google: não entra", logado_como() is None and "verificado" in r.get_data(as_text=True))

# ---------- 4. Cadastro novo pelo Google (sem senha) ----------
r = ida_e_volta_google({"sub": "g-novo", "email": "dra.nova@gmail.com", "email_verified": True, "name": "Dra. Nova Google"})
html = r.get_data(as_text=True)
checar("Sem conta: vai para o cadastro", "Cadastro com a conta Google" in html and "dra.nova@gmail.com" in html)
checar("Cadastro com Google: nome preenchido e sem campos de senha",
       'value="Dra. Nova Google"' in html and 'type="password" name="senha"' not in html
       and 'name="senha_confirmacao"' not in html)
checar("Cadastro com Google: o CPF não promete ser o login (sem senha, entra pelo Google)",
       "CPF (será seu login)" not in html and "Você vai entrar pelo Google" in html)
r = client.post("/cadastro", data={
    "nome": "Dra. Nova Google", "papel": "medico", "cpf": "111.222.334-77",
    "crm_numero": "54321", "crm_uf": "ES", "data_nascimento": "10/05/1985",
    "email": "tentando.trocar@exemplo.com",  # ignorado: vale o e-mail do Google
    "telefone": "(27) 99999-1234", "cep": "29010-000", "rua": "Rua A", "numero": "1",
    "bairro": "Centro", "cidade": "Vitoria", "uf": "ES",
}, follow_redirects=True)
nova = usuario("dra.nova@gmail.com")
checar("Conta criada com o e-mail do Google (não o do formulário)", nova is not None and usuario("tentando.trocar@exemplo.com") is None)
checar("Conta nova: ligada ao Google e sem senha", nova.google_sub == "g-novo" and not nova.tem_senha)
checar("Cadastro com Google já entra no sistema", logado_como() == str(nova.id))

# ---------- 5. Sem senha: Meus dados e Criar senha pedem confirmação pelo Google ----------
html = client.get("/meus-dados").get_data(as_text=True)
checar("Meus dados (sem senha): pede 'Confirmar com Google', não senha",
       "Confirmar com Google" in html and 'name="senha_atual"' not in html)
html = client.get("/trocar-senha").get_data(as_text=True)
checar("Criar senha: antes pede confirmação pelo Google", "Criar senha" in html and "Confirmar com Google" in html)
r = client.post("/trocar-senha", data={"senha_nova": "nova123", "senha_confirmacao": "nova123"}, follow_redirects=True)
checar("Sem confirmar, não cria senha", not usuario("dra.nova@gmail.com").tem_senha)

r = ida_e_volta_google({"sub": "g-outra", "email": "outra@gmail.com", "email_verified": True}, intencao="confirmar")
checar("Confirmar com OUTRA conta Google é recusado", "não é a conta Google ligada" in r.get_data(as_text=True))

ida_e_volta_google({"sub": "g-novo", "email": "dra.nova@gmail.com", "email_verified": True}, intencao="confirmar", proximo="/trocar-senha")
html = client.get("/meus-dados").get_data(as_text=True)
checar("Depois de confirmar pelo Google, Meus dados libera a edição", 'id="campos-meus-dados" disabled' not in html)
r = client.post("/trocar-senha", data={"senha_nova": "nova123", "senha_confirmacao": "nova123"}, follow_redirects=True)
checar("Depois de confirmar, cria a senha sem pedir senha atual", usuario("dra.nova@gmail.com").checar_senha("nova123"))
client.get("/logout")
r = client.post("/login", data={"identificador": "111.222.334-77", "senha": "nova123"}, follow_redirects=True)
checar("Com a senha criada, entra também com CPF e senha", logado_como() == str(nova.id))
client.get("/logout")

# ---------- 6. Ligar e desligar a conta Google em Meus dados ----------
client.post("/login", data={"identificador": "secretaria@clinicavitoria.com", "senha": "123456"}, follow_redirects=True)
ana = usuario("secretaria@clinicavitoria.com")
checar("Secretária entrou com senha", logado_como() == str(ana.id))
r = ida_e_volta_google({"sub": "g-carlos", "email": "outro@gmail.com", "email_verified": True}, intencao="vincular")
checar("Não liga uma conta Google que já é de outro usuário", usuario("secretaria@clinicavitoria.com").google_sub is None)
ida_e_volta_google({"sub": "g-ana", "email": "ana.pessoal@gmail.com", "email_verified": True}, intencao="vincular")
checar("Liga a conta Google (e-mail diferente do cadastro)", usuario("secretaria@clinicavitoria.com").google_sub == "g-ana")
client.post("/conta/google/desvincular", follow_redirects=True)
checar("Com senha, consegue desligar a conta Google", usuario("secretaria@clinicavitoria.com").google_sub is None)
client.get("/logout")

with app.app_context():
    u = Usuario.query.filter_by(email="dra.nova@gmail.com").first()
    u.senha_hash = None  # volta a não ter senha
    db.session.commit()
ida_e_volta_google({"sub": "g-novo", "email": "dra.nova@gmail.com", "email_verified": True})
client.post("/conta/google/desvincular", follow_redirects=True)
checar("Sem senha, NÃO deixa desligar o Google (ficaria sem acesso)", usuario("dra.nova@gmail.com").google_sub == "g-novo")
client.get("/logout")

# ---------- 7. Conta desativada não entra ----------
with app.app_context():
    u = Usuario.query.filter_by(email="dra.nova@gmail.com").first()
    u.ativo = False
    db.session.commit()
r = ida_e_volta_google({"sub": "g-novo", "email": "dra.nova@gmail.com", "email_verified": True})
checar("Conta desativada não entra pelo Google", logado_como() is None and "desativada" in r.get_data(as_text=True))

# ---------- 8. Sem as chaves, o recurso some ----------
del os.environ["GOOGLE_CLIENT_ID"]
html = client.get("/login").get_data(as_text=True)
checar("Sem GOOGLE_CLIENT_ID, o botão não aparece", "Continuar com Google" not in html and "ou entre com CPF e senha" not in html)

print("\nTodos os testes do login com Google passaram.")
