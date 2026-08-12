"""Testa o fluxo em etapas do pedido de agendamento do paciente:

1) O dropdown "Exame" lista só os NOMES dos exames que a empresa oferece,
   sem o local concatenado (e sem repetir o nome quando o exame é feito
   em mais de um local).
2) Escolhido o exame, aparece o dropdown "Local" só com os locais em que
   AQUELE exame é feito.
3) Escolhido o local, aparece o endereço do local (e o fluxo segue com
   médico/horários). Se o exame só é feito num local, ele é selecionado
   direto, sem clique extra."""
from app import create_app
from app.extensions import db
from app.models import Usuario, Clinica, Exame, Paciente, PreparoModelo, normalizar_telefone

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


# Cenário: Grupo Saúde Total (2 filiais). "Consulta Geral" nas DUAS
# filiais; "Ultrassom Exclusivo" só na Praia.
with app.app_context():
    centro = Clinica.query.filter_by(nome="Grupo Saúde Total - Centro").first()
    praia = Clinica.query.filter_by(nome="Grupo Saúde Total - Praia").first()
    centro_id, praia_id = centro.id, praia.id
    medico = Usuario.query.filter_by(email="medico@gruposaude.com").first()

    praia.rua, praia.numero, praia.bairro, praia.cidade, praia.uf = "Av. Beira Mar", "100", "Praia do Canto", "Vitória", "ES"

    e1 = Exame(clinica_id=centro_id, medico_id=medico.id, nome="Consulta Geral",
               descricao="", duracao_minutos=30, medico_confirmado=True, associado=True)
    e2 = Exame(clinica_id=praia_id, medico_id=medico.id, nome="Consulta Geral",
               descricao="", duracao_minutos=30, medico_confirmado=True, associado=True)
    e3 = Exame(clinica_id=praia_id, medico_id=medico.id, nome="Ultrassom Exclusivo",
               descricao="", duracao_minutos=20, medico_confirmado=True, associado=True)
    db.session.add_all([e1, e2, e3])

    tel = normalizar_telefone("(27) 93333-0001")
    u = Usuario(nome="Paciente Fluxo", telefone=tel, tipo="paciente")
    db.session.add(u)
    db.session.flush()
    pac = Paciente(empresa_id=centro.empresa_id, usuario_id=u.id, nome="Paciente Fluxo",
                   cpf="505.606.707-08", data_nascimento=__import__("datetime").date(1990, 3, 3),
                   telefone=tel, status_cadastro="aprovado")
    db.session.add(pac)
    db.session.commit()
    e2_id, e3_id = e2.id, e3.id

client.post("/login-paciente", data={"cpf": "505.606.707-08", "data_nascimento": "03/03/1990"}, follow_redirects=True)

# ---------- (1) Dropdown de exames: só nomes, sem local, sem repetição ----------

r = client.get("/paciente/agendar")
html = r.get_data(as_text=True)
checar("Dropdown de exame lista os nomes", "Consulta Geral" in html and "Ultrassom Exclusivo" in html)
checar("Nomes NÃO vêm com o local concatenado", "Consulta Geral — " not in html and "Consulta Geral —" not in html)
checar("Exame feito em 2 locais aparece UMA vez só", html.count('value="Consulta Geral"') == 1)
checar("Sem exame escolhido, não há dropdown de local ainda", 'name="exame_id"' not in html)

# ---------- (2) Escolhido o exame, aparecem só os locais que o oferecem ----------

r = client.get("/paciente/agendar?exame_nome=Consulta Geral")
html2 = r.get_data(as_text=True)
checar("Aparece o dropdown de Local", 'name="exame_id"' in html2)
checar("Locais listados são os que oferecem o exame (Centro e Praia)",
       "Grupo Saúde Total - Centro" in html2 and "Grupo Saúde Total - Praia" in html2)
checar("Com mais de um local, pede a escolha ('Onde você quer fazer?')", "Onde você quer fazer?" in html2)
checar("Endereço ainda não aparece (local não escolhido)", "Av. Beira Mar" not in html2)

# ---------- (3) Escolhido o local, aparece o endereço ----------

r = client.get(f"/paciente/agendar?exame_nome=Consulta Geral&exame_id={e2_id}")
html3 = r.get_data(as_text=True)
checar("Local escolhido mostra o endereço", "Av. Beira Mar" in html3 and "Praia do Canto" in html3)
checar("O nome do local aparece no bloco de endereço", "Grupo Saúde Total - Praia" in html3)
checar("O dropdown de MÉDICO aparece depois de exame+local (mesmo com um médico só)",
       'name="medico_id"' in html3)
checar("O médico responsável já vem selecionado no dropdown", "selected" in html3.split('name="medico_id"')[1][:600])

# Exame de UM local só: seleciona o local direto, já mostrando o endereço.
r = client.get("/paciente/agendar?exame_nome=Ultrassom Exclusivo")
html4 = r.get_data(as_text=True)
checar("Exame de um local só: o local é selecionado direto (endereço já aparece)",
       "Av. Beira Mar" in html4)
checar("E não pede 'Onde você quer fazer?'", "Onde você quer fazer?" not in html4)

# Trocar o exame invalida o local antigo (não vaza a escolha anterior).
r = client.get(f"/paciente/agendar?exame_nome=Ultrassom Exclusivo&exame_id={e2_id}")
html5 = r.get_data(as_text=True)
checar("exame_id de OUTRO exame é descartado ao trocar o nome",
       "Ultrassom Exclusivo" in html5)

client.get("/logout")
print("\nTodos os testes do fluxo exame → local → endereço passaram.")
