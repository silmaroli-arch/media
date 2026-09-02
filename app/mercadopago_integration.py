"""Fatia 8 (gateway de pagamento real - Mercado Pago): gera a cobrança de UM
mês específico da licença individual de um médico via Checkout Pro (link de
pagamento) e valida a notificação automática recebida por webhook.

Isto é uma camada ADITIVA ao controle manual já existente - o dono continua
podendo marcar um mês como pago/não pago na mão (ver
app/routes_dono.py:usuario_licenca_pagamento_marcar), útil pra Pix fora do
sistema, acordos informais, período de transição etc (decisão do Silvan de
manter os dois caminhos, não substituir um pelo outro). Este módulo só entra
em ação quando o dono aperta "Gerar cobrança" pra um mês específico.

Configuração necessária (variáveis de ambiente - NUNCA em código nem no
repositório, ver .env.example, mesmo princípio das credenciais de WhatsApp
em app/routes_whatsapp.py/app/whatsapp_envio.py):
- MERCADOPAGO_ACCESS_TOKEN: Access Token da conta Mercado Pago (Suas
  integrações > credenciais de teste ou de produção) - usado pra criar a
  preferência de pagamento e consultar o status de um pagamento. Comece
  pelas credenciais de TESTE até confirmar que o fluxo (cobrança -> webhook
  -> mês marcado como pago) funciona de ponta a ponta antes de trocar pelas
  de produção.
- MERCADOPAGO_WEBHOOK_SECRET: chave secreta gerada em Suas integrações >
  Webhooks > Configurar notificações - usada só pra validar a assinatura de
  cada notificação recebida (cabeçalho X-Signature, ver `_assinatura_valida`
  abaixo). Sem essa variável, o webhook recusa QUALQUER notificação (falha
  fechada - mesmo padrão do webhook de WhatsApp).

Ver também app/routes_pagamentos_webhook.py (a rota que recebe a
notificação e chama `_assinatura_valida`/`consultar_pagamento` daqui) e
app/routes_dono.py:usuario_licenca_pagamento_cobrar (a rota que chama
`criar_preferencia_pagamento`).
"""
import hashlib
import hmac
import os

import requests
from flask import request, url_for

MP_API_BASE = "https://api.mercadopago.com"


class MercadoPagoNaoConfigurado(Exception):
    """MERCADOPAGO_ACCESS_TOKEN não está definido - o gateway ainda não foi
    configurado nesta instalação (normal em dev/teste, ou antes do Silvan
    cadastrar as credenciais em produção)."""


def _access_token():
    token = os.environ.get("MERCADOPAGO_ACCESS_TOKEN")
    if not token:
        raise MercadoPagoNaoConfigurado(
            "MERCADOPAGO_ACCESS_TOKEN não configurado - defina no .env pra poder "
            "gerar cobranças reais pelo Mercado Pago."
        )
    return token


def criar_preferencia_pagamento(pagamento):
    """Cria uma preferência de pagamento (Checkout Pro) no Mercado Pago pra
    UM mês específico (um LicencaPagamento) e grava mp_preference_id/
    mp_status/mp_init_point/valor no próprio registro - quem chama decide
    quando dar commit. Devolve a URL de pagamento (init_point) pro dono
    repassar ao médico (o médico também vê o link em "Minha licença").

    Levanta MercadoPagoNaoConfigurado se as credenciais não estiverem no
    .env, e ValueError se não houver valor mensal definido pra esse médico
    (não dá pra cobrar um valor que não existe - o dono precisa preencher
    Usuario.valor_licenca_mensal em /dono/usuarios antes)."""
    usuario = pagamento.usuario
    valor = pagamento.valor if pagamento.valor is not None else usuario.valor_licenca_mensal
    if not valor or float(valor) <= 0:
        raise ValueError(
            "Defina o valor mensal deste médico (em /dono/usuarios) antes de gerar a cobrança."
        )

    payload = {
        "items": [{
            "title": f"Licença MedIA - {usuario.nome} - {pagamento.mes.strftime('%m/%Y')}",
            "quantity": 1,
            "currency_id": "BRL",
            "unit_price": float(valor),
        }],
        "external_reference": f"licenca_pagamento:{pagamento.id}",
        "notification_url": url_for("pagamentos_webhook.mercadopago_webhook", _external=True),
    }
    if usuario.email:
        payload["payer"] = {"email": usuario.email}

    resposta = requests.post(
        f"{MP_API_BASE}/checkout/preferences",
        json=payload,
        headers={"Authorization": f"Bearer {_access_token()}"},
        timeout=15,
    )
    resposta.raise_for_status()
    dados = resposta.json()

    # Trava o valor cobrado NESTA preferência - se o dono mudar
    # valor_licenca_mensal depois, a cobrança já gerada não muda sozinha
    # (mesma lógica de "fotografia" de garantir_meses_licenca).
    pagamento.valor = valor
    pagamento.mp_preference_id = dados.get("id")
    pagamento.mp_status = "pendente"
    pagamento.mp_init_point = dados.get("init_point")

    return dados.get("init_point")


def _assinatura_valida(data_id, request_id):
    """Confere o cabeçalho X-Signature (HMAC-SHA256 sobre um "manifest" com
    id/request-id/timestamp, usando o secret configurado em Suas
    integrações > Webhooks) - formato oficial do Mercado Pago. Falha
    fechada: sem MERCADOPAGO_WEBHOOK_SECRET configurado, nenhuma
    notificação é aceita (mesmo princípio do webhook de WhatsApp em
    app/routes_whatsapp.py:_assinatura_valida)."""
    from flask import current_app

    secret = os.environ.get("MERCADOPAGO_WEBHOOK_SECRET")
    if not secret:
        current_app.logger.warning(
            "Webhook do Mercado Pago recebido, mas MERCADOPAGO_WEBHOOK_SECRET "
            "não está configurado - recusando por segurança."
        )
        return False

    cabecalho = request.headers.get("X-Signature", "")
    partes = dict(item.split("=", 1) for item in cabecalho.split(",") if "=" in item)
    ts, v1 = partes.get("ts"), partes.get("v1")
    if not ts or not v1:
        return False

    # Ordem e formato exigidos pela documentação do Mercado Pago - só entra
    # no manifest o que realmente veio na notificação (data.id e/ou
    # request-id podem faltar em alguns tipos de evento).
    manifest = ""
    if data_id:
        manifest += f"id:{data_id};"
    if request_id:
        manifest += f"request-id:{request_id};"
    manifest += f"ts:{ts};"

    esperado = hmac.new(secret.encode("utf-8"), manifest.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, v1)


def consultar_pagamento(payment_id):
    """Busca o pagamento na API do Mercado Pago pelo id - o webhook NUNCA
    confia só no conteúdo da notificação em si (que só avisa "algo mudou",
    e poderia em tese ser forjado se a assinatura falhasse silenciosamente
    em algum ponto); sempre confirma o status real direto na fonte antes de
    marcar qualquer mês como pago."""
    resposta = requests.get(
        f"{MP_API_BASE}/v1/payments/{payment_id}",
        headers={"Authorization": f"Bearer {_access_token()}"},
        timeout=15,
    )
    resposta.raise_for_status()
    return resposta.json()
