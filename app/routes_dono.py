from datetime import datetime, date
from functools import wraps

from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Grupo, Agendamento, PlataformaConfig, GrupoPaciente, ChamadaIA, Usuario, Paciente, GrupoMembro
from app.clinica_utils import verificar_vencimento_grupo
from app.custo_ia import PRECOS_POR_MILHAO_TOKENS, COTACAO_USD_PARA_BRL

dono_bp = Blueprint("dono", __name__, url_prefix="/dono")


def dono_required(f):
    @wraps(f)
    def decorado(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_dono:
            flash("Acesso restrito ao dono da plataforma.", "danger")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorado


def _usuarios_com_custo():
    """Monta a lista de todo Usuario da equipe (médico/secretária),
    junto com o(s) grupo(s) de trabalho de cada um (ou nenhum, pra uma
    conta "solo" - ver Fatia 6) e o custo estimado de IA (ver
    app.custo_ia/app.models.ChamadaIA). Usado tanto no dashboard
    principal quanto na tela `usuarios` (mantida como um link direto pra
    essa mesma lista, sem o resto do dashboard)."""
    lista_usuarios = (
        Usuario.query.filter(Usuario.tipo.in_(["medico", "secretaria"]))
        .order_by(Usuario.criado_em.desc()).all()
    )

    nomes_de_grupo_por_usuario = {}
    for gm in GrupoMembro.query.filter(GrupoMembro.ativo.is_(True)).all():
        nomes_de_grupo_por_usuario.setdefault(gm.usuario_id, []).append(gm.grupo.nome)

    custo_por_usuario = {}
    for c in ChamadaIA.query.filter(ChamadaIA.usuario_id.isnot(None)).all():
        item = custo_por_usuario.setdefault(c.usuario_id, {"total_chamadas": 0, "custo_total": 0.0, "tem_custo_desconhecido": False})
        item["total_chamadas"] += 1
        if c.custo_estimado_usd is not None:
            item["custo_total"] += float(c.custo_estimado_usd)
        if c.preco_desconhecido:
            item["tem_custo_desconhecido"] = True

    return [
        {
            "usuario": u,
            "grupos": nomes_de_grupo_por_usuario.get(u.id, []),
            "custo": custo_por_usuario.get(u.id),
        }
        for u in lista_usuarios
    ]


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

    # Desde a Fatia 6, uma conta pode existir "solo" (sem Grupo nenhum) -
    # por isso os números de Grupo acima ficam zerados/baixos mesmo com
    # gente cadastrada de verdade e usando o sistema normalmente. Traz a
    # lista de usuários (com o custo de IA de cada um) direto aqui no
    # dashboard principal, pra não dar a impressão de que "não tem
    # ninguém cadastrado" - ver `_usuarios_com_custo` acima.
    linhas_usuarios = _usuarios_com_custo()
    custo_total_usuarios = sum(l["custo"]["custo_total"] for l in linhas_usuarios if l["custo"])

    return render_template(
        "dono/dashboard.html", grupos=grupos, resumo=resumo, hoje=date.today(), config=config,
        linhas_usuarios=linhas_usuarios, custo_total_usuarios=custo_total_usuarios,
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


# As 3 IAs suportadas hoje no chat de dúvidas do paciente (ver
# app.ia_preparo._PROVEDORES_CHAT) - mantido também aqui para validar o
# formulário sem precisar importar app.ia_preparo (evita import cruzado
# desnecessário; são só nomes/strings, não lógica).
PROVEDORES_CHAT_VALIDOS = ("Gemini", "ChatGPT", "Claude")


@dono_bp.route("/configuracoes/ia-chat", methods=["POST"])
@login_required
@dono_required
def configuracoes_ia_chat():
    """Escolhe quais 2 das 3 IAs (Gemini/ChatGPT/Claude) respondem o chat
    de dúvidas do paciente - ver PlataformaConfig.ia_chat_provedor_1/2 e
    app.ia_preparo.responder_com_ia. A Claude continua sempre fazendo o
    papel de árbitro/síntese quando as duas divergem, mesmo se não for
    uma das duas escolhidas aqui - ver comentário em responder_com_ia."""
    config = PlataformaConfig.obter()
    provedor_1 = request.form.get("ia_chat_provedor_1")
    provedor_2 = request.form.get("ia_chat_provedor_2")

    if provedor_1 not in PROVEDORES_CHAT_VALIDOS or provedor_2 not in PROVEDORES_CHAT_VALIDOS:
        flash("Selecione duas IAs válidas.", "danger")
        return redirect(url_for("dono.dashboard"))
    if provedor_1 == provedor_2:
        flash("Escolha duas IAs diferentes para responder o chat de dúvidas.", "danger")
        return redirect(url_for("dono.dashboard"))

    config.ia_chat_provedor_1 = provedor_1
    config.ia_chat_provedor_2 = provedor_2
    db.session.commit()
    flash(f"Chat de dúvidas do paciente agora responde com {provedor_1} e {provedor_2}.", "success")
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


@dono_bp.route("/usuarios")
@login_required
@dono_required
def usuarios():
    """Lista TODOS os usuários da equipe (médico/secretária) cadastrados
    na plataforma - independente de terem um Grupo de trabalho ou não.

    Importante desde a Fatia 6 (ver docstring de app.routes_auth.cadastro):
    uma conta pode existir "solo", sem nenhum Grupo, plenamente usável
    (cadastra paciente/exame/agendamento com escopo pessoal). O
    dashboard principal (`dashboard`, acima) só lista Grupos e itera os
    membros de cada um - uma conta solo nunca aparece ali, ficando
    completamente invisível pro dono da plataforma. Esta tela cobre esse
    ponto cego, listando a partir do Usuario direto, não do Grupo.

    Já traz junto o custo estimado de IA de cada usuário (ver
    app.custo_ia e app.models.ChamadaIA) - cada linha tem um botão que
    abre o detalhe das chamadas individuais daquele usuário
    (`custo_ia_usuario`, mesma tela usada pelo painel de custo em
    `custo_ia`).

    Desde que essa lista passou a aparecer também direto no dashboard
    principal (ver `dashboard` acima), esta rota serve como um link pra
    ver SÓ essa lista, sem o restante da tela de Grupos."""
    return render_template("dono/usuarios.html", linhas=_usuarios_com_custo())


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

    # Tabela de preços por token, só para consulta (ver app.custo_ia) -
    # ordenada por modelo, pra quem quiser conferir/entender de onde vem
    # cada valor estimado acima, sem precisar abrir o código.
    precos_por_token = sorted(
        (
            {
                "modelo": modelo,
                "preco_entrada_usd": preco_entrada,
                "preco_saida_usd": preco_saida,
            }
            for modelo, (preco_entrada, preco_saida) in PRECOS_POR_MILHAO_TOKENS.items()
        ),
        key=lambda i: i["modelo"],
    )

    return render_template(
        "dono/custo_ia.html", linhas=linhas, custo_total_geral=custo_total_geral,
        tem_custo_desconhecido_geral=tem_custo_desconhecido_geral,
        precos_por_token=precos_por_token, cotacao_usd_brl=COTACAO_USD_PARA_BRL,
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
