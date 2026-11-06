"""Atualizar um preparo com um PDF novo (pedido do Silvan, 2026-11-06).

O médico importa o PDF atualizado na tela de EDITAR o preparo: a extração
preenche o formulário (sobrescrevendo o que está na tela), a revisão mostra o
que mudou em relação ao preparo atual e, ao salvar, o PDF novo substitui o
anterior - que fica guardado como versão anterior. Os exames e agendamentos
ligados continuam os mesmos e o paciente passa a ver o conteúdo novo ao abrir
o link; ninguém é avisado."""


def _norm(texto):
    return " ".join((texto or "").lower().split())


def _conjuntos(modelo, sugestao):
    """{secao: (atual, novo)} - conjuntos de itens normalizados."""
    def s(itens):
        return {i for i in itens if i[0]}

    atual = {
        "Cortes de alimentação": s((_norm(c.descricao), c.horas_antes) for c in modelo.cortes),
        "Alimentos": s((_norm(a.nome), bool(a.permitido)) for a in modelo.alimentos),
        "Medicamentos a suspender": s((_norm(m.medicamento.nome), m.dias_antes) for m in modelo.medicamentos_suspensos),
        "Medicamentos a manter": s((_norm(m.nome),) for m in modelo.medicamentos_mantidos),
        "Outras orientações": s((_norm(i.texto),) for i in modelo.informacoes_gerais),
        "Exames anteriores": s((_norm(e.nome), e.dias_antes) for e in modelo.exames_anteriores_proibidos),
    }
    novo = {
        "Cortes de alimentação": s((_norm(c.get("descricao")), c.get("horas_antes")) for c in sugestao.get("cortes") or []),
        "Alimentos": s((_norm(a.get("nome")), bool(a.get("permitido"))) for a in sugestao.get("alimentos") or []),
        "Medicamentos a suspender": s((_norm(m.get("nome")), m.get("dias_antes")) for m in sugestao.get("medicamentos") or []),
        "Medicamentos a manter": s((_norm(m.get("nome")),) for m in sugestao.get("medicamentos_mantidos") or []),
        "Outras orientações": s((_norm(i.get("texto")),) for i in sugestao.get("informacoes_gerais") or []),
        "Exames anteriores": s((_norm(e.get("nome")), e.get("dias_antes")) for e in sugestao.get("exames_anteriores") or []),
    }
    return {secao: (atual[secao], novo[secao]) for secao in atual}


def resumo_mudancas(modelo, sugestao):
    """Lista de textos curtos com o que muda ao aplicar `sugestao` (a
    importação do PDF novo) sobre o `modelo` atual. Vazia = nada muda."""
    mudancas = []
    if _norm(sugestao.get("instrucoes")) != _norm(modelo.instrucoes):
        mudancas.append("Instruções gerais: o texto muda")
    nome_novo = (sugestao.get("nome_sugerido") or "").strip()
    if nome_novo and _norm(nome_novo) != _norm(modelo.nome):
        mudancas.append(f"Nome: o PDF sugere \"{nome_novo}\" (o atual é \"{modelo.nome}\")")
    for secao, (atual, novo) in _conjuntos(modelo, sugestao).items():
        entram, saem = len(novo - atual), len(atual - novo)
        if entram or saem:
            partes = []
            if entram:
                partes.append(f"{entram} novo(s)")
            if saem:
                partes.append(f"{saem} removido(s)")
            mudancas.append(f"{secao}: " + ", ".join(partes))
    return mudancas
