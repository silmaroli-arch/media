"""Busca na internet quando uma IA não reconhece um nome (pedido do Silvan,
2026-10-25): uma busca por nome, só em sites escolhidos, guardada para não
repetir, com limite diário; as 2 IAs respondem de novo com o resultado e o
médico sempre revisa. Sem rede: a API é simulada no nível HTTP."""
import json
from unittest.mock import MagicMock, patch

import anthropic
import httpx

from app import create_app, db
from app import busca_internet
from app.ia_preparo import MARCADOR_NAO_RECONHECIDO, responder_com_ia
from app.models import ChamadaIA, Exame, NomePesquisado, PlataformaConfig, Usuario

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


pedidos = []
RESPOSTA_TEXTO = {"valor": "Zorvantex = semaglutida, agonista do GLP-1 (classe do Ozempic)"}


def responder(request):
    corpo = json.loads(request.content)
    pedidos.append(corpo)
    return httpx.Response(200, json={
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 3000, "output_tokens": 80, "server_tool_use": {"web_search_requests": 1}},
        "content": [
            {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {"query": "Zorvantex"}},
            {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1", "content": [
                {"type": "web_search_result", "url": "https://consultas.anvisa.gov.br/zorvantex", "title": "Zorvantex",
                 "encrypted_content": "x", "page_age": None}]},
            {"type": "text", "text": RESPOSTA_TEXTO["valor"]},
        ],
    })


cliente_falso = anthropic.Anthropic(api_key="teste", http_client=httpx.Client(transport=httpx.MockTransport(responder)))

checar("Acha o nome entre aspas na resposta 'não reconheci'",
       busca_internet.nome_nao_reconhecido(['Não reconheci o medicamento "Zorvantex". Pode me informar...']) == "Zorvantex")

with app.app_context(), patch("app.ia_preparo._cliente_anthropic", return_value=cliente_falso):
    checar("Busca desligada (padrão): não pesquisa", busca_internet.pesquisar("Zorvantex") is None and not pedidos)
    config = PlataformaConfig.obter()
    config.busca_internet_ativa, config.busca_internet_limite_dia = True, 2
    db.session.commit()

    r = busca_internet.pesquisar("Zorvantex")
    pedido = pedidos[-1]
    ferramenta = pedido["tools"][0]
    checar("Pede a busca da Anthropic só nos sites escolhidos, no máximo 2 buscas",
           ferramenta["type"] == "web_search_20260209" and ferramenta["max_uses"] == 2
           and "gov.br" in ferramenta["allowed_domains"] and pedido["model"] == "claude-sonnet-5-5")
    checar("Guarda o resultado e as fontes", r.resultado.startswith("Zorvantex = semaglutida")
           and r.lista_fontes == ["https://consultas.anvisa.gov.br/zorvantex"] and r.status == "pesquisado")
    chamada = ChamadaIA.query.filter_by(tipo_uso="busca_internet").one()
    checar("Custo inclui a busca (US$ 0,01) além dos tokens",
           abs(float(chamada.custo_estimado_usd) - (3000 * 2 + 80 * 10) / 1e6 - 0.01) < 1e-6)
    n = len(pedidos)
    checar("Mesmo nome (outra grafia) não é pesquisado de novo",
           busca_internet.pesquisar("zorvantex ") .id == r.id and len(pedidos) == n)

    RESPOSTA_TEXTO["valor"] = "NAO_ENCONTRADO"
    r2 = busca_internet.pesquisar("Xablau")
    checar("Fontes não confirmam: guarda sem resultado e não entra no contexto",
           r2.resultado is None and busca_internet.linha_contexto(r2) == "")
    checar("Limite diário atingido: não pesquisa mais", busca_internet.pesquisar("Outronome") is None and len(pedidos) == n + 1)

    r.status = "rejeitado"
    db.session.commit()
    checar("Rejeitado pelo dono: não vai mais para o contexto", busca_internet.linha_contexto(r) == "")
    r.status = "pesquisado"
    db.session.commit()

    # Fluxo completo: a 1ª rodada não reconhece; com a pesquisa, responde.
    config.ia_chat_provedor_1, config.ia_chat_provedor_2 = "Claude", "ChatGPT"
    db.session.commit()
    exame = Exame.query.first()
    contextos = []

    def tentar(nome, pergunta, contexto, *a, **k):
        contextos.append(contexto)
        if "Pesquisa na internet" in contexto:
            return ("O Zorvantex (semaglutida) deve ser suspenso 14 dias antes.", MagicMock(), True, False, False)
        return ('Não reconheci o medicamento "Zorvantex". Pode me informar o princípio ativo?', MagicMock(), True,
                False, MARCADOR_NAO_RECONHECIDO)

    # (o nome já está guardado - sem árbitro/síntese de verdade aqui)
    with patch("app.ia_preparo._tentar_provedor", side_effect=tentar), \
            patch("app.ia_preparo._cliente_anthropic", return_value=None):
        resultado = responder_com_ia("Preciso parar o Zorvantex?", exame)
    checar("Com a pesquisa, as 2 IAs respondem de novo com o resultado no contexto",
           len(contextos) == 4 and "Zorvantex = semaglutida" in contextos[-1]
           and "suspenso 14 dias" in (resultado["final"] or ""))
    checar("... e a resposta sempre passa pelo médico, que vê o que foi achado e a fonte",
           resultado["exige_revisao_medicamento"] is True and "anvisa.gov.br" in resultado["pesquisa_internet"])

    config.busca_internet_ativa = False
    db.session.commit()
    contextos.clear()
    with patch("app.ia_preparo._tentar_provedor", side_effect=tentar):
        resultado = responder_com_ia("Preciso parar o Zorvantex?", exame)
    checar("Busca desligada: fica no 'não reconheci', sem nova rodada",
           len(contextos) == 2 and resultado["pesquisa_internet"] is None)
    # WhatsApp: a pergunta fica com o que foi pesquisado, para o médico ver.
    from app.models import Grupo, Paciente, PerguntaPendente
    from app.whatsapp_conversa import processar_mensagem

    telefone = "+5527900008888"
    processar_mensagem(telefone, "111.222.333-00")
    processar_mensagem(telefone, "25121978")
    achado = "Zorvantex = semaglutida (fontes: https://consultas.anvisa.gov.br/zorvantex)"
    resultado_ia = {"final": "O Zorvantex (semaglutida) deve ser suspenso 14 dias antes.", "falhas": [],
                    "sem_sentido": False, "exige_revisao_medicamento": True, "pesquisa_internet": achado,
                    "por_provedor": {"Claude": "O Zorvantex...", "ChatGPT": None, "Gemini": None},
                    "situacao": {"Claude": "respondeu", "ChatGPT": "nao_sabe"}}
    with patch("app.whatsapp_conversa.responder_com_ia", return_value=resultado_ia):
        processar_mensagem(telefone, "Preciso parar o Zorvantex antes do exame?")
    pedro = Paciente.query.filter_by(nome="Pedro Souza").first()
    pendente = PerguntaPendente.query.filter_by(paciente_id=pedro.id).order_by(PerguntaPendente.id.desc()).first()
    checar("Pergunta do WhatsApp guarda o que foi pesquisado e vai para o médico",
           pendente.pesquisa_internet == achado and pendente.status == "aguardando_aprovacao")
    vitoria_id = Grupo.query.filter_by(nome="Clínica Vitória").first().id
    dono_email = Usuario.query.filter_by(tipo="dono").first().email

medico = app.test_client()
medico.post("/login", data={"identificador": "medica2@clinicavitoria.com", "senha": "123456"})
medico.post("/equipe/clinica", data={"clinica_id": str(vitoria_id)}, follow_redirects=True)
html = medico.get("/equipe/portal").get_data(as_text=True)
checar("Portal de atendimento mostra a pesquisa na internet com a fonte",
       "Pesquisa na internet:</strong> Zorvantex = semaglutida" in html)

d = app.test_client()
d.post("/login", data={"identificador": dono_email, "senha": "123456"})
html = d.get("/dono/").get_data(as_text=True)
checar("Painel do dono tem a configuração da busca e o menu 'Nomes pesquisados'",
       "Busca na internet quando a IA não reconhece um nome" in html and "Nomes pesquisados" in html)
d.post("/dono/configuracoes/busca-internet", data={"busca_internet_ativa": "1", "busca_internet_limite_dia": "15"})
with app.app_context():
    c = PlataformaConfig.obter()
    checar("Dono liga a busca e define o limite", c.busca_internet_ativa and c.busca_internet_limite_dia == 15)
    rid = NomePesquisado.query.filter_by(nome="Zorvantex").one().id
html = d.get("/dono/nomes-pesquisados").get_data(as_text=True)
checar("Tela lista o nome, o resultado e a fonte", "Zorvantex = semaglutida" in html and "consultas.anvisa.gov.br" in html)
d.post("/dono/nomes-pesquisados", data={"id": rid, "acao": "confirmado"})
with app.app_context():
    r = NomePesquisado.query.get(rid)
    checar("Confirmar marca como conferido", r.status == "confirmado" and "conferida pela equipe" in busca_internet.linha_contexto(r))
d.post("/dono/nomes-pesquisados", data={"id": rid, "acao": "apagar"})
with app.app_context():
    checar("Apagar remove (pesquisa de novo na próxima vez)", NomePesquisado.query.get(rid) is None)
m = app.test_client()
m.post("/login", data={"identificador": "medico@clinicavitoria.com", "senha": "123456"})
checar("Só o dono acessa", m.get("/dono/nomes-pesquisados").status_code in (302, 403))

print("\nTodos os testes da busca na internet passaram.")
