"""Integração opcional com APIs de IA (Claude/Anthropic e, opcionalmente,
ChatGPT/OpenAI) para responder dúvidas de pacientes de forma mais flexível
do que a correspondência por palavra-chave de `app.faq_engine` — interpreta
a pergunta em linguagem natural usando como contexto os dados estruturados
do preparo do exame (cortes, medicamentos, alimentos, exames anteriores,
informações gerais), já com os prazos calculados a partir do agendamento.

Só é usada quando pelo menos uma das variáveis de ambiente
ANTHROPIC_API_KEY / OPENAI_API_KEY está configurada (ver .env.example) —
sem nenhuma delas, `responder_com_ia` sempre retorna None e o sistema
continua funcionando só com a correspondência por palavra-chave de
app.faq_engine (ver app.routes_paciente.chat), do jeito que já funcionava
antes. Também nunca "trava" o chat: qualquer erro de rede/API é tratado
como "não conseguiu responder agora" e cai de volta para o mesmo caminho
de sempre.

Quando as DUAS chaves estão configuradas, consulta as duas IAs para a
mesma pergunta ("reforço mútuo"): se concordam, usa a resposta da Claude
normalmente; se divergem em algum ponto prático, retorna as duas lado a
lado com um aviso, para o médico revisar com mais atenção antes de
aprovar (ver app.routes_paciente.chat e medico/perguntas.html) — nunca
tenta "resolver" a diferença sozinha.

Importante: é instruída a responder SÓ com base no preparo cadastrado, e a
sinalizar quando não tem certeza (em vez de arriscar uma informação
médica errada) — nesse caso a pergunta continua sendo encaminhada para a
secretaria, exatamente como quando a correspondência por palavra-chave
não encontra nada."""
import os

MARCADOR_NAO_SEI = "NAO_SEI_ENCAMINHAR"

# Pode ser trocado por variável de ambiente sem precisar mexer no código —
# útil pra ajustar custo/qualidade sem um novo deploy.
#
# Usamos o Sonnet (não o Haiku) como padrão porque essa tarefa depende de
# reconhecimento de marcas comerciais de medicamento (ex.: "Ecasil" =
# ácido acetilsalicílico/AAS) para conseguir casar a pergunta do paciente
# com o que já está cadastrado no preparo (ver a regra de "IDENTIDADE" no
# PROMPT_SISTEMA abaixo) — o Haiku errou esse tipo de reconhecimento em
# testes reais. Custa mais por chamada, mas o volume de perguntas de
# paciente é baixo o suficiente pra isso não pesar, e o ganho de acerto
# nesse tipo de pergunta compensa.
MODELO_PADRAO = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
# Mantido no gpt-4o-mini (mais barato) do lado da OpenAI — como as duas IAs
# são consultadas juntas e comparadas (reforço mútuo, ver
# responder_com_ia), a Claude já mais forte reconhecendo a marca cobre boa
# parte do ganho sem precisar subir o custo dos dois lados ao mesmo tempo.
MODELO_OPENAI_PADRAO = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

PROMPT_SISTEMA = """Você é um assistente virtual de uma clínica, respondendo dúvidas de pacientes sobre o preparo para um exame médico. Responda SOMENTE com base nas informações do preparo fornecidas pelo usuário — nunca invente prazos, medicamentos, alimentos ou características de produtos (cor, sabor, composição, marca) que não estejam explicitamente listadas ali.

Regras importantes:
- Responda em português do Brasil, de forma direta, curta (no máximo 3-4 frases) e acolhedora.
- Você pode (e deve) fazer um pequeno raciocínio sobre IDENTIDADE do que foi cadastrado — por exemplo, reconhecer que "gatorade" citado na pergunta é o mesmo item cadastrado como "Gatorade de cor clara", ou que uma fruta específica (ex.: laranja) está coberta por uma categoria genérica cadastrada (ex.: "Frutas"), ou que um medicamento citado pela marca corresponde a um item cadastrado por outro nome.
- Preste atenção especial a essa identidade quando a pergunta for sobre um MEDICAMENTO citado por nome comercial/marca (ex.: "Ecasil", "Somalgin", "AAS", "Aspirina" são todos nomes comerciais de ácido acetilsalicílico no Brasil) — use seu conhecimento geral de farmácia para identificar o princípio ativo ou a classe do medicamento perguntado, e então verifique se esse princípio ativo/classe corresponde a algum item já cadastrado no preparo (pelo nome ou pela categoria informada), mesmo que o nome comercial citado pelo paciente seja diferente do nome cadastrado.
- Quando a pergunta for sobre um MEDICAMENTO que você consegue identificar (nome, princípio ativo ou classe), mas que não corresponde a NENHUM item cadastrado neste preparo (nem pelo nome, nem pela categoria) — por exemplo, um anticoagulante que não está na lista —, NÃO responda com NAO_SEI_ENCAMINHAR. Em vez disso, escreva uma resposta curta que: (1) diga claramente que esse medicamento específico não está cadastrado no preparo deste exame; (2) compartilhe, de forma genérica, o que normalmente se sabe sobre esse tipo de medicamento em relação a exames como este (ex.: "anticoagulantes geralmente precisam ser suspensos antes de exames com risco de sangramento, como colonoscopia"); e (3) oriente o paciente a confirmar com a secretaria/médico responsável o prazo exato para o caso dele, já que isso depende do protocolo da clínica e pode precisar do aval de quem prescreveu o medicamento. NUNCA afirme um prazo de suspensão específico (em dias/horas) para um medicamento que não está cadastrado — isso continua proibido mesmo nesse tipo de resposta; a resposta serve para orientar, não para decidir por conta própria. (Essa resposta ainda passa pela aprovação do médico antes de ir ao paciente, então é melhor dar essa orientação cautelosa do que deixar a pergunta sem nenhum rascunho.)
- NUNCA faça o raciocínio de identidade acima sobre uma CARACTERÍSTICA do produto que os dados não informam (ex.: qual é a cor de um sabor específico de bebida, se um alimento tem ou não determinado ingrediente). Isso é inventar informação, mesmo que pareça um "senso comum" — cores de sabores variam por marca/país e você pode errar. Nesses casos, explique a regra cadastrada (ex.: "só é permitido líquido de cor clara") e oriente o paciente a verificar essa característica específica por conta própria (observando a embalagem) ou perguntar à secretaria — nunca afirme se aquele sabor/produto específico atende ou não à regra quando isso não estiver explícito nos dados.
- Quando o item tiver um prazo/data calculado nos dados fornecidos, cite esse prazo/data na resposta.
- Reserve o texto NAO_SEI_ENCAMINHAR só para perguntas que genuinamente não têm nenhuma informação útil a dar (ex.: assunto totalmente fora do preparo, ou um item que você não consegue identificar de jeito nenhum) — nesse caso, responda EXATAMENTE com esse texto, nada mais, nenhuma outra palavra, nenhuma pontuação extra.
- Nunca responda sobre assuntos fora do preparo deste exame específico (ex.: diagnósticos, tratamentos, outros exames)."""


def _cliente_anthropic():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=api_key)
    except Exception:
        # Cobre tanto a falta da biblioteca quanto qualquer erro ao
        # construir o cliente (ex.: incompatibilidade de versão entre a
        # lib e uma de suas dependências, como já aconteceu com a lib da
        # OpenAI abaixo) — nunca deve derrubar o chat do paciente com um
        # erro 500, só faz o sistema seguir sem essa IA.
        return None


def _cliente_openai():
    """Segunda IA opcional (ChatGPT/OpenAI), usada em conjunto com a Claude
    (ver responder_com_ia) para dar mais confiança às respostas antes de
    irem para a aprovação do médico — nunca sozinha no lugar da Claude, só
    quando ANTHROPIC_API_KEY também estiver configurada é que as duas são
    combinadas; se só OPENAI_API_KEY estiver configurada, funciona como
    IA única (mesma lógica de "não sei" e mesmo prompt da Claude)."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        import openai
        return openai.OpenAI(api_key=api_key)
    except Exception:
        # Mesma lógica de _cliente_anthropic() acima: nunca deixar um
        # problema aqui (falta da lib, ou um erro de construção do
        # cliente — foi exatamente isso que causou o 500 real: a versão
        # antiga da lib "openai" passava um parâmetro "proxies" que a
        # versão do "httpx" instalada junto já não aceita mais) derrubar
        # o chat do paciente.
        return None


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


def _perguntar_claude(cliente, pergunta_usuario, contexto):
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


def _perguntar_chatgpt(cliente, pergunta_usuario, contexto):
    try:
        resposta = cliente.chat.completions.create(
            model=MODELO_OPENAI_PADRAO,
            max_tokens=300,
            messages=[
                {"role": "system", "content": PROMPT_SISTEMA},
                {
                    "role": "user",
                    "content": f"Dados do preparo:\n{contexto}\n\nPergunta do paciente: {pergunta_usuario}",
                },
            ],
        )
    except Exception:
        return None
    texto = (resposta.choices[0].message.content or "").strip()
    if not texto or MARCADOR_NAO_SEI in texto:
        return None
    return texto


def _respostas_divergem(cliente_anthropic, resposta_a, resposta_b):
    """Quando as duas IAs respondem, usa uma chamada extra rápida e barata
    (Claude Haiku, poucos tokens) só para CLASSIFICAR se as duas respostas
    passam a mesma orientação prática ao paciente - não reescreve nem
    tenta "resolver" a diferença sozinha, só sinaliza para o médico
    revisar com mais atenção quando elas divergem."""
    if not cliente_anthropic:
        # Sem a Claude disponível para julgar, não dá pra comparar - trata
        # como divergência (mais seguro pedir revisão do que presumir
        # concordância sem checar).
        return True
    try:
        veredito = cliente_anthropic.messages.create(
            model=MODELO_PADRAO,
            max_tokens=5,
            system=(
                "Compare as duas respostas abaixo, dadas por assistentes diferentes à "
                "mesma pergunta de um paciente sobre preparo de exame. Responda SOMENTE "
                "com a palavra SIM se elas passam a mesma orientação prática ao "
                "paciente, ou NAO se divergem em algum ponto que mudaria o que o "
                "paciente deveria fazer."
            ),
            messages=[{
                "role": "user",
                "content": f"Resposta 1: {resposta_a}\n\nResposta 2: {resposta_b}",
            }],
        )
        texto = "".join(getattr(bloco, "text", "") for bloco in veredito.content).strip().upper()
        return texto.startswith("NAO")
    except Exception:
        return True


def _sintetizar_resposta(cliente_anthropic, pergunta_usuario, resposta_a, resposta_b):
    """Quando as duas IAs respondem de forma divergente, usa uma chamada
    extra à Claude para propor UMA única resposta final que já concilia
    as duas — em vez de só colar as duas respostas lado a lado, tenta de
    fato sugerir a melhor síntese, mantendo a mesma cautela do prompt
    principal (nunca inventar informação que não esteja em nenhuma das
    duas respostas, e recomendar confirmar com a secretaria quando as
    respostas realmente se contradizem num ponto que mudaria a orientação
    ao paciente). Continua sendo só uma SUGESTÃO — o médico revisa e edita
    antes de aprovar, igual a qualquer outro rascunho da IA.

    Retorna None se não conseguir sintetizar (Claude indisponível ou erro
    na chamada) — nesse caso quem chamou deve cair de volta para mostrar
    as duas respostas lado a lado, nunca travar a aprovação por causa
    disso."""
    if not cliente_anthropic:
        return None
    try:
        sintese = cliente_anthropic.messages.create(
            model=MODELO_PADRAO,
            max_tokens=300,
            system=(
                "Duas IAs diferentes responderam à mesma pergunta de um paciente "
                "sobre preparo de exame, com respostas que divergem em algum ponto. "
                "Sua tarefa é propor UMA única resposta final, em português do "
                "Brasil, curta (no máximo 3-4 frases) e acolhedora, que concilie as "
                "duas — priorizando a informação mais completa e mais segura "
                "(quando uma resposta é mais cautelosa que a outra, prefira a mais "
                "cautelosa). NUNCA invente uma informação que não esteja em "
                "nenhuma das duas respostas. Se as duas realmente se contradizem "
                "num ponto que mudaria o que o paciente deveria fazer (e não dá "
                "para saber qual está certa), não tente adivinhar: responda "
                "recomendando que o paciente confirme com a secretaria/clínica "
                "esse ponto específico, deixando claro qual é o ponto de dúvida. "
                "Responda SOMENTE com o texto que será enviado direto ao "
                "paciente — sem títulos como 'Resposta final:', sem aspas "
                "envolvendo tudo, e sem nenhum comentário sobre as duas IAs ou "
                "sobre o processo de conciliação."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Pergunta do paciente: {pergunta_usuario}\n\n"
                    f"Resposta 1: {resposta_a}\n\nResposta 2: {resposta_b}"
                ),
            }],
        )
        texto = "".join(getattr(bloco, "text", "") for bloco in sintese.content).strip()
        return texto or None
    except Exception:
        return None


def responder_com_ia(pergunta_usuario, exame):
    """Tenta responder a pergunta do paciente usando IA, com o preparo do
    exame como contexto. Quando tanto ANTHROPIC_API_KEY quanto
    OPENAI_API_KEY estão configuradas, consulta as DUAS (Claude e
    ChatGPT) e as combina por "reforço mútuo": se concordam, o rascunho
    final é a resposta da Claude; se divergem em algum ponto prático, o
    rascunho final junta as duas lado a lado com um aviso, para o médico
    revisar com mais atenção antes de aprovar. Com só uma das chaves
    configurada, funciona com aquela IA sozinha, exatamente como antes.

    Retorna um dicionário {"final": ..., "claude": ..., "chatgpt": ...} —
    "claude" e "chatgpt" são as respostas cruas de cada IA (para a tela de
    aprovação mostrar lado a lado, ver medico/perguntas.html), e "final" é
    o rascunho que efetivamente vira o PerguntaPendente.resposta_sugerida_ia
    (já com a lógica de reforço mútuo acima aplicada). Todos os campos vêm
    None quando: nenhuma API está configurada; a(s) chamada(s) falharam
    (rede, limite de uso etc.); ou a(s) IA(s) sinalizaram que não têm
    certeza. Quando "final" é None, a pergunta segue para a
    correspondência por palavra-chave e, por fim, para a fila da
    secretaria — o comportamento de antes não muda."""
    cliente_anthropic = _cliente_anthropic()
    cliente_openai = _cliente_openai()
    if not cliente_anthropic and not cliente_openai:
        return {"final": None, "claude": None, "chatgpt": None}

    contexto = _formatar_contexto_preparo(exame)

    resposta_claude = (
        _perguntar_claude(cliente_anthropic, pergunta_usuario, contexto) if cliente_anthropic else None
    )
    resposta_chatgpt = (
        _perguntar_chatgpt(cliente_openai, pergunta_usuario, contexto) if cliente_openai else None
    )

    if resposta_claude and resposta_chatgpt:
        if _respostas_divergem(cliente_anthropic, resposta_claude, resposta_chatgpt):
            sintese = _sintetizar_resposta(cliente_anthropic, pergunta_usuario, resposta_claude, resposta_chatgpt)
            if sintese:
                # O rascunho final é o próprio texto sintetizado, já pronto
                # para envio ao paciente — as respostas individuais do
                # Claude e do ChatGPT continuam visíveis acima (ver
                # medico/perguntas.html), então não é preciso repetir aviso
                # nenhum aqui dentro do textarea.
                final = sintese
            else:
                # Não conseguiu sintetizar (Claude indisponível ou erro na
                # chamada) — cai de volta para mostrar as duas respostas
                # completas, em vez de travar a aprovação por causa disso.
                final = (
                    "⚠️ As duas IAs consultadas (Claude e ChatGPT) deram respostas "
                    "diferentes para esta pergunta — revise com atenção antes de "
                    "aprovar.\n\n"
                    f"Resposta do Claude:\n{resposta_claude}\n\n"
                    f"Resposta do ChatGPT:\n{resposta_chatgpt}"
                )
        else:
            final = resposta_claude
    else:
        final = resposta_claude or resposta_chatgpt

    return {"final": final, "claude": resposta_claude, "chatgpt": resposta_chatgpt}
