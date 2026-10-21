"""Dados pessoais do paciente nas respostas sem gravá-los no FAQ (pedido do
Silvan, 2026-10-21).

As respostas aprovadas viram FAQ e são reenviadas a QUALQUER paciente que
fizer a mesma pergunta - uma resposta com "seu exame é dia 29/10 às 11:40"
mandaria o horário de uma paciente para todo mundo. Por isso a IA (e o
médico, se quiser) escreve marcadores no lugar dos dados pessoais, o FAQ
guarda o texto COM os marcadores, e o sistema troca cada marcador pelos
dados do paciente na hora de enviar, a partir do agendamento dele - sem
IA, então sem risco de errar a conta:

- {data_exame}      -> 29/10/2026
- {hora_exame}      -> 11:40
- {3 dias antes}    -> 26/10/2026  (mesma conta dos prazos do preparo)
- {12 horas antes}  -> 28/10/2026 às 23:40

Sem agendamento conhecido, o marcador vira um texto genérico ("3 dias
antes do exame"), para a frase continuar fazendo sentido."""
import re
from datetime import timedelta

from app.models import Agendamento

_MARCADOR = re.compile(r"\{\s*(data_exame|hora_exame|(\d+)\s+(dias?|horas?)\s+antes)\s*\}", re.IGNORECASE)


def agendamento_do_paciente(paciente_id, exame_id):
    """O agendamento deste paciente para este exame: o em aberto mais
    próximo do fim (mesma ideia do "exame em foco"), senão o mais recente."""
    if not paciente_id or not exame_id:
        return None
    consulta = Agendamento.query.filter_by(paciente_id=paciente_id, exame_id=exame_id)
    return (
        consulta.filter(Agendamento.encerrado_em.is_(None)).order_by(Agendamento.data_hora.desc()).first()
        or consulta.order_by(Agendamento.data_hora.desc()).first()
    )


def preencher_marcadores(texto, agendamento):
    """Troca os marcadores pelos dados de `agendamento` (ou por um texto
    genérico, sem agendamento)."""
    if not texto:
        return texto
    data_hora = agendamento.data_hora if agendamento else None

    def trocar(m):
        nome = m.group(1).lower()
        if nome == "data_exame":
            return data_hora.strftime("%d/%m/%Y") if data_hora else "a data do seu exame"
        if nome == "hora_exame":
            return data_hora.strftime("%H:%M") if data_hora else "o horário do seu exame"
        quantidade = int(m.group(2))
        if m.group(3).lower().startswith("dia"):
            if not data_hora:
                return f"{quantidade} dias antes do exame"
            return (data_hora - timedelta(days=quantidade)).strftime("%d/%m/%Y")
        if not data_hora:
            return f"{quantidade} horas antes do exame"
        return (data_hora - timedelta(hours=quantidade)).strftime("%d/%m/%Y às %H:%M")

    return _MARCADOR.sub(trocar, texto)


def resposta_para_paciente(texto, paciente_id, exame_id):
    return preencher_marcadores(texto, agendamento_do_paciente(paciente_id, exame_id))
