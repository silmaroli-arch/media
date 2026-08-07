import json
import os
import warnings
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from flask import Flask
from app.extensions import db, login_manager

load_dotenv()  # lê variáveis do arquivo .env, se existir


def _carregar_info_deploy(base_dir: str):
    """Lê o arquivo "deploy_info.json" (gerado automaticamente pelo pipeline
    de deploy do GitHub Actions, ver .github/workflows/deploy.yml) para saber
    qual commit e em que horário este ambiente foi publicado por último.

    Em desenvolvimento local (ou se o arquivo não existir por qualquer
    motivo) simplesmente não mostra nada — não é um erro."""
    caminho = os.path.join(base_dir, "deploy_info.json")
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            info = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None

    deploy_em_local = info.get("deploy_em")
    if deploy_em_local:
        try:
            # O pipeline grava o horário em UTC - convertemos para o
            # horário de Brasília (usado pela equipe), em vez de mostrar UTC.
            dt_utc = datetime.fromisoformat(deploy_em_local.replace("Z", "+00:00"))
            dt_br = dt_utc.astimezone(ZoneInfo("America/Sao_Paulo"))
            deploy_em_local = dt_br.strftime("%d/%m/%Y %H:%M") + " (horário de Brasília)"
        except ValueError:
            pass

    return {
        "commit_curto": info.get("commit_curto", "?"),
        "branch": info.get("branch", "?"),
        "deploy_em_local": deploy_em_local or "desconhecido",
    }


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

    @app.context_processor
    def injetar_info_deploy():
        # Disponível em TODOS os templates (não só para quem está logado),
        # para dar para checar se o deploy automático rodou até na tela de
        # login. Lido uma única vez na inicialização do app, não a cada
        # requisição.
        return {"versao_info": info_deploy}

    db.init_app(app)
    login_manager.init_app(app)

    from app.models import Usuario

    @login_manager.user_loader
    def load_user(user_id):
        return Usuario.query.get(int(user_id))

    from app.routes_auth import auth_bp
    from app.routes_medico import medico_bp
    from app.routes_paciente import paciente_bp
    from app.routes_dono import dono_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(medico_bp)
    app.register_blueprint(paciente_bp)
    app.register_blueprint(dono_bp)

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
        """Disponibiliza a clínica atual (e a lista de clínicas do usuário)
        em todos os templates, para a navbar mostrar o nome da clínica e o
        link de "trocar clínica" sem cada view precisar passar isso."""
        from flask_login import current_user
        if current_user.is_authenticated and current_user.is_staff:
            from app.clinica_utils import clinica_atual, clinicas_do_usuario
            return {
                "clinica_atual_navbar": clinica_atual(),
                "clinicas_do_usuario_navbar": clinicas_do_usuario(),
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

    return app
