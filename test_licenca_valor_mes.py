"""Alterar o valor de um mês ainda não pago (pedido do Silvan, 2026-10-27)."""
from app import create_app, db
from app.models import LicencaPagamento, Usuario

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False


def checar(nome, condicao):
    print(f"[{'OK' if condicao else 'FALHOU'}] {nome}")
    assert condicao, nome


with app.app_context():
    dono_email = Usuario.query.filter_by(tipo="dono").first().email
    medico = Usuario.query.filter_by(email="medico@clinicavitoria.com").first()
    medico_id = medico.id

from datetime import date
with app.app_context():
    db.session.add(LicencaPagamento(usuario_id=medico_id, mes=date(2027, 3, 1), pago=False, valor=150))
    db.session.commit()

dono = app.test_client()
dono.post("/login", data={"identificador": dono_email, "senha": "123456"})
html = dono.get(f"/dono/usuarios/{medico_id}/licenca/pagamentos").get_data(as_text=True)
checar("Calendário mostra o campo para alterar o valor de meses não pagos", 'name="valor"' in html and "Alterar" in html)
with app.app_context():
    mes = LicencaPagamento.query.filter_by(usuario_id=medico_id, pago=False, mes=date(2027, 3, 1)).first()
    mes.valor = 150
    mes.mp_preference_id, mes.mp_status, mes.mp_init_point = "pref-1", "pending", "https://mp/link"
    mes.pix_qr_code, mes.pix_payment_id = "000201...", "999"
    db.session.commit()
    mes_id = mes.id

url = f"/dono/usuarios/{medico_id}/licenca/pagamentos/{mes_id}/valor"
r = dono.post(url, data={"valor": "1,00"}, follow_redirects=True)
with app.app_context():
    m = db.session.get(LicencaPagamento, mes_id)
    checar("Altera o valor do mês (aceita vírgula)", float(m.valor) == 1.0 and "alterado para R$ 1.00" in r.get_data(as_text=True))
    checar("A cobrança antiga (link/Pix) é apagada", m.mp_preference_id is None and m.mp_init_point is None and m.mp_status is None
           and m.pix_qr_code is None and m.pix_payment_id is None)
for ruim in ("0", "-5", "abc", ""):
    r = dono.post(url, data={"valor": ruim}, follow_redirects=True)
    with app.app_context():
        checar(f"Valor inválido '{ruim}' é recusado", "Informe um valor válido" in r.get_data(as_text=True) and float(db.session.get(LicencaPagamento, mes_id).valor) == 1.0)
with app.app_context():
    m = db.session.get(LicencaPagamento, mes_id)
    m.pago = True
    db.session.commit()
r = dono.post(url, data={"valor": "99"}, follow_redirects=True)
with app.app_context():
    checar("Mês já pago não pode mudar de valor", "já está pago" in r.get_data(as_text=True) and float(db.session.get(LicencaPagamento, mes_id).valor) == 1.0)
medico = app.test_client()
medico.post("/login", data={"identificador": "medico@clinicavitoria.com", "senha": "123456"})
checar("Médico não consegue alterar o valor", medico.post(url, data={"valor": "1"}).status_code in (302, 403, 404))
with app.app_context():
    checar("... e o valor continua o mesmo", float(db.session.get(LicencaPagamento, mes_id).valor) == 1.0)
print("TUDO OK")
