"""Aviso ao médico de pergunta nova: sininho + template do WhatsApp (2026-11-16)."""
from unittest.mock import patch

from app import create_app, db
from app.models import Exame, Grupo, Notificacao, Paciente, PerguntaPendente, Usuario
from app.push_notificacoes import notificar_equipe_nova_pergunta

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False


def checar(nome, condicao):
    print(f"[{'OK' if condicao else 'FALHOU'}] {nome}")
    assert condicao, nome


with app.app_context():
    grupo = Grupo.query.filter_by(nome="Clínica Vitória").first()
    exame = Exame.query.filter_by(grupo_id=grupo.id).first()
    paciente = Paciente.query.filter_by(nome="Pedro Souza").first()
    medico = exame.medicos[0]
    medico.telefone = "27999990000"
    p = PerguntaPendente(grupo_id=grupo.id, paciente_id=paciente.id, exame_id=exame.id, pergunta="Posso tomar vacina?", status="pendente")
    db.session.add(p)
    db.session.commit()
    medico_id, email, grupo_id = medico.id, medico.email, grupo.id
    with patch("app.push_notificacoes.enviar_mensagem_whatsapp") as envio:
        notificar_equipe_nova_pergunta(p)
    n = Notificacao.query.filter_by(usuario_id=medico_id, tipo="pergunta_nova").all()
    checar("Cria a notificação do sininho para o médico", len(n) == 1 and "Posso tomar vacina?" in n[0].mensagem
           and n[0].link_endpoint == "medico.aprovacao_rapida" and not n[0].lida)
    checar("Texto do botão da notificação", n[0].acao_texto == "Clique aqui para responder a pergunta")
    kw = envio.call_args.kwargs
    checar("WhatsApp usa o template próprio, com 4 variáveis", kw["nome_template_env"] == "WHATSAPP_META_TEMPLATE_MEDICO_NOVA_PERGUNTA"
           and kw["content_variables"][1:3] == ["Pedro Souza", "Posso tomar vacina?"] and kw["content_variables"][3].endswith("/equipe/aprovacao-rapida"))
    checar("Mantém o texto livre como reserva", "Posso tomar vacina?" in kw["texto"])

c = app.test_client()
c.post("/login", data={"identificador": email, "senha": "123456"})
c.post("/equipe/clinica", data={"clinica_id": str(grupo_id)}, follow_redirects=True)
html = c.get("/equipe/").get_data(as_text=True)
checar("O sininho mostra a pergunta nova", "Nova pergunta de paciente" in html)
print("TUDO OK")
