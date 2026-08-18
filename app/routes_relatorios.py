"""Relatórios gerenciais para a equipe da clínica (secretária/médico):
agenda/operação, pacientes e desempenho por médico. Cada relatório aceita
filtro de período (e, quando faz sentido, de médico) pela query string,
mostra uma tabela com totais e um gráfico, e pode ser exportado em CSV,
Excel ou PDF pela mesma URL (parâmetro `formato`).

Vale sempre a mesma regra de escopo dos outros módulos: tudo é filtrado
pelas filiais acessíveis do usuário dentro da EMPRESA atual
(`filiais_atuais_ids()`), nunca vaza dado de outra empresa."""
from collections import OrderedDict

from flask import Blueprint, render_template, request
from flask_login import login_required, current_user

from app.models import Agendamento, Paciente
from app.clinica_utils import filtro_escopo_atual, empresa_atual
from app.routes_medico import (
    staff_required, eh_medico, _filtro_pacientes_da_empresa,
    _medicos_do_escopo_atual,
)
from app.relatorios_utils import (
    periodo_do_filtro, intervalo_datetime, exportar_csv, exportar_xlsx, exportar_pdf,
)

relatorios_bp = Blueprint("relatorios", __name__, url_prefix="/equipe/relatorios")


def _medico_restrito_a_si_mesmo():
    """Um médico sem perm_equipe só deve ver os próprios números nos
    relatórios que comparam pessoas (desempenho, agenda por médico) —
    mesma regra já aplicada em outras telas (ex.: pacientes_lista)."""
    return eh_medico() and not current_user.perm_equipe


def _filtro_medico_id():
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
    data_inicio, data_fim = periodo_do_filtro()
    inicio_dt, fim_dt = intervalo_datetime(data_inicio, data_fim)

    agendamentos_periodo = Agendamento.query.filter(
        filtro_escopo_atual(Agendamento.grupo_id, Agendamento.criado_por_id),
        Agendamento.data_hora.between(inicio_dt, fim_dt),
    )
    total_agendamentos = agendamentos_periodo.count()

    novos_pacientes = Paciente.query.filter(
        _filtro_pacientes_da_empresa(), Paciente.criado_em.between(inicio_dt, fim_dt),
    ).count()
    cadastros_pendentes = Paciente.query.filter(_filtro_pacientes_da_empresa(), Paciente.status_cadastro == "pendente").count()

    return render_template(
        "relatorios/index.html",
        data_inicio=data_inicio, data_fim=data_fim,
        total_agendamentos=total_agendamentos,
        novos_pacientes=novos_pacientes, cadastros_pendentes=cadastros_pendentes,
    )


@relatorios_bp.route("/agenda")
@login_required
@staff_required
def agenda():
    data_inicio, data_fim = periodo_do_filtro()
    inicio_dt, fim_dt = intervalo_datetime(data_inicio, data_fim)
    medico_id = _filtro_medico_id()

    consulta = Agendamento.query.filter(
        filtro_escopo_atual(Agendamento.grupo_id, Agendamento.criado_por_id),
        Agendamento.data_hora.between(inicio_dt, fim_dt),
    )
    if medico_id:
        consulta = consulta.filter(Agendamento.medico_id == medico_id)
    agendamentos = consulta.order_by(Agendamento.data_hora.asc()).all()

    por_exame = OrderedDict()
    por_dia = OrderedDict()
    for a in agendamentos:
        por_exame[a.exame.nome] = por_exame.get(a.exame.nome, 0) + 1
        chave_dia = a.data_hora.strftime("%d/%m")
        por_dia[chave_dia] = por_dia.get(chave_dia, 0) + 1

    total = len(agendamentos)

    linhas = [
        [
            a.data_hora.strftime("%d/%m/%Y %H:%M"),
            a.paciente.nome,
            a.exame.nome,
            a.medico.nome,
        ]
        for a in agendamentos
    ]
    cabecalho = ["Data/hora", "Paciente", "Exame", "Médico"]

    formato = request.args.get("formato")
    if formato == "csv":
        return exportar_csv("agenda", data_inicio, data_fim, cabecalho, linhas)
    if formato == "xlsx":
        return exportar_xlsx("agenda", data_inicio, data_fim, "Relatório de agenda/operação", cabecalho, linhas)
    if formato == "pdf":
        resumo = [
            f"<b>Total de agendamentos:</b> {total}",
        ]
        return exportar_pdf("agenda", data_inicio, data_fim, "Relatório de agenda/operação", cabecalho, linhas, resumo)

    return render_template(
        "relatorios/agenda.html",
        data_inicio=data_inicio, data_fim=data_fim, medico_id=medico_id,
        medicos=_medicos_do_escopo_atual(empresa_atual()), restrito_a_si_mesmo=_medico_restrito_a_si_mesmo(),
        agendamentos=agendamentos, total=total,
        por_exame=por_exame, por_dia=por_dia,
    )


@relatorios_bp.route("/pacientes")
@login_required
@staff_required
def pacientes():
    data_inicio, data_fim = periodo_do_filtro()
    inicio_dt, fim_dt = intervalo_datetime(data_inicio, data_fim)

    novos = (
        Paciente.query
        .filter(_filtro_pacientes_da_empresa(), Paciente.criado_em.between(inicio_dt, fim_dt))
        .order_by(Paciente.criado_em.asc())
        .all()
    )
    total_ativos = Paciente.query.filter(_filtro_pacientes_da_empresa(), Paciente.status_cadastro == "aprovado").count()
    total_pendentes = Paciente.query.filter(_filtro_pacientes_da_empresa(), Paciente.status_cadastro == "pendente").count()
    total_rejeitados = Paciente.query.filter(_filtro_pacientes_da_empresa(), Paciente.status_cadastro == "rejeitado").count()

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
    data_inicio, data_fim = periodo_do_filtro()
    inicio_dt, fim_dt = intervalo_datetime(data_inicio, data_fim)

    # Fatia 6: sem Grupo (conta solo), não há "equipe" pra listar - o
    # próprio usuário (se médico) é o único médico do escopo.
    medicos = _medicos_do_escopo_atual(empresa_atual())
    if _medico_restrito_a_si_mesmo():
        medicos = [m for m in medicos if m.id == current_user.id]

    linhas_dados = []
    for medico in medicos:
        total = Agendamento.query.filter(
            filtro_escopo_atual(Agendamento.grupo_id, Agendamento.criado_por_id),
            Agendamento.medico_id == medico.id,
            Agendamento.data_hora.between(inicio_dt, fim_dt),
        ).count()

        linhas_dados.append({"medico": medico, "total": total})

    linhas_dados.sort(key=lambda d: d["total"], reverse=True)

    linhas = [
        [d["medico"].nome, d["total"]]
        for d in linhas_dados
    ]
    cabecalho = ["Médico", "Agendamentos"]

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
