from datetime import datetime, date
from functools import wraps

from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Grupo, Agendamento, PlataformaConfig, GrupoPaciente, ChamadaIA, Usuario, Paciente
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


@dono_bp.route("/custo-ia")
@login_required
@dono_required
def custo_ia():
    """Painel de custo ESTIMADO das chamadas de IA (Gemini/ChatGPT/Claude),
    somado por quem gerou cada chamada - um Usuario da equipe/médico (ao
    importar um PDF de preparo, ver app.ia_pdf_preparo) ou um Paciente
    (ao usar o chat de dúvidas, ver app.ia_preparo). Ver app.custo_ia
    para o cálculo do custo (a partir da contagem de tokens devolvida
    por cada API - nenhum provedor devolve o valor em dólares direto) e
    app.models.ChamadaIA para o que fica registrado por chamada.

    Soma tudo em memória (não em SQL) de propósito - o volume de
    chamadas de IA de uma clínica é baixo o bastante pra isso não pesar,
    e evita ter que lidar com agregação de custo NULL (modelo sem preço
    cadastrado na tabela, ver `preco_desconhecido`) direto na query."""
    todas = ChamadaIA.query.order_by(ChamadaIA.criado_em.desc()).all()

    por_pessoa = {}
    for c in todas:
        if c.usuario_id:
            chave = ("usuario", c.usuario_id)
            nome = c.usuario.nome if c.usuario else f"Usuário #{c.usuario_id} (removido)"
        else:
            chave = ("paciente", c.paciente_id)
            nome = c.paciente.nome if c.paciente else f"Paciente #{c.paciente_id} (removido)"

        item = por_pessoa.setdefault(chave, {
            "tipo": chave[0], "id": chave[1], "nome": nome,
            "total_chamadas": 0, "custo_total": 0.0, "tem_custo_desconhecido": False,
            "ultima_chamada_em": c.criado_em,
        })
        item["total_chamadas"] += 1
        if c.custo_estimado_usd is not None:
            item["custo_total"] += float(c.custo_estimado_usd)
        if c.preco_desconhecido:
            item["tem_custo_desconhecido"] = True

    linhas = sorted(por_pessoa.values(), key=lambda i: i["custo_total"], reverse=True)
    custo_total_geral = sum(i["custo_total"] for i in linhas)
    tem_custo_desconhecido_geral = any(i["tem_custo_desconhecido"] for i in linhas)

    return render_template(
        "dono/custo_ia.html", linhas=linhas, custo_total_geral=custo_total_geral,
        tem_custo_desconhecido_geral=tem_custo_desconhecido_geral,
    )


@dono_bp.route("/custo-ia/usuario/<int:usuario_id>")
@login_required
@dono_required
def custo_ia_usuario(usuario_id):
    """Detalhe das chamadas de IA feitas por um Usuario da equipe (ao
    importar PDFs de preparo) - ver `custo_ia` acima."""
    usuario = Usuario.query.get_or_404(usuario_id)
    chamadas = (
        ChamadaIA.query.filter_by(usuario_id=usuario_id)
        .order_by(ChamadaIA.criado_em.desc()).all()
    )
    return render_template(
        "dono/custo_ia_detalhe.html", pessoa_nome=usuario.nome, chamadas=chamadas,
    )


@dono_bp.route("/custo-ia/paciente/<int:paciente_id>")
@login_required
@dono_required
def custo_ia_paciente(paciente_id):
    """Detalhe das chamadas de IA feitas em nome de um Paciente (ao usar
    o chat de dúvidas) - ver `custo_ia` acima."""
    paciente = Paciente.query.get_or_404(paciente_id)
    chamadas = (
        ChamadaIA.query.filter_by(paciente_id=paciente_id)
        .order_by(ChamadaIA.criado_em.desc()).all()
    )
    return render_template(
        "dono/custo_ia_detalhe.html", pessoa_nome=paciente.nome, chamadas=chamadas,
    )
