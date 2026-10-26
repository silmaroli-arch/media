"""Horário de Brasília (pedido do Silvan, 2026-10-26: a tela mostrava a
hora do servidor, que é UTC, 3h à frente do relógio da equipe).

Os campos "_em" e `criado_em` do banco são gravados em UTC (datetime.utcnow);
já `Agendamento.data_hora` é o horário local marcado, sem conversão."""
from datetime import datetime
from zoneinfo import ZoneInfo

BRASILIA = ZoneInfo("America/Sao_Paulo")


def utc_para_brasilia(dt):
    """datetime UTC (sem fuso, como vem do banco) no horário de Brasília,
    também sem fuso. None continua None."""
    if not dt:
        return dt
    return dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(BRASILIA).replace(tzinfo=None)


def agora_brasilia():
    """O "agora" de Brasília, sem fuso - comparável com `data_hora` dos agendamentos."""
    return utc_para_brasilia(datetime.utcnow())
