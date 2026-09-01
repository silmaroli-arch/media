"""Testa a Fatia 8 (licença individual): o médico tem que ter acesso a
quando sua licença vence, com um menu para isso (inclusive no app mobile -
sem d-md-none/oculto_no_celular_do_medico). A cobrança é POR MÉDICO, vale
desde o cadastro, independente de Grupo (decisão do Silvan) - por enquanto
é só informativo (vencer não bloqueia o acesso).

Roda contra SQLite local (não usa a DATABASE_URL do .env) e reseta esse
banco de teste no início — não toca no banco real. Para rodar:
    DATABASE_URL=sqlite:///teste_licenca.db python test_licenca_medico.py
"""
from datetime import date, timedelta

from app import create_app
from app.extensions import db
from app.db_utils import resetar_banco
from app.models import Usuario, PlataformaConfig

app = create_app()
client = app.test_client()

with app.app_context():
    resetar_banco(db)


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


# ---------- Cadastro público de médico já recebe vencimento de trial ----------
r = client.post("/cadastro", data={
    "nome": "Dra. Licença Teste",
    "papel": "medico",
    "cpf": "123.456.789-09", "crm_numero": "11111", "crm_uf": "ES",
    "email": "licenca.medica@example.com",
    "senha": "123456",
}, follow_redirects=True)
checar("Cadastro responde 200", r.status_code == 200)

with app.app_context():
    medica = Usuario.query.filter_by(email="licenca.medica@example.com").first()
    checar("Usuário foi criado", medica is not None)
    checar("Licença nasce em trial", medica.licenca_status == "trial")
    trial_dias = PlataformaConfig.obter().trial_dias
    checar(
        "Vencimento é hoje + trial_dias configurado",
        medica.licenca_vencimento == date.today() + timedelta(days=trial_dias),
    )
    medica_id = medica.id

# ---------- Cadastro de secretária NÃO recebe licença individual ----------
client.get("/logout")
r_sec = client.post("/cadastro", data={
    "nome": "Secretária Teste",
    "papel": "secretaria",
    "cpf": "987.654.321-00",
    "email": "secretaria.licenca@example.com",
    "senha": "123456",
}, follow_redirects=True)
checar("Cadastro de secretária responde 200", r_sec.status_code == 200)

with app.app_context():
    secretaria = Usuario.query.filter_by(email="secretaria.licenca@example.com").first()
    checar("Secretária não recebe vencimento de licença (não se aplica a ela)",
           secretaria.licenca_vencimento is None)

client.get("/logout")

# ---------- Tela "Minha licença" do médico ----------
client.post("/login", data={"identificador": "licenca.medica@example.com", "senha": "123456"})
r_licenca = client.get("/equipe/minha-licenca")
checar("Tela 'Minha licença' responde 200", r_licenca.status_code == 200)
html_licenca = r_licenca.get_data(as_text=True)
checar("Mostra o status (trial)", "Em teste" in html_licenca or "trial" in html_licenca.lower())
checar("É só informativo, não fala em bloqueio", "bloqueia" in html_licenca.lower())

# O menu (base.html) precisa expor o link tanto no desktop quanto no mobile
# (pedido explícito do Silvan: "No app Mobile ele tem que ter um menu para
# isso") - por isso o item não pode levar d-md-none nem
# oculto_no_celular_do_medico.
r_painel = client.get("/equipe/")
html_painel = r_painel.get_data(as_text=True)
checar("Painel do médico responde 200", r_painel.status_code == 200)
checar("Menu tem o link 'Minha licença'", "Minha licença" in html_painel)
checar("Link de 'Minha licença' não está escondido no mobile (sem d-md-none)",
       'href="/equipe/minha-licenca"' in html_painel and 'd-md-none">\n        <a class="nav-link {% if request.endpoint ==' not in html_painel)

client.get("/logout")

# ---------- Secretária não vê nem acessa a tela (não tem licença individual) ----------
client.post("/login", data={"identificador": "secretaria.licenca@example.com", "senha": "123456"})
r_painel_sec = client.get("/equipe/")
checar("Painel da secretária não mostra 'Minha licença'", "Minha licença" not in r_painel_sec.get_data(as_text=True))
r_licenca_sec = client.get("/equipe/minha-licenca", follow_redirects=True)
checar("Secretária é redirecionada para fora da tela de licença", "Minha licença" not in r_licenca_sec.get_data(as_text=True) or "não" in r_licenca_sec.get_data(as_text=True).lower())

client.get("/logout")

# ---------- verificar_vencimento_licenca(): trial vencido vira inadimplente, só informativo ----------
with app.app_context():
    medica = Usuario.query.get(medica_id)
    medica.licenca_vencimento = date.today() - timedelta(days=1)
    db.session.commit()

client.post("/login", data={"identificador": "licenca.medica@example.com", "senha": "123456"})
r_venceu = client.get("/equipe/minha-licenca")
checar("Tela ainda responde 200 mesmo com licença vencida", r_venceu.status_code == 200)
with app.app_context():
    medica = Usuario.query.get(medica_id)
    checar("Status virou 'inadimplente' automaticamente ao vencer", medica.licenca_status == "inadimplente")
r_painel_venceu = client.get("/equipe/")
checar("Acesso ao painel continua liberado mesmo com licença vencida (só informativo, sem bloqueio)",
       r_painel_venceu.status_code == 200)

client.get("/logout")

# ---------- Dono da plataforma edita a licença de um médico ----------
with app.app_context():
    dono = Usuario.query.filter_by(tipo="dono").first()
    if not dono:
        dono = Usuario(nome="Dono Teste", email="dono.teste@example.com", tipo="dono")
        dono.set_senha("123456")
        db.session.add(dono)
        db.session.commit()
    dono_email = dono.email

client.post("/login", data={"identificador": dono_email, "senha": "123456"})

r_usuarios = client.get("/dono/usuarios")
checar("Tela dono/usuarios responde 200", r_usuarios.status_code == 200)
checar("Coluna de licença aparece na lista", "Licença" in r_usuarios.get_data(as_text=True))

r_editar = client.post(f"/dono/usuarios/{medica_id}/licenca", data={
    "licenca_status": "ativa",
    "licenca_vencimento": (date.today() + timedelta(days=90)).isoformat(),
    "valor_licenca_mensal": "175,50",
}, follow_redirects=True)
checar("Edição de licença pelo dono responde 200", r_editar.status_code == 200)

with app.app_context():
    medica = Usuario.query.get(medica_id)
    checar("Dono conseguiu reativar a licença do médico", medica.licenca_status == "ativa")
    checar("Dono conseguiu mudar o vencimento", medica.licenca_vencimento == date.today() + timedelta(days=90))
    checar("Dono conseguiu definir o valor mensal (aceita vírgula decimal)", float(medica.valor_licenca_mensal) == 175.50)

checar("Valor mensal aparece formatado na lista do dono", "R$ 175.50/mês" in r_editar.get_data(as_text=True))

# Limpar o valor (deixar em branco) precisa voltar a NULL, não travar num
# valor antigo.
r_limpar = client.post(f"/dono/usuarios/{medica_id}/licenca", data={
    "licenca_status": "ativa",
    "licenca_vencimento": (date.today() + timedelta(days=90)).isoformat(),
    "valor_licenca_mensal": "",
}, follow_redirects=True)
checar("Limpar o valor mensal responde 200", r_limpar.status_code == 200)
with app.app_context():
    medica = Usuario.query.get(medica_id)
    checar("Valor mensal voltou a ficar em branco (None)", medica.valor_licenca_mensal is None)

with app.app_context():
    secretaria = Usuario.query.filter_by(email="secretaria.licenca@example.com").first()
    r_404 = client.post(f"/dono/usuarios/{secretaria.id}/licenca", data={"licenca_status": "ativa"})
    checar("Editar licença de uma secretária (não se aplica) dá 404", r_404.status_code == 404)

# ---------- Calendário de pagamento mensal (Fatia 8) ----------
r_cal = client.get(f"/dono/usuarios/{medica_id}/licenca/pagamentos")
checar("Calendário de pagamento do dono responde 200", r_cal.status_code == 200)
html_cal = r_cal.get_data(as_text=True)
checar("Mês atual aparece como 'Não pago' por padrão", "Não pago" in html_cal)

with app.app_context():
    from app.models import LicencaPagamento
    pagamento_mes_atual = LicencaPagamento.query.filter_by(
        usuario_id=medica_id, mes=date.today().replace(day=1)
    ).first()
    checar("Mês atual foi gerado automaticamente ao abrir a tela", pagamento_mes_atual is not None)
    checar("Mês atual nasce como não pago", pagamento_mes_atual.pago is False)
    pagamento_id = pagamento_mes_atual.id

r_marcar = client.post(
    f"/dono/usuarios/{medica_id}/licenca/pagamentos/{pagamento_id}/marcar",
    follow_redirects=True,
)
checar("Marcar mês como pago responde 200", r_marcar.status_code == 200)
checar("Tela volta mostrando 'Pago'", "Pago" in r_marcar.get_data(as_text=True))

with app.app_context():
    pagamento = LicencaPagamento.query.get(pagamento_id)
    checar("Mês foi marcado como pago no banco", pagamento.pago is True)
    checar("pago_em foi registrado", pagamento.pago_em is not None)

# Tentar marcar o pagamento de uma médica usando o id de OUTRA (usuario_id
# não bate com o dono do pagamento) tem que dar 404, não deixar vazar.
with app.app_context():
    secretaria = Usuario.query.filter_by(email="secretaria.licenca@example.com").first()
    r_cross = client.post(f"/dono/usuarios/{secretaria.id}/licenca/pagamentos/{pagamento_id}/marcar")
    checar("Marcar pagamento de outro usuário (id não bate) dá 404", r_cross.status_code == 404)

client.get("/logout")

# ---------- O médico vê o calendário na tela "Minha licença" ----------
client.post("/login", data={"identificador": "licenca.medica@example.com", "senha": "123456"})
r_minha = client.get("/equipe/minha-licenca")
checar("Tela do médico ainda responde 200 com o calendário", r_minha.status_code == 200)
html_minha = r_minha.get_data(as_text=True)
checar("Calendário de pagamento aparece na tela do médico", "Calendário de pagamento" in html_minha)
checar("Médico vê que o mês atual está pago (marcado pelo dono acima)", "Pago" in html_minha)

print("\nTodos os testes de licença individual passaram.")
