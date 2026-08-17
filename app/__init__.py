import json
import os
import warnings
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from flask import Flask
from app.extensions import db, login_manager

load_dotenv()  # lê variáveis do arquivo .env, se existir


def _formatar_data_br(dt_utc):
    """Formata um datetime em UTC (sem timezone, como vem do banco) para o
    horário de Brasília (usado pela equipe) — mesmo formato usado tanto no
    rodapé "último deploy" quanto no histórico de versões da tela de
    login."""
    if not dt_utc:
        return "desconhecido"
    dt_br = dt_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("America/Sao_Paulo"))
    return dt_br.strftime("%d/%m/%Y %H:%M") + " (horário de Brasília)"


def _carregar_info_deploy(base_dir: str):
    """Lê o arquivo "deploy_info.json" (gerado automaticamente pelo pipeline
    de deploy do GitHub Actions, ver .github/workflows/deploy.yml) para saber
    qual commit, mensagem e em que horário este ambiente foi publicado por
    último.

    Em desenvolvimento local (ou se o arquivo não existir por qualquer
    motivo) simplesmente não mostra nada — não é um erro."""
    caminho = os.path.join(base_dir, "deploy_info.json")
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            info = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None

    deploy_em_iso = info.get("deploy_em")
    deploy_em_dt = None
    if deploy_em_iso:
        try:
            # O pipeline grava o horário em UTC.
            deploy_em_dt = datetime.fromisoformat(deploy_em_iso.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            pass

    return {
        "commit": info.get("commit"),
        "commit_curto": info.get("commit_curto", "?"),
        "branch": info.get("branch", "?"),
        "mensagem": info.get("mensagem"),
        "deploy_em_dt": deploy_em_dt,
        "deploy_em_local": _formatar_data_br(deploy_em_dt) if deploy_em_dt else (deploy_em_iso or "desconhecido"),
    }


def _registrar_deploy_atual(info_deploy):
    """Grava uma linha no histórico de versões (tabela historico_deploy) na
    primeira vez que o app sobe depois de um deploy novo neste ambiente —
    a dedupe é pelo hash do commit, então reinícios do mesmo deploy (sem
    commit novo) não duplicam a linha. Cada ambiente (media-dev, media-qa,
    media-prod) tem seu próprio banco, então acumula seu próprio
    histórico. Roda dentro do app_context, depois do db.create_all() (ver
    create_app), então a tabela já existe."""
    if not info_deploy or not info_deploy.get("commit"):
        return
    from app.models import HistoricoDeploy
    try:
        ja_existe = HistoricoDeploy.query.filter_by(commit=info_deploy["commit"]).first()
        if ja_existe:
            return
        db.session.add(HistoricoDeploy(
            commit=info_deploy["commit"],
            commit_curto=info_deploy.get("commit_curto"),
            branch=info_deploy.get("branch"),
            mensagem=info_deploy.get("mensagem"),
            deploy_em=info_deploy.get("deploy_em_dt"),
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()


def _carregar_historico_deploy(limite=10):
    """Últimos N deploys registrados neste ambiente, mais recente primeiro
    — usado só para mostrar "o que foi publicado" na tela de login, ao
    lado da data do último deploy."""
    from app.models import HistoricoDeploy
    try:
        registros = (
            HistoricoDeploy.query
            .order_by(HistoricoDeploy.deploy_em.desc().nullslast(), HistoricoDeploy.id.desc())
            .limit(limite)
            .all()
        )
    except Exception:
        return []
    return [
        {
            "commit_curto": r.commit_curto,
            "branch": r.branch,
            "mensagem": r.mensagem,
            "deploy_em_local": _formatar_data_br(r.deploy_em),
        }
        for r in registros
    ]


def _resolver_uri_banco(base_dir: str) -> str:
    uri = os.environ.get("DATABASE_URL")

    if not uri:
        warnings.warn(
            "DATABASE_URL não definida. Usando SQLite local apenas como fallback de "
            "emergência — configure o arquivo .env com a conexão do PostgreSQL. "
            "Veja .env.example."
        )
        return "sqlite:///" + os.path.join(base_dir, "preparo_exames.db")

    # Alguns provedores (Heroku, Render, algumas libs antigas) ainda entregam
    # a URL com o prefixo "postgres://", que o SQLAlchemy moderno não aceita.
    if uri.startswith("postgres://"):
        uri = uri.replace("postgres://", "postgresql://", 1)

    # Usamos o driver "psycopg" (versão 3) em vez do "psycopg2", porque ele
    # tem pacotes pré-compilados disponíveis para versões mais novas do
    # Python bem mais rápido (evita erros de compilação no Windows). O .env
    # continua simples, com "postgresql://" — aqui a gente troca por
    # "postgresql+psycopg://" para o SQLAlchemy usar o driver certo.
    if uri.startswith("postgresql://"):
        uri = uri.replace("postgresql://", "postgresql+psycopg://", 1)

    return uri


def create_app():
    app = Flask(__name__)
    base_dir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "chave-secreta-para-demonstracao-troque-em-producao")
    app.config["SQLALCHEMY_DATABASE_URI"] = _resolver_uri_banco(base_dir)
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,  # evita erros de conexão "caída" em bancos remotos
    }

    info_deploy = _carregar_info_deploy(base_dir)

    db.init_app(app)
    login_manager.init_app(app)

    from flask import session as sessao_flask
    from flask_login import user_logged_in, user_logged_out

    @user_logged_in.connect_via(app)
    @user_logged_out.connect_via(app)
    def _limpar_contexto_da_sessao(_sender, user=None, **_extra):
        """Zera o contexto de tenant guardado na sessão (empresa atual e a
        filial padrão de formulário) a cada login/logout — importante em
        computadores compartilhados (ex.: recepção da clínica), onde uma
        pessoa faz logout e outra entra no mesmo navegador. Fica aqui (e não
        em routes_auth) para valer para qualquer fluxo de login."""
        sessao_flask.pop("empresa_id", None)
        sessao_flask.pop("clinica_id", None)

    from app.models import Usuario

    @login_manager.user_loader
    def load_user(user_id):
        return Usuario.query.get(int(user_id))

    from app.routes_auth import auth_bp
    from app.routes_medico import medico_bp
    from app.routes_paciente import paciente_bp
    from app.routes_dono import dono_bp
    from app.routes_relatorios import relatorios_bp
    from app.routes_grupo import grupo_bp
    from app.routes_whatsapp import whatsapp_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(medico_bp)
    app.register_blueprint(paciente_bp)
    app.register_blueprint(dono_bp)
    app.register_blueprint(relatorios_bp)
    app.register_blueprint(grupo_bp)
    app.register_blueprint(whatsapp_bp)

    @app.template_filter("hora_hhmm")
    def hora_hhmm(valor):
        """Formata um horário (objeto `time` do banco, ou string 'HH:MM'
        vinda de uma sugestão de importação) sempre como texto 'HH:MM',
        ou '' quando vazio — usado no formulário de preparo para não
        espalhar essa checagem em cada template."""
        if not valor:
            return ""
        if hasattr(valor, "strftime"):
            return valor.strftime("%H:%M")
        return str(valor)

    @app.context_processor
    def injetar_contexto_clinica():
        """Disponibiliza em todos os templates o GRUPO atual (o que
        delimita o que a pessoa vê - ver app/clinica_utils.py, Fatia 5), as
        "filiais" dele (sempre 0 ou 1 elemento a partir desta fatia - não
        existe mais "várias filiais numa empresa") e os grupos do usuário —
        para a navbar mostrar o grupo e o link de "trocar" (só quando há
        mais de um), e para as listas
        mostrarem a coluna "Filial" só quando faz diferença."""
        from flask_login import current_user
        if current_user.is_authenticated and current_user.is_staff:
            from app.clinica_utils import (
                clinica_atual, clinicas_do_usuario, empresa_atual,
                empresas_do_usuario, filiais_atuais,
            )
            filiais = filiais_atuais()
            return {
                "clinica_atual_navbar": clinica_atual(),
                "clinicas_do_usuario_navbar": clinicas_do_usuario(),
                "empresa_atual_navbar": empresa_atual(),
                "empresas_do_usuario_navbar": empresas_do_usuario(),
                "filiais_atuais_navbar": filiais,
                # True quando a pessoa atua em mais de uma filial — nesse
                # caso as telas mostram a filial de cada registro e pedem a
                # filial nos formulários de cadastro.
                "mostrar_filial": len(filiais) > 1,
            }
        return {}

    @app.route("/")
    def index():
        from flask import redirect, url_for
        from flask_login import current_user
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if current_user.is_dono:
            return redirect(url_for("dono.dashboard"))
        if current_user.is_staff:
            return redirect(url_for("medico.dashboard"))
        return redirect(url_for("paciente.dashboard"))

    with app.app_context():
        db.create_all()
        _registrar_deploy_atual(info_deploy)
        historico_deploy_lista = _carregar_historico_deploy()

    @app.context_processor
    def injetar_info_deploy():
        # Disponível em TODOS os templates (não só para quem está logado),
        # para dar para checar se o deploy automático rodou até na tela de
        # login. Lido uma única vez na inicialização do app, não a cada
        # requisição.
        return {"versao_info": info_deploy, "historico_deploy": historico_deploy_lista}

    return app
