"""Relatórios gerenciais para a equipe da clínica (secretária/médico):
financeiro, agenda/operação, pacientes e desempenho por médico. Cada
relatório aceita filtro de período (e, quando faz sentido, de médico) pela
query string, mostra uma tabela com totais e um gráfico, e pode ser
exportado em CSV, Excel ou PDF pela mesma URL (parâmetro `formato`).

Vale sempre a mesma regra de escopo dos outros módulos: tudo é filtrado
pela `clinica_atual()`, nunca vaza dado de outra clínica/filial."""
from collections import OrderedDict

from flask import Blueprint, render_template, request
from flask_login import login_required, current_user
from sqlalchemy import func

from app.extensions import db
from app.models import Agendamento, Pagamento, Paciente
from app.clinica_utils import clinica_atual
from app.routes_medico import staff_required, eh_medico, medicos_da_clinica
from app.relatorios_utils import (
    periodo_do_filtro, intervalo_datetime, exportar_csv, exportar_xlsx, exportar_pdf,
)

relatorios_bp = Blueprint("relatorios", __name__, url_prefix="/equipe/relatorios")

FORMAS_PAGAMENTO_LABEL = {
    "dinheiro": "Dinheiro", "cartao": "Cartão", "pix": "Pix", "outro": "Outro",
}
STATUS_AGENDAMENTO_LABEL = OrderedDict([
    ("solicitado", "Solicitado"), ("agendado", "Agendado"),
    ("confirmado", "Confirmado"), ("realizado", "Realizado"),
    ("nao_compareceu", "Não compareceu"), ("cancelado", "Cancelado"),
])
STATUS_NFSE_LABEL = {
    "nao_emitida": "Não emitida", "simulada": "Simulada (modo teste)",
    "assinada_pendente_envio": "Pendente de envio/contingência", "enviada": "Enviada",
}


def _medico_restrito_a_si_mesmo():
    """Um médico sem perm_equipe só deve ver os próprios números nos
    relatórios que comparam pessoas (desempenho, agenda por médico) —
    mesma regra já aplicada em outras telas (ex.: pacientes_lista)."""
    return eh_medico() and not current_user.perm_equipe


def _filtro_medico_id(clinica):
    """Lê o médico escolhido no filtro (`medico_id` na query string).
    Quando o usuário logado é um médico sem perm_equipe, ignora o que
    vier na query string e força o próprio id — o filtro na tela nem
    aparece pra essa pessoa, mas isso evita que alguém monte a URL na mão
    para ver o desempenho de outro médico."""
    if _medico_restrito_a_si_mesmo():
        return current_user.id
    bruto = request.args.get("medico_id", "").strip()
    if bruto.isdigit():
        return int(bruto)
    return None


@relatorios_bp.route("/")
@login_required
@staff_required
def index():
    """Painel com um resumo rápido de cada área — cada card leva para o
    relatório completo, já com os mesmos 30 dias usados aqui."""
    clinica = clinica_atual()
    data_inicio, data_fim = periodo_do_filtro()
    inicio_dt, fim_dt = intervalo_datetime(data_inicio, data_fim)

    receita_periodo = (
        db.session.query(func.coalesce(func.sum(Pagamento.valor_final), 0))
        .join(Agendamento, Agendamento.id == Pagamento.agendamento_id)
        .filter(Agendamento.clinica_id == clinica.id, Pagamento.pago_em.between(inicio_dt, fim_dt))
        .scalar()
    )

    agendamentos_periodo = Agendamento.query.filter(
        Agendamento.clinica_id == clinica.id, Agendamento.data_hora.between(inicio_dt, fim_dt),
    )
    total_agendamentos = agendamentos_periodo.count()
    total_realizados = agendamentos_periodo.filter(Agendamento.status == "realizado").count()
    total_cancelados = agendamentos_periodo.filter(Agendamento.status == "cancelado").count()
    total_no_show = agendamentos_periodo.filter(Agendamento.status == "nao_compareceu").count()

    novos_pacientes = Paciente.query.filter(
        Paciente.clinica_id == clinica.id, Paciente.criado_em.between(inicio_dt, fim_dt),
    ).count()
    cadastros_pendentes = Paciente.query.filter_by(clinica_id=clinica.id, status_cadastro="pendente").count()

    return render_template(
        "relatorios/index.html",
        data_inicio=data_inicio, data_fim=data_fim,
        receita_periodo=receita_periodo,
        total_agendamentos=total_agendamentos, total_realizados=total_realizados, total_cancelados=total_cancelados,
        total_no_show=total_no_show,
        novos_pacientes=novos_pacientes, cadastros_pendentes=cadastros_pendentes,
    )


@relatorios_bp.route("/financeiro")
@login_required
@staff_required
def financeiro():
    clinica = clinica_atual()
    data_inicio, data_fim = periodo_do_filtro()
    inicio_dt, fim_dt = intervalo_datetime(data_inicio, data_fim)
    medico_id = _filtro_medico_id(clinica)

    consulta = (
        Pagamento.query
        .join(Agendamento, Agendamento.id == Pagamento.agendamento_id)
        .filter(Agendamento.clinica_id == clinica.id, Pagamento.pago_em.between(inicio_dt, fim_dt))
    )
    if medico_id:
        consulta = consulta.filter(Agendamento.medico_id == medico_id)
    pagamentos = consulta.order_by(Pagamento.pago_em.asc()).all()

    total_bruto = sum((p.valor_procedimento for p in pagamentos), start=0)
    total_descontos = sum((p.valor_procedimento - p.valor_final for p in pagamentos), start=0)
    total_liquido = sum((p.valor_final for p in pagamentos), start=0)

    por_forma = OrderedDict()
    por_dia = OrderedDict()
    por_status_nfse = OrderedDict()
    for p in pagamentos:
        chave_forma = FORMAS_PAGAMENTO_LABEL.get(p.forma_pagamento, p.forma_pagamento or "Não informado")
        por_forma[chave_forma] = por_forma.get(chave_forma, 0) + p.valor_final

        chave_dia = p.pago_em.strftime("%d/%m")
        por_dia[chave_dia] = por_dia.get(chave_dia, 0) + float(p.valor_final)

        chave_nfse = STATUS_NFSE_LABEL.get(p.nfse_status, p.nfse_status or "Não emitida")
        por_status_nfse[chave_nfse] = por_status_nfse.get(chave_nfse, 0) + 1

    linhas = [
        [
            p.pago_em.strftime("%d/%m/%Y %H:%M"),
            p.agendamento.paciente.nome,
            p.agendamento.exame.nome,
            p.agendamento.medico.nome,
            f"{p.valor_procedimento:.2f}".replace(".", ","),
            f"{(p.valor_procedimento - p.valor_final):.2f}".replace(".", ","),
            f"{p.valor_final:.2f}".replace(".", ","),
            FORMAS_PAGAMENTO_LABEL.get(p.forma_pagamento, p.forma_pagamento or "-"),
            STATUS_NFSE_LABEL.get(p.nfse_status, p.nfse_status or "-"),
        ]
        for p in pagamentos
    ]
    cabecalho = ["Data", "Paciente", "Exame", "Médico", "Valor bruto (R$)", "Desconto (R$)", "Valor líquido (R$)", "Forma de pagamento", "NFS-e"]

    formato = request.args.get("formato")
    if formato == "csv":
        return exportar_csv("financeiro", data_inicio, data_fim, cabecalho, linhas)
    if formato == "xlsx":
        return exportar_xlsx("financeiro", data_inicio, data_fim, "Relatório financeiro", cabecalho, linhas)
    if formato == "pdf":
        resumo = [
            f"<b>Total recebido:</b> R$ {total_liquido:.2f}".replace(".", ","),
            f"<b>Total de descontos concedidos:</b> R$ {total_descontos:.2f}".replace(".", ","),
            f"<b>Quantidade de pagamentos:</b> {len(pagamentos)}",
        ]
        return exportar_pdf("financeiro", data_inicio, data_fim, "Relatório financeiro", cabecalho, linhas, resumo)

    return render_template(
        "relatorios/financeiro.html",
        data_inicio=data_inicio, data_fim=data_fim, medico_id=medico_id,
        medicos=medicos_da_clinica(clinica), restrito_a_si_mesmo=_medico_restrito_a_si_mesmo(),
        pagamentos=pagamentos, total_bruto=total_bruto, total_descontos=total_descontos, total_liquido=total_liquido,
        por_forma=por_forma, por_dia=por_dia, por_status_nfse=por_status_nfse,
    )


@relatorios_bp.route("/agenda")
@login_required
@staff_required
def agenda():
    clinica = clinica_atual()
    data_inicio, data_fim = periodo_do_filtro()
    inicio_dt, fim_dt = intervalo_datetime(data_inicio, data_fim)
    medico_id = _filtro_medico_id(clinica)

    consulta = Agendamento.query.filter(
        Agendamento.clinica_id == clinica.id, Agendamento.data_hora.between(inicio_dt, fim_dt),
    )
    if medico_id:
        consulta = consulta.filter(Agendamento.medico_id == medico_id)
    agendamentos = consulta.order_by(Agendamento.data_hora.asc()).all()

    por_status = OrderedDict((rotulo, 0) for rotulo in STATUS_AGENDAMENTO_LABEL.values())
    por_exame = OrderedDict()
    por_dia = OrderedDict()
    for a in agendamentos:
        por_status[STATUS_AGENDAMENTO_LABEL.get(a.status, a.status)] = por_status.get(STATUS_AGENDAMENTO_LABEL.get(a.status, a.status), 0) + 1
        por_exame[a.exame.nome] = por_exame.get(a.exame.nome, 0) + 1
        chave_dia = a.data_hora.strftime("%d/%m")
        por_dia[chave_dia] = por_dia.get(chave_dia, 0) + 1

    total = len(agendamentos)
    total_cancelados = sum(1 for a in agendamentos if a.status == "cancelado")
    total_realizados = sum(1 for a in agendamentos if a.status == "realizado")
    total_no_show = sum(1 for a in agendamentos if a.status == "nao_compareceu")
    taxa_cancelamento = (total_cancelados / total * 100) if total else 0
    taxa_no_show = (total_no_show / total * 100) if total else 0

    linhas = [
        [
            a.data_hora.strftime("%d/%m/%Y %H:%M"),
            a.paciente.nome,
            a.exame.nome,
            a.medico.nome,
            STATUS_AGENDAMENTO_LABEL.get(a.status, a.status),
        ]
        for a in agendamentos
    ]
    cabecalho = ["Data/hora", "Paciente", "Exame", "Médico", "Status"]

    formato = request.args.get("formato")
    if formato == "csv":
        return exportar_csv("agenda", data_inicio, data_fim, cabecalho, linhas)
    if formato == "xlsx":
        return exportar_xlsx("agenda", data_inicio, data_fim, "Relatório de agenda/operação", cabecalho, linhas)
    if formato == "pdf":
        resumo = [
            f"<b>Total de agendamentos:</b> {total}",
            f"<b>Realizados:</b> {total_realizados}  ·  <b>Cancelados:</b> {total_cancelados} ({taxa_cancelamento:.1f}%)".replace(".", ","),
            f"<b>Não compareceu:</b> {total_no_show} ({taxa_no_show:.1f}%)".replace(".", ","),
        ]
        return exportar_pdf("agenda", data_inicio, data_fim, "Relatório de agenda/operação", cabecalho, linhas, resumo)

    return render_template(
        "relatorios/agenda.html",
        data_inicio=data_inicio, data_fim=data_fim, medico_id=medico_id,
        medicos=medicos_da_clinica(clinica), restrito_a_si_mesmo=_medico_restrito_a_si_mesmo(),
        agendamentos=agendamentos, total=total, total_realizados=total_realizados,
        total_cancelados=total_cancelados, taxa_cancelamento=taxa_cancelamento,
        total_no_show=total_no_show, taxa_no_show=taxa_no_show,
        por_status=por_status, por_exame=por_exame, por_dia=por_dia,
    )


@relatorios_bp.route("/pacientes")
@login_required
@staff_required
def pacientes():
    clinica = clinica_atual()
    data_inicio, data_fim = periodo_do_filtro()
    inicio_dt, fim_dt = intervalo_datetime(data_inicio, data_fim)

    novos = (
        Paciente.query
        .filter(Paciente.clinica_id == clinica.id, Paciente.criado_em.between(inicio_dt, fim_dt))
        .order_by(Paciente.criado_em.asc())
        .all()
    )
    total_ativos = Paciente.query.filter_by(clinica_id=clinica.id, status_cadastro="aprovado").count()
    total_pendentes = Paciente.query.filter_by(clinica_id=clinica.id, status_cadastro="pendente").count()
    total_rejeitados = Paciente.query.filter_by(clinica_id=clinica.id, status_cadastro="rejeitado").count()

    por_dia = OrderedDict()
    por_origem = OrderedDict([("Cadastrado pela equipe", 0), ("Auto-cadastro pelo app", 0)])
    for p in novos:
        chave_dia = p.criado_em.strftime("%d/%m")
        por_dia[chave_dia] = por_dia.get(chave_dia, 0) + 1
        # Quando o cadastro nasce pendente é porque veio do auto-cadastro
        # (ver auth.cadastro_paciente) - cadastro feito pela equipe já
        # nasce aprovado direto, nunca passa por "pendente".
        if p.status_cadastro == "pendente" or p.usuario_id is not None:
            por_origem["Auto-cadastro pelo app"] += 1
        else:
            por_origem["Cadastrado pela equipe"] += 1

    linhas = [
        [
            p.criado_em.strftime("%d/%m/%Y %H:%M"),
            p.nome, p.cpf or "-", p.telefone or "-",
            "Auto-cadastro pelo app" if p.usuario_id else "Cadastrado pela equipe",
            {"aprovado": "Aprovado", "pendente": "Pendente", "rejeitado": "Rejeitado"}.get(p.status_cadastro, p.status_cadastro),
        ]
        for p in novos
    ]
    cabecalho = ["Cadastrado em", "Nome", "CPF", "Telefone", "Origem", "Status do cadastro"]

    formato = request.args.get("formato")
    if formato == "csv":
        return exportar_csv("pacientes", data_inicio, data_fim, cabecalho, linhas)
    if formato == "xlsx":
        return exportar_xlsx("pacientes", data_inicio, data_fim, "Relatório de pacientes", cabecalho, linhas)
    if formato == "pdf":
        resumo = [
            f"<b>Novos pacientes no período:</b> {len(novos)}",
            f"<b>Total de pacientes ativos na clínica:</b> {total_ativos}  ·  <b>Cadastros pendentes:</b> {total_pendentes}",
        ]
        return exportar_pdf("pacientes", data_inicio, data_fim, "Relatório de pacientes", cabecalho, linhas, resumo)

    return render_template(
        "relatorios/pacientes.html",
        data_inicio=data_inicio, data_fim=data_fim,
        novos=novos, total_ativos=total_ativos, total_pendentes=total_pendentes, total_rejeitados=total_rejeitados,
        por_dia=por_dia, por_origem=por_origem,
    )


@relatorios_bp.route("/desempenho-medico")
@login_required
@staff_required
def desempenho_medico():
    clinica = clinica_atual()
    data_inicio, data_fim = periodo_do_filtro()
    inicio_dt, fim_dt = intervalo_datetime(data_inicio, data_fim)

    medicos = medicos_da_clinica(clinica)
    if _medico_restrito_a_si_mesmo():
        medicos = [m for m in medicos if m.id == current_user.id]

    linhas_dados = []
    for medico in medicos:
        agendamentos_medico = Agendamento.query.filter(
            Agendamento.clinica_id == clinica.id, Agendamento.medico_id == medico.id,
            Agendamento.data_hora.between(inicio_dt, fim_dt),
        ).all()
        total = len(agendamentos_medico)
        realizados = sum(1 for a in agendamentos_medico if a.status == "realizado")
        cancelados = sum(1 for a in agendamentos_medico if a.status == "cancelado")
        no_shows = sum(1 for a in agendamentos_medico if a.status == "nao_compareceu")

        receita = (
            db.session.query(func.coalesce(func.sum(Pagamento.valor_final), 0))
            .join(Agendamento, Agendamento.id == Pagamento.agendamento_id)
            .filter(
                Agendamento.clinica_id == clinica.id, Agendamento.medico_id == medico.id,
                Pagamento.pago_em.between(inicio_dt, fim_dt),
            )
            .scalar()
        )

        linhas_dados.append({
            "medico": medico, "total": total, "realizados": realizados, "cancelados": cancelados,
            "no_shows": no_shows,
            "taxa_cancelamento": (cancelados / total * 100) if total else 0,
            "taxa_no_show": (no_shows / total * 100) if total else 0,
            "receita": receita,
        })

    linhas_dados.sort(key=lambda d: d["receita"], reverse=True)

    linhas = [
        [
            d["medico"].nome, d["total"], d["realizados"], d["cancelados"], d["no_shows"],
            f"{d['taxa_cancelamento']:.1f}".replace(".", ","),
            f"{d['taxa_no_show']:.1f}".replace(".", ","),
            f"{d['receita']:.2f}".replace(".", ","),
        ]
        for d in linhas_dados
    ]
    cabecalho = ["Médico", "Agendamentos", "Realizados", "Cancelados", "Não compareceu", "Taxa de cancelamento (%)", "Taxa de no-show (%)", "Receita (R$)"]

    formato = request.args.get("formato")
    if formato == "csv":
        return exportar_csv("desempenho_medico", data_inicio, data_fim, cabecalho, linhas)
    if formato == "xlsx":
        return exportar_xlsx("desempenho_medico", data_inicio, data_fim, "Relatório de desempenho por médico", cabecalho, linhas)
    if formato == "pdf":
        return exportar_pdf("desempenho_medico", data_inicio, data_fim, "Relatório de desempenho por médico", cabecalho, linhas)

    return render_template(
        "relatorios/desempenho_medico.html",
        data_inicio=data_inicio, data_fim=data_fim,
        linhas_dados=linhas_dados, restrito_a_si_mesmo=_medico_restrito_a_si_mesmo(),
    )
