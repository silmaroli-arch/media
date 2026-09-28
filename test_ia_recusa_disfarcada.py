"""Testa a rede de segurança contra "recusa disfarçada de resposta" em
app.ia_preparo (pedido do Silvan, 2026-09-14, depois de um caso real):
perguntado o tempo de antecedência para chegar no exame - informação que
não existe em nenhum campo estruturado do preparo -, o modelo (Gemini/
ChatGPT/Claude, dependendo do configurado) não respondeu com o marcador
`MARCADOR_NAO_SEI_ENCAMINHAR` como o prompt pede; em vez disso, escreveu
uma resposta em português corrido dizendo que não tinha essa informação e
recomendando confirmar com a secretaria. Como o texto não era literalmente
o marcador, o sistema tratou como resposta válida e mandou direto pro
paciente, em vez de encaminhar para o médico.

`app.ia_preparo._eh_recusa_generica_disfarcada` reconhece esse padrão (uma
declaração de "não tenho essa informação" combinada com uma recomendação
de falar com a secretaria/clínica/equipe) e trata como se fosse o
marcador - a pergunta segue para a fila do médico do mesmo jeito que uma
pergunta sem correspondência nenhuma. Testado aqui como função pura
(sem app_context, sem banco, sem chamar nenhuma IA de verdade) - é só
uma checagem de texto."""
from app.ia_preparo import _eh_recusa_generica_disfarcada


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


# --- Casos que DEVEM ser reconhecidos como recusa disfarçada ---

checar(
    "Caso real que motivou esta correção (tempo de antecedência)",
    _eh_recusa_generica_disfarcada(
        "Não há informações específicas sobre o tempo de antecedência que "
        "você deve chegar para o exame. Recomendo confirmar essa informação "
        "com a secretaria ou diretamente com a clínica, pois eles podem "
        "fornecer detalhes importantes. Se precisar de mais ajuda sobre o "
        "preparo, estou à disposição! 😊"
    ),
)
checar(
    "Variação mais curta, mesmo padrão (não tenho + fale com a equipe)",
    _eh_recusa_generica_disfarcada("Não temos essa informação cadastrada. Fale com a equipe para confirmar."),
)
checar(
    "Reconhece mesmo sem nenhum acento (texto já vem sem acentuação)",
    _eh_recusa_generica_disfarcada("Nao consta essa informacao no preparo cadastrado. Verifique com a recepcao."),
)
checar(
    "Reconhece em caixa alta/mista",
    _eh_recusa_generica_disfarcada("NÃO ESTÁ ESPECIFICADO no preparo. Confirme com a SECRETARIA."),
)
checar(
    "Caso real que motivou esta correção (bug 2026-09-24 - pergunta sobre "
    "maconha, resposta no imperativo 'entre em contato', não 'entrar em "
    "contato' - o regex antigo só reconhecia a forma infinitiva e deixou "
    "passar direto pro paciente sem passar pelo médico)",
    _eh_recusa_generica_disfarcada(
        "Não há nenhuma informação sobre maconha nas orientações de preparo "
        "deste exame. Para esclarecer essa dúvida específica, entre em "
        "contato com a secretaria da clínica ou com o médico responsável."
    ),
)
checar(
    "Outras conjugações de 'entrar em contato' também são reconhecidas (entrei/entrando)",
    _eh_recusa_generica_disfarcada("Não possuo essa informação cadastrada. Estou entrando em contato com a clínica por você não ser possível, verifique direto."),
)
checar(
    "Variação com 'procure a secretaria' também é reconhecida",
    _eh_recusa_generica_disfarcada("Não encontrei essa informação no preparo. Procure a secretaria para mais detalhes."),
)

# --- Casos que NÃO devem disparar (resposta de verdade, com dado cadastrado) ---

checar(
    "Resposta de verdade que só cita a secretaria de passagem NÃO é recusa",
    not _eh_recusa_generica_disfarcada(
        "O jejum deve ser de 8 horas antes do exame, iniciando às 22h do dia "
        "anterior. Qualquer dúvida, fale com a secretaria."
    ),
)
checar(
    "Resposta comum, sem nenhuma menção a não ter informação, NÃO é recusa",
    not _eh_recusa_generica_disfarcada("Água pura é permitida durante o jejum, sem restrição de quantidade."),
)
checar(
    "Medicamento não cadastrado, mas com orientação genérica de verdade (regra do PROMPT_SISTEMA) NÃO é recusa",
    not _eh_recusa_generica_disfarcada(
        "Não encontrei esse medicamento cadastrado neste preparo especificamente. "
        "Anticoagulantes geralmente precisam ser suspensos antes de exames com "
        "risco de sangramento, como colonoscopia."
    ),
)
checar(
    "Texto vazio não quebra a função (mesmo não sendo chamada nesse caso - ver _perguntar_*)",
    not _eh_recusa_generica_disfarcada(""),
)

print("\nTodos os testes da rede de segurança contra recusa disfarçada passaram.")
