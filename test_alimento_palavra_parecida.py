"""Tolerância a erro de digitação não casa palavras curtas só parecidas
(relatado pelo Silvan, 2026-10-21): "carro" e "claro" passavam (ratio 0.8)
e "Posso ir de carro?" recebia a resposta pronta de "Líquidos claros". A
resposta pronta de alimento saiu (toda pergunta fora da FAQ vai para as 2
IAs), mas a mesma comparação ainda é usada pela FAQ do MedIA
(app.base_conhecimento). Palavra curta aceita só 1 letra de diferença;
longa, até 2."""
from app.faq_engine import _palavras_correspondem


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


checar("'carro' não é 'claro'", not _palavras_correspondem("carro", "claro"))
checar("'feijao' não é 'feijoa'", not _palavras_correspondem("feijao", "feijoa"))
checar("Erro de 1 letra continua reconhecido ('amendoin' = 'amendoim')", _palavras_correspondem("amendoin", "amendoim"))
checar("Palavra longa aceita até 2 letras ('chocolatte' = 'chocolate')", _palavras_correspondem("chocolatte", "chocolate"))
checar("Palavra de até 3 letras só casa idêntica", not _palavras_correspondem("sim", "som"))

print("\nTodos os testes de palavra parecida passaram.")
