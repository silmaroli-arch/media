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
html = d.get("/dono/").get_data(as_text=True)
checar("Painel do dono mostra o card 'Modelos das IAs' com o modelo em uso",
       "Modelos das IAs" in html and "claude-sonnet-5-5" in html and 'name="modelo_openai"' in html)
checar("Modelos numa lista de seleção, com o preço e a opção 'Outro...'",
       '<select name="modelo_claude"' in html and "claude-opus-5-5 - US$ 4,00 / 20,00 por milhão" in html
       and 'value="__outro__"' in html)
d.post("/dono/configuracoes/modelos-ia", data={"modelo_claude": "", "modelo_openai": "gpt-5.4-mini",
                                               "modelo_claude_validador": "", "modelo_gemini": ""})
with app.app_context():
    checar("Salvar grava o modelo informado e deixa vazio o que ficou em branco",
           PlataformaConfig.obter().modelo_openai == "gpt-5.4-mini" and PlataformaConfig.obter().modelo_claude is None
           and modelo_openai() == "gpt-5.4-mini")
d.post("/dono/configuracoes/modelos-ia", data={"modelo_claude": "__outro__", "modelo_claude_outro": "claude-sonnet-5-6",
                                               "modelo_openai": "gpt-5.4-mini"})
with app.app_context():
    checar("'Outro...' grava o nome digitado", PlataformaConfig.obter().modelo_claude == "claude-sonnet-5-6")
html = d.get("/dono/").get_data(as_text=True)
checar("... e ele continua aparecendo selecionado na lista", '<option value="claude-sonnet-5-6" selected>' in html)
d.post("/dono/configuracoes/modelos-ia", data={"modelo_claude": "", "modelo_openai": "gpt-5.4-mini"})
r = d.post("/dono/configuracoes/modelos-ia", data={"modelo_openai": "gpt 5; drop table"}, follow_redirects=True)
checar("Nome inválido é recusado", "Nome de modelo inválido" in r.get_data(as_text=True))


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
       and "<strong>ChatGPT</strong> - <code>gpt-5.4-mini</code>: sem chave de API" in html)
checar("... e o resultado some ao recarregar", 'id="resultado-teste-modelos"' not in d.get("/dono/").get_data(as_text=True))

print("\nTodos os testes dos modelos novos passaram.")
