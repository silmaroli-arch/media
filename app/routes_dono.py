from datetime import datetime, date
from functools import wraps

from flask import Blueprint, render_template, redirect, url_for, request, flash, abort, current_app
from sqlalchemy import func
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Grupo, Agendamento, PlataformaConfig, GrupoPaciente, ChamadaIA, Usuario, Paciente, GrupoMembro, LicencaPagamento, garantir_meses_licenca, meses_consecutivos_sem_pagar, MensagemSuporte, Notificacao, TipoExame, PreparoModelo, BaseConhecimentoItem, BaseConhecimentoHistorico, BaseConhecimentoSugestao
from app.base_conhecimento import (
    PROVEDORES_BUSCA, PROVEDOR_PALAVRA_CHAVE, provedor_configurado, atualizar_embedding_do_item, buscar_na_base,
    LIMIAR_PALAVRA_CHAVE, LIMIAR_EMBEDDING,
)
from app.clinica_utils import verificar_vencimento_grupo
from app.custo_ia import PRECOS_POR_MILHAO_TOKENS, COTACAO_USD_PARA_BRL
from app.mercadopago_integration import (
    criar_preferencia_pagamento, criar_preferencia_pagamento_anual,
    MercadoPagoNaoConfigurado,
)
from app.exclusao_usuario import verificar_bloqueios_exclusao, excluir_usuario_e_dados
from app.limpar_dados import apagar_todos_os_dados
from app.performance_teste import (
    contar_dados_teste, gerar_medicos_teste, gerar_pacientes_teste, apagar_dados_teste,
)

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

    # Calendário de pagamento (Fatia 8): mostra de cara se o mês corrente já
    # foi marcado como pago pra cada médico, sem precisar abrir o
    # calendário completo de cada um - ver usuario_licenca_pagamentos.
    # Garante o mês atual pra cada médico antes de contar meses seguidos
    # sem pagar - sem isso, um médico que ninguém abriu a tela dele ainda
    # este mês ficaria subcontado (mês atual "não existe" em vez de "não
    # pago").
    # Atualiza trial->ativa/inadimplência de cada médico antes de exibir a
    # lista - não existe job em segundo plano, então isso é conferido
    # sempre que o dono olha a lista (mesmo padrão de
    # verificar_vencimento_grupo no dashboard de Grupos, e do
    # staff_required no lado do médico).
    houve_mudanca = False
    for u in lista_usuarios:
        if garantir_meses_licenca(u):
            houve_mudanca = True
        if u.tipo == "medico" and u.verificar_vencimento_licenca():
            houve_mudanca = True
    if houve_mudanca:
        db.session.commit()

    mes_atual = date.today().replace(day=1)
    pago_mes_atual_por_usuario = {
        p.usuario_id: p.pago
        for p in LicencaPagamento.query.filter_by(mes=mes_atual).all()
    }

    # Restruturação de 2026-09-02: o limite de meses pra aviso de
    # inadimplência deixou de ser por médico e virou um único parâmetro
    # global (PlataformaConfig.aviso_inadimplencia_meses).
    limite_aviso_inadimplencia = PlataformaConfig.obter().aviso_inadimplencia_meses or 2

    linhas = []
    for u in lista_usuarios:
        meses_sem_pagar = meses_consecutivos_sem_pagar(u)
        linhas.append({
            "usuario": u,
            "grupos": nomes_de_grupo_por_usuario.get(u.id, []),
            "custo": custo_por_usuario.get(u.id),
            "pago_mes_atual": pago_mes_atual_por_usuario.get(u.id),
            "meses_sem_pagar": meses_sem_pagar,
            "em_alerta_inadimplencia": (
                u.tipo == "medico" and meses_sem_pagar >= limite_aviso_inadimplencia
            ),
        })
    return linhas


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

    # Pedido do Silvan (2026-09-29): a licença é sempre POR MÉDICO (Fatia
    # 8), nunca por Grupo/clínica - então o resumo acima (baseado em
    # Grupo.status) não reflete a realidade de quem cobra o quê. Este
    # resumo por status de licença de médico é o que realmente importa
    # (mesmos 4 números - total/ativas/trial/inadimplentes+bloqueadas -
    # só que contando Usuario.licenca_status em vez de Grupo.status).
    # Conta direto no banco (GROUP BY), sem carregar cada Usuario - com
    # potencialmente milhares de médicos (ex.: teste de performance, ver
    # app/performance_teste.py), carregar todo mundo em Python pra só
    # contar seria um desperdício. De propósito, NÃO chama
    # Usuario.verificar_vencimento_licenca() aqui (isso já roda a cada
    # acesso autenticado do próprio médico, ver staff_required) - repetir
    # isso pra cada médico só pra exibir o dashboard do dono adicionaria
    # uma consulta extra por médico, o que aqui seria contraproducente.
    contagem_licencas = dict(
        db.session.query(Usuario.licenca_status, func.count(Usuario.id))
        .filter(Usuario.tipo == "medico")
        .group_by(Usuario.licenca_status)
        .all()
    )
    resumo_licencas = {
        "total": sum(contagem_licencas.values()),
        "ativas": contagem_licencas.get("ativa", 0),
        "trial": contagem_licencas.get("trial", 0),
        "inadimplentes": contagem_licencas.get("inadimplente", 0),
        "bloqueadas": contagem_licencas.get("bloqueada", 0),
    }

    # Desde a Fatia 6, uma conta pode existir "solo" (sem Grupo nenhum) -
    # por isso os números de Grupo acima ficam zerados/baixos mesmo com
    # gente cadastrada de verdade e usando o sistema normalmente. Traz a
    # lista de usuários (com o custo de IA de cada um) direto aqui no
    # dashboard principal, pra não dar a impressão de que "não tem
    # ninguém cadastrado" - ver `_usuarios_com_custo` acima.
    linhas_usuarios = _usuarios_com_custo()
    custo_total_usuarios = sum(l["custo"]["custo_total"] for l in linhas_usuarios if l["custo"])

    # Contagem de mensagens novas do "Fale com a gente" (ver MensagemSuporte
    # em app/models.py), pra mostrar um badge no menu sem precisar abrir a
    # tela de mensagens.
    mensagens_suporte_novas = MensagemSuporte.query.filter_by(status="nova").count()

    return render_template(
        "dono/dashboard.html", grupos=grupos, resumo=resumo, resumo_licencas=resumo_licencas,
        hoje=date.today(), config=config,
        linhas_usuarios=linhas_usuarios, custo_total_usuarios=custo_total_usuarios,
        mensagens_suporte_novas=mensagens_suporte_novas,
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
    flash(f"Duração do trial atualizada para {trial_dias} dia(s). Vale para novos grupos e médicos cadastrados a partir de agora.", "success")
    return redirect(url_for("dono.dashboard"))


@dono_bp.route("/configuracoes/licenca-medico", methods=["POST"])
@login_required
@dono_required
def configuracoes_licenca_medico():
    """Restruturação de 2026-09-02 (pedido do Silvan): valor mensal padrão
    e limite de meses pra aviso de inadimplência deixaram de ser
    configuráveis por médico (ver antiga dono.usuario_licenca_editar) e
    viraram parâmetros globais da plataforma, editados aqui."""
    config = PlataformaConfig.obter()

    aviso_meses = request.form.get("aviso_inadimplencia_meses", type=int)
    if not aviso_meses or aviso_meses < 1:
        flash("Informe um número de meses para aviso de inadimplência válido (maior que zero).", "danger")
        return redirect(url_for("dono.dashboard"))
    config.aviso_inadimplencia_meses = aviso_meses

    valor_str = request.form.get("valor_licenca_padrao", "").strip().replace(",", ".")
    if valor_str:
        try:
            config.valor_licenca_padrao = float(valor_str)
        except ValueError:
            flash("Valor mensal padrão inválido.", "danger")
            return redirect(url_for("dono.dashboard"))
    else:
        config.valor_licenca_padrao = None

    # Pedido do Silvan (2026-09-10): valor anual padrão, INDEPENDENTE do
    # mensal acima (não é calculado como desconto) - ver
    # PlataformaConfig.valor_licenca_anual_padrao em models.py.
    valor_anual_str = request.form.get("valor_licenca_anual_padrao", "").strip().replace(",", ".")
    if valor_anual_str:
        try:
            config.valor_licenca_anual_padrao = float(valor_anual_str)
        except ValueError:
            flash("Valor anual padrão inválido.", "danger")
            return redirect(url_for("dono.dashboard"))
    else:
        config.valor_licenca_anual_padrao = None

    db.session.commit()
    flash("Configuração da licença de médico atualizada.", "success")
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


@dono_bp.route("/configuracoes/ia-validador", methods=["POST"])
@login_required
@dono_required
def configuracoes_ia_validador():
    """Escolhe qual das 3 IAs (Gemini/ChatGPT/Claude) faz a checagem
    dedicada de "isso faz sentido e é sobre este exame?" ANTES de
    qualquer chamada de resposta de verdade (pedido do Silvan,
    2026-09-24 - ver PlataformaConfig.ia_validador_pergunta e
    app.ia_preparo.validar_pergunta). Uma ÚNICA IA (diferente do chat de
    respostas, que usa 2 com reforço mútuo) - independente da escolha em
    "IAs que respondem o chat de dúvidas" acima."""
    config = PlataformaConfig.obter()
    provedor = request.form.get("ia_validador_pergunta")

    if provedor not in PROVEDORES_CHAT_VALIDOS:
        flash("Selecione uma IA válida para o validador de pergunta.", "danger")
        return redirect(url_for("dono.dashboard"))

    config.ia_validador_pergunta = provedor
    db.session.commit()
    flash(f"Validador de pergunta agora usa {provedor}.", "success")
    return redirect(url_for("dono.dashboard"))


@dono_bp.route("/configuracoes/limite-perguntas", methods=["POST"])
@login_required
@dono_required
def configuracoes_limite_perguntas():
    """Limite diário de mensagens que um paciente pode mandar sobre um
    MESMO exame, por WhatsApp (pedido do Silvan, 2026-09-24) - global
    para toda a plataforma (ver PlataformaConfig.limite_perguntas_dia_
    exame e app.whatsapp_conversa._excedeu_limite_perguntas_dia). Campo
    em branco = sem limite (comportamento padrão, sem restrição
    nenhuma)."""
    config = PlataformaConfig.obter()
    limite_str = request.form.get("limite_perguntas_dia_exame", "").strip()

    if not limite_str:
        config.limite_perguntas_dia_exame = None
        db.session.commit()
        flash("Limite diário de mensagens por exame removido - sem restrição.", "success")
        return redirect(url_for("dono.dashboard"))

    limite = request.form.get("limite_perguntas_dia_exame", type=int)
    if not limite or limite < 1:
        flash("Informe um limite diário válido (maior que zero), ou deixe em branco para não ter limite.", "danger")
        return redirect(url_for("dono.dashboard"))

    config.limite_perguntas_dia_exame = limite
    db.session.commit()
    flash(f"Limite diário de mensagens por exame atualizado para {limite}.", "success")
    return redirect(url_for("dono.dashboard"))


@dono_bp.route("/configuracoes/dicionario-chat", methods=["POST"])
@login_required
@dono_required
def configuracoes_dicionario_chat():
    """Liga/desliga a checagem de "duas ou mais palavras desconhecidas
    pelo dicionário de português" no chat de WhatsApp (pedido do Silvan,
    2026-09-29 - ver PlataformaConfig.verificar_dicionario_chat e
    app.whatsapp_conversa._eh_mensagem_com_muitas_palavras_desconhecidas).
    Criada depois de constatar que o dicionário genérico não conhece nome
    de medicamento (ex.: "paracetamol", "dipirona"), fazendo perguntas de
    paciente legítimas serem recusadas como "não consegui entender"."""
    config = PlataformaConfig.obter()
    config.verificar_dicionario_chat = bool(request.form.get("verificar_dicionario_chat"))
    db.session.commit()
    flash(
        "Checagem de dicionário no chat de WhatsApp "
        + ("ativada." if config.verificar_dicionario_chat else "desativada."),
        "success",
    )
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


@dono_bp.route("/usuarios/<int:usuario_id>/licenca", methods=["POST"])
@login_required
@dono_required
def usuario_licenca_editar(usuario_id):
    """Restruturação de 2026-09-02 (pedido do Silvan): a licença de um
    médico deixou de ser controlada campo-a-campo por aqui - trial→ativa é
    automático (ver Usuario.verificar_vencimento_licenca) e o vencimento do
    trial não é mais uma data digitada à mão. O único valor que continua
    editável por médico é o valor mensal cobrado (nasce com o padrão
    global de PlataformaConfig.valor_licenca_padrao, mas pode ser
    reajustado individualmente)."""
    usuario = Usuario.query.get_or_404(usuario_id)
    if usuario.tipo != "medico":
        abort(404)

    valor_str = request.form.get("valor_licenca_mensal", "").strip().replace(",", ".")
    if valor_str:
        try:
            usuario.valor_licenca_mensal = float(valor_str)
        except ValueError:
            flash("Valor mensal inválido.", "danger")
            return redirect(url_for("dono.usuarios"))
    else:
        usuario.valor_licenca_mensal = None

    # Pedido do Silvan (2026-09-10): valor anual individual deste médico -
    # mesmo padrão do mensal acima (nasce do padrão global, dono pode
    # reajustar por médico). Ver Usuario.valor_licenca_anual em models.py.
    valor_anual_str = request.form.get("valor_licenca_anual", "").strip().replace(",", ".")
    if valor_anual_str:
        try:
            usuario.valor_licenca_anual = float(valor_anual_str)
        except ValueError:
            flash("Valor anual inválido.", "danger")
            return redirect(url_for("dono.usuarios"))
    else:
        usuario.valor_licenca_anual = None

    db.session.commit()
    flash(f"Valor da licença de '{usuario.nome}' atualizado.", "success")
    return redirect(url_for("dono.usuarios"))


@dono_bp.route("/usuarios/<int:usuario_id>/licenca/bloquear", methods=["POST"])
@login_required
@dono_required
def usuario_licenca_bloquear(usuario_id):
    """Restruturação de 2026-09-02 (pedido do Silvan): "bloquear o acesso"
    é a ÚNICA ação manual que sobra sobre o status da licença de um médico
    - todo o resto (trial→ativa, ativa→inadimplente e de volta) é
    automático (ver Usuario.verificar_vencimento_licenca)."""
    usuario = Usuario.query.get_or_404(usuario_id)
    if usuario.tipo != "medico":
        abort(404)

    usuario.licenca_status = "bloqueada"
    db.session.commit()
    flash(f"Acesso de '{usuario.nome}' bloqueado.", "success")
    return redirect(url_for("dono.usuarios"))


@dono_bp.route("/usuarios/<int:usuario_id>/licenca/desbloquear", methods=["POST"])
@login_required
@dono_required
def usuario_licenca_desbloquear(usuario_id):
    """Reverte o bloqueio manual - o médico volta pra "ativa" (a checagem
    automática, no próximo acesso dele, reavalia se ele deveria estar em
    "inadimplente" de novo, ver Usuario.verificar_vencimento_licenca)."""
    usuario = Usuario.query.get_or_404(usuario_id)
    if usuario.tipo != "medico":
        abort(404)

    usuario.licenca_status = "ativa"
    db.session.commit()
    flash(f"Acesso de '{usuario.nome}' desbloqueado.", "success")
    return redirect(url_for("dono.usuarios"))


@dono_bp.route("/usuarios/<int:usuario_id>/licenca/pagamentos")
@login_required
@dono_required
def usuario_licenca_pagamentos(usuario_id):
    """Calendário de pagamento mensal do médico (convive com licenca_status/
    licenca_vencimento, que continuam controlando o trial/status geral) -
    controle 100% manual do dono, sem gateway de pagamento integrado
    (decisão do Silvan). Gera os meses que faltam (desde o cadastro) antes
    de exibir, pra ninguém precisar "abrir o mês" manualmente."""
    usuario = Usuario.query.get_or_404(usuario_id)
    if usuario.tipo != "medico":
        abort(404)

    if garantir_meses_licenca(usuario):
        db.session.commit()

    pagamentos = (
        LicencaPagamento.query.filter_by(usuario_id=usuario.id)
        .order_by(LicencaPagamento.mes.desc())
        .all()
    )
    return render_template("dono/usuario_licenca_pagamentos.html", usuario=usuario, pagamentos=pagamentos)


@dono_bp.route("/usuarios/<int:usuario_id>/licenca/pagamentos/<int:pagamento_id>/marcar", methods=["POST"])
@login_required
@dono_required
def usuario_licenca_pagamento_marcar(usuario_id, pagamento_id):
    """Alterna um mês entre pago/não pago - marcado manualmente pelo dono
    (não existe gateway de pagamento integrado nesta versão)."""
    usuario = Usuario.query.get_or_404(usuario_id)
    pagamento = LicencaPagamento.query.get_or_404(pagamento_id)
    if pagamento.usuario_id != usuario.id:
        abort(404)

    pagamento.pago = not pagamento.pago
    pagamento.pago_em = datetime.utcnow() if pagamento.pago else None
    db.session.commit()

    flash(
        f"{usuario.nome}: mês {pagamento.mes.strftime('%m/%Y')} marcado como {'pago' if pagamento.pago else 'não pago'}.",
        "success",
    )
    return redirect(url_for("dono.usuario_licenca_pagamentos", usuario_id=usuario.id))


@dono_bp.route("/usuarios/<int:usuario_id>/licenca/pagamentos/<int:pagamento_id>/cobrar", methods=["POST"])
@login_required
@dono_required
def usuario_licenca_pagamento_cobrar(usuario_id, pagamento_id):
    """Gera (ou regenera) a cobrança REAL desse mês via Mercado Pago
    (Checkout Pro) - fica ao lado do "marcar como pago" manual acima, não no
    lugar dele (decisão do Silvan de manter os dois caminhos: Pix fora do
    sistema, acordos informais etc continuam podendo ser marcados na mão).
    O link gerado aparece aqui pro dono repassar, e também na tela "Minha
    licença" do próprio médico (ver routes_medico.py:minha_licenca)."""
    usuario = Usuario.query.get_or_404(usuario_id)
    pagamento = LicencaPagamento.query.get_or_404(pagamento_id)
    if pagamento.usuario_id != usuario.id:
        abort(404)

    try:
        criar_preferencia_pagamento(pagamento)
    except MercadoPagoNaoConfigurado:
        flash(
            "Mercado Pago ainda não está configurado nesta instalação "
            "(defina MERCADOPAGO_ACCESS_TOKEN no .env).",
            "danger",
        )
        return redirect(url_for("dono.usuario_licenca_pagamentos", usuario_id=usuario.id))
    except ValueError as erro:
        flash(str(erro), "danger")
        return redirect(url_for("dono.usuario_licenca_pagamentos", usuario_id=usuario.id))
    except Exception:
        current_app.logger.exception(
            "Falha ao criar cobrança no Mercado Pago para o pagamento %s.", pagamento.id
        )
        flash("Não foi possível gerar a cobrança agora - tente novamente em instantes.", "danger")
        return redirect(url_for("dono.usuario_licenca_pagamentos", usuario_id=usuario.id))

    db.session.commit()
    flash(f"Cobrança gerada para {usuario.nome} ({pagamento.mes.strftime('%m/%Y')}).", "success")
    return redirect(url_for("dono.usuario_licenca_pagamentos", usuario_id=usuario.id))


@dono_bp.route("/usuarios/<int:usuario_id>/licenca/pagamentos/<int:pagamento_id>/cobrar-anual", methods=["POST"])
@login_required
@dono_required
def usuario_licenca_pagamento_cobrar_anual(usuario_id, pagamento_id):
    """Pedido do Silvan (2026-09-10, licença anual): mesma ideia de
    usuario_licenca_pagamento_cobrar acima, só que cobrando o valor ANUAL
    de uma vez (pagamento único via Checkout Pro, decisão do Silvan de não
    usar assinatura recorrente por enquanto) - só faz sentido quando
    `usuario.ciclo_licenca == "anual"` (o próprio médico escolhe isso em
    "Minha licença", ver medico.licenca_escolher_ciclo). O link gerado
    aparece aqui pro dono repassar, e também em "Minha licença" do médico.

    `pagamento_id` é o LicencaPagamento do mês em que o ciclo anual
    começa (normalmente o mês vigente, já existente via
    garantir_meses_licenca) - ver docstring de
    mercadopago_integration.criar_preferencia_pagamento_anual para o
    porquê de usar esse registro como "âncora" da cobrança."""
    usuario = Usuario.query.get_or_404(usuario_id)
    pagamento = LicencaPagamento.query.get_or_404(pagamento_id)
    if pagamento.usuario_id != usuario.id:
        abort(404)
    if usuario.ciclo_licenca != "anual":
        flash(f"{usuario.nome} não está no ciclo de cobrança anual.", "danger")
        return redirect(url_for("dono.usuario_licenca_pagamentos", usuario_id=usuario.id))

    valor_anual = usuario.valor_licenca_anual
    try:
        criar_preferencia_pagamento_anual(pagamento, valor_anual)
    except MercadoPagoNaoConfigurado:
        flash(
            "Mercado Pago ainda não está configurado nesta instalação "
            "(defina MERCADOPAGO_ACCESS_TOKEN no .env).",
            "danger",
        )
        return redirect(url_for("dono.usuario_licenca_pagamentos", usuario_id=usuario.id))
    except ValueError as erro:
        flash(str(erro), "danger")
        return redirect(url_for("dono.usuario_licenca_pagamentos", usuario_id=usuario.id))
    except Exception:
        current_app.logger.exception(
            "Falha ao criar cobrança anual no Mercado Pago para o pagamento %s.", pagamento.id
        )
        flash("Não foi possível gerar a cobrança agora - tente novamente em instantes.", "danger")
        return redirect(url_for("dono.usuario_licenca_pagamentos", usuario_id=usuario.id))

    db.session.commit()
    flash(f"Cobrança anual gerada para {usuario.nome} (a partir de {pagamento.mes.strftime('%m/%Y')}).", "success")
    return redirect(url_for("dono.usuario_licenca_pagamentos", usuario_id=usuario.id))


@dono_bp.route("/usuarios/<int:usuario_id>/excluir", methods=["POST"])
@login_required
@dono_required
def usuario_excluir(usuario_id):
    """Exclusão PERMANENTE de um médico ou secretária (substitui a antiga
    tela "Limpar dados de teste", que apagava o banco inteiro sem login -
    decisão do Silvan de trocar por uma opção escopada a uma pessoa,
    disponível de verdade em produção). Apaga a conta e tudo relacionado a
    ela (exames/agendamentos em que é responsável, licença, custo de IA,
    convites, vínculo com grupos) - pacientes cadastrados por ela só
    perdem essa atribuição, não são apagados (ver app/exclusao_usuario.py).

    Confirmação: exige a SENHA do próprio dono (decisão do Silvan) - não
    basta estar logado, porque essa ação não tem volta."""
    usuario = Usuario.query.get_or_404(usuario_id)
    if usuario.tipo not in ("medico", "secretaria"):
        abort(404)

    senha_confirmacao = request.form.get("senha_confirmacao", "")
    if not current_user.checar_senha(senha_confirmacao):
        flash("Senha incorreta - a conta NÃO foi excluída.", "danger")
        return redirect(url_for("dono.usuarios"))

    bloqueios = verificar_bloqueios_exclusao(usuario)
    if bloqueios:
        for mensagem in bloqueios:
            flash(mensagem, "danger")
        return redirect(url_for("dono.usuarios"))

    nome = usuario.nome
    excluir_usuario_e_dados(usuario)
    db.session.commit()
    flash(f'"{nome}" e todos os dados associados foram excluídos permanentemente.', "success")
    return redirect(url_for("dono.usuarios"))


@dono_bp.route("/limpar-dados", methods=["POST"])
@login_required
@dono_required
def limpar_dados_banco():
    """Apaga TODOS os dados operacionais da plataforma (médicos,
    secretárias, pacientes, grupos, exames, preparos, agendamentos,
    conversas de WhatsApp, histórico de IA etc.) - pedido explícito do
    Silvan (2026-09-10). Ver app/limpar_dados.py para o histórico completo
    de por que isso é sensível (substituiu uma ferramenta parecida que já
    tinha sido removida antes por ser insegura) e o que exatamente é
    apagado/preservado.

    Disponível para qualquer dono da plataforma, em QUALQUER ambiente
    (inclusive produção) - decisão explícita do Silvan, mesmo depois de
    avisado do histórico acima. Duas confirmações antes de executar,
    nenhuma delas contornável: a senha do próprio dono (mesmo padrão de
    usuario_excluir) e a digitação literal de "APAGAR TUDO" - a ideia é
    tornar bem difícil de disparar isso sem querer, já que não tem volta
    (nenhum backup automático é feito aqui)."""
    senha_confirmacao = request.form.get("senha_confirmacao", "")
    frase_confirmacao = request.form.get("frase_confirmacao", "").strip()

    if frase_confirmacao != "APAGAR TUDO":
        flash('Digite exatamente "APAGAR TUDO" para confirmar - nada foi apagado.', "danger")
        return redirect(url_for("dono.dashboard"))

    if not current_user.checar_senha(senha_confirmacao):
        flash("Senha incorreta - nada foi apagado.", "danger")
        return redirect(url_for("dono.dashboard"))

    apagar_todos_os_dados(current_user)
    db.session.commit()
    flash(
        "Todos os dados foram apagados (médicos, secretárias, pacientes, grupos, exames, preparos, "
        "agendamentos, conversas e histórico de IA). Sua conta de dono continua ativa.",
        "warning",
    )
    return redirect(url_for("dono.dashboard"))


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


@dono_bp.route("/usuarios/aplicar-valor-padrao", methods=["POST"])
@login_required
@dono_required
def licencas_aplicar_valor_padrao():
    """Pedido do Silvan (2026-09-29): o valor mensal padrão
    (PlataformaConfig.valor_licenca_padrao) só é copiado pro médico NO
    CADASTRO (ver routes_auth.cadastro) - médico que já existia antes do
    padrão ser definido (ou que se cadastrou com ele em branco) continua
    sem valor pra sempre, e "Minha licença" dele não consegue gerar link/
    Pix. Este botão preenche isso de uma vez.

    Regras (decididas com o Silvan):
    - só médico que está SEM valor (None) recebe o padrão - valor
      individual já negociado nunca é sobrescrito;
    - o valor anual segue a mesma regra, com o padrão anual (se houver);
    - os meses EM ABERTO desses médicos (ciclo mensal, não pagos, ainda
      sem link/Pix gerado e sem valor) recebem o novo valor - mês pago ou
      com cobrança já gerada nunca muda (é uma "fatura já emitida", ver
      LicencaPagamento.valor). Sem nenhuma chamada de rede."""
    config = PlataformaConfig.obter()
    padrao_mensal = config.valor_licenca_padrao
    padrao_anual = config.valor_licenca_anual_padrao
    if not padrao_mensal and not padrao_anual:
        flash(
            "Defina primeiro o valor mensal (ou anual) padrão em Configurações > Licença de médico.",
            "warning",
        )
        return redirect(url_for("dono.usuarios"))

    medicos_mensal = medicos_anual = meses_atualizados = 0
    for medico in Usuario.query.filter(Usuario.tipo == "medico").all():
        if padrao_mensal and medico.valor_licenca_mensal is None:
            medico.valor_licenca_mensal = padrao_mensal
            medicos_mensal += 1
        if padrao_anual and medico.valor_licenca_anual is None:
            medico.valor_licenca_anual = padrao_anual
            medicos_anual += 1

        if medico.ciclo_licenca == "mensal" and medico.valor_licenca_mensal is not None:
            abertos = LicencaPagamento.query.filter(
                LicencaPagamento.usuario_id == medico.id,
                LicencaPagamento.pago.is_(False),
                LicencaPagamento.valor.is_(None),
                LicencaPagamento.mp_init_point.is_(None),
                LicencaPagamento.pix_qr_code.is_(None),
            ).all()
            for pagamento in abertos:
                pagamento.valor = medico.valor_licenca_mensal
                meses_atualizados += 1

    db.session.commit()
    if medicos_mensal or medicos_anual or meses_atualizados:
        flash(
            f"Valor padrão aplicado: {medicos_mensal} médico(s) sem valor mensal, "
            f"{medicos_anual} sem valor anual, {meses_atualizados} mês(es) em aberto atualizado(s). "
            "Médicos que já tinham valor próprio não foram alterados.",
            "success",
        )
    else:
        flash("Nada a atualizar - todos os médicos já têm valor definido.", "success")
    return redirect(url_for("dono.usuarios"))


@dono_bp.route("/usuarios/gerar-cobrancas-ano", methods=["POST"])
@login_required
@dono_required
def licencas_gerar_cobrancas_ano():
    """Garante que existe o ITEM de pagamento (LicencaPagamento, "não
    pago") de cada mês que falta neste ano civil (do mês seguinte ao
    atual até dezembro, inclusive), para todo médico em ciclo MENSAL com
    licença já ATIVA ou INADIMPLENTE (médico em TRIAL ainda não é
    cobrado, então não faz sentido pré-criar item de pagamento pra ele;
    médico em ciclo ANUAL usa o próprio fluxo de cobrança anual - ver
    usuario_licenca_pagamento_cobrar_anual - que já cobre o ano inteiro
    num pagamento único).

    Redesenho de 2026-09-29 (pedido do Silvan, depois de um 502 Bad
    Gateway real ao clicar aqui com 1000 médicos de teste de performance
    cadastrados - ver app/performance_teste.py): esta rota ANTES também
    gerava, pra cada médico e cada mês, a cobrança REAL no Mercado Pago
    (link de Checkout Pro + Pix) - ou seja, até ~6 chamadas de rede por
    médico, TODAS dentro da mesma requisição HTTP. Com uma base grande
    de médicos, isso travava o worker do Render até ele matar a
    requisição (502), bem antes do Mercado Pago terminar de responder.
    Pior ainda: o Pix expira em ~30 minutos (ver
    app.mercadopago_integration.criar_cobranca_pix) - gerar um Pix hoje
    para um mês de dezembro nunca fazia sentido, ele já estaria expirado
    há meses quando alguém finalmente fosse usá-lo.

    Agora esta rota faz só a parte BARATA e que faz sentido gerar com
    antecedência (criar a linha do mês, sem nenhuma chamada de rede) -
    gerar a cobrança de verdade (link OU Pix) continua sendo uma ação
    manual, feita quando alguém realmente for cobrar aquele mês
    especificamente:
    - o dono gera o link em Usuários > (médico) > calendário de
      pagamento > "Gerar cobrança" (ver usuario_licenca_pagamento_cobrar);
    - o próprio médico gera o Pix em "Minha licença" (ver
      medico.minha_licenca_gerar_pix) quando for pagar.
    Sem chamada de rede nenhuma, não há mais risco de travar a
    requisição, então também não precisa mais de nenhum limite de
    quantos médicos processar por clique."""
    hoje = date.today()
    if hoje.month == 12:
        flash("Já estamos em dezembro - não há mais meses restantes neste ano civil pra gerar.", "warning")
        return redirect(url_for("dono.usuarios"))
    mes_fim = date(hoje.year, 12, 1)

    medicos = Usuario.query.filter(
        Usuario.tipo == "medico",
        Usuario.ciclo_licenca == "mensal",
        Usuario.licenca_status.in_(["ativa", "inadimplente"]),
    ).all()

    itens_criados = 0
    for medico in medicos:
        itens_criados += len(garantir_meses_licenca(medico, fim=mes_fim))
    db.session.commit()

    if itens_criados:
        flash(
            f"{itens_criados} item(ns) de pagamento criado(s), cobrindo {len(medicos)} médico(s) - "
            "gere o link ou o Pix de cada mês individualmente, na hora de cobrar de verdade.",
            "success",
        )
    else:
        flash(f"Nenhum item novo - os {len(medicos)} médico(s) elegível(is) já tinham todos os meses deste ano gerados.", "success")
    return redirect(url_for("dono.usuarios"))


# ---------- Tipos de exame (lista do dropdown do cadastro de preparo) ----------

@dono_bp.route("/tipos-exame")
@login_required
@dono_required
def tipos_exame():
    """Pedido do Silvan (2026-09-29): o dono mantém a lista de tipos de
    exame que exigem preparo (ver app.models.TipoExame e
    app.tipos_exame_padrao para a lista inicial). Mostra também quantos
    preparos usam cada tipo, para não inativar/renomear às cegas."""
    tipos = TipoExame.query.order_by(TipoExame.ordem, TipoExame.nome).all()
    uso = dict(
        db.session.query(PreparoModelo.tipo_exame_id, func.count(PreparoModelo.id))
        .filter(PreparoModelo.tipo_exame_id.isnot(None))
        .group_by(PreparoModelo.tipo_exame_id)
        .all()
    )
    sem_tipo = PreparoModelo.query.filter(PreparoModelo.tipo_exame_id.is_(None)).count()
    return render_template("dono/tipos_exame.html", tipos=tipos, uso=uso, sem_tipo=sem_tipo)


@dono_bp.route("/tipos-exame/novo", methods=["POST"])
@login_required
@dono_required
def tipos_exame_novo():
    nome = request.form.get("nome", "").strip()
    especialidades = request.form.get("especialidades", "").strip()
    if not nome:
        flash("Informe o nome do tipo de exame.", "danger")
        return redirect(url_for("dono.tipos_exame"))
    if TipoExame.query.filter(func.lower(TipoExame.nome) == nome.lower()).first():
        flash("Já existe um tipo de exame com esse nome.", "danger")
        return redirect(url_for("dono.tipos_exame"))
    ultima_ordem = db.session.query(func.max(TipoExame.ordem)).scalar() or 0
    db.session.add(TipoExame(nome=nome, especialidades=especialidades, ativo=True, ordem=ultima_ordem + 1))
    db.session.commit()
    flash(f"Tipo de exame \"{nome}\" adicionado.", "success")
    return redirect(url_for("dono.tipos_exame"))


@dono_bp.route("/tipos-exame/<int:tipo_id>/editar", methods=["POST"])
@login_required
@dono_required
def tipos_exame_editar(tipo_id):
    """Renomeia e/ou ajusta as especialidades (texto separado por vírgula)
    de um tipo. Renomear é seguro: os preparos apontam pelo id."""
    tipo = TipoExame.query.get_or_404(tipo_id)
    nome = request.form.get("nome", "").strip()
    if not nome:
        flash("Informe o nome do tipo de exame.", "danger")
        return redirect(url_for("dono.tipos_exame"))
    outro = TipoExame.query.filter(func.lower(TipoExame.nome) == nome.lower(), TipoExame.id != tipo.id).first()
    if outro:
        flash("Já existe outro tipo de exame com esse nome.", "danger")
        return redirect(url_for("dono.tipos_exame"))
    tipo.nome = nome
    tipo.especialidades = request.form.get("especialidades", "").strip()
    db.session.commit()
    flash("Tipo de exame atualizado.", "success")
    return redirect(url_for("dono.tipos_exame"))


@dono_bp.route("/tipos-exame/<int:tipo_id>/alternar", methods=["POST"])
@login_required
@dono_required
def tipos_exame_alternar(tipo_id):
    """Ativa/inativa um tipo. Inativo some do dropdown de novos preparos,
    mas os preparos que já o usam continuam com ele (nada é apagado)."""
    tipo = TipoExame.query.get_or_404(tipo_id)
    tipo.ativo = not tipo.ativo
    db.session.commit()
    flash(f"Tipo \"{tipo.nome}\" {'ativado' if tipo.ativo else 'inativado'}.", "success")
    return redirect(url_for("dono.tipos_exame"))


# ---------- Base de conhecimento compartilhada (a "terceira IA") ----------

LIMITE_ITENS_BASE_NA_TELA = 300
LIMITE_EMBEDDINGS_POR_CLIQUE = 40


def _registrar_historico_base(item, motivo):
    """Guarda a versão ATUAL (antes de mudar) do item no histórico - permite
    desfazer uma atualização ruim (decisão do Silvan)."""
    db.session.add(BaseConhecimentoHistorico(
        item_id=item.id, pergunta=item.pergunta, resposta=item.resposta,
        alterado_por_nome=current_user.nome, motivo=motivo,
    ))


@dono_bp.route("/base-conhecimento")
@login_required
@dono_required
def base_conhecimento():
    """Pedido do Silvan (2026-09-29): gestão da base compartilhada - o
    interruptor da terceira IA, o provedor de busca, os itens (filtráveis) e
    um "Testar busca" para calibrar a busca com perguntas reais antes de
    ligar de vez. Ver app.base_conhecimento e app.models.BaseConhecimentoItem."""
    config = PlataformaConfig.obter()
    tipo_id = request.args.get("tipo", type=int)
    texto = request.args.get("q", "").strip()
    status = request.args.get("status", "")

    consulta = BaseConhecimentoItem.query
    if tipo_id:
        consulta = consulta.filter(BaseConhecimentoItem.tipo_exame_id == tipo_id)
    if status in ("ativo", "inativo"):
        consulta = consulta.filter(BaseConhecimentoItem.status == status)
    if texto:
        like = f"%{texto}%"
        consulta = consulta.filter(
            BaseConhecimentoItem.pergunta.ilike(like) | BaseConhecimentoItem.resposta.ilike(like)
        )
    total = consulta.count()
    itens = consulta.order_by(BaseConhecimentoItem.tipo_exame_id, BaseConhecimentoItem.id).limit(LIMITE_ITENS_BASE_NA_TELA).all()

    teste_pergunta = request.args.get("teste", "").strip()
    teste_resultados = None
    if teste_pergunta:
        teste_resultados = buscar_na_base(teste_pergunta, tipo_exame_id=request.args.get("teste_tipo", type=int), limite=5)

    provedor = provedor_configurado()
    return render_template(
        "dono/base_conhecimento.html", config=config, itens=itens, total=total,
        tipos=TipoExame.query.order_by(TipoExame.ordem, TipoExame.nome).all(),
        filtro_tipo=tipo_id, filtro_texto=texto, filtro_status=status,
        provedores=PROVEDORES_BUSCA, provedor_atual=provedor,
        sem_embedding=BaseConhecimentoItem.query.filter(
            BaseConhecimentoItem.status == "ativo", BaseConhecimentoItem.embedding.is_(None)
        ).count() if provedor != PROVEDOR_PALAVRA_CHAVE else 0,
        nao_revisados=BaseConhecimentoItem.query.filter_by(revisado=False).count(),
        sugestoes_pendentes=BaseConhecimentoSugestao.query.filter_by(status="pendente").count(),
        teste_pergunta=teste_pergunta, teste_resultados=teste_resultados,
        limiar_palavra=LIMIAR_PALAVRA_CHAVE, limiar_embedding=LIMIAR_EMBEDDING,
        limite_tela=LIMITE_ITENS_BASE_NA_TELA,
    )


@dono_bp.route("/base-conhecimento/config", methods=["POST"])
@login_required
@dono_required
def base_conhecimento_config():
    """Interruptor da terceira IA e provedor de busca (ver
    PlataformaConfig.base_conhecimento_ativa / base_busca_provedor)."""
    provedor = request.form.get("base_busca_provedor", PROVEDOR_PALAVRA_CHAVE)
    if provedor not in PROVEDORES_BUSCA:
        flash("Provedor de busca inválido.", "danger")
        return redirect(url_for("dono.base_conhecimento"))
    config = PlataformaConfig.obter()
    mudou_provedor = config.base_busca_provedor != provedor
    config.base_conhecimento_ativa = request.form.get("base_conhecimento_ativa") == "on"
    config.base_aprendizado_ativo = request.form.get("base_aprendizado_ativo") == "on"
    config.base_busca_provedor = provedor
    db.session.commit()
    flash(
        "Configuração da base de conhecimento salva."
        + (" Como o provedor mudou, use \"Calcular vetores de busca\" para os itens existentes." if mudou_provedor and provedor != PROVEDOR_PALAVRA_CHAVE else ""),
        "success",
    )
    return redirect(url_for("dono.base_conhecimento"))


@dono_bp.route("/base-conhecimento/novo", methods=["POST"])
@login_required
@dono_required
def base_conhecimento_novo():
    tipo_id = request.form.get("tipo_exame_id", type=int)
    pergunta = request.form.get("pergunta", "").strip()
    resposta = request.form.get("resposta", "").strip()
    if not tipo_id or not TipoExame.query.get(tipo_id):
        flash("Escolha o tipo de exame.", "danger")
        return redirect(url_for("dono.base_conhecimento"))
    if not pergunta or not resposta:
        flash("Pergunta e resposta são obrigatórias.", "danger")
        return redirect(url_for("dono.base_conhecimento", tipo=tipo_id))
    item = BaseConhecimentoItem(
        tipo_exame_id=tipo_id, pergunta=pergunta, resposta=resposta,
        fonte_nome=request.form.get("fonte_nome", "").strip() or None,
        fonte_url=request.form.get("fonte_url", "").strip() or None,
        origem="dono", status="ativo", revisado=True,
        autor_usuario_id=current_user.id, autor_nome=current_user.nome,
    )
    db.session.add(item)
    db.session.flush()
    atualizar_embedding_do_item(item)
    db.session.commit()
    flash("Item adicionado à base de conhecimento.", "success")
    return redirect(url_for("dono.base_conhecimento", tipo=tipo_id))


@dono_bp.route("/base-conhecimento/sugestoes")
@login_required
@dono_required
def base_sugestoes():
    """Fila de sugestões dos médicos (alteração de item ou item novo)."""
    pendentes = BaseConhecimentoSugestao.query.filter_by(status="pendente").order_by(BaseConhecimentoSugestao.criado_em).all()
    decididas = BaseConhecimentoSugestao.query.filter(BaseConhecimentoSugestao.status != "pendente").order_by(
        BaseConhecimentoSugestao.decidido_em.desc()).limit(30).all()
    return render_template("dono/base_sugestoes.html", pendentes=pendentes, decididas=decididas)


def _notificar_autor_sugestao(sug, texto):
    if sug.autor_usuario_id:
        db.session.add(Notificacao(
            usuario_id=sug.autor_usuario_id, tipo="sugestao_base", titulo="Sua sugestão para a base compartilhada",
            mensagem=texto, link_endpoint="medico.base_compartilhada",
        ))


@dono_bp.route("/base-conhecimento/sugestoes/<int:sug_id>/aprovar", methods=["POST"])
@login_required
@dono_required
def base_sugestao_aprovar(sug_id):
    """Aplica a sugestão (o dono pode ajustar o texto antes). Alteração: guarda
    a versão anterior no histórico. Item novo: entra já revisado."""
    sug = BaseConhecimentoSugestao.query.get_or_404(sug_id)
    if sug.status != "pendente":
        flash("Esta sugestão já foi analisada.", "info")
        return redirect(url_for("dono.base_sugestoes"))
    pergunta = request.form.get("pergunta", "").strip() or sug.pergunta
    resposta = request.form.get("resposta", "").strip() or sug.resposta
    item = sug.item
    if item:
        _registrar_historico_base(item, f"Sugestão do médico {sug.autor_nome or ''} aprovada".strip())
        item.pergunta, item.resposta = pergunta, resposta
        item.revisado = True
    else:
        item = BaseConhecimentoItem(
            tipo_exame_id=sug.tipo_exame_id, pergunta=pergunta, resposta=resposta, origem="medico",
            status="ativo", revisado=True, autor_usuario_id=sug.autor_usuario_id, autor_nome=sug.autor_nome,
        )
        db.session.add(item)
        db.session.flush()
    atualizar_embedding_do_item(item)
    sug.status = "aprovada"
    sug.decidido_em = datetime.utcnow()
    sug.resposta_dono = request.form.get("resposta_dono", "").strip() or None
    _notificar_autor_sugestao(sug, "Sua sugestão foi aprovada e já está na base compartilhada.")
    db.session.commit()
    flash("Sugestão aprovada e aplicada à base.", "success")
    return redirect(url_for("dono.base_sugestoes"))


@dono_bp.route("/base-conhecimento/sugestoes/<int:sug_id>/rejeitar", methods=["POST"])
@login_required
@dono_required
def base_sugestao_rejeitar(sug_id):
    sug = BaseConhecimentoSugestao.query.get_or_404(sug_id)
    if sug.status != "pendente":
        flash("Esta sugestão já foi analisada.", "info")
        return redirect(url_for("dono.base_sugestoes"))
    sug.status = "rejeitada"
    sug.decidido_em = datetime.utcnow()
    sug.resposta_dono = request.form.get("resposta_dono", "").strip() or None
    _notificar_autor_sugestao(sug, "Sua sugestão não foi aceita." + (f" Motivo: {sug.resposta_dono}" if sug.resposta_dono else ""))
    db.session.commit()
    flash("Sugestão rejeitada.", "success")
    return redirect(url_for("dono.base_sugestoes"))


@dono_bp.route("/base-conhecimento/<int:item_id>/editar", methods=["POST"])
@login_required
@dono_required
def base_conhecimento_editar(item_id):
    """Edita pergunta/resposta/fonte/tipo. Se o texto mudou, guarda a versão
    anterior no histórico e recalcula o vetor de busca. Editar pelo dono já
    conta como revisado."""
    item = BaseConhecimentoItem.query.get_or_404(item_id)
    pergunta = request.form.get("pergunta", "").strip()
    resposta = request.form.get("resposta", "").strip()
    tipo_id = request.form.get("tipo_exame_id", type=int)
    if not pergunta or not resposta:
        flash("Pergunta e resposta são obrigatórias.", "danger")
        return redirect(url_for("dono.base_conhecimento", tipo=item.tipo_exame_id))
    if not tipo_id or not TipoExame.query.get(tipo_id):
        tipo_id = item.tipo_exame_id

    texto_mudou = pergunta != item.pergunta or resposta != item.resposta
    if texto_mudou:
        _registrar_historico_base(item, "Edição pelo dono")
        item.pergunta = pergunta
        item.resposta = resposta
    item.tipo_exame_id = tipo_id
    item.fonte_nome = request.form.get("fonte_nome", "").strip() or None
    item.fonte_url = request.form.get("fonte_url", "").strip() or None
    item.revisado = True
    if texto_mudou:
        atualizar_embedding_do_item(item)
    db.session.commit()
    flash("Item atualizado.", "success")
    return redirect(url_for("dono.base_conhecimento", tipo=item.tipo_exame_id))


@dono_bp.route("/base-conhecimento/<int:item_id>/alternar", methods=["POST"])
@login_required
@dono_required
def base_conhecimento_alternar(item_id):
    """Ativa/inativa um item (inativo nunca é usado nas respostas, mas nada
    é apagado)."""
    item = BaseConhecimentoItem.query.get_or_404(item_id)
    item.status = "inativo" if item.status == "ativo" else "ativo"
    db.session.commit()
    flash(f"Item {'ativado' if item.status == 'ativo' else 'inativado'}.", "success")
    return redirect(url_for("dono.base_conhecimento", tipo=item.tipo_exame_id))


@dono_bp.route("/base-conhecimento/<int:item_id>/desfazer", methods=["POST"])
@login_required
@dono_required
def base_conhecimento_desfazer(item_id):
    """Volta o item para a versão anterior mais recente do histórico (a
    versão atual também é guardada, então dá para "refazer")."""
    item = BaseConhecimentoItem.query.get_or_404(item_id)
    anterior = item.historico[0] if item.historico else None
    if not anterior:
        flash("Este item não tem versão anterior.", "warning")
        return redirect(url_for("dono.base_conhecimento", tipo=item.tipo_exame_id))
    pergunta_antiga, resposta_antiga = anterior.pergunta, anterior.resposta
    _registrar_historico_base(item, "Antes de desfazer")
    item.pergunta = pergunta_antiga
    item.resposta = resposta_antiga
    atualizar_embedding_do_item(item)
    db.session.commit()
    flash("Item voltou para a versão anterior.", "success")
    return redirect(url_for("dono.base_conhecimento", tipo=item.tipo_exame_id))


@dono_bp.route("/base-conhecimento/<int:item_id>/revisar", methods=["POST"])
@login_required
@dono_required
def base_conhecimento_revisar(item_id):
    item = BaseConhecimentoItem.query.get_or_404(item_id)
    item.revisado = True
    db.session.commit()
    flash("Item marcado como revisado.", "success")
    return redirect(url_for("dono.base_conhecimento", tipo=item.tipo_exame_id))


@dono_bp.route("/base-conhecimento/calcular-vetores", methods=["POST"])
@login_required
@dono_required
def base_conhecimento_calcular_vetores():
    """Calcula o vetor de busca dos itens ativos que ainda não têm (ou que
    vieram de outro modelo), no provedor de embeddings atual. Limite por
    clique de propósito (cada item é uma chamada de rede - mesma lição do 502
    de "Gerar cobranças do ano"): se sobrar, basta clicar de novo."""
    provedor = provedor_configurado()
    if provedor == PROVEDOR_PALAVRA_CHAVE:
        flash("O provedor atual é palavra-chave e não usa vetores. Escolha OpenAI ou Gemini primeiro.", "warning")
        return redirect(url_for("dono.base_conhecimento"))
    pendentes = BaseConhecimentoItem.query.filter(
        BaseConhecimentoItem.status == "ativo", BaseConhecimentoItem.embedding.is_(None)
    ).limit(LIMITE_EMBEDDINGS_POR_CLIQUE + 1).all()
    if not pendentes:
        flash("Todos os itens ativos já têm vetor de busca.", "success")
        return redirect(url_for("dono.base_conhecimento"))
    sobra = len(pendentes) > LIMITE_EMBEDDINGS_POR_CLIQUE
    ok = falhas = 0
    for item in pendentes[:LIMITE_EMBEDDINGS_POR_CLIQUE]:
        if atualizar_embedding_do_item(item, provedor):
            ok += 1
        else:
            falhas += 1
            if falhas >= 3 and ok == 0:
                break  # provavelmente sem chave/credencial - não insistir item por item
    db.session.commit()
    if ok == 0 and falhas:
        flash("Não foi possível calcular os vetores. Verifique se a chave de API do provedor está configurada.", "danger")
    else:
        flash(
            f"{ok} vetor(es) calculado(s)" + (f", {falhas} falhou(aram)" if falhas else "")
            + (". Ainda há itens pendentes: clique de novo." if sobra else "."),
            "success" if not falhas else "warning",
        )
    return redirect(url_for("dono.base_conhecimento"))


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



# ---------- "Fale com a gente" (mensagens de médico/secretária) ----------

@dono_bp.route("/mensagens-suporte")
@login_required
@dono_required
def mensagens_suporte():
    """Lista todas as mensagens de todas as clínicas (ver MensagemSuporte
    em app/models.py) - as mais novas primeiro, pra o dono sempre ver o
    que ainda não foi respondido no topo."""
    mensagens = (
        MensagemSuporte.query.order_by(
            MensagemSuporte.status == "respondida",
            MensagemSuporte.criado_em.desc(),
        ).all()
    )
    return render_template("dono/mensagens_suporte.html", mensagens=mensagens)


@dono_bp.route("/mensagens-suporte/<int:mensagem_id>/responder", methods=["POST"])
@login_required
@dono_required
def mensagens_suporte_responder(mensagem_id):
    mensagem = MensagemSuporte.query.get_or_404(mensagem_id)
    resposta = request.form.get("resposta", "").strip()
    if not resposta:
        flash("Escreva uma resposta antes de enviar.", "danger")
        return redirect(url_for("dono.mensagens_suporte"))
    mensagem.resposta = resposta
    mensagem.status = "respondida"
    mensagem.respondida_em = datetime.utcnow()
    # Avisa quem perguntou pelo sininho de notificações (pedido do Silvan,
    # 2026-09-25 - ver Notificacao em app/models.py).
    db.session.add(Notificacao(
        usuario_id=mensagem.usuario_id,
        tipo="resposta_suporte",
        titulo="Resposta do \"Fale com a gente\"",
        mensagem=resposta,
        link_endpoint="medico.fale_com_a_gente",
    ))
    db.session.commit()
    flash("Resposta enviada.", "success")
    return redirect(url_for("dono.mensagens_suporte"))


@dono_bp.route("/mensagens-suporte/<int:mensagem_id>/marcar-lida", methods=["POST"])
@login_required
@dono_required
def mensagens_suporte_marcar_lida(mensagem_id):
    mensagem = MensagemSuporte.query.get_or_404(mensagem_id)
    if mensagem.status == "nova":
        mensagem.status = "lida"
        db.session.commit()
    return redirect(url_for("dono.mensagens_suporte"))


# ---------- Anúncios (avisos manuais do dono, via sininho de notificações) ----------

@dono_bp.route("/anuncios", methods=["GET"])
@login_required
@dono_required
def anuncios():
    """Formulário pra o dono escrever um aviso e mandar pra um médico/
    secretária específico ou pra todo mundo (pedido do Silvan, 2026-09-25)
    - vira uma Notificacao (ver app/models.py) pra cada destinatário,
    mostrada no sininho do cabeçalho dele."""
    equipe = (
        Usuario.query.filter(Usuario.tipo.in_(["medico", "secretaria"]))
        .order_by(Usuario.nome)
        .all()
    )
    return render_template("dono/anuncios.html", equipe=equipe)


@dono_bp.route("/anuncios/enviar", methods=["POST"])
@login_required
@dono_required
def anuncio_enviar():
    destinatario = request.form.get("destinatario", "todos")
    titulo = request.form.get("titulo", "").strip()
    mensagem = request.form.get("mensagem", "").strip()
    if not titulo or not mensagem:
        flash("Preencha o título e a mensagem antes de enviar.", "danger")
        return redirect(url_for("dono.anuncios"))

    if destinatario == "todos":
        destinatarios = Usuario.query.filter(Usuario.tipo.in_(["medico", "secretaria"])).all()
    else:
        destinatarios = Usuario.query.filter(
            Usuario.id == destinatario, Usuario.tipo.in_(["medico", "secretaria"])
        ).all()
        if not destinatarios:
            flash("Destinatário inválido.", "danger")
            return redirect(url_for("dono.anuncios"))

    for usuario in destinatarios:
        db.session.add(Notificacao(
            usuario_id=usuario.id, tipo="anuncio", titulo=titulo, mensagem=mensagem,
        ))
    db.session.commit()
    flash(
        f"Anúncio enviado para {len(destinatarios)} pessoa{'s' if len(destinatarios) != 1 else ''}.",
        "success",
    )
    return redirect(url_for("dono.anuncios"))



@dono_bp.route("/ferramentas/performance")
@login_required
@dono_required
def ferramentas_performance():
    """Ferramenta de teste de performance (pedido do Silvan, 2026-09-29,
    ver app/performance_teste.py) - gera médicos e pacientes sintéticos
    em massa direto no banco, pra observar como a aplicação se comporta
    com uma base bem maior do que a atual. Pensada só para o ambiente de
    teste (media-dev) - link no menu do painel ("Ferramentas", pedido do
    Silvan, 2026-09-29), mas com toda ação (gerar/apagar) exigindo a
    senha do próprio dono, mesmo padrão de dono.limpar_dados_banco."""
    qtd_medicos, qtd_pacientes = contar_dados_teste()
    return render_template(
        "dono/ferramentas_performance.html",
        qtd_medicos=qtd_medicos, qtd_pacientes=qtd_pacientes,
    )


@dono_bp.route("/ferramentas/performance/gerar", methods=["POST"])
@login_required
@dono_required
def ferramentas_performance_gerar():
    senha_confirmacao = request.form.get("senha_confirmacao", "")
    if not current_user.checar_senha(senha_confirmacao):
        flash("Senha incorreta - nada foi gerado.", "danger")
        return redirect(url_for("dono.ferramentas_performance"))

    try:
        qtd_medicos = int(request.form.get("qtd_medicos", "0"))
        qtd_pacientes = int(request.form.get("qtd_pacientes", "0"))
    except ValueError:
        flash("Quantidade inválida.", "danger")
        return redirect(url_for("dono.ferramentas_performance"))

    # Limite por clique (pedido de segurança, não do Silvan): evita travar
    # a requisição/worker do Render gerando um volume enorme de uma vez só
    # - para um lote grande, é só clicar mais de uma vez.
    qtd_medicos = max(0, min(qtd_medicos, 2000))
    qtd_pacientes = max(0, min(qtd_pacientes, 10000))

    criados_medicos = gerar_medicos_teste(qtd_medicos) if qtd_medicos else 0
    criados_pacientes = gerar_pacientes_teste(qtd_pacientes) if qtd_pacientes else 0

    if not criados_medicos and not criados_pacientes:
        flash("Nada gerado - informe uma quantidade de médicos e/ou pacientes maior que zero.", "warning")
    else:
        flash(
            f"Gerados {criados_medicos} médico(s) e {criados_pacientes} paciente(s) de teste.",
            "success",
        )
    return redirect(url_for("dono.ferramentas_performance"))


@dono_bp.route("/ferramentas/performance/limpar", methods=["POST"])
@login_required
@dono_required
def ferramentas_performance_limpar():
    senha_confirmacao = request.form.get("senha_confirmacao", "")
    if not current_user.checar_senha(senha_confirmacao):
        flash("Senha incorreta - nada foi apagado.", "danger")
        return redirect(url_for("dono.ferramentas_performance"))

    qtd_medicos, qtd_pacientes = apagar_dados_teste()
    db.session.commit()
    flash(
        f"Removidos {qtd_medicos} médico(s) e {qtd_pacientes} paciente(s) de teste.",
        "success",
    )
    return redirect(url_for("dono.ferramentas_performance"))
