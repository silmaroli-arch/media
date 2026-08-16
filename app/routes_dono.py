from datetime import datetime, date
from functools import wraps

from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Empresa, ClinicaMembro, Agendamento, PlataformaConfig, GrupoPaciente
from app.clinica_utils import verificar_vencimento_empresa

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
    empresas = Empresa.query.order_by(Empresa.criado_em.desc()).all()

    # Atualiza o status de quem venceu o trial antes de exibir a lista —
    # não existe um job em segundo plano, então isso é conferido sempre que
    # alguém (aqui, o dono) olha a lista de empresas.
    for e in empresas:
        verificar_vencimento_empresa(e)

    resumo = {
        "total": len(empresas),
        "ativas": sum(1 for e in empresas if e.status == "ativa"),
        "trial": sum(1 for e in empresas if e.status == "trial"),
        "inadimplentes": sum(1 for e in empresas if e.status == "inadimplente"),
        "bloqueadas": sum(1 for e in empresas if e.status == "bloqueada"),
    }

    config = PlataformaConfig.obter()

    return render_template(
        "dono/dashboard.html", empresas=empresas, resumo=resumo, hoje=date.today(), config=config,
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
    flash(f"Duração do trial atualizada para {trial_dias} dia(s). Vale para novas empresas cadastradas a partir de agora.", "success")
    return redirect(url_for("dono.dashboard"))


@dono_bp.route("/empresas/<int:empresa_id>")
@login_required
@dono_required
def empresa_detalhe(empresa_id):
    empresa = Empresa.query.get_or_404(empresa_id)
    verificar_vencimento_empresa(empresa)

    filial_ids = [f.id for f in empresa.filiais]
    # Fatia 5: paciente é uma identidade global (ver Paciente em
    # app/models.py) - a contagem "desta empresa" passa a ser por
    # GrupoPaciente, nos Grupos pareados das filiais dela.
    grupo_ids = [f.grupo_pareado().id for f in empresa.filiais]
    total_pacientes = (
        db.session.query(GrupoPaciente.paciente_id)
        .filter(GrupoPaciente.grupo_id.in_(grupo_ids or [0]))
        .distinct()
        .count()
    )
    total_agendamentos = Agendamento.query.filter(Agendamento.clinica_id.in_(filial_ids)).count() if filial_ids else 0
    membros_por_filial = {
        f.id: ClinicaMembro.query.filter_by(clinica_id=f.id).all() for f in empresa.filiais
    }

    return render_template(
        "dono/empresa_detalhe.html",
        empresa=empresa,
        membros_por_filial=membros_por_filial,
        total_pacientes=total_pacientes,
        total_agendamentos=total_agendamentos,
        medicos=empresa.medicos_distintos,
        valor_estimado=empresa.valor_mensal_estimado,
    )


@dono_bp.route("/empresas/<int:empresa_id>/editar", methods=["POST"])
@login_required
@dono_required
def empresa_editar(empresa_id):
    empresa = Empresa.query.get_or_404(empresa_id)

    empresa.status = request.form.get("status", empresa.status)
    vencimento_str = request.form.get("data_vencimento", "").strip()
    if vencimento_str:
        try:
            empresa.data_vencimento = datetime.strptime(vencimento_str, "%Y-%m-%d").date()
        except ValueError:
            flash("Data de vencimento inválida.", "danger")
            return redirect(url_for("dono.empresa_detalhe", empresa_id=empresa.id))
    empresa.observacoes_pagamento = request.form.get("observacoes_pagamento", "").strip()

    valor_str = request.form.get("valor_por_medico", "").strip().replace(",", ".")
    if valor_str:
        try:
            empresa.valor_por_medico = float(valor_str)
        except ValueError:
            flash("Valor por médico inválido.", "danger")
            return redirect(url_for("dono.empresa_detalhe", empresa_id=empresa.id))
    else:
        empresa.valor_por_medico = None

    db.session.commit()
    flash(f"Empresa '{empresa.nome}' atualizada.", "success")
    return redirect(url_for("dono.empresa_detalhe", empresa_id=empresa.id))


@dono_bp.route("/empresas/<int:empresa_id>/bloquear", methods=["POST"])
@login_required
@dono_required
def empresa_bloquear(empresa_id):
    empresa = Empresa.query.get_or_404(empresa_id)
    empresa.status = "bloqueada"
    db.session.commit()
    flash(f"Acesso da empresa '{empresa.nome}' (todas as filiais) foi bloqueado.", "warning")
    return redirect(url_for("dono.empresa_detalhe", empresa_id=empresa.id))


@dono_bp.route("/empresas/<int:empresa_id>/desbloquear", methods=["POST"])
@login_required
@dono_required
def empresa_desbloquear(empresa_id):
    empresa = Empresa.query.get_or_404(empresa_id)
    empresa.status = "ativa"
    db.session.commit()
    flash(f"Acesso da empresa '{empresa.nome}' foi restabelecido.", "success")
    return redirect(url_for("dono.empresa_detalhe", empresa_id=empresa.id))
