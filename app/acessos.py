"""Contador de acessos ao site e ao sistema (pedido do Silvan, 2026-11-06).

Conta, por dia, em 4 áreas: o site público (início, login, cadastro, termos),
a equipe (médicos e secretárias logados), o portal do paciente e o link de
preparo do WhatsApp. Duas medidas: visitas (páginas abertas) e visitantes
(pessoas diferentes no dia).

Privacidade (LGPD): nada de IP nem de dado pessoal no banco. Quem é diferente
é contado por um hash com sal diário (não reversível) guardado só por 2 dias;
o que fica para sempre são apenas os totais por dia. Robôs (buscadores,
monitores, pré-visualização de link do WhatsApp) e o dono da plataforma não
entram na conta. Falha aqui nunca derruba a página."""
import hashlib
import logging
import re
from datetime import timedelta

from flask import request
from flask_login import current_user

from app.extensions import db
from app.fuso import agora_brasilia

logger = logging.getLogger(__name__)

AREAS = {
    "site": "Site público",
    "equipe": "Equipe (médicos e secretárias)",
    "paciente": "Portal do paciente",
    "link_preparo": "Link de preparo (WhatsApp)",
}
IGNORAR_PREFIXOS = ("/static", "/webhooks", "/whatsapp", "/dono", "/sw.js", "/manifest.json", "/equipe/notificacoes",
                    "/equipe/ajuda", "/favicon")
RE_ROBO = re.compile(r"bot|crawl|spider|slurp|preview|facebookexternalhit|whatsapp|monitor|uptime|health|curl|wget|python-requests|render|headless|lighthouse",
                     re.IGNORECASE)


def area_do_caminho(caminho):
    if caminho.startswith(IGNORAR_PREFIXOS):
        return None
    if caminho.startswith("/paciente/preparo/"):
        return "link_preparo"
    if caminho.startswith("/paciente"):
        return "paciente"
    if caminho.startswith("/equipe"):
        return "equipe"
    return "site"


def _chave_visitante(dia, area, secret):
    if current_user.is_authenticated:
        base = f"u{current_user.id}"
    else:
        ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()
        base = f"{ip}|{request.headers.get('User-Agent', '')}"
    return hashlib.sha256(f"{dia.isoformat()}|{area}|{base}|{secret}".encode("utf-8")).hexdigest()[:32]


def registrar(resposta, secret):
    """Conta a página que acabou de ser aberta (chamado depois de cada resposta)."""
    from app.models import AcessoDia, AcessoVisitanteDia

    if request.method != "GET" or resposta.status_code != 200 or resposta.mimetype != "text/html":
        return
    if request.headers.get("X-Requested-With") or RE_ROBO.search(request.headers.get("User-Agent", "")):
        return
    if current_user.is_authenticated and getattr(current_user, "is_dono", False):
        return
    area = area_do_caminho(request.path)
    if area is None:
        return
    dia = agora_brasilia().date()
    registro = AcessoDia.query.filter_by(dia=dia, area=area).first()
    if registro is None:
        registro = AcessoDia(dia=dia, area=area, visitas=0, visitantes=0)
        db.session.add(registro)
        AcessoVisitanteDia.query.filter(AcessoVisitanteDia.dia < dia - timedelta(days=1)).delete(synchronize_session=False)
    registro.visitas = (registro.visitas or 0) + 1
    chave = _chave_visitante(dia, area, secret)
    if not AcessoVisitanteDia.query.filter_by(dia=dia, area=area, chave=chave).first():
        db.session.add(AcessoVisitanteDia(dia=dia, area=area, chave=chave))
        registro.visitantes = (registro.visitantes or 0) + 1
    db.session.commit()


def init_app(app):
    @app.after_request
    def _contar_acesso(resposta):
        try:
            registrar(resposta, app.config["SECRET_KEY"])
        except Exception:
            db.session.rollback()
            logger.exception("Falha ao contar o acesso")
        return resposta
