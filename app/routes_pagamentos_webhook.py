"""Fatia 8 (gateway de pagamento real - Mercado Pago): recebe a confirmação
automática de pagamento de um mês da licença individual de um médico. Ver
app/mercadopago_integration.py para a criação da cobrança (preferência) e a
validação de assinatura (`_assinatura_valida`, `consultar_pagamento`).

Rota pública (sem @login_required) por natureza - é o Mercado Pago quem
chama, não uma pessoa logada - protegida pela validação de assinatura,
mesmo padrão do webhook de WhatsApp (app/routes_whatsapp.py).

Sempre responde 200, mesmo quando recusa por assinatura inválida/ausente,
sem data.id, ou sem achar o pagamento correspondente - devolver um erro
faria o Mercado Pago reentregar a mesma notificação indefinidamente,
achando que falhou (mesmo motivo do webhook de WhatsApp)."""
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request

from app.extensions import db
from app.mercadopago_integration import _assinatura_valida, consultar_pagamento
from app.models import LicencaPagamento

pagamentos_webhook_bp = Blueprint("pagamentos_webhook", __name__, url_prefix="/webhooks")


@pagamentos_webhook_bp.route("/mercadopago", methods=["POST"])
def mercadopago_webhook():
    corpo = request.get_json(silent=True) or {}
    data_id = request.args.get("data.id") or (corpo.get("data") or {}).get("id")
    request_id = request.headers.get("X-Request-Id", "")

    if not _assinatura_valida(data_id, request_id):
        current_app.logger.warning("Webhook do Mercado Pago recusado (assinatura inválida ou ausente).")
        return jsonify({"status": "assinatura invalida"}), 200

    if not data_id:
        # Alguns tipos de evento (ex.: "merchant_order") não trazem um
        # pagamento de verdade - não há o que processar, mas não é erro.
        return jsonify({"status": "sem data.id, ignorado"}), 200

    try:
        pagamento_mp = consultar_pagamento(data_id)
    except Exception:
        current_app.logger.exception("Falha ao consultar pagamento %s no Mercado Pago.", data_id)
        return jsonify({"status": "erro ao consultar"}), 200

    referencia = pagamento_mp.get("external_reference") or ""
    if not referencia.startswith("licenca_pagamento:"):
        return jsonify({"status": "referencia desconhecida, ignorado"}), 200

    try:
        pagamento_id = int(referencia.split(":", 1)[1])
    except ValueError:
        return jsonify({"status": "referencia invalida"}), 200

    pagamento = LicencaPagamento.query.get(pagamento_id)
    if not pagamento:
        return jsonify({"status": "pagamento nao encontrado"}), 200

    status_mp = pagamento_mp.get("status")
    pagamento.mp_payment_id = str(pagamento_mp.get("id")) if pagamento_mp.get("id") is not None else None
    pagamento.mp_status = status_mp
    if status_mp == "approved" and not pagamento.pago:
        pagamento.pago = True
        pagamento.pago_em = datetime.utcnow()
    db.session.commit()

    return jsonify({"status": "ok"}), 200
