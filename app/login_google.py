"""Login com Google - só para a EQUIPE (médico, secretária e dono), pedido
do Silvan (2026-10-19). Paciente continua entrando com CPF + data de
nascimento (auth.login_paciente).

Um único caminho de ida e volta com o Google (OpenID Connect, só nome e
e-mail - sem acesso a Gmail/Drive/contatos), usado para três coisas,
guardadas na sessão como "intenção" antes de ir ao Google:

- "login": entra com a conta da equipe ligada a essa conta Google (pelo
  `Usuario.google_sub`) ou, na primeira vez, pela conta com o MESMO e-mail
  (e já liga as duas). Sem conta nenhuma -> vai para o cadastro público
  com nome e e-mail já preenchidos (e-mail travado, sem senha - a senha é
  opcional e pode ser criada depois em "Trocar senha").
- "vincular": quem já está logado liga uma conta Google à própria conta
  (Meus dados) - serve para quem se cadastrou com um e-mail que não é do
  Google, como o da clínica.
- "confirmar": quem não tem senha confirma a identidade pelo Google para
  liberar "Meus dados" e "Criar senha" (mesma janela de tempo da
  confirmação por senha - ver auth.meus_dados).

Só aceita e-mail que o Google marca como verificado. Sem
GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET configurados, o recurso fica
desligado sozinho (os botões nem aparecem) - mesmo padrão de "falha
aberta" do WhatsApp e do push.
"""
import os
from datetime import datetime

from authlib.integrations.flask_client import OAuth
from flask import Blueprint, current_app, flash, redirect, request, session, url_for
from flask_login import current_user, login_required, login_user

from app.extensions import db
from app.models import Usuario

oauth = OAuth()
google_bp = Blueprint("google_login", __name__)

INTENCOES = ("login", "vincular", "confirmar")
SESSAO_CADASTRO = "google_cadastro"


def configurado():
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


def init_app(app):
    app.register_blueprint(google_bp)

    @app.context_processor
    def _injetar_login_google():
        return {
            "google_login_ativo": configurado(),
            "google_cadastro": session.get(SESSAO_CADASTRO),
        }

    if not configurado():
        return
    oauth.init_app(app)
    oauth.register(
        name="google",
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )


def _url_retorno():
    """Endereço de volta do Google - precisa bater EXATAMENTE com o
    cadastrado no Google Cloud Console. No Render o app recebe a
    requisição por trás de um proxy, como http, então o https real vem no
    cabeçalho X-Forwarded-Proto."""
    url = url_for("google_login.retorno", _external=True)
    if request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip() == "https" and url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    return url


def _proximo_seguro(valor):
    """Só aceita caminho interno (começa com "/" e não com "//") - evita
    usar o login como redirecionador para outro site."""
    if valor and valor.startswith("/") and not valor.startswith("//"):
        return valor
    return None


@google_bp.route("/login/google")
def iniciar():
    if not configurado():
        flash("O login com Google não está disponível no momento.", "warning")
        return redirect(url_for("auth.login"))
    intencao = request.args.get("intencao", "login")
    if intencao not in INTENCOES:
        intencao = "login"
    if intencao in ("vincular", "confirmar") and not current_user.is_authenticated:
        return redirect(url_for("auth.login"))
    session["google_intencao"] = intencao
    session["google_proximo"] = _proximo_seguro(request.args.get("next"))
    return oauth.google.authorize_redirect(_url_retorno())


@google_bp.route("/login/google/retorno")
def retorno():
    if not configurado():
        return redirect(url_for("auth.login"))
    intencao = session.pop("google_intencao", "login")
    proximo = session.pop("google_proximo", None)

    try:
        token = oauth.google.authorize_access_token()
    except Exception:
        # Inclui a pessoa ter clicado em "Cancelar" na tela do Google.
        current_app.logger.warning("Login com Google não concluído.", exc_info=True)
        flash("Não foi possível concluir a entrada com o Google. Tente de novo.", "danger")
        return redirect(url_for("auth.meus_dados") if intencao != "login" and current_user.is_authenticated else url_for("auth.login"))

    info = token.get("userinfo") or {}
    sub = info.get("sub")
    email = (info.get("email") or "").strip().lower()
    if not sub or not email or not info.get("email_verified"):
        flash("Sua conta Google não tem um e-mail verificado - não dá para usá-la aqui.", "danger")
        return redirect(url_for("auth.login"))

    if intencao == "vincular":
        return _vincular(sub, email)
    if intencao == "confirmar":
        return _confirmar(sub, proximo)
    return _entrar(sub, email, (info.get("name") or "").strip(), proximo)


def _entrar(sub, email, nome, proximo):
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    usuario = Usuario.query.filter_by(google_sub=sub).first()
    if not usuario:
        usuario = (
            Usuario.query.filter(Usuario.email == email, Usuario.tipo != "paciente")
            .order_by(Usuario.id)
            .first()
        )
        if usuario and usuario.google_sub:
            # Mesmo e-mail, mas a conta já está ligada a OUTRA conta Google.
            flash("Esta conta já está ligada a outra conta Google. Entre com CPF e senha.", "danger")
            return redirect(url_for("auth.login"))
        if usuario:
            usuario.google_sub = sub
            db.session.commit()

    if usuario and usuario.tipo == "paciente":
        flash("A área do paciente usa CPF e data de nascimento.", "info")
        return redirect(url_for("auth.login_paciente"))

    if not usuario:
        session[SESSAO_CADASTRO] = {"sub": sub, "email": email, "nome": nome}
        flash("Não encontramos uma conta com esse e-mail. Complete seu cadastro abaixo.", "info")
        return redirect(url_for("auth.cadastro"))

    if not usuario.ativo:
        flash("Sua conta está desativada. Fale com o administrador da plataforma.", "danger")
        return redirect(url_for("auth.login"))

    # Mesma limpeza do login por senha (computadores compartilhados).
    session.pop("clinica_id", None)
    session.pop("grupo_ativo_id", None)
    session.pop(SESSAO_CADASTRO, None)
    login_user(usuario)
    return redirect(proximo or url_for("index"))


@login_required
def _vincular(sub, email):
    outro = Usuario.query.filter(Usuario.google_sub == sub, Usuario.id != current_user.id).first()
    if outro:
        flash("Essa conta Google já está ligada a outro usuário do MedIA.", "danger")
        return redirect(url_for("auth.meus_dados"))
    current_user.google_sub = sub
    db.session.commit()
    flash(f"Conta Google ({email}) ligada. A partir de agora você pode usar \"Continuar com Google\".", "success")
    return redirect(url_for("auth.meus_dados"))


@login_required
def _confirmar(sub, proximo):
    if not current_user.google_sub or current_user.google_sub != sub:
        flash("Essa não é a conta Google ligada ao seu usuário.", "danger")
        return redirect(url_for("index"))
    session["meus_dados_confirmado_em"] = datetime.utcnow().isoformat()
    return redirect(proximo or url_for("auth.meus_dados"))


@google_bp.route("/conta/google/desvincular", methods=["POST"])
@login_required
def desvincular():
    # Sem senha, desligar o Google deixaria a pessoa sem nenhum jeito de entrar.
    if not current_user.tem_senha:
        flash("Crie uma senha antes de desligar a conta Google - senão você fica sem acesso.", "danger")
        return redirect(url_for("auth.meus_dados"))
    current_user.google_sub = None
    db.session.commit()
    flash("Conta Google desligada. Entre com CPF e senha.", "success")
    return redirect(url_for("auth.meus_dados"))
