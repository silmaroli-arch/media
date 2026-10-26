"""Horários gravados em UTC aparecem no horário de Brasília (2026-10-26)."""
from datetime import datetime

from app import create_app
from app.fuso import agora_brasilia, utc_para_brasilia

app = create_app()


def checar(nome, condicao):
    print(f"[{'OK' if condicao else 'FALHOU'}] {nome}")
    assert condicao, nome


checar("21:43 UTC vira 18:43 em Brasília", utc_para_brasilia(datetime(2026, 10, 26, 21, 43)) == datetime(2026, 10, 26, 18, 43))
checar("Passa da meia-noite para o dia anterior", utc_para_brasilia(datetime(2026, 10, 27, 1, 5)) == datetime(2026, 10, 26, 22, 5))
checar("None continua None", utc_para_brasilia(None) is None)
checar("agora_brasilia fica 3h atrás do UTC", 2 * 3600 < (datetime.utcnow() - agora_brasilia()).total_seconds() < 4 * 3600)
filtro = app.jinja_env.filters["hora_br"]
checar("Filtro hora_br formata no horário de Brasília", filtro(datetime(2026, 10, 26, 21, 43)) == "26/10/2026 18:43"
       and filtro(datetime(2026, 10, 26, 21, 43), "%H:%M") == "18:43" and filtro(None) == "")
print("TUDO OK")
