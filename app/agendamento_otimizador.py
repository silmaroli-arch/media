"""Otimizador simples de sugestão de horários de agendamento.

Dado um exame (com sua duração, ver `Exame.duracao_minutos`) e um médico
(com seu horário de atendimento por dia da semana, ver `MedicoHorario`),
sugere os próximos horários livres na agenda — descontando os
agendamentos que já existem para aquele médico naquele dia (qualquer
status diferente de "cancelado" ocupa o horário).

Usado tanto pela tela da secretaria/médico quanto pela solicitação de
agendamento feita pelo próprio paciente pelo aplicativo (ver
app.routes_paciente.solicitar_agendamento).
"""
from datetime import datetime, timedelta, date as date_cls

from app.models import Agendamento, MedicoHorario, MedicoBloqueio

DURACAO_PADRAO_MINUTOS = 30


def _duracao_do_agendamento(agendamento):
    minutos = (agendamento.exame.duracao_minutos if agendamento.exame else None) or DURACAO_PADRAO_MINUTOS
    return timedelta(minutes=minutos)


def conflito_de_agenda(medico_id, data_hora, duracao_minutos, ignorar_agendamento_id=None):
    """Agendamento existente do MÉDICO que colide com o intervalo
    [data_hora, data_hora + duração) - em QUALQUER clínica em que ele
    atenda, inclusive de outra empresa. É o teste de "choque de horário"
    do médico multi-clínica: a secretária da Clínica 1 não deve conseguir
    marcar um horário em que ele já atende na Clínica 2 (as empresas não
    se enxergam, mas a agenda do médico é uma só). Retorna o agendamento
    conflitante (pra mensagem citar o local) ou None."""
    duracao = timedelta(minutes=duracao_minutos or DURACAO_PADRAO_MINUTOS)
    inicio, fim = data_hora, data_hora + duracao
    # Janela de busca: nenhum exame dura um dia inteiro, então qualquer
    # agendamento que começe até 24h antes já cobre todos os casos de
    # sobreposição possíveis.
    candidatos = Agendamento.query.filter(
        Agendamento.medico_id == medico_id,
        Agendamento.status.in_(("solicitado", "agendado", "confirmado")),
        Agendamento.data_hora < fim,
        Agendamento.data_hora > inicio - timedelta(hours=24),
    ).all()
    for a in candidatos:
        if ignorar_agendamento_id and a.id == ignorar_agendamento_id:
            continue
        if inicio < a.data_hora + _duracao_do_agendamento(a) and fim > a.data_hora:
            return a
    return None


def _horarios_do_medico(clinica_id, medico_id):
    horarios = MedicoHorario.query.filter_by(
        clinica_id=clinica_id, medico_id=medico_id, ativo=True
    ).all()
    return {h.dia_semana: h for h in horarios}


def medico_tem_bloqueio(clinica_id, medico_id, momento):
    """True se o médico bloqueou a agenda (compromisso próprio) cobrindo
    esse instante específico — usado para validar um agendamento manual
    antes de criá-lo."""
    return (
        MedicoBloqueio.query.filter(
            MedicoBloqueio.clinica_id == clinica_id,
            MedicoBloqueio.medico_id == medico_id,
            MedicoBloqueio.data_inicio <= momento,
            MedicoBloqueio.data_fim > momento,
        ).first()
        is not None
    )


def _bloqueios_intervalos_do_dia(clinica_id, medico_id, dia):
    """Intervalos (datetime, datetime) de bloqueio de agenda do médico que
    tocam o dia informado — usado para não sugerir horários dentro deles."""
    inicio_dia = datetime.combine(dia, datetime.min.time())
    fim_dia = inicio_dia + timedelta(days=1)
    bloqueios = MedicoBloqueio.query.filter(
        MedicoBloqueio.clinica_id == clinica_id,
        MedicoBloqueio.medico_id == medico_id,
        MedicoBloqueio.data_inicio < fim_dia,
        MedicoBloqueio.data_fim > inicio_dia,
    ).all()
    return [(b.data_inicio, b.data_fim) for b in bloqueios]


def sugerir_horarios(exame, medico, clinica, data_inicio=None, quantidade=5, dias_maximos=60):
    """Retorna uma lista de até `quantidade` objetos datetime com horários
    livres para agendar este exame com este médico, a partir de
    `data_inicio` (hoje, por padrão) — procurando em até `dias_maximos`
    dias corridos. Retorna lista vazia se o médico não tiver nenhum
    horário de atendimento cadastrado e ativo nesta filial."""
    duracao = timedelta(minutes=exame.duracao_minutos or DURACAO_PADRAO_MINUTOS)
    horarios_por_dia = _horarios_do_medico(clinica.id, medico.id)
    if not horarios_por_dia:
        return []

    agora = datetime.utcnow()
    dia_atual = data_inicio.date() if isinstance(data_inicio, datetime) else (data_inicio or agora.date())

    sugestoes = []
    for _ in range(dias_maximos):
        dia_semana = dia_atual.weekday()
        horario = horarios_por_dia.get(dia_semana)
        if horario and horario.hora_inicio and horario.hora_fim:
            # Ocupação em TODAS as clínicas do médico (não só nesta) - a
            # agenda do médico é uma só: um horário tomado na Clínica 2
            # não pode ser sugerido na Clínica 1 (choque de horário do
            # médico multi-clínica). Cada agendamento ocupa o tempo do SEU
            # exame (não o do exame sendo marcado agora).
            ocupados = (
                Agendamento.query.filter(
                    Agendamento.medico_id == medico.id,
                    Agendamento.status != "cancelado",
                    Agendamento.data_hora >= datetime.combine(dia_atual, horario.hora_inicio) - timedelta(hours=24),
                    Agendamento.data_hora < datetime.combine(dia_atual, horario.hora_fim),
                )
                .all()
            )
            ocupados_intervalos = [
                (a.data_hora, a.data_hora + _duracao_do_agendamento(a)) for a in ocupados
            ]
            ocupados_intervalos += _bloqueios_intervalos_do_dia(clinica.id, medico.id, dia_atual)

            slot = datetime.combine(dia_atual, horario.hora_inicio)
            fim_expediente = datetime.combine(dia_atual, horario.hora_fim)
            while slot + duracao <= fim_expediente:
                if slot > agora and not any(
                    slot < fim_ocupado and slot + duracao > inicio_ocupado
                    for inicio_ocupado, fim_ocupado in ocupados_intervalos
                ):
                    sugestoes.append(slot)
                    if len(sugestoes) >= quantidade:
                        return sugestoes
                slot += duracao

        dia_atual += timedelta(days=1)

    return sugestoes
