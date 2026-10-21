"""Resposta pronta de alimento não casa palavras curtas só parecidas
(relatado pelo Silvan, 2026-10-21): "Posso ir de carro?" recebia "Sim,
Líquidos claros está entre os alimentos sugeridos..." porque "carro" e
"claro" passavam na tolerância a erro de digitação. Palavra curta agora
aceita só 1 letra de diferença; erro de digitação real continua valendo."""
from types import SimpleNamespace

from app.faq_engine import _palavras_correspondem, buscar_resposta_alimento


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def alimento(nome, permitido):
    return SimpleNamespace(nome=nome, permitido=permitido, horas_antes=None, dias_antes=None)


exame = SimpleNamespace(
    preparo=SimpleNamespace(alimentos=[
        alimento("Líquidos claros", True), alimento("Amendoim", False), alimento("Chocolate", False),
    ]),
    agendamentos=[],
)

checar("'carro' não é 'claro'", not _palavras_correspondem("carro", "claro"))
checar("'Posso ir de carro?' não vira resposta de 'Líquidos claros'",
       buscar_resposta_alimento("Posso ir de carro?", exame) is None)
checar("'Posso tomar líquidos claros?' continua respondida",
       "Líquidos claros" in (buscar_resposta_alimento("Posso tomar líquidos claros?", exame) or ""))
checar("Erro de digitação de 1 letra continua reconhecido ('amendoin')",
       "Amendoim" in (buscar_resposta_alimento("Posso comer amendoin?", exame) or ""))
checar("... e em palavra longa, até 2 letras ('chocolatte')",
       "Chocolate" in (buscar_resposta_alimento("Posso comer chocolatte?", exame) or ""))

print("\nTodos os testes de palavra parecida passaram.")
