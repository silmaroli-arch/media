from datetime import date, datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from flask_login import login_user, logout_user, login_required, current_user

from app.extensions import db
from app.models import Usuario, Empresa, Clinica, ClinicaMembro, PlataformaConfig, normalizar_telefone

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")
        usuario = Usuario.query.filter_by(email=email).first()

        if usuario and usuario.ativo and usuario.checar_senha(senha):
            # Remove qualquer seleção de clínica de uma sessão anterior —
            # importante em computadores compartilhados (ex.: recepção da
            # clínica), onde uma pessoa pode fazer logout e outra logar
            # em seguida no mesmo navegador.
            session.pop("clinica_id", None)
            login_user(usuario)
            return redirect(url_for("index"))

        flash("E-mail ou senha inválidos.", "danger")

    return render_template("auth/login.html")


@auth_bp.route("/login-paciente", methods=["GET", "POST"])
def login_paciente():
    """Login do paciente: sem senha, feito só com telefone + data de
    nascimento (a secretária/médico cadastra o telefone e a data de
    nascimento na hora de criar o paciente — ver medico.pacientes_novo)."""
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        telefone = normalizar_telefone(request.form.get("telefone", ""))
        data_nascimento_str = request.form.get("data_nascimento", "").strip()

        data_nascimento = None
        if data_nascimento_str:
            # Aceita o formato brasileiro (DD/MM/AAAA, usado pelo campo com
            # máscara) e, por compatibilidade, o formato ISO (AAAA-MM-DD,
            # usado antes quando o campo era um <input type="date"> nativo).
            for formato in ("%d/%m/%Y", "%Y-%m-%d"):
                try:
                    data_nascimento = datetime.strptime(data_nascimento_str, formato).date()
                    break
                except ValueError:
                    continue

        usuario = Usuario.query.filter_by(telefone=telefone, tipo="paciente").first() if telefone else None

        if (
            usuario
            and usuario.ativo
            and data_nascimento
            and usuario.paciente
            and usuario.paciente.data_nascimento == data_nascimento
        ):
            session.pop("clinica_id", None)
            login_user(usuario)
            return redirect(url_for("index"))

        flash("Telefone ou data de nascimento incorretos.", "danger")

    return render_template("auth/login_paciente.html")


@auth_bp.route("/logout")
@login_required
def logout():
    session.pop("clinica_id", None)
    logout_user()
    return redirect(url_for("auth.login"))


@auth_bp.route("/trocar-senha", methods=["GET", "POST"])
@login_required
def trocar_senha():
    """Disponível para dono, secretária e médico trocarem a própria senha.
    Pacientes não têm senha — o acesso deles é por telefone + data de
    nascimento (ver auth.login_paciente)."""
    if not current_user.tem_senha:
        flash("Seu acesso não usa senha — você entra informando telefone e data de nascimento.", "info")
        return redirect(url_for("index"))

    if request.method == "POST":
        senha_atual = request.form.get("senha_atual", "")
        senha_nova = request.form.get("senha_nova", "")
        senha_confirmacao = request.form.get("senha_confirmacao", "")

        if not current_user.checar_senha(senha_atual):
            flash("Senha atual incorreta.", "danger")
            return render_template("auth/trocar_senha.html")

        if len(senha_nova) < 6:
            flash("A nova senha deve ter pelo menos 6 caracteres.", "danger")
            return render_template("auth/trocar_senha.html")

        if senha_nova != senha_confirmacao:
            flash("A confirmação não corresponde à nova senha.", "danger")
            return render_template("auth/trocar_senha.html")

        current_user.set_senha(senha_nova)
        db.session.commit()
        flash("Senha alterada com sucesso.", "success")
        return redirect(url_for("index"))

    return render_template("auth/trocar_senha.html")


@auth_bp.route("/cadastro", methods=["GET", "POST"])
def cadastro():
    """Cadastro público: qualquer pessoa pode criar uma empresa nova na
    plataforma, já com sua primeira filial. Nem toda clínica tem uma
    secretária, então quem se cadastra escolhe se é médico ou secretário(a)
    — mas de qualquer forma, por ser quem está criando a empresa, essa
    pessoa recebe todas as permissões administrativas (pode cadastrar
    pacientes, gerenciar equipe, filiais e os dados da clínica). Depois,
    ela pode ajustar as permissões de cada pessoa que adicionar à equipe."""
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        nome_empresa = request.form.get("nome_empresa", "").strip()
        nome_filial = request.form.get("nome_filial", "").strip()
        nome = request.form.get("nome", "").strip()
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")
        papel = request.form.get("papel", "secretaria")

        if not nome_empresa or not nome_filial or not nome or not email or not senha:
            flash("Preencha todos os campos.", "danger")
            return render_template("auth/cadastro.html")

        if papel not in ("medico", "secretaria"):
            flash("Escolha se você é médico(a) ou secretário(a).", "danger")
            return render_template("auth/cadastro.html")

        if len(senha) < 6:
            flash("A senha deve ter pelo menos 6 caracteres.", "danger")
            return render_template("auth/cadastro.html")

        if Usuario.query.filter_by(email=email).first():
            flash("Já existe uma conta com esse e-mail. Faça login ou use outro e-mail.", "danger")
            return render_template("auth/cadastro.html")

        trial_dias = PlataformaConfig.obter().trial_dias
        empresa = Empresa(
            nome=nome_empresa,
            email_contato=email,
            status="trial",
            data_vencimento=date.today() + timedelta(days=trial_dias),
        )
        db.session.add(empresa)
        db.session.flush()

        filial = Clinica(empresa_id=empresa.id, nome=nome_filial, email_contato=email)
        db.session.add(filial)
        db.session.flush()

        usuario = Usuario(nome=nome, email=email, tipo=papel)
        usuario.set_senha(senha)
        # Quem cria a empresa é a administradora inicial — recebe todas as
        # permissões administrativas independentemente de ser médico(a) ou
        # secretário(a), já que a clínica pode não ter uma secretária.
        usuario.conceder_todas_permissoes()
        db.session.add(usuario)
        db.session.flush()

        vinculo = ClinicaMembro(clinica_id=filial.id, usuario_id=usuario.id, ativo=True)
        db.session.add(vinculo)
        db.session.commit()

        login_user(usuario)
        session["clinica_id"] = filial.id

        flash(
            f"Empresa '{empresa.nome}' criada com sucesso, com a filial '{filial.nome}'! "
            "Agora você já pode cadastrar outras filiais, médicos, secretárias e pacientes.",
            "success",
        )
        return redirect(url_for("medico.dashboard"))

    return render_template("auth/cadastro.html")
