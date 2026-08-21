"""Integração opcional com APIs de IA (Gemini/Google, ChatGPT/OpenAI e
Claude/Anthropic) para responder dúvidas de pacientes de forma mais
flexível do que a correspondência por palavra-chave de `app.faq_engine` —
interpreta a pergunta em linguagem natural usando como contexto os dados
estruturados do preparo do exame (cortes, medicamentos, alimentos, exames
anteriores, informações gerais), já com os prazos calculados a partir do
agendamento.

O dono da plataforma escolhe quais 2 das 3 IAs respondem o chat (ver
PlataformaConfig.ia_chat_provedor_1/2, configurável em
/dono/configuracoes) — por padrão Claude+ChatGPT. Só é usada de fato
quando pelo menos uma das duas escolhidas tem sua variável de ambiente de
API key configurada (ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY,
ver .env.example) — sem nenhuma delas, `responder_com_ia` sempre retorna
None e o sistema continua funcionando só com a correspondência por
palavra-chave de app.faq_engine (ver app.routes_paciente.chat), do jeito
que já funcionava antes. Também nunca "trava" o chat: qualquer erro de
rede/API é tratado como "não conseguiu responder agora" e cai de volta
para o mesmo caminho de sempre.

Quando as DUAS IAs escolhidas respondem, consulta as duas para a mesma
pergunta ("reforço mútuo"): se concordam, usa a resposta da IA configurada
como "provedor 1" normalmente; se divergem em algum ponto prático, tenta
sintetizar uma única resposta conciliando as duas (ou, se não conseguir,
retorna as duas lado a lado com um aviso), para o médico revisar com mais
atenção antes de aprovar (ver app.routes_paciente.chat e
medico/perguntas.html) — nunca tenta "resolver" a diferença sozinha sem
mostrar o processo. Esse julgamento de divergência/síntese é SEMPRE feito
pela Claude, mesmo quando ela não é uma das duas escolhidas para
responder (decisão do dono).

Importante: é instruída a responder SÓ com base no preparo cadastrado, e a
sinalizar quando não tem certeza (em vez de arriscar uma informação
médica errada) — nesse caso a pergunta continua sendo encaminhada para a
secretaria, exatamente como quando a correspondência por palavra-chave
não encontra nada."""
import os

from app.custo_ia import registrar_chamada_ia

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
# Mesma variável de ambiente já usada pelo import de PDF (ver
# app.ia_pdf_preparo.MODELO_PADRAO) - um único lugar para trocar o modelo
# do Gemini em toda a aplicação.
MODELO_GEMINI_PADRAO = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")

PROMPT_SISTEMA = """Você é um assistente virtual de uma clínica, respondendo dúvidas de pacientes sobre o preparo para um exame médico. Responda SOMENTE com base nas informações do preparo fornecidas pelo usuário — nunca invente prazos, medicamentos, alimentos ou características de produtos (cor, sabor, composição, marca) que não estejam explicitamente listadas ali.

Regras importantes:
- Responda em português do Brasil, de forma direta, curta (no máximo 3-4 frases) e acolhedora.
- Você pode (e deve) fazer um pequeno raciocínio sobre IDENTIDADE do que foi cadastrado — por exemplo, reconhecer que "gatorade" citado na pergunta é o mesmo item cadastrado como "Gatorade de cor clara", ou que uma fruta específica (ex.: laranja) está coberta por uma categoria genérica cadastrada (ex.: "Frutas"), ou que um medicamento citado pela marca corresponde a um item cadastrado por outro nome.
- Preste atenção especial a essa identidade quando a pergunta for sobre um MEDICAMENTO citado por nome comercial/marca (ex.: "Ecasil", "Somalgin", "AAS", "Aspirina" são todos nomes comerciais de ácido acetilsalicílico no Brasil) — use seu conhecimento geral de farmácia para identificar o princípio ativo ou a classe do medicamento perguntado, e então verifique se esse princípio ativo/classe corresponde a algum item já cadastrado no preparo (pelo nome ou pela categoria informada), mesmo que o nome comercial citado pelo paciente seja diferente do nome cadastrado.
- Quando a pergunta for sobre um MEDICAMENTO que você consegue identificar (nome, princípio ativo ou classe), mas que não corresponde a NENHUM item cadastrado neste preparo (nem pelo nome, nem pela categoria) — por exemplo, um anticoagulante que não está na lista —, NÃO responda com NAO_SEI_ENCAMINHAR. Em vez disso, escreva uma resposta curta que: (1) diga claramente que esse medicamento específico não está cadastrado no preparo deste exame; e (2) compartilhe, de forma genérica, o que normalmente se sabe sobre esse tipo de medicamento em relação a exames como este (ex.: "anticoagulantes geralmente precisam ser suspensos antes de exames com risco de sangramento, como colonoscopia"). NUNCA afirme um prazo de suspensão específico (em dias/horas) para um medicamento que não está cadastrado — isso continua proibido mesmo nesse tipo de resposta. Não é preciso terminar orientando o paciente a confirmar com a secretaria/médico — essa resposta já vai passar pela revisão e aprovação do médico antes de chegar ao paciente (ver o restante do fluxo), então essa recomendação final é redundante; o médico que revisa decide se quer complementar a resposta.
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
    """Segunda IA opcional (ChatGPT/OpenAI), usada em conjunto com outra
    IA (ver responder_com_ia) para dar mais confiança às respostas antes
    de irem para a aprovação do médico — se só OPENAI_API_KEY estiver
    configurada, funciona como IA única (mesma lógica de "não sei" e
    mesmo prompt das outras)."""
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


def _cliente_gemini():
    """Terceira IA opcional (Gemini/Google), no mesmo padrão das outras
    duas - ver app.ia_pdf_preparo._cliente_gemini, que usa exatamente o
    mesmo client/lib (google-genai) para o import de PDF. Antes da
    escolha de 2-de-3 provedores (ver PlataformaConfig.ia_chat_*), o chat
    de dúvidas só usava Claude+ChatGPT; Gemini nunca respondia perguntas
    de paciente, só era usado no import de PDF."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        from google import genai
        return genai.Client(api_key=api_key)
    except Exception:
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


def _perguntar_claude(cliente, pergunta_usuario, contexto, paciente_id=None):
    """Devolve uma tupla `(texto_ou_None, chamada_ou_None)` - `chamada` é
    o `ChamadaIA` já registrado (ver app.custo_ia.registrar_chamada_ia),
    para quem chamou poder marcar depois `.resposta_final_usada` assim
    que souber se esta resposta específica "venceu" (só é sabido depois
    que a(s) outra(s) IA(s) também já responderam - ver
    responder_com_ia)."""
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
        # Nunca chegou a receber resposta (rede, chave inválida, limite de
        # uso etc.) - sem custo real pra registrar, ver docstring de
        # app.custo_ia.registrar_chamada_ia.
        return None, None
    uso = getattr(mensagem, "usage", None)
    chamada = registrar_chamada_ia(
        "chat_duvida_paciente", "Claude", getattr(mensagem, "model", MODELO_PADRAO),
        getattr(uso, "input_tokens", None), getattr(uso, "output_tokens", None),
        sucesso=True, paciente_id=paciente_id,
    )
    texto = "".join(getattr(bloco, "text", "") for bloco in mensagem.content).strip()
    if not texto or MARCADOR_NAO_SEI in texto:
        return None, chamada
    return texto, chamada


def _perguntar_chatgpt(cliente, pergunta_usuario, contexto, paciente_id=None):
    """Ver docstring de `_perguntar_claude` acima - mesmo contrato de
    retorno `(texto_ou_None, chamada_ou_None)`."""
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
        return None, None
    uso = getattr(resposta, "usage", None)
    chamada = registrar_chamada_ia(
        "chat_duvida_paciente", "ChatGPT", getattr(resposta, "model", MODELO_OPENAI_PADRAO),
        getattr(uso, "prompt_tokens", None), getattr(uso, "completion_tokens", None),
        sucesso=True, paciente_id=paciente_id,
    )
    texto = (resposta.choices[0].message.content or "").strip()
    if not texto or MARCADOR_NAO_SEI in texto:
        return None, chamada
    return texto, chamada


def _perguntar_gemini(cliente, pergunta_usuario, contexto, paciente_id=None):
    """Ver docstring de `_perguntar_claude` acima - mesmo contrato de
    retorno `(texto_ou_None, chamada_ou_None)`. Mesma lib/cliente do
    import de PDF (ver app.ia_pdf_preparo), mas aqui a chamada é bem mais
    simples (só texto, sem PDF em anexo, sem retry de sobrecarga - o
    volume de perguntas do chat é baixo, e um erro passageiro aqui
    simplesmente faz o Gemini "não responder" a esta pergunta, igual a
    qualquer outro erro de IA no chat)."""
    try:
        from google.genai import types as genai_types
        resposta = cliente.models.generate_content(
            model=MODELO_GEMINI_PADRAO,
            contents=f"Dados do preparo:\n{contexto}\n\nPergunta do paciente: {pergunta_usuario}",
            config=genai_types.GenerateContentConfig(
                system_instruction=PROMPT_SISTEMA,
                max_output_tokens=300,
            ),
        )
    except Exception:
        return None, None
    uso = getattr(resposta, "usage_metadata", None)
    chamada = registrar_chamada_ia(
        "chat_duvida_paciente", "Gemini", getattr(resposta, "model_version", None) or MODELO_GEMINI_PADRAO,
        getattr(uso, "prompt_token_count", None), getattr(uso, "candidates_token_count", None),
        sucesso=True, paciente_id=paciente_id,
    )
    texto = (getattr(resposta, "text", None) or "").strip()
    if not texto or MARCADOR_NAO_SEI in texto:
        return None, chamada
    return texto, chamada


def _respostas_divergem(cliente_anthropic, resposta_a, resposta_b, paciente_id=None):
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
        uso = getattr(veredito, "usage", None)
        registrar_chamada_ia(
            "chat_duvida_paciente", "Claude", getattr(veredito, "model", MODELO_PADRAO),
            getattr(uso, "input_tokens", None), getattr(uso, "output_tokens", None),
            sucesso=True, paciente_id=paciente_id,
        )
        texto = "".join(getattr(bloco, "text", "") for bloco in veredito.content).strip().upper()
        return texto.startswith("NAO")
    except Exception:
        return True


def _sintetizar_resposta(cliente_anthropic, pergunta_usuario, resposta_a, resposta_b, paciente_id=None):
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
        uso = getattr(sintese, "usage", None)
        texto = "".join(getattr(bloco, "text", "") for bloco in sintese.content).strip()
        # Quando dá certo, o texto desta chamada é literalmente o que vira
        # a resposta final (ver responder_com_ia) - diferente das duas
        # respostas "cruas" que ela concilia, que nesse caso NÃO são a
        # resposta final (ver marcação em responder_com_ia).
        registrar_chamada_ia(
            "chat_duvida_paciente", "Claude", getattr(sintese, "model", MODELO_PADRAO),
            getattr(uso, "input_tokens", None), getattr(uso, "output_tokens", None),
            sucesso=True, paciente_id=paciente_id, resposta_final_usada=bool(texto),
        )
        return texto or None
    except Exception:
        return None


# Providers suportados no chat de dúvidas - fábrica de cliente + função de
# pergunta para cada um, usados de forma genérica em responder_com_ia
# conforme a escolha do dono (ver PlataformaConfig.ia_chat_provedor_1/2).
_PROVEDORES_CHAT = {
    "Claude": (_cliente_anthropic, _perguntar_claude),
    "ChatGPT": (_cliente_openai, _perguntar_chatgpt),
    "Gemini": (_cliente_gemini, _perguntar_gemini),
}
# Nome do campo em PerguntaPendente.resposta_bruta_<provedor> para cada um
# (ver app.routes_paciente e app.whatsapp_conversa).
CAMPO_RESPOSTA_BRUTA = {"Claude": "claude", "ChatGPT": "chatgpt", "Gemini": "gemini"}


def responder_com_ia(pergunta_usuario, exame, paciente_id=None):
    """Tenta responder a pergunta do paciente usando IA, com o preparo do
    exame como contexto. As duas IAs que respondem são escolhidas pelo
    dono da plataforma entre Gemini/ChatGPT/Claude (ver
    PlataformaConfig.ia_chat_provedor_1/2, configurável em
    /dono/configuracoes) - por padrão Claude+ChatGPT, mesmo par de
    sempre. As duas são combinadas por "reforço mútuo": se concordam, o
    rascunho final é a resposta da IA configurada como "provedor 1"; se
    divergem em algum ponto prático, o rascunho final junta as duas lado
    a lado com um aviso (ou uma síntese, ver abaixo), para o médico
    revisar com mais atenção antes de aprovar. Com só uma das duas
    configuradas realmente disponível (chave de API ausente na outra),
    funciona com aquela IA sozinha.

    IMPORTANTE: o julgamento de divergência e a síntese da resposta final
    (ver _respostas_divergem/_sintetizar_resposta) são SEMPRE feitos pela
    Claude, MESMO QUE ela não seja uma das duas IAs escolhidas para
    responder - decisão do dono (2026-08-21): a Claude continua no papel
    de árbitro/conciliadora em qualquer combinação. Se ANTHROPIC_API_KEY
    não estiver configurada, esse papel simplesmente não é exercido (cai
    nos mesmos fallbacks de sempre: trata como divergência, e não
    consegue sintetizar - mostra as duas respostas lado a lado).

    `paciente_id` (opcional) é só pra registrar de quem é o custo de cada
    chamada feita aqui dentro (ver app.custo_ia.registrar_chamada_ia,
    usado pelo painel de custo na área do dono) - não afeta a resposta
    de forma nenhuma, e pode ser omitido sem quebrar nada (só deixa de
    saber a quem atribuir aquele custo no painel).

    Retorna um dicionário {"final": ..., "por_provedor": {"Claude": ...,
    "ChatGPT": ..., "Gemini": ...}} — "por_provedor" tem a resposta crua
    de cada IA consultada (None para a que não foi escolhida, ou não
    respondeu a esta pergunta), usado por app.routes_paciente e
    app.whatsapp_conversa para preencher os 3 campos
    PerguntaPendente.resposta_bruta_<provedor> (ver
    medico/perguntas.html, que só mostra as colunas preenchidas). "final"
    é o rascunho que efetivamente vira o
    PerguntaPendente.resposta_sugerida_ia (já com a lógica de reforço
    mútuo acima aplicada) - vem None quando: nenhuma das duas IAs
    escolhidas está configurada; a(s) chamada(s) falharam (rede, limite
    de uso etc.); ou a(s) IA(s) sinalizaram que não têm certeza. Quando
    "final" é None, a pergunta segue para a correspondência por
    palavra-chave e, por fim, para a fila da secretaria — o comportamento
    de antes não muda."""
    from app.models import PlataformaConfig

    config = PlataformaConfig.obter()
    provedor_a = config.ia_chat_provedor_1 or "Claude"
    provedor_b = config.ia_chat_provedor_2 or "ChatGPT"

    cliente_a_factory, perguntar_a = _PROVEDORES_CHAT[provedor_a]
    cliente_b_factory, perguntar_b = _PROVEDORES_CHAT[provedor_b]
    cliente_a = cliente_a_factory()
    cliente_b = cliente_b_factory()

    # Cliente da Claude para o papel de árbitro/conciliadora (ver docstring
    # acima) - reaproveita o cliente já criado se Claude for uma das duas
    # respondentes, senão cria um cliente Anthropic só para esse papel.
    if provedor_a == "Claude":
        cliente_arbitro = cliente_a
    elif provedor_b == "Claude":
        cliente_arbitro = cliente_b
    else:
        cliente_arbitro = _cliente_anthropic()

    respostas_por_provedor = {"Claude": None, "ChatGPT": None, "Gemini": None}
    if not cliente_a and not cliente_b:
        return {"final": None, "por_provedor": respostas_por_provedor}

    contexto = _formatar_contexto_preparo(exame)

    resposta_a, chamada_a = (
        perguntar_a(cliente_a, pergunta_usuario, contexto, paciente_id) if cliente_a else (None, None)
    )
    resposta_b, chamada_b = (
        perguntar_b(cliente_b, pergunta_usuario, contexto, paciente_id) if cliente_b else (None, None)
    )
    respostas_por_provedor[provedor_a] = resposta_a
    respostas_por_provedor[provedor_b] = resposta_b

    if resposta_a and resposta_b:
        if _respostas_divergem(cliente_arbitro, resposta_a, resposta_b, paciente_id):
            sintese = _sintetizar_resposta(cliente_arbitro, pergunta_usuario, resposta_a, resposta_b, paciente_id)
            if sintese:
                # O rascunho final é o próprio texto sintetizado, já pronto
                # para envio ao paciente — as respostas individuais de cada
                # IA continuam visíveis acima (ver medico/perguntas.html),
                # então não é preciso repetir aviso nenhum aqui dentro do
                # textarea. As duas respostas cruas foram só INSUMO da
                # síntese, não a resposta final literal - ver
                # _sintetizar_resposta, que já marca a própria chamada como
                # `resposta_final_usada`.
                final = sintese
                if chamada_a:
                    chamada_a.resposta_final_usada = False
                if chamada_b:
                    chamada_b.resposta_final_usada = False
            else:
                # Não conseguiu sintetizar (Claude indisponível ou erro na
                # chamada) — cai de volta para mostrar as duas respostas
                # completas, em vez de travar a aprovação por causa disso.
                # Aqui as duas respostas cruas aparecem LITERALMENTE no
                # texto final, então as duas contaram.
                final = (
                    f"⚠️ As duas IAs consultadas ({provedor_a} e {provedor_b}) deram respostas "
                    "diferentes para esta pergunta — revise com atenção antes de "
                    "aprovar.\n\n"
                    f"Resposta do {provedor_a}:\n{resposta_a}\n\n"
                    f"Resposta do {provedor_b}:\n{resposta_b}"
                )
                if chamada_a:
                    chamada_a.resposta_final_usada = True
                if chamada_b:
                    chamada_b.resposta_final_usada = True
        else:
            # Concordam - usa a resposta do "provedor 1" (ordem escolhida
            # pelo dono em /dono/configuracoes) como rascunho final, mesma
            # regra de sempre preferir uma IA "principal" quando as duas já
            # dizem a mesma coisa (antes disso era sempre a Claude
            # especificamente; agora é sempre a IA configurada como
            # primeira, que pode ou não ser a Claude).
            final = resposta_a
            if chamada_a:
                chamada_a.resposta_final_usada = True
            if chamada_b:
                chamada_b.resposta_final_usada = False
    else:
        final = resposta_a or resposta_b
        if chamada_a:
            chamada_a.resposta_final_usada = bool(resposta_a)
        if chamada_b:
            chamada_b.resposta_final_usada = bool(resposta_b)

    return {"final": final, "por_provedor": respostas_por_provedor}
