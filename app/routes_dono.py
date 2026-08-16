from datetime import datetime, date
from functools import wraps

from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Grupo, Agendamento, PlataformaConfig, GrupoPaciente
from app.clinica_utils import verificar_vencimento_grupo

dono_bp = Blueprint("dono", __name__, url_prefix="/dono")


def dono_required(f):
    @wraps(f)
    def decorado(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_dono:
            flash("Acesso restrito ao dono da plataforma.", "danger")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorado


@dono_bp.route("/")
@login_required
@dono_required
def dashboard():
    # Fatia 5: cobrança passa a ser por Grupo (cada Grupo já é a própria
    # unidade autônoma, equivalente a uma filial de antes - ver decisão de
    # negócio no plano da Fatia 5, passo 1).
    grupos = Grupo.query.order_by(Grupo.criado_em.desc()).all()

    # Atualiza o status de quem venceu o trial antes de exibir a lista —
    # não existe um job em segundo plano, então isso é conferido sempre que
    # alguém (aqui, o dono) olha a lista de grupos.
    for g in grupos:
        verificar_vencimento_grupo(g)

    resumo = {
        "total": len(grupos),
        "ativas": sum(1 for g in grupos if g.status == "ativa"),
        "trial": sum(1 for g in grupos if g.status == "trial"),
        "inadimplentes": sum(1 for g in grupos if g.status == "inadimplente"),
        "bloqueadas": sum(1 for g in grupos if g.status == "bloqueada"),
    }

    config = PlataformaConfig.obter()

    return render_template(
        "dono/dashboard.html", grupos=grupos, resumo=resumo, hoje=date.today(), config=config,
    )


@dono_bp.route("/configuracoes", methods=["POST"])
@login_required
@dono_required
def configuracoes():
    config = PlataformaConfig.obter()
    trial_dias = request.form.get("trial_dias", type=int)
    if not trial_dias or trial_dias < 1:
        flash("Informe um número de dias de trial válido (maior que zero).", "danger")
        return redirect(url_for("dono.dashboard"))

    config.trial_dias = trial_dias
    db.session.commit()
    flash(f"Duração do trial atualizada para {trial_dias} dia(s). Vale para novos grupos cadastrados a partir de agora.", "success")
    return redirect(url_for("dono.dashboard"))


@dono_bp.route("/grupos/<int:grupo_id>")
@login_required
@dono_required
def grupo_detalhe(grupo_id):
    grupo = Grupo.query.get_or_404(grupo_id)
    verificar_vencimento_grupo(grupo)

    total_pacientes = (
        db.session.query(GrupoPaciente.paciente_id)
        .filter(GrupoPaciente.grupo_id == grupo.id)
        .distinct()
        .count()
    )
    total_agendamentos = Agendamento.query.filter(Agendamento.grupo_id == grupo.id).count()

    return render_template(
        "dono/grupo_detalhe.html",
        grupo=grupo,
        total_pacientes=total_pacientes,
        total_agendamentos=total_agendamentos,
        medicos=grupo.medicos_distintos,
        valor_estimado=grupo.valor_mensal_estimado,
    )


@dono_bp.route("/grupos/<int:grupo_id>/editar", methods=["POST"])
@login_required
@dono_required
def grupo_editar(grupo_id):
    grupo = Grupo.query.get_or_404(grupo_id)

    grupo.status = request.form.get("status", grupo.status)
    vencimento_str = request.form.get("data_vencimento", "").strip()
    if vencimento_str:
        try:
            grupo.data_vencimento = datetime.strptime(vencimento_str, "%Y-%m-%d").date()
        except ValueError:
            flash("Data de vencimento inválida.", "danger")
            return redirect(url_for("dono.grupo_detalhe", grupo_id=grupo.id))
    grupo.observacoes_pagamento = request.form.get("observacoes_pagamento", "").strip()

    valor_str = request.form.get("valor_por_medico", "").strip().replace(",", ".")
    if valor_str:
        try:
            grupo.valor_por_medico = float(valor_str)
        except ValueError:
            flash("Valor por médico inválido.", "danger")
            return redirect(url_for("dono.grupo_detalhe", grupo_id=grupo.id))
    else:
        grupo.valor_por_medico = None

    db.session.commit()
    flash(f"Grupo '{grupo.nome}' atualizado.", "success")
    return redirect(url_for("dono.grupo_detalhe", grupo_id=grupo.id))


@dono_bp.route("/grupos/<int:grupo_id>/bloquear", methods=["POST"])
@login_required
@dono_required
def grupo_bloquear(grupo_id):
    grupo = Grupo.query.get_or_404(grupo_id)
    grupo.status = "bloqueada"
    db.session.commit()
    flash(f"Acesso do grupo '{grupo.nome}' foi bloqueado.", "warning")
    return redirect(url_for("dono.grupo_detalhe", grupo_id=grupo.id))


@dono_bp.route("/grupos/<int:grupo_id>/desbloquear", methods=["POST"])
@login_required
@dono_required
def grupo_desbloquear(grupo_id):
    grupo = Grupo.query.get_or_404(grupo_id)
    grupo.status = "ativa"
    db.session.commit()
    flash(f"Acesso do grupo '{grupo.nome}' foi restabelecido.", "success")
    return redirect(url_for("dono.grupo_detalhe", grupo_id=grupo.id))
