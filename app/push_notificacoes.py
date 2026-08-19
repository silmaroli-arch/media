"""Notificação push (Web Push) para o PWA da equipe.

Objetivo: avisar o MÉDICO no celular assim que chega uma pergunta nova de
paciente (por WhatsApp ou pela área web), sem depender do WhatsApp de
volta - resolve o mesmo problema que a Fatia 7 tentava resolver via
Content Template da Twilio/Meta, mas do lado da EQUIPE (o paciente
continua conversando pelo WhatsApp normalmente). Decisão do Silvan: só o
médico recebe (não secretária/administrativo), mesmo que outras pessoas
tenham vínculo ativo no mesmo Grupo (ver _usuarios_para_notificar).

Como funciona: o navegador (Chrome/Edge no Android, Safari no iOS 16.4+
com o PWA instalado na tela de início) gera uma "inscrição" (endpoint +
chaves de criptografia) quando o usuário autoriza notificações - isso é
salvo em PushSubscription (ver app.models). Para mandar uma notificação,
o servidor assina a mensagem com uma chave VAPID própria (par de chaves
gerado uma vez, ver gerar_chaves_vapid.py) e entrega ao serviço de push
do navegador (ex.: FCM do Chrome, APNs via webkit no Safari) - o
navegador então entrega ao service worker (app/static/sw.js) mesmo com o
site fechado.

Sem as chaves VAPID configuradas (env vars VAPID_PUBLIC_KEY/
VAPID_PRIVATE_KEY/VAPID_CLAIM_EMAIL), toda notificação é silenciosamente
pulada - mesmo padrão de "falha aberta sem quebrar o resto do sistema"
usado em app.whatsapp_envio.
"""
import json
import logging

from flask import current_app
from pywebpush import WebPushException, webpush

from app.extensions import db
from app.models import GrupoMembro, PushSubscription, Usuario

logger = logging.getLogger(__name__)


def _vapid_configurado():
    return bool(
        current_app.config.get("VAPID_PRIVATE_KEY")
        and current_app.config.get("VAPID_CLAIM_EMAIL")
    )


def _usuarios_para_notificar(pergunta):
    """Quem deve ser avisado desta pergunta: só quem é MÉDICO (tipo ==
    "medico") - decisão do Silvan de que a notificação é só para o
    médico, não para secretária/administrativo, mesmo que eles também
    tenham vínculo ativo no Grupo. Entre os médicos, todo mundo com
    vínculo ativo no Grupo dela, ou só o próprio dono da pergunta (conta
    solo sem Grupo ainda, e só se ele mesmo for médico). Simplificação
    aceita aqui: um médico que só atende exames de outro colega também
    recebe o aviso (a restrição fina por exame só existe na LISTAGEM,
    ver routes_medico._restringir_perguntas_para_medico) - é só um alerta,
    quem abrir a lista continua vendo apenas o que tem permissão."""
    if pergunta.grupo_id:
        membros = (
            GrupoMembro.query
            .join(Usuario, Usuario.id == GrupoMembro.usuario_id)
            .filter(GrupoMembro.grupo_id == pergunta.grupo_id, GrupoMembro.ativo.is_(True), Usuario.tipo == "medico")
            .all()
        )
        return [m.usuario_id for m in membros]
    if pergunta.criado_por_id:
        dono = Usuario.query.get(pergunta.criado_por_id)
        if dono and dono.tipo == "medico":
            return [dono.id]
        return []
    return []


def _enviar_para_subscription(subscription, payload):
    try:
        webpush(
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
            },
            data=json.dumps(payload),
            vapid_private_key=current_app.config["VAPID_PRIVATE_KEY"],
            vapid_claims={"sub": current_app.config["VAPID_CLAIM_EMAIL"]},
        )
    except WebPushException as exc:
        status = getattr(exc.response, "status_code", None)
        if status in (404, 410):
            # Inscrição expirada/revogada (ex.: usuário desinstalou o PWA
            # ou trocou de aparelho) - remove para não tentar de novo.
            db.session.delete(subscription)
            db.session.commit()
        else:
            logger.exception("Falha ao enviar notificação push: %s", exc)


def notificar_equipe_nova_pergunta(pergunta):
    """Chamar logo depois de criar (e comitar) uma PerguntaPendente nova
    com status "pendente" ou "aguardando_aprovacao" - ver os 4 pontos de
    criação em app.routes_paciente e app.whatsapp_conversa."""
    if not _vapid_configurado():
        return

    usuarios_ids = _usuarios_para_notificar(pergunta)
    if not usuarios_ids:
        return

    subscriptions = PushSubscription.query.filter(
        PushSubscription.usuario_id.in_(usuarios_ids)
    ).all()
    if not subscriptions:
        return

    payload = {
        "title": "Nova pergunta de paciente",
        "body": f'{pergunta.paciente.nome}: "{pergunta.pergunta}"',
        "url": "/equipe/perguntas",
    }
    for subscription in subscriptions:
        _enviar_para_subscription(subscription, payload)
