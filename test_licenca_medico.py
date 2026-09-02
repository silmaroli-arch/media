"""Testa a Fatia 8 (licença individual) e a restruturação de 2026-09-02
pedida pelo Silvan: o médico tem que ter acesso a quando sua licença vence,
com um menu para isso (inclusive no app mobile - sem d-md-none/
oculto_no_celular_do_medico). A cobrança é POR MÉDICO, vale desde o
cadastro, independente de Grupo (decisão do Silvan).

Restruturação de 2026-09-02 (pedido do Silvan) coberta aqui:
1. Todo médico nasce em trial; ao vencer o número de dias configurado
   globalmente (PlataformaConfig.trial_dias, mesmo campo usado por Grupo),
   passa automaticamente para "ativa" - sem precisar de nenhum job
   agendado, a checagem roda a cada acesso autenticado (ver
   routes_medico.staff_required / Usuario.verificar_vencimento_licenca).
2. valor_licenca_mensal nasce a partir do valor padrão global
   (PlataformaConfig.valor_licenca_padrao), mas o dono continua podendo
   reajustar individualmente em /dono/usuarios.
3. O limite de meses pra aviso de inadimplência deixou de ser configurável
   por médico e virou global (PlataformaConfig.aviso_inadimplencia_meses,
   editado em /dono/configuracoes) - ao cruzar o limite, o status muda
   sozinho para "inadimplente" (só um aviso, não bloqueia o acesso) e volta
   pra "ativa" sozinho assim que o atraso é regularizado.
4. A única ação manual do dono sobre o status é bloquear/desbloquear o
   acesso (usuario_licenca_bloquear/usuario_licenca_desbloquear) - não
   existe mais um <select> de status nem um campo de vencimento editável
   à mão.
5. O painel do próprio médico ("Meu painel") mostra um selo com o status
   atual da licença (trial/Ativa/Bloqueada/Pendente de pagamento).

Roda contra SQLite local (não usa a DATABASE_URL do .env) e reseta esse
banco de teste no início — não toca no banco real. Para rodar:
    rm -f preparo_exames.db && python3 test_licenca_medico.py
"""
from datetime import date, timedelta

from app import create_app
from app.extensions import db
from app.db_utils import resetar_banco
from app.models import Usuario, PlataformaConfig, LicencaPagamento, meses_consecutivos_sem_pagar, _mes_anterior, garantir_meses_licenca

app = create_app()
client = app.test_client()

with app.app_context():
    resetar_banco(db)


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


# ---------- Cadastro público de médico já recebe vencimento de trial e o valor padrão global ----------
with app.app_context():
    PlataformaConfig.obter().valor_licenca_padrao = 190.00
    db.session.commit()

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
    checar("Valor mensal nasce a partir do padrão global", float(medica.valor_licenca_mensal) == 190.00)
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
    checar("Secretária não recebe valor mensal (não se aplica a ela)",
           secretaria.valor_licenca_mensal is None)

client.get("/logout")

# ---------- Tela "Minha licença" do médico ----------
client.post("/login", data={"identificador": "licenca.medica@example.com", "senha": "123456"})
r_licenca = client.get("/equipe/minha-licenca")
checar("Tela 'Minha licença' responde 200", r_licenca.status_code == 200)
html_licenca = r_licenca.get_data(as_text=True)
checar("Mostra o status (trial)", "Em teste" in html_licenca or "trial" in html_licenca.lower())

# O selo de status também aparece no painel principal ("Meu painel").
r_painel_medica = client.get("/equipe/")
checar("Painel do médico mostra o selo de status da licença (Em teste)", "Em teste" in r_painel_medica.get_data(as_text=True))

# O menu (base.html) precisa expor o link tanto no desktop quanto no mobile
# (pedido explícito do Silvan: "No app Mobile ele tem que ter um menu para
# isso") - por isso o item não pode levar d-md-none nem
# oculto_no_celular_do_medico.
html_painel = r_painel_medica.get_data(as_text=True)
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

# ---------- Trial vencido vira "ativa" automaticamente (restruturação de 2026-09-02) ----------
with app.app_context():
    medica = Usuario.query.get(medica_id)
    medica.licenca_vencimento = date.today() - timedelta(days=1)
    db.session.commit()

# A checagem roda a qualquer acesso autenticado (staff_required), não só
# na tela "Minha licença" - entrar direto no painel já é suficiente.
client.post("/login", data={"identificador": "licenca.medica@example.com", "senha": "123456"})
r_painel_venceu = client.get("/equipe/")
checar("Painel responde 200 com trial vencido", r_painel_venceu.status_code == 200)
with app.app_context():
    medica = Usuario.query.get(medica_id)
    checar("Trial vencido vira 'ativa' automaticamente (não mais 'inadimplente')", medica.licenca_status == "ativa")
checar("Selo do painel já mostra 'Ativa'", "Ativa" in r_painel_venceu.get_data(as_text=True))

client.get("/logout")

# ---------- Dono da plataforma: só pode ajustar o valor mensal e bloquear/desbloquear ----------
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
checar("Não existe mais seletor de status manual (trial/ativa/inadimplente/bloqueada)",
       'name="licenca_status"' not in r_usuarios.get_data(as_text=True))
checar("Não existe mais campo de vencimento editável à mão",
       'name="licenca_vencimento"' not in r_usuarios.get_data(as_text=True))
checar("Botão de bloquear acesso aparece", "Bloquear acesso" in r_usuarios.get_data(as_text=True))

r_editar = client.post(f"/dono/usuarios/{medica_id}/licenca", data={
    "valor_licenca_mensal": "175,50",
}, follow_redirects=True)
checar("Edição de valor mensal pelo dono responde 200", r_editar.status_code == 200)

with app.app_context():
    medica = Usuario.query.get(medica_id)
    checar("Dono conseguiu mudar o valor mensal individualmente", float(medica.valor_licenca_mensal) == 175.50)

checar("Valor mensal aparece formatado na lista do dono", "R$ 175.50/mês" in r_editar.get_data(as_text=True))

# Limpar o valor (deixar em branco) precisa voltar a NULL, não travar num
# valor antigo.
r_limpar = client.post(f"/dono/usuarios/{medica_id}/licenca", data={"valor_licenca_mensal": ""}, follow_redirects=True)
checar("Limpar o valor mensal responde 200", r_limpar.status_code == 200)
with app.app_context():
    medica = Usuario.query.get(medica_id)
    checar("Valor mensal voltou a ficar em branco (None)", medica.valor_licenca_mensal is None)

with app.app_context():
    secretaria = Usuario.query.filter_by(email="secretaria.licenca@example.com").first()
    r_404 = client.post(f"/dono/usuarios/{secretaria.id}/licenca", data={"valor_licenca_mensal": "100"})
    checar("Editar licença de uma secretária (não se aplica) dá 404", r_404.status_code == 404)

# ---------- Bloquear/desbloquear é a única ação manual sobre o status ----------
r_bloquear = client.post(f"/dono/usuarios/{medica_id}/licenca/bloquear", follow_redirects=True)
checar("Bloquear responde 200", r_bloquear.status_code == 200)
with app.app_context():
    medica = Usuario.query.get(medica_id)
    checar("Status virou 'bloqueada'", medica.licenca_status == "bloqueada")
checar("Badge 'bloqueada' aparece na lista", "bloqueada" in r_bloquear.get_data(as_text=True))
checar("Botão vira 'Desbloquear acesso'", "Desbloquear acesso" in r_bloquear.get_data(as_text=True))

with app.app_context():
    secretaria = Usuario.query.filter_by(email="secretaria.licenca@example.com").first()
    r_bloquear_sec = client.post(f"/dono/usuarios/{secretaria.id}/licenca/bloquear")
    checar("Bloquear secretária (não se aplica) dá 404", r_bloquear_sec.status_code == 404)

# Enquanto bloqueada, a checagem automática de trial/inadimplência NUNCA
# tira o médico do bloqueio sozinha - só o dono desbloqueando.
with app.app_context():
    medica = Usuario.query.get(medica_id)
    mudou = medica.verificar_vencimento_licenca()
    checar("verificar_vencimento_licenca() não mexe em quem está bloqueada", medica.licenca_status == "bloqueada" and mudou is False)

r_desbloquear = client.post(f"/dono/usuarios/{medica_id}/licenca/desbloquear", follow_redirects=True)
checar("Desbloquear responde 200", r_desbloquear.status_code == 200)
with app.app_context():
    medica = Usuario.query.get(medica_id)
    checar("Status voltou a 'ativa'", medica.licenca_status == "ativa")

# ---------- Calendário de pagamento mensal (Fatia 8) - inalterado ----------
r_cal = client.get(f"/dono/usuarios/{medica_id}/licenca/pagamentos")
checar("Calendário de pagamento do dono responde 200", r_cal.status_code == 200)
html_cal = r_cal.get_data(as_text=True)
checar("Mês atual aparece como 'Não pago' por padrão", "Não pago" in html_cal)

with app.app_context():
    pagamento_mes_atual = LicencaPagamento.query.filter_by(
        usuario_id=medica_id, mes=date.today().replace(day=1)
    ).first()
    checar("Mês atual foi gerado automaticamente ao abrir a tela", pagamento_mes_atual is not None)
    pagamento_id = pagamento_mes_atual.id

r_marcar = client.post(
    f"/dono/usuarios/{medica_id}/licenca/pagamentos/{pagamento_id}/marcar",
    follow_redirects=True,
)
checar("Marcar mês como pago responde 200", r_marcar.status_code == 200)
checar("Tela volta mostrando 'Pago'", "Pago" in r_marcar.get_data(as_text=True))

client.get("/logout")

# ---------- Aviso automático de inadimplência: agora é GLOBAL, não por médico ----------
client.post("/login", data={"identificador": dono_email, "senha": "123456"})

with app.app_context():
    checar("Limite de aviso global nasce com o padrão de 2 meses", PlataformaConfig.obter().aviso_inadimplencia_meses == 2)
    checar("Médica em dia não conta meses seguidos sem pagar (mês atual foi marcado pago acima)",
           meses_consecutivos_sem_pagar(Usuario.query.get(medica_id)) == 0)

# O dono edita o limite de aviso pela tela de Configurações (global agora,
# não mais por médico) - Usuario não tem mais esse campo.
with app.app_context():
    checar("Usuario não tem mais um campo aviso_inadimplencia_meses individual",
           not hasattr(Usuario, "aviso_inadimplencia_meses"))

r_config_aviso = client.post("/dono/configuracoes/licenca-medico", data={
    "aviso_inadimplencia_meses": "1",
    "valor_licenca_padrao": "190,00",
}, follow_redirects=True)
checar("Editar o limite de aviso global responde 200", r_config_aviso.status_code == 200)
with app.app_context():
    checar("Limite de aviso global foi atualizado para 1 mês", PlataformaConfig.obter().aviso_inadimplencia_meses == 1)

# Valor inválido (zero) é rejeitado com mensagem clara, sem mudar o que já
# estava salvo.
r_config_invalido = client.post("/dono/configuracoes/licenca-medico", data={
    "aviso_inadimplencia_meses": "0",
    "valor_licenca_padrao": "190,00",
}, follow_redirects=True)
checar("Limite de aviso inválido (zero) responde 200 com aviso", r_config_invalido.status_code == 200)
checar("Mensagem de erro aparece na tela", "maior que zero" in r_config_invalido.get_data(as_text=True).lower())
with app.app_context():
    checar("Limite de aviso global não mudou com valor inválido", PlataformaConfig.obter().aviso_inadimplencia_meses == 1)

# Cria um médico com vários meses seguidos sem pagar, pra cruzar o novo
# limite global (1 mês) e disparar o destaque em /dono/usuarios.
with app.app_context():
    inadimplente = Usuario(nome="Dr. Sempre Atrasado", email="inadimplente.licenca@example.com", tipo="medico")
    inadimplente.set_senha("123456")
    inadimplente.licenca_status = "ativa"
    db.session.add(inadimplente)
    db.session.commit()
    garantir_meses_licenca(inadimplente)
    mes_atual = date.today().replace(day=1)
    mes_passado = _mes_anterior(mes_atual)
    db.session.add(LicencaPagamento(usuario_id=inadimplente.id, mes=mes_passado, pago=False))
    db.session.commit()
    inadimplente_id = inadimplente.id

    checar("Um mês seguido sem pagar já cruza o novo limite global de 1",
           meses_consecutivos_sem_pagar(Usuario.query.get(inadimplente_id)) >= 1)

r_usuarios_alerta = client.get("/dono/usuarios")
checar("Tela dono/usuarios responde 200 com médico em alerta", r_usuarios_alerta.status_code == 200)
html_alerta = r_usuarios_alerta.get_data(as_text=True)
checar("Banner de alerta de inadimplência aparece na tela", "sem pagar há tempo demais" in html_alerta)
checar("Nome do médico em alerta aparece no banner", "Dr. Sempre Atrasado" in html_alerta)
checar("Linha do médico em alerta é destacada (table-danger)", "table-danger" in html_alerta)

client.get("/logout")

# ---------- verificar_vencimento_licenca(): ativa -> inadimplente automático, e de volta ----------
client.post("/login", data={"identificador": "inadimplente.licenca@example.com", "senha": "123456"})
r_painel_inadimplente = client.get("/equipe/")
checar("Painel do médico inadimplente responde 200 (acesso não é bloqueado)", r_painel_inadimplente.status_code == 200)
with app.app_context():
    inadimplente = Usuario.query.get(inadimplente_id)
    checar("Status mudou sozinho para 'inadimplente' ao cruzar o limite global", inadimplente.licenca_status == "inadimplente")
checar("Selo do painel mostra 'Pendente de pagamento'", "Pendente de pagamento" in r_painel_inadimplente.get_data(as_text=True))

# Regulariza o atraso (marca os meses como pagos) e confirma que volta pra
# "ativa" sozinho no próximo acesso.
with app.app_context():
    LicencaPagamento.query.filter_by(usuario_id=inadimplente_id).update({"pago": True})
    db.session.commit()

r_painel_regularizado = client.get("/equipe/")
checar("Painel responde 200 depois de regularizar", r_painel_regularizado.status_code == 200)
with app.app_context():
    inadimplente = Usuario.query.get(inadimplente_id)
    checar("Status voltou sozinho para 'ativa' ao regularizar o atraso", inadimplente.licenca_status == "ativa")

client.get("/logout")

print("\nTodos os testes de licença individual passaram.")
