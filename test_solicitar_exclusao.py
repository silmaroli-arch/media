"""Pedido de exclusão da própria conta (pedido do Silvan, 2026-10-21): o
médico/secretária pede em Meus dados, com o motivo; o dono vê na área do
dono e exclui (ou descarta)."""
from app import create_app, db
from app.models import SolicitacaoExclusao, Usuario

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


with app.app_context():
    medico = Usuario(nome="Dr. Vai Embora", email="embora@teste.com", tipo="medico", cpf="529.982.247-25")
    medico.set_senha("123456")
    db.session.add(medico)
    dono = Usuario.query.filter_by(tipo="dono").first()
    if not dono:
        dono = Usuario(nome="Dono", email="dono@plataforma.com", tipo="dono")
        db.session.add(dono)
    dono.set_senha("senha-dono")
    db.session.commit()
    medico_id, dono_email = medico.id, dono.email

m = app.test_client()
m.post("/login", data={"identificador": "embora@teste.com", "senha": "123456"})
m.post("/meus-dados", data={"acao": "confirmar_senha", "senha_atual": "123456"})
html = m.get("/meus-dados").get_data(as_text=True)
checar("Meus dados tem 'Solicitar exclusão da conta' e a janela com o motivo",
       'data-bs-target="#modal-solicitar-exclusao"' in html and 'name="motivo"' in html)
m.post("/meus-dados/solicitar-exclusao", data={"motivo": ""})
with app.app_context():
    checar("Sem motivo: não registra", SolicitacaoExclusao.query.count() == 0)
m.post("/meus-dados/solicitar-exclusao", data={"motivo": "Não uso mais a plataforma."})
m.post("/meus-dados/solicitar-exclusao", data={"motivo": "De novo."})
with app.app_context():
    checar("Com motivo: registra um pedido pendente (sem duplicar)",
           SolicitacaoExclusao.query.filter_by(usuario_id=medico_id, status="pendente").count() == 1)
html = m.get("/meus-dados").get_data(as_text=True)
checar("Meus dados mostra o pedido aguardando, com o motivo", "aguardando o administrador" in html and "Não uso mais a plataforma." in html)
m.post("/meus-dados/cancelar-exclusao")
with app.app_context():
    checar("Cancelar: o pedido fica cancelado", SolicitacaoExclusao.query.filter_by(status="cancelada").count() == 1)

d = app.test_client()
d.post("/login", data={"identificador": dono_email, "senha": "senha-dono"})
m.post("/meus-dados/solicitar-exclusao", data={"motivo": "Mudei de clínica."})
html = d.get("/dono/").get_data(as_text=True)
checar("Área do dono: aba Usuários com o número de pedidos e a lista com o motivo",
       'title="Pedidos de exclusão de conta">1<' in html and "Mudei de clínica." in html and "Dr. Vai Embora" in html)
with app.app_context():
    pedido_id = SolicitacaoExclusao.query.filter_by(status="pendente").first().id
d.post(f"/dono/solicitacoes-exclusao/{pedido_id}/descartar")
with app.app_context():
    checar("Descartar: a conta continua e o pedido sai da lista",
           SolicitacaoExclusao.query.get(pedido_id).status == "descartada" and Usuario.query.get(medico_id) is not None)

m.post("/meus-dados/solicitar-exclusao", data={"motivo": "Agora é sério."})
d.post(f"/dono/usuarios/{medico_id}/excluir", data={"senha_confirmacao": "senha-dono"})
with app.app_context():
    pedido = SolicitacaoExclusao.query.filter_by(motivo="Agora é sério.").first()
    checar("Dono exclui: a conta some e o pedido fica concluído, com nome e motivo",
           Usuario.query.get(medico_id) is None and pedido.status == "concluida"
           and pedido.usuario_id is None and pedido.nome == "Dr. Vai Embora")

p = app.test_client()
p.post("/login", data={"identificador": dono_email, "senha": "senha-dono"})
checar("Conta de dono não pede exclusão por aqui", p.post("/meus-dados/solicitar-exclusao", data={"motivo": "x"}).status_code == 404)

print("\nTodos os testes de pedido de exclusão passaram.")
