"""Notificação da equipe (push do PWA + WhatsApp) quando chega uma
pergunta nova de paciente.

Objetivo original (push): avisar o MÉDICO no celular assim que chega uma
pergunta nova de paciente (por WhatsApp ou pela área web), sem depender
do WhatsApp de volta - resolve o mesmo problema que a Fatia 7 tentava
resolver via template aprovado na Meta, mas do lado da EQUIPE (o
paciente continua conversando pelo WhatsApp normalmente). Decisão do
Silvan: só o médico recebe (não secretária/administrativo), mesmo que
outras pessoas tenham vínculo ativo no mesmo Grupo (ver
_usuarios_para_notificar).

Como funciona o push: o navegador (Chrome/Edge no Android, Safari no iOS
16.4+ com o PWA instalado na tela de início) gera uma "inscrição"
(endpoint + chaves de criptografia) quando o usuário autoriza
notificações - isso é salvo em PushSubscription (ver app.models). Para
mandar uma notificação, o servidor assina a mensagem com uma chave VAPID
própria (par de chaves gerado uma vez, ver gerar_chaves_vapid.py) e
entrega ao serviço de push do navegador (ex.: FCM do Chrome, APNs via
webkit no Safari) - o navegador então entrega ao service worker
(app/static/sw.js) mesmo com o site fechado.

Sem as chaves VAPID configuradas (env vars VAPID_PUBLIC_KEY/
VAPID_PRIVATE_KEY/VAPID_CLAIM_EMAIL), o push é silenciosamente pulado -
mesmo padrão de "falha aberta sem quebrar o resto do sistema" usado em
app.whatsapp_envio.

Aviso por WhatsApp (pedido do Silvan, 2026-09-11): além do push, o
médico responsável também recebe um aviso de texto livre no PRÓPRIO
WhatsApp (ver _notificar_whatsapp_medicos) - decisão explícita do Silvan
de tentar texto livre primeiro, em vez de criar já um template aprovado
na Meta. Texto livre só é entregue de fato se o médico tiver mandado
mensagem para o número da clínica nas últimas 24h (ver docstring de
app.whatsapp_envio.enviar_mensagem_whatsapp) - fora dessa janela (o caso
mais comum), a Meta recusa e o aviso simplesmente não chega, sem quebrar
nada (o push e a fila em /equipe/perguntas continuam funcionando
normalmente). Se isso se mostrar pouco confiável na prática, o próximo
passo é criar um template aprovado dedicado (mesmo padrão dos outros
avisos em app.whatsapp_envio) para não depender da janela de 24h.

Link clicável no aviso (pedido do Silvan, 2026-09-11): o texto do aviso
tenta incluir um link direto e completo (com https://...) para a tela
/equipe/perguntas, em vez de só o caminho relativo - ver _link_perguntas.
Isso depende da env var opcional APP_URL_PUBLICA (ex.:
"https://dev.media.med.br") com a URL pública do site; sem essa
variável configurada, o texto cai no caminho relativo de sempre (sem
link clicável no WhatsApp, mas nada quebra) - mesmo padrão de falha
aberta usado no resto do módulo.
"""
import json
import logging
import os

from flask import current_app
from pywebpush import WebPushException, webpush

from app.extensions import db
from app.models import GrupoMembro, PushSubscription, Usuario
from app.whatsapp_envio import enviar_mensagem_whatsapp

logger = logging.getLogger(__name__)


def _vapid_configurado():
    return bool(
        current_app.config.get("VAPID_PRIVATE_KEY")
        and current_app.config.get("VAPID_CLAIM_EMAIL")
    )


def _usuarios_para_notificar(pergunta):
    """Quem deve ser avisado desta pergunta: só o(s) MÉDICO(S) responsável
    (is) pelo exame dela - o médico principal (Exame.medico_id) mais os
    médicos "extra" associados (Exame.medicos_extra, ver Exame.medicos) -
    ou, quando a pergunta é GERAL (sem exame associado), os médicos do
    Grupo que também administram pacientes (perm_pacientes), já que só
    esses veem pergunta sem exame na tela de perguntas. Esta é a MESMA
    regra usada para decidir o que aparece na listagem de cada médico
    (ver routes_medico._restringir_perguntas_para_medico) - a
    notificação não pode ser mais ampla que o que a pessoa realmente
    pode ver e responder."""
    if pergunta.exame_id:
        exame = pergunta.exame
        return [m.id for m in exame.medicos] if exame else []

    # Pergunta geral (sem exame) - só médicos com perm_pacientes, dentro
    # do mesmo Grupo (ou o próprio dono, se for uma conta solo).
    if pergunta.grupo_id:
        membros = (
            GrupoMembro.query
            .join(Usuario, Usuario.id == GrupoMembro.usuario_id)
            .filter(
                GrupoMembro.grupo_id == pergunta.grupo_id,
                GrupoMembro.ativo.is_(True),
                Usuario.tipo == "medico",
                Usuario.perm_pacientes.is_(True),
            )
            .all()
        )
        return [m.usuario_id for m in membros]
    if pergunta.criado_por_id:
        dono = Usuario.query.get(pergunta.criado_por_id)
        if dono and dono.tipo == "medico" and dono.perm_pacientes:
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
    com status "pendente" ou "aguardando_aprovacao" - ver os pontos de
    criação em app.routes_paciente e app.whatsapp_conversa. Manda push
    (se VAPID configurado) e um aviso por WhatsApp (texto livre, ver
    _notificar_whatsapp_medicos) para os mesmos médicos - os dois canais
    são independentes: um falhar/estar desconfigurado não afeta o
    outro."""
    usuarios_ids = _usuarios_para_notificar(pergunta)
    if not usuarios_ids:
        return

    if _vapid_configurado():
        subscriptions = PushSubscription.query.filter(
            PushSubscription.usuario_id.in_(usuarios_ids)
        ).all()
        if subscriptions:
            payload = {
                "title": "Nova pergunta de paciente",
                "body": f'{pergunta.paciente.nome}: "{pergunta.pergunta}"',
                "url": "/equipe/perguntas",
            }
            for subscription in subscriptions:
                _enviar_para_subscription(subscription, payload)

    _notificar_whatsapp_medicos(pergunta, usuarios_ids)


def _link_perguntas():
    """Monta o link para a tela /equipe/perguntas usando a URL pública do
    site (env var opcional APP_URL_PUBLICA, ex.: "https://dev.media.med.br")
    - com ela configurada, retorna um link completo e clicável (ex.:
    "https://dev.media.med.br/equipe/perguntas"); sem ela, cai no caminho
    relativo de sempre ("/equipe/perguntas", sem link clicável no
    WhatsApp) - ver docstring do módulo. Não usa url_for(_external=True)
    de propósito: sem SERVER_NAME/ProxyFix configurado, o esquema gerado
    poderia sair como "http://" mesmo em produção (atrás do proxy do
    Render)."""
    base = os.environ.get("APP_URL_PUBLICA")
    if base:
        return f"{base.rstrip('/')}/equipe/perguntas"
    return "/equipe/perguntas"


def _notificar_whatsapp_medicos(pergunta, usuarios_ids):
    """Manda um aviso de texto livre pelo WhatsApp para cada médico
    responsável (mesma lista de `usuarios_ids` do push, calculada por
    _usuarios_para_notificar) que tenha telefone cadastrado - ver
    docstring do módulo sobre a limitação da janela de 24h. Usa
    enviar_mensagem_whatsapp sem template (content_variables=None), que
    força o caminho de texto livre independente de qualquer
    WHATSAPP_META_TEMPLATE_* configurado - ver
    app.whatsapp_envio.enviar_mensagem_whatsapp. O link incluído no texto
    vem de _link_perguntas (ver docstring dela)."""
    medicos = Usuario.query.filter(
        Usuario.id.in_(usuarios_ids),
        Usuario.telefone.isnot(None),
    ).all()
    for medico in medicos:
        enviar_mensagem_whatsapp(
            medico.telefone,
            texto=(
                f'Nova pergunta de {pergunta.paciente.nome}: '
                f'"{pergunta.pergunta}". Responda em {_link_perguntas()}.'
            ),
        )
