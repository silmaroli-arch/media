"""Confiar na IA fora do preparo/FAQ (pedido do Silvan, 2026-10-27): com a
aprovação desativada, respostas de conhecimento geral continuavam indo ao
médico; com o parâmetro ligado, vão direto ao paciente."""
from unittest.mock import patch

from app import create_app, db
from app.models import Exame, Grupo, Paciente, PerguntaPendente, Usuario

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False
client = app.test_client()


def checar(nome, condicao):
    print(f"[{'OK' if condicao else 'FALHOU'}] {nome}")
    assert condicao, nome


def resultado(texto, **extra):
    return {"final": texto, "por_provedor": {"Claude": texto, "ChatGPT": None, "Gemini": None}, "falhas": [],
            "sem_sentido": False, "exige_revisao_medicamento": True, **extra}


def perguntar(pergunta, exame_id, texto):
    with patch("app.routes_paciente.responder_com_ia", return_value=resultado(texto)):
        return client.post("/paciente/chat", data={"pergunta": pergunta, "exame_id": str(exame_id)},
                           follow_redirects=True).get_data(as_text=True)


with app.app_context():
    grupo = Grupo.query.filter_by(nome="Clínica Vitória").first()
    grupo_id = grupo.id
    exame_id = Exame.query.filter_by(grupo_id=grupo_id, nome="Colonoscopia").first().id
    joao_id = Paciente.query.filter_by(cpf="123.456.789-00").first().id
    checar("Padrão: não confia na IA fora do preparo", grupo.confiar_ia_fora_do_preparo is False)

# ---------- tela do médico ----------
medico = app.test_client()
medico.post("/login", data={"identificador": "medico@clinicavitoria.com", "senha": "123456"})
medico.post("/equipe/clinica", data={"empresa_id": str(grupo_id)}, follow_redirects=True)
html = medico.get("/equipe/").get_data(as_text=True)
checar("Painel tem o parâmetro 'Confiar na IA...' (travado com a aprovação ativada)",
       "Confiar na IA mesmo quando a resposta não está no preparo nem no FAQ" in html and 'id="confiar-ia"' in html
       and "Só vale com a aprovação desativada" in html)
r = medico.post("/equipe/perguntas/configuracao/confiar-ia", data={"confiar_ia": "1"}, follow_redirects=True)
with app.app_context():
    checar("Ligar grava no Grupo e avisa que ainda depende da aprovação desativada",
           Grupo.query.get(grupo_id).confiar_ia_fora_do_preparo is True and "só vale com a aprovação desativada" in r.get_data(as_text=True))
medico.post("/equipe/perguntas/configuracao", data={"origem": "painel"})  # sem aprovacao_ativa = desativa
with app.app_context():
    checar("Aprovação desativada", Grupo.query.get(grupo_id).aprovacao_perguntas_paciente is False)
html = medico.get("/equipe/").get_data(as_text=True)
checar("Com a aprovação desativada o parâmetro fica ativo e ligado",
       'id="confiar-ia"' in html and "Ligado" in html and "também vão direto ao paciente" in html)

# ---------- paciente: confiança ligada, resposta de conhecimento geral vai direto ----------
client.post("/login-paciente", data={"cpf": "123.456.789-00", "data_nascimento": "1985-04-12"}, follow_redirects=True)
pergunta = "[teste-confiar] Posso tomar nimesulida antes do exame?"
html = perguntar(pergunta, exame_id, "A nimesulida é um anti-inflamatório que pode ser tomada normalmente.")
checar("Confiando na IA: resposta de conhecimento geral vai direto ao paciente", "anti-inflamatório que pode ser tomada" in html)
with app.app_context():
    p = PerguntaPendente.query.filter_by(paciente_id=joao_id, pergunta=pergunta).first()
    checar("... e foi registrada como respondida (sem fila do médico)", p is not None and p.status == "respondida")

# ---------- "Não reconheci": nunca vai direto, mesmo confiando ----------
pergunta3 = "[teste-confiar] Posso tomar o Xyzvital antes do exame?"
with patch("app.routes_paciente.responder_com_ia", return_value=resultado(
        'Não reconheci o "Xyzvital".', situacao={"Claude": "respondeu_nao_reconhecido", "ChatGPT": "nao_sabe"})):
    html = client.post("/paciente/chat", data={"pergunta": pergunta3, "exame_id": str(exame_id)}, follow_redirects=True).get_data(as_text=True)
with app.app_context():
    p = PerguntaPendente.query.filter_by(paciente_id=joao_id, pergunta=pergunta3).first()
    checar("'Não reconheci' fica com o médico mesmo confiando na IA", p is not None and p.status == "aguardando_aprovacao"
           and "Não reconheci" not in html.replace(pergunta3, ""))
# ---------- IA não sabe (NAO_SEI): vai para o médico, sem rascunho ----------
pergunta4 = "[teste-confiar] Qual a cor do carro do médico?"
with patch("app.routes_paciente.responder_com_ia", return_value={"final": None, "por_provedor": {"Claude": None, "ChatGPT": None, "Gemini": None},
                                                                  "falhas": [], "sem_sentido": False, "exige_revisao_medicamento": False,
                                                                  "situacao": {"Claude": "nao_sabe", "ChatGPT": "nao_sabe"}}):
    client.post("/paciente/chat", data={"pergunta": pergunta4, "exame_id": str(exame_id)}, follow_redirects=True)
with app.app_context():
    p = PerguntaPendente.query.filter_by(paciente_id=joao_id, pergunta=pergunta4).first()
    checar("Se a IA não sabe, a pergunta vai para a fila do médico sem resposta (mesmo confiando)",
           p is not None and p.status == "pendente" and not p.resposta_sugerida_ia)

# ---------- desligado: volta a esperar o médico ----------
medico.post("/equipe/perguntas/configuracao/confiar-ia", data={})
with app.app_context():
    checar("Desligar grava", Grupo.query.get(grupo_id).confiar_ia_fora_do_preparo is False)
pergunta2 = "[teste-confiar] Posso tomar ibuprofeno antes do exame?"
html = perguntar(pergunta2, exame_id, "O ibuprofeno é um anti-inflamatório permitido.")
with app.app_context():
    p = PerguntaPendente.query.filter_by(paciente_id=joao_id, pergunta=pergunta2).first()
    checar("Sem confiar: continua indo para o médico", p is not None and p.status == "aguardando_aprovacao"
           and "anti-inflamatório permitido" not in html)
print("TUDO OK")
