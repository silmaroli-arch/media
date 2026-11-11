"""Contador de acessos ao site e ao sistema (pedido do Silvan, 2026-11-06).

Conta, por dia, em 4 áreas: o site público (início, login, cadastro, termos),
a equipe (médicos e secretárias logados), o portal do paciente e o link de
preparo do WhatsApp. Duas medidas: visitas (páginas abertas) e visitantes
(pessoas diferentes no dia).

VISITA = UM ACESSO (2026-11-11): navegar e usar funções NÃO soma. Conta-se uma
visita quando a pessoa entra numa área e outra só depois de 30 minutos parada
(cookie de sessão "ms", que guarda só as letras das áreas já abertas nesta
sessão, sem identificar ninguém). "Visitantes" continuam sendo as pessoas
diferentes no dia.

Para investigar picos há também o detalhe por página e por tipo de navegador
(AcessoDetalheDia) e a lista de APARELHOS (AcessoDispositivoDia): cada
navegador recebe um código aleatório genérico, guardado em cookie por 90 dias,
sem IP e sem ligação com a pessoa. Robôs costumam não guardar cookie: aparecem
como muitos aparelhos "novos" com 1 visita só.

Só conta quem parece gente (2026-11-11): o navegador avisa (POST /_a, script em
templates/_contador.html) depois de 2 segundos com a página visível e algum
gesto (toque, rolagem, tecla, mouse) e devolvendo o cookie de aparelho. Robôs de
varredura abrem a página, mas não fazem nada disso, então deixam de inflar os
números.

Privacidade (LGPD): nada de IP nem de dado pessoal no banco. Quem é diferente
é contado por um hash com sal diário (não reversível) guardado só por 2 dias;
o que fica para sempre são apenas os totais por dia. Robôs (buscadores,
monitores, pré-visualização de link do WhatsApp) e o dono da plataforma não
entram na conta. Falha aqui nunca derruba a página."""
import hashlib
import logging
import re
import secrets
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


RE_CELULAR = re.compile(r"mobile|android|iphone|ipad", re.IGNORECASE)
RE_NAVEGADOR = re.compile(r"mozilla|chrome|safari|firefox|edg|opera|samsungbrowser", re.IGNORECASE)


def tipo_navegador(user_agent):
    """Celular, Computador, Programa/script (não parece navegador) ou
    Sem identificação (cabeçalho vazio)."""
    ua = (user_agent or "").strip()
    if not ua:
        return "Sem identificação"
    if not RE_NAVEGADOR.search(ua):
        return "Programa/script"
    return "Celular" if RE_CELULAR.search(ua) else "Computador"


def normalizar_pagina(caminho):
    """O caminho sem códigos que mudam a cada pessoa (token do link de preparo)."""
    caminho = re.sub(r"^(/paciente/preparo/)[^/]+", r"\1...", caminho)
    return caminho[:120] or "/"


COOKIE_SESSAO = "ms"
DURACAO_SESSAO = 30 * 60
LETRA_AREA = {"site": "s", "equipe": "e", "paciente": "p", "link_preparo": "l"}
COOKIE_DISPOSITIVO = "mid"
VALIDADE_COOKIE = 90 * 24 * 3600
RE_CODIGO = re.compile(r"[0-9A-F]{8}")


def _chave_visitante(dia, area, secret):
    if current_user.is_authenticated:
        base = f"u{current_user.id}"
    else:
        ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()
        base = f"{ip}|{request.headers.get('User-Agent', '')}"
    return hashlib.sha256(f"{dia.isoformat()}|{area}|{base}|{secret}".encode("utf-8")).hexdigest()[:32]


RE_CAMINHO = re.compile(r"/[A-Za-z0-9_\-./]*")


def registrar(caminho, resposta, secret):
    """Conta uma página aberta por uma pessoa (o navegador avisa em /_a)."""
    from app.models import AcessoDetalheDia, AcessoDia, AcessoDispositivoDia, AcessoVisitanteDia

    if RE_ROBO.search(request.headers.get("User-Agent", "")):
        return
    if current_user.is_authenticated and getattr(current_user, "is_dono", False):
        return
    if not isinstance(caminho, str) or len(caminho) > 200 or not RE_CAMINHO.fullmatch(caminho):
        return
    area = area_do_caminho(caminho)
    if area is None:
        return
    dia = agora_brasilia().date()
    # Sessão: uma visita por área a cada 30 min de uso (o cookie renova a cada aviso).
    abertas = {c for c in request.cookies.get(COOKIE_SESSAO, "") if c in "sepl"}
    letra = LETRA_AREA[area]
    visita_da_area = letra not in abertas
    sessao_nova = not abertas
    abertas.add(letra)
    resposta.set_cookie(COOKIE_SESSAO, "".join(sorted(abertas)), max_age=DURACAO_SESSAO, httponly=True,
                        samesite="Lax", secure=request.is_secure)
    registro = AcessoDia.query.filter_by(dia=dia, area=area).first()
    if registro is None:
        registro = AcessoDia(dia=dia, area=area, visitas=0, visitantes=0)
        db.session.add(registro)
        AcessoVisitanteDia.query.filter(AcessoVisitanteDia.dia < dia - timedelta(days=1)).delete(synchronize_session=False)
        AcessoDetalheDia.query.filter(AcessoDetalheDia.dia < dia - timedelta(days=35)).delete(synchronize_session=False)
        AcessoDispositivoDia.query.filter(AcessoDispositivoDia.dia < dia - timedelta(days=90)).delete(synchronize_session=False)
    pagina, navegador = normalizar_pagina(caminho), tipo_navegador(request.headers.get("User-Agent"))
    detalhe = AcessoDetalheDia.query.filter_by(dia=dia, area=area, pagina=pagina, navegador=navegador).first()
    if detalhe is None:
        detalhe = AcessoDetalheDia(dia=dia, area=area, pagina=pagina, navegador=navegador, visitas=0)
        db.session.add(detalhe)
    if visita_da_area:
        detalhe.visitas = (detalhe.visitas or 0) + 1
        registro.visitas = (registro.visitas or 0) + 1
    chave = _chave_visitante(dia, area, secret)
    if not AcessoVisitanteDia.query.filter_by(dia=dia, area=area, chave=chave).first():
        db.session.add(AcessoVisitanteDia(dia=dia, area=area, chave=chave))
        registro.visitantes = (registro.visitantes or 0) + 1
    # Aparelho: código aleatório genérico guardado num cookie (sem IP, sem nome).
    codigo = request.cookies.get(COOKIE_DISPOSITIVO, "")
    codigo_novo = not RE_CODIGO.fullmatch(codigo)
    if codigo_novo:
        codigo = secrets.token_hex(4).upper()
        resposta.set_cookie(COOKIE_DISPOSITIVO, codigo, max_age=VALIDADE_COOKIE, httponly=True, samesite="Lax",
                            secure=request.is_secure)
    agora = agora_brasilia()
    aparelho = AcessoDispositivoDia.query.filter_by(dia=dia, codigo=codigo).first()
    if aparelho is None:
        aparelho = AcessoDispositivoDia(dia=dia, codigo=codigo, tipo=navegador, visitas=0, primeiro_em=agora, novo=codigo_novo)
        db.session.add(aparelho)
    if sessao_nova:
        aparelho.visitas = (aparelho.visitas or 0) + 1
    aparelho.ultimo_em = agora
    db.session.commit()


def init_app(app):
    @app.route("/_a", methods=["POST"], endpoint="contar_acesso")
    def contar_acesso():
        """Aviso do navegador de que uma pessoa está mesmo na página."""
        resposta = app.response_class(status=204)
        if request.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
            return resposta
        try:
            dados = request.get_json(silent=True) or {}
            registrar(dados.get("p"), resposta, app.config["SECRET_KEY"])
        except Exception:
            db.session.rollback()
            logger.exception("Falha ao contar o acesso")
        return resposta
