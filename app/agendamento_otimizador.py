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

from app.models import Agendamento, MedicoHorario

DURACAO_PADRAO_MINUTOS = 30


def _horarios_do_medico(clinica_id, medico_id):
    horarios = MedicoHorario.query.filter_by(
        clinica_id=clinica_id, medico_id=medico_id, ativo=True
    ).all()
    return {h.dia_semana: h for h in horarios}


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
            ocupados = (
                Agendamento.query.filter(
                    Agendamento.medico_id == medico.id,
                    Agendamento.clinica_id == clinica.id,
                    Agendamento.status != "cancelado",
                    Agendamento.data_hora >= datetime.combine(dia_atual, horario.hora_inicio),
                    Agendamento.data_hora < datetime.combine(dia_atual, horario.hora_fim),
                )
                .all()
            )
            ocupados_intervalos = [(a.data_hora, a.data_hora + duracao) for a in ocupados]

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
