"""Chat de ajuda sobre o uso do app (pedido do Silvan, 2026-10-23): botão
"Ajuda" para a equipe; a IA é escolhida pelo dono; quando não sabe, a
dúvida pode ir para o "Fale com a gente"; o dono vê as perguntas."""
from types import SimpleNamespace
from unittest.mock import patch

from app import create_app, db
from app.ajuda_app import MARCADOR_NAO_SEI, PROMPT_SISTEMA
from app.models import AjudaMensagem, ChamadaIA, MensagemSuporte, PlataformaConfig, Usuario

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


chamadas = []


def cliente_claude(texto):
    def criar(**kwargs):
        chamadas.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(text=texto)], usage=SimpleNamespace(input_tokens=100, output_tokens=20),
                               model="claude")
    return SimpleNamespace(messages=SimpleNamespace(create=criar))


def ia(texto):
    return patch.dict("app.ia_preparo._PROVEDORES_CHAT", {"Claude": (lambda: cliente_claude(texto), None)})


with app.app_context():
    PlataformaConfig.obter().ia_ajuda_app = "Claude"
    db.session.commit()
    dono_email = Usuario.query.filter_by(tipo="dono").first().email

checar("O prompt traz o manual e o marcador de 'não sei'", "Agenda de Exames" in PROMPT_SISTEMA and MARCADOR_NAO_SEI in PROMPT_SISTEMA)

c = app.test_client()
c.post("/login", data={"identificador": "secretaria@clinicavitoria.com", "senha": "123456"})
html = c.get("/equipe/medico-agenda", follow_redirects=True).get_data(as_text=True)
checar("O botão 'Ajuda' aparece para a equipe", 'id="ajudaBotao"' in html and "/equipe/ajuda" in html)

with ia('Na "Agenda de Exames", clique no exame e use "Remarcar para".'):
    r = c.post("/equipe/ajuda", json={"pergunta": "Como remarco um exame?", "tela": "medico.medico_agenda_pessoal"})
dados = r.get_json()
checar("Responde com a IA configurada", r.status_code == 200 and "Remarcar para" in dados["resposta"] and not dados["nao_soube"])
checar("... mandando o manual e a tela atual", "MANUAL DO MedIA" in chamadas[-1]["system"]
       and "Tela atual: Agenda de Exames" in chamadas[-1]["messages"][-1]["content"])
with ia("Clique em Cancelar exame."):
    c.post("/equipe/ajuda", json={"pergunta": "E para cancelar?", "tela": "medico.medico_agenda_pessoal"})
checar("Leva a conversa anterior como contexto", [m["role"] for m in chamadas[-1]["messages"]] == ["user", "assistant", "user"])
with app.app_context():
    checar("Registra o custo como 'ajuda_app'", ChamadaIA.query.filter_by(tipo_uso="ajuda_app").count() == 2)

with ia(f"{MARCADOR_NAO_SEI}\nPreciso que você fale com o suporte."):
    dados = c.post("/equipe/ajuda", json={"pergunta": "Dá para integrar com o meu sistema de prontuário?"}).get_json()
checar("Não sabe: avisa sem o marcador e oferece o suporte", dados["nao_soube"] and MARCADOR_NAO_SEI not in dados["resposta"])
r = c.post(f"/equipe/ajuda/{dados['id']}/suporte")
c.post(f"/equipe/ajuda/{dados['id']}/suporte")
with app.app_context():
    sup = MensagemSuporte.query.filter(MensagemSuporte.mensagem.contains("prontuário")).all()
    checar("'Enviar para o suporte' vira uma mensagem do Fale com a gente (uma só)",
           r.status_code == 200 and len(sup) == 1 and AjudaMensagem.query.get(dados["id"]).enviada_suporte)

with patch.dict("app.ia_preparo._PROVEDORES_CHAT", {"Claude": (lambda: None, None)}):
    dados = c.post("/equipe/ajuda", json={"pergunta": "Oi?"}).get_json()
checar("IA indisponível: resposta de aviso e opção de suporte", dados["nao_soube"] and "suporte" in dados["resposta"])

d = app.test_client()
d.post("/login", data={"identificador": dono_email, "senha": "123456"})
d.post("/dono/configuracoes/ia-ajuda", data={"ia_ajuda_app": "Gemini"})
with app.app_context():
    checar("Dono escolhe a IA da ajuda", PlataformaConfig.obter().ia_ajuda_app == "Gemini")
html = d.get("/dono/ajuda-app?nao_soube=1").get_data(as_text=True)
checar("Dono vê as perguntas (filtro 'não soube')",
       "Dá para integrar com o meu sistema de prontuário?" in html and "Como remarco um exame?" not in html)
checar("Dono não tem o botão Ajuda (é da equipe)", 'id="ajudaBotao"' not in d.get("/dono/").get_data(as_text=True))

print("\nTodos os testes da ajuda do app passaram.")
