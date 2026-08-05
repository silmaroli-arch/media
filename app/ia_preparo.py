"""Integração opcional com a API da Claude (Anthropic) para responder
dúvidas de pacientes de forma mais flexível do que a correspondência por
palavra-chave de `app.faq_engine` — interpreta a pergunta em linguagem
natural usando como contexto os dados estruturados do preparo do exame
(cortes, medicamentos, alimentos, exames anteriores, informações gerais),
já com os prazos calculados a partir do agendamento.

Só é usada quando a variável de ambiente ANTHROPIC_API_KEY está
configurada (ver .env.example) — sem ela, `responder_com_ia` sempre
retorna None e o sistema continua funcionando só com a correspondência
por palavra-chave de app.faq_engine (ver app.routes_paciente.chat), do
jeito que já funcionava antes. Também nunca "trava" o chat: qualquer erro
de rede/API é tratado como "não conseguiu responder agora" e cai de volta
para o mesmo caminho de sempre.

Importante: é instruída a responder SÓ com base no preparo cadastrado, e a
sinalizar quando não tem certeza (em vez de arriscar uma informação
médica errada) — nesse caso a pergunta continua sendo encaminhada para a
secretaria, exatamente como quando a correspondência por palavra-chave
não encontra nada."""
import os

MARCADOR_NAO_SEI = "NAO_SEI_ENCAMINHAR"

# Pode ser trocado por variável de ambiente sem precisar mexer no código —
# útil pra ajustar custo/qualidade sem um novo deploy.
MODELO_PADRAO = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5")

PROMPT_SISTEMA = """Você é um assistente virtual de uma clínica, respondendo dúvidas de pacientes sobre o preparo para um exame médico. Responda SOMENTE com base nas informações do preparo fornecidas pelo usuário — nunca invente prazos, medicamentos, alimentos ou características de produtos (cor, sabor, composição, marca) que não estejam explicitamente listadas ali.

Regras importantes:
- Responda em português do Brasil, de forma direta, curta (no máximo 3-4 frases) e acolhedora.
- Você pode (e deve) fazer um pequeno raciocínio sobre IDENTIDADE do que foi cadastrado — por exemplo, reconhecer que "gatorade" citado na pergunta é o mesmo item cadastrado como "Gatorade de cor clara", ou que uma fruta específica (ex.: laranja) está coberta por uma categoria genérica cadastrada (ex.: "Frutas"), ou que um medicamento citado pela marca corresponde a um item cadastrado por outro nome.
- NUNCA faça esse raciocínio sobre uma CARACTERÍSTICA do produto que os dados não informam (ex.: qual é a cor de um sabor específico de bebida, se um alimento tem ou não determinado ingrediente). Isso é inventar informação, mesmo que pareça um "senso comum" — cores de sabores variam por marca/país e você pode errar. Nesses casos, explique a regra cadastrada (ex.: "só é permitido líquido de cor clara") e oriente o paciente a verificar essa característica específica por conta própria (observando a embalagem) ou perguntar à secretaria — nunca afirme se aquele sabor/produto específico atende ou não à regra quando isso não estiver explícito nos dados.
- Quando o item tiver um prazo/data calculado nos dados fornecidos, cite esse prazo/data na resposta.
- Se a pergunta for sobre algo que genuinamente NÃO está coberto pelas informações fornecidas (não dá pra saber com o que foi passado), responda EXATAMENTE com o texto: NAO_SEI_ENCAMINHAR — nada mais, nenhuma outra palavra, nenhuma pontuação extra. É melhor admitir que não sabe do que arriscar uma informação médica errada.
- Nunca responda sobre assuntos fora do preparo deste exame específico (ex.: diagnósticos, tratamentos, outros exames)."""


def _cliente():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
    except ImportError:
        return None
    return anthropic.Anthropic(api_key=api_key)


def _formatar_contexto_preparo(exame):
    """Serializa o preparo do exame (e o agendamento mais recente do
    paciente, se houver) num texto simples — os prazos já vêm calculados
    aqui (mesmas contas que a tela do paciente e o chat por palavra-chave
    já fazem), pra IA não precisar (e não arriscar errar) fazer aritmética
    de data por conta própria."""
    preparo = exame.preparo
    partes = [f"Exame: {exame.nome}"]
    if exame.descricao:
        partes.append(f"Descrição do exame: {exame.descricao}")

    agendamento = None
    if exame.agendamentos:
        agendamento = sorted(exame.agendamentos, key=lambda a: a.data_hora)[-1]
    if agendamento:
        partes.append(f"Data/hora do exame agendado: {agendamento.data_hora.strftime('%d/%m/%Y às %H:%M')}")

    if not preparo:
        partes.append("Nenhuma instrução de preparo cadastrada para este exame.")
        return "\n".join(partes)

    if preparo.cortes:
        partes.append("\nCortes de alimentação/líquido:")
        for c in preparo.cortes:
            linha = f"- {c.descricao}: proibido a partir de {c.horas_antes} horas antes do exame"
            if agendamento:
                linha += f" (ou seja, a partir de {c.limite(agendamento.data_hora).strftime('%d/%m/%Y às %H:%M')})"
            partes.append(linha)

    if preparo.medicamentos_suspensos:
        partes.append("\nMedicamentos que precisam ser suspensos:")
        for ms in preparo.medicamentos_suspensos:
            nome = ms.medicamento.nome if ms.medicamento else "?"
            categoria = ms.medicamento.categoria if ms.medicamento else None
            linha = f"- {nome}"
            if categoria:
                linha += f" (categoria: {categoria})"
            linha += f": suspender {ms.dias_antes} dias antes do exame"
            if agendamento:
                linha += f" (a partir de {ms.limite(agendamento.data_hora).strftime('%d/%m/%Y')})"
            if ms.observacao:
                linha += f". Observação: {ms.observacao}"
            partes.append(linha)

    if preparo.medicamentos_mantidos:
        partes.append("\nMedicamentos que PODEM ser mantidos (não precisam ser suspensos):")
        for mm in preparo.medicamentos_mantidos:
            linha = f"- {mm.nome}"
            if mm.observacao:
                linha += f": {mm.observacao}"
            partes.append(linha)

    if preparo.observacoes_medicamentos:
        partes.append(f"\nObservação geral sobre medicamentos: {preparo.observacoes_medicamentos}")

    if preparo.alimentos:
        proibidos = [a for a in preparo.alimentos if not a.permitido]
        permitidos = [a for a in preparo.alimentos if a.permitido]
        if proibidos:
            partes.append("\nAlimentos/bebidas PROIBIDOS:")
            for a in proibidos:
                linha = f"- {a.nome}"
                if (a.horas_antes is not None or a.dias_antes is not None) and agendamento:
                    prazo = a.horas_antes if a.horas_antes is not None else a.dias_antes
                    unidade = "horas" if a.horas_antes is not None else "dias"
                    linha += f": evitar a partir de {a.limite_formatado(agendamento.data_hora)} ({prazo} {unidade} antes do exame)"
                partes.append(linha)
        if permitidos:
            partes.append("\nAlimentos/bebidas PERMITIDOS (sugestão de consumo):")
            for a in permitidos:
                partes.append(f"- {a.nome}")

    if preparo.exames_anteriores_proibidos:
        partes.append("\nExames/procedimentos que o paciente NÃO pode ter feito recentemente:")
        for e in preparo.exames_anteriores_proibidos:
            linha = f"- {e.nome}"
            if e.dias_antes is not None and agendamento:
                linha += (
                    f": não deve ter sido feito desde {e.limite(agendamento.data_hora).strftime('%d/%m/%Y')} "
                    f"({e.dias_antes} dias antes do exame)"
                )
            partes.append(linha)

    if preparo.informacoes_gerais:
        partes.append("\nOutras orientações:")
        for info in preparo.informacoes_gerais:
            linha = f"- {info.texto}"
            if agendamento:
                limite_info = info.limite(agendamento.data_hora)
                if limite_info:
                    linha += f" (prazo: {limite_info.strftime('%d/%m/%Y às %H:%M')})"
            partes.append(linha)

    if preparo.instrucoes:
        partes.append(f"\nInstruções gerais em texto livre:\n{preparo.instrucoes}")

    return "\n".join(partes)


def responder_com_ia(pergunta_usuario, exame):
    """Tenta responder a pergunta do paciente usando a API da Claude, com o
    preparo do exame como contexto. Retorna a resposta (string) quando a
    IA respondeu com confiança, ou None quando: a API não está
    configurada; a chamada falhou (rede, limite de uso etc.); ou a
    própria IA sinalizou que não tem certeza. Em qualquer caso de None, a
    pergunta segue para a correspondência por palavra-chave e, por fim,
    para a fila da secretaria — o comportamento de antes não muda."""
    cliente = _cliente()
    if not cliente:
        return None

    contexto = _formatar_contexto_preparo(exame)
    try:
        mensagem = cliente.messages.create(
            model=MODELO_PADRAO,
            max_tokens=300,
            system=PROMPT_SISTEMA,
            messages=[{
                "role": "user",
                "content": f"Dados do preparo:\n{contexto}\n\nPergunta do paciente: {pergunta_usuario}",
            }],
        )
    except Exception:
        return None

    texto = "".join(getattr(bloco, "text", "") for bloco in mensagem.content).strip()
    if not texto or MARCADOR_NAO_SEI in texto:
        return None
    return texto
