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
from datetime import date, datetime, timedelta

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


# ---------- O médico não vê marcador (pedido do Silvan, 2026-10-21) ----------
# Na aprovação ele lê e edita o texto já com as datas do paciente; ao gravar
# no FAQ, `generalizar_datas` troca de volta as datas deste paciente por
# marcadores. Onde não há paciente (Meu FAQ), os marcadores aparecem como
# "[data do exame]" etc. (`para_leitura`/`de_leitura`).

# Só datas do mês anterior ao exame até o dia dele viram marcador - uma data
# sem relação com o exame, digitada de propósito, fica como está.
JANELA_DIAS_GENERALIZAR = 31

_DATA_HORA = re.compile(r"\b(\d{2})/(\d{2})/(\d{4}) às (\d{1,2}):(\d{2})\b")
_DATA = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")
_HORA = re.compile(r"\b(\d{1,2}):(\d{2})\b")


def generalizar_datas(texto, agendamento):
    """O contrário de `preencher_marcadores`: datas/horários do exame deste
    paciente (e prazos contados a partir dele) voltam a ser marcadores."""
    if not texto or not agendamento:
        return texto
    exame = agendamento.data_hora

    def data_hora(m):
        try:
            valor = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), int(m.group(4)), int(m.group(5)))
        except ValueError:
            return m.group(0)
        if valor == exame:
            return "{data_exame} às {hora_exame}"
        diferenca = exame - valor
        horas, resto = divmod(diferenca.total_seconds(), 3600)
        if resto == 0 and 0 < horas <= JANELA_DIAS_GENERALIZAR * 24:
            return "{%d horas antes}" % horas
        return m.group(0)

    def data(m):
        try:
            valor = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return m.group(0)
        dias = (exame.date() - valor).days
        if dias == 0:
            return "{data_exame}"
        if 0 < dias <= JANELA_DIAS_GENERALIZAR:
            return "{%d dias antes}" % dias
        return m.group(0)

    def hora(m):
        if (int(m.group(1)), int(m.group(2))) == (exame.hour, exame.minute):
            return "{hora_exame}"
        return m.group(0)

    texto = _DATA_HORA.sub(data_hora, texto)
    texto = _DATA.sub(data, texto)
    return _HORA.sub(hora, texto)


def para_leitura(texto):
    """Marcadores em texto legível, para telas sem paciente (Meu FAQ)."""
    if not texto:
        return texto

    def trocar(m):
        nome = m.group(1).lower()
        if nome == "data_exame":
            return "[data do exame]"
        if nome == "hora_exame":
            return "[horário do exame]"
        unidade = "dias" if m.group(3).lower().startswith("dia") else "horas"
        return f"[{int(m.group(2))} {unidade} antes do exame]"

    return _MARCADOR.sub(trocar, texto)


_LEGIVEL = re.compile(
    r"\[\s*(data do exame|hor[áa]rio do exame|(\d+)\s+(dias?|horas?)\s+antes do exame)\s*\]", re.IGNORECASE,
)


def de_leitura(texto):
    """O contrário de `para_leitura` (ao salvar o Meu FAQ)."""
    if not texto:
        return texto

    def trocar(m):
        nome = m.group(1).lower()
        if nome == "data do exame":
            return "{data_exame}"
        if nome.startswith("hor") and "exame" in nome and not m.group(2):
            return "{hora_exame}"
        unidade = "dias" if m.group(3).lower().startswith("dia") else "horas"
        return "{%d %s antes}" % (int(m.group(2)), unidade)

    return _LEGIVEL.sub(trocar, texto)
