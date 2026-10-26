"""Modelos novos (2026-10-25): Claude Sonnet 5.5 nas respostas, Haiku 5.5 no
validador, GPT-5 com os parâmetros novos. Confere o pedido HTTP que o SDK
monta (sem sair para a internet) e a leitura das respostas."""
import json
from types import SimpleNamespace

import anthropic
import httpx
import openai

from app.ia_modelos import (
    FOLGA_RACIOCINIO, criar_chat_openai, criar_mensagem_claude, modelo_sem_painel, texto_claude,
)


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


pedidos = []


def responder_anthropic(request):
    corpo = json.loads(request.content)
    pedidos.append((str(request.url), dict(request.headers), corpo))
    return httpx.Response(200, json={
        "id": "msg_1", "type": "message", "role": "assistant", "model": corpo["model"], "stop_reason": "end_turn",
        "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 5},
        "content": [{"type": "thinking", "thinking": "", "signature": "x"}, {"type": "text", "text": "Pode sim."}],
    })


checar("Padrões: Sonnet 5.5 nas respostas e Haiku 5.5 no validador",
       modelo_sem_painel("claude") == "claude-sonnet-5-5" and modelo_sem_painel("claude_validador") == "claude-haiku-5-5")

cliente = anthropic.Anthropic(api_key="teste", http_client=httpx.Client(transport=httpx.MockTransport(responder_anthropic)))
r = criar_mensagem_claude(cliente, "claude-sonnet-5-5", 300, esforco="medium", system="regras",
                          messages=[{"role": "user", "content": "posso?"}])
url, cabecalhos, corpo = pedidos[-1]
checar("Sonnet 5.5: folga para o raciocínio e effort no pedido",
       corpo["max_tokens"] == 300 + FOLGA_RACIOCINIO and corpo["output_config"] == {"effort": "medium"})
checar("Sonnet 5.5: fallback do servidor ligado", corpo.get("fallbacks") == "default"
       and "server-side-fallback-2026-07-01" in cabecalhos.get("anthropic-beta", ""))
checar("Sem temperature (recusado nos modelos novos)", "temperature" not in corpo)
checar("Texto lido sem o bloco de raciocínio", texto_claude(r) == "Pode sim.")

criar_mensagem_claude(cliente, "claude-haiku-5-5", 120, system="v", messages=[{"role": "user", "content": "x"}])
corpo = pedidos[-1][2]
checar("Haiku 5.5: effort baixo, sem fallback", corpo["output_config"] == {"effort": "low"} and "fallbacks" not in corpo)

criar_mensagem_claude(cliente, "claude-sonnet-4-5", 300, messages=[{"role": "user", "content": "x"}])
corpo = pedidos[-1][2]
checar("Modelo antigo: pedido como antes", corpo["max_tokens"] == 300 and "output_config" not in corpo)

checar("Recusa por segurança vira texto vazio (a IA 'não respondeu')",
       texto_claude(SimpleNamespace(stop_reason="refusal", content=[SimpleNamespace(type="text", text="x")])) == "")

pedidos_openai = []


def responder_openai(request):
    corpo = json.loads(request.content)
    pedidos_openai.append(corpo)
    return httpx.Response(200, json={
        "id": "c1", "object": "chat.completion", "created": 1, "model": corpo["model"],
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    })


oa = openai.OpenAI(api_key="teste", http_client=httpx.Client(transport=httpx.MockTransport(responder_openai)))
criar_chat_openai(oa, "gpt-5.4-mini", 300, esforco="medium", messages=[{"role": "user", "content": "x"}])
checar("GPT-5: max_completion_tokens com folga e reasoning_effort",
       pedidos_openai[-1].get("max_completion_tokens") == 300 + FOLGA_RACIOCINIO
       and "max_tokens" not in pedidos_openai[-1] and pedidos_openai[-1].get("reasoning_effort") == "medium")
criar_chat_openai(oa, "gpt-4o-mini", 300, messages=[{"role": "user", "content": "x"}])
checar("gpt-4o-mini: max_tokens como antes", pedidos_openai[-1].get("max_tokens") == 300)

from app.custo_ia import calcular_custo_usd
checar("Custo dos modelos novos na tabela", calcular_custo_usd("claude-sonnet-5-5", 1_000_000, 0) == 2.0
       and calcular_custo_usd("claude-haiku-5-5", 0, 1_000_000) == 0.5)

# Modelo escolhido pelo dono no painel (2026-10-25) vale mais que o servidor.
import os
from unittest.mock import patch

from app import create_app, db
from app.ia_modelos import modelo_claude, modelo_openai
from app.models import PlataformaConfig, Usuario

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False
with app.app_context(), patch.dict(os.environ, {"ANTHROPIC_MODEL": "claude-sonnet-4-5"}):
    checar("Sem nada no painel, vale a variável do servidor", modelo_claude() == "claude-sonnet-4-5")
    PlataformaConfig.obter().modelo_claude = "claude-opus-5-5"
    db.session.commit()
    checar("Com o painel preenchido, vale o do painel", modelo_claude() == "claude-opus-5-5")
    PlataformaConfig.obter().modelo_claude = None
    db.session.commit()
    dono_email = Usuario.query.filter_by(tipo="dono").first().email

d = app.test_client()
d.post("/login", data={"identificador": dono_email, "senha": "123456"})
from app.ia_modelos import (
    PONTOS, chamar_texto, modelo_claude_sintese, modelo_do_ponto, modelo_padrao_do_ponto, provedor_do_modelo,
)

checar("Provedor sai do nome do modelo",
       [provedor_do_modelo(n) for n in ("claude-haiku-5-5", "gpt-5.4-mini", "o3-mini", "gemini-flash-latest", "llama")]
       == ["Claude", "ChatGPT", "ChatGPT", "Gemini", None])
with app.app_context():
    checar("Sem escolha, cada ponto usa o padrão de antes",
           modelo_do_ponto("resposta_1") == "claude-sonnet-5-5" and modelo_do_ponto("resposta_2") == "gpt-4o-mini"
           and modelo_do_ponto("validador") == "claude-haiku-5-5" and modelo_do_ponto("sintese") == "claude-sonnet-5-5"
           and modelo_do_ponto("pdf_1") == "gemini-flash-latest" and modelo_do_ponto("busca") == "claude-sonnet-5-5")

html = d.get("/dono/").get_data(as_text=True)
checar("Painel do dono: uma linha por ponto de uso, com o modelo em uso",
       "Modelo de IA de cada ponto do sistema" in html and all(f'name="ponto_{k}"' in html for k in PONTOS)
       and "Malu (ajuda do app)" in html and "Busca na internet" in html)
checar("Pontos agrupados por tema, na ordem", html.index("Respostas ao paciente (WhatsApp e link)") < html.index("Importação do PDF do preparo") < html.index("Ajuda e aprendizado") < html.index("Pesquisa na internet"))
checar("Modelos numa lista de seleção, com o preço e a opção 'Outro...'",
       "claude-opus-5-5 - US$ 4,00 / 20,00 por milhão" in html and 'value="__outro__"' in html)
checar("A busca só oferece modelos Claude", "gpt-5.4-mini" not in html.split('name="ponto_busca"')[1].split("</select>")[0])

r = d.post("/dono/configuracoes/modelos-ia", data={"ponto_malu": "gemini-flash-lite-latest", "ponto_validador": "gpt-5.4-nano"})
with app.app_context():
    checar("Salvar grava o modelo de cada ponto; o provedor vem do nome",
           modelo_do_ponto("malu") == "gemini-flash-lite-latest" and modelo_do_ponto("validador") == "gpt-5.4-nano"
           and modelo_do_ponto("resposta_1") == "claude-sonnet-5-5")
d.post("/dono/configuracoes/modelos-ia", data={"ponto_malu": "__outro__", "ponto_malu_outro": "claude-sonnet-5-6"})
with app.app_context():
    checar("'Outro...' grava o nome digitado", modelo_do_ponto("malu") == "claude-sonnet-5-6")
html = d.get("/dono/").get_data(as_text=True)
checar("... e ele continua aparecendo selecionado na lista", '<option value="claude-sonnet-5-6" selected>' in html)
r = d.post("/dono/configuracoes/modelos-ia", data={"ponto_malu": "llama-3"}, follow_redirects=True)
checar("Nome de outra empresa desconhecida é recusado", "não reconhecido" in r.get_data(as_text=True))
r = d.post("/dono/configuracoes/modelos-ia", data={"ponto_busca": "gpt-5.4-mini"}, follow_redirects=True)
checar("Busca na internet recusa modelo que não é Claude", "busca da Anthropic" in r.get_data(as_text=True))
r = d.post("/dono/configuracoes/modelos-ia", data={"ponto_resposta_1": "claude-haiku-5-5", "ponto_resposta_2": "claude-sonnet-5-5"},
           follow_redirects=True)
checar("As duas IAs das respostas precisam ser de empresas diferentes", "empresas diferentes" in r.get_data(as_text=True))
d.post("/dono/configuracoes/modelos-ia", data={})
with app.app_context():
    checar("Tudo em branco volta aos padrões", PlataformaConfig.obter().modelos_pontos in (None, "{}")
           and modelo_do_ponto("malu") == "claude-sonnet-5-5")

# Respostas com a 1ª IA sendo o ChatGPT e a 2ª o Gemini: reserva precisa ser Claude.
r = d.post("/dono/configuracoes/modelos-ia", data={"ponto_resposta_1": "gpt-5.4-mini", "ponto_resposta_2": "gemini-flash-latest"},
           follow_redirects=True)
with app.app_context():
    checar("Troca das IAs das respostas é aceita (reserva cai no que sobrar)",
           modelo_do_ponto("resposta_1") == "gpt-5.4-mini" and modelo_do_ponto("resposta_2") == "gemini-flash-latest")
d.post("/dono/configuracoes/modelos-ia", data={})


class _Resp:
    usage = SimpleNamespace(input_tokens=1, output_tokens=1)
    stop_reason = "end_turn"
    content = [SimpleNamespace(type="text", text="ok")]


falso = SimpleNamespace(messages=SimpleNamespace(create=lambda **k: _Resp()))
with patch("app.ia_preparo._cliente_anthropic", return_value=falso), \
        patch("app.ia_preparo._cliente_openai", return_value=None), patch("app.ia_preparo._cliente_gemini", return_value=None):
    html = d.post("/dono/configuracoes/modelos-ia/testar", follow_redirects=True).get_data(as_text=True)
checar("Testar mostra, dentro do card, quem respondeu e quem está sem chave",
       'id="resultado-teste-modelos"' in html and "<code>claude-sonnet-5-5</code>: respondeu (ok)" in html
       and "<strong>Resposta ao paciente - 2ª IA</strong> - <code>gpt-4o-mini</code>: sem chave de API" in html)
checar("... e o resultado some ao recarregar", 'id="resultado-teste-modelos"' not in d.get("/dono/").get_data(as_text=True))

# chamar_texto fala com a API certa conforme o modelo.
chamadas = []
cli_claude = SimpleNamespace(messages=SimpleNamespace(create=lambda **k: chamadas.append(("claude", k["model"])) or _Resp()))
r = chamar_texto(cli_claude, "claude-haiku-5-5", "sys", [{"role": "user", "content": "oi"}], 50)
checar("chamar_texto com modelo Claude devolve texto, provedor e tokens",
       r.texto == "ok" and r.provedor == "Claude" and (r.entrada, r.saida) == (1, 1) and chamadas == [("claude", "claude-haiku-5-5")])
cli_gpt = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **k: chamadas.append(("gpt", k["model"])) or SimpleNamespace(
    choices=[SimpleNamespace(message=SimpleNamespace(content=" oi "))], usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4), model=k["model"]))))
r = chamar_texto(cli_gpt, "gpt-5.4-nano", "sys", [{"role": "user", "content": "oi"}], 50)
checar("chamar_texto com modelo GPT usa a API da OpenAI", r.texto == "oi" and r.provedor == "ChatGPT" and (r.entrada, r.saida) == (3, 4))

# Perfis prontos e junção das respostas com modelo próprio.
with app.app_context():
    checar("Junção das respostas tem modelo próprio (padrão Sonnet 5.5)", modelo_claude_sintese() == "claude-sonnet-5-5")
d.post("/dono/configuracoes/modelos-ia/perfil", data={"perfil": "essencial"})
with app.app_context():
    checar("Perfil Essencial: Haiku nas respostas/validador, Sonnet na junção, gpt-5.4-nano na 2ª IA",
           modelo_do_ponto("resposta_1") == "claude-haiku-5-5" and modelo_do_ponto("resposta_2") == "gpt-5.4-nano"
           and modelo_do_ponto("validador") == "claude-haiku-5-5" and modelo_do_ponto("sintese") == "claude-sonnet-5-5"
           and modelo_do_ponto("malu") == "claude-haiku-5-5")
sistemas = []
sintese = SimpleNamespace(messages=SimpleNamespace(create=lambda **k: sistemas.append(k["model"]) or SimpleNamespace(
    content=[SimpleNamespace(type="text", text="ok")], usage=SimpleNamespace(input_tokens=1, output_tokens=1), model=k["model"],
    stop_reason="end_turn")))
from app.ia_preparo import _sintetizar_resposta
with app.app_context():
    _sintetizar_resposta(sintese, "Posso comer pepino?", "a", "b")
checar("Com o Essencial, a junção usa o Sonnet e não o Haiku das respostas", sistemas == ["claude-sonnet-5-5"])
d.post("/dono/configuracoes/modelos-ia/perfil", data={"perfil": "completo"})
with app.app_context():
    checar("Perfil Completo: Sonnet na 1ª IA e gpt-5.4-mini na 2ª",
           modelo_do_ponto("resposta_1") == "claude-sonnet-5-5" and modelo_do_ponto("resposta_2") == "gpt-5.4-mini")
checar("Perfil inválido é recusado", "Perfil inválido" in d.post("/dono/configuracoes/modelos-ia/perfil", data={"perfil": "x"}, follow_redirects=True).get_data(as_text=True))

# O ponto escolhido chega de fato na chamada: validador com outro modelo.
from app.ia_preparo import validar_pergunta
from app.models import Exame

usados = []
cli_validador = SimpleNamespace(messages=SimpleNamespace(create=lambda **k: usados.append(k["model"]) or SimpleNamespace(
    content=[SimpleNamespace(type="text", text="VALIDA")], usage=SimpleNamespace(input_tokens=1, output_tokens=1), model=k["model"],
    stop_reason="end_turn")))
d.post("/dono/configuracoes/modelos-ia", data={"ponto_validador": "claude-opus-5-5"})
with app.app_context(), patch.dict("app.ia_preparo._PROVEDORES_CHAT", {"Claude": (lambda: cli_validador, None)}):
    resultado = validar_pergunta("Posso comer pepino?", Exame.query.first())
checar("O validador chama o modelo escolhido no ponto", usados == ["claude-opus-5-5"] and resultado["classificacao"] == "valida")

print("\nTodos os testes dos modelos novos passaram.")
