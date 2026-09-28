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
não encontra nada.

Rede de segurança contra "recusa disfarçada de resposta" (pedido do
Silvan, 2026-09-14, depois de um caso real: perguntado o tempo de
antecedência pra chegar no exame - informação que não existe em nenhum
campo estruturado do preparo -, o modelo NÃO respondeu com o marcador
`NAO_SEI_ENCAMINHAR` como deveria; em vez disso, escreveu uma resposta em
português corrida dizendo que não tinha essa informação e recomendando
confirmar com a secretaria. Como o texto não era literalmente o
marcador, o sistema tratou como uma resposta válida e mandou direto pro
paciente, em vez de encaminhar pro médico. Isso é uma falha do modelo em
seguir a instrução do prompt (não-determinístico - às vezes ele usa o
marcador do jeito pedido, às vezes prefere formular a própria recusa em
texto livre), então não dá pra confiar só no prompt pra evitar de novo.
`_eh_recusa_generica_disfarcada` (abaixo) é uma checagem extra, no
código, que reconhece esse padrão de resposta (uma declaração de "não
tenho essa informação" combinada com uma recomendação de falar com a
secretaria/clínica/equipe) e trata como se fosse o marcador - encaminha
pro médico do mesmo jeito. Deliberadamente conservadora (as duas partes
do padrão precisam bater) pra não descartar por engano uma resposta de
verdade que só cita a secretaria de passagem (ex.: uma orientação válida
que termina com "qualquer dúvida, fale com a secretaria").

Julgamento de "isso faz sentido?" pela própria IA (pedido do Silvan,
2026-09-24): além da checagem por regras fixas em
app.whatsapp_conversa._eh_mensagem_sem_sentido_minimo (grátis, instantânea,
pega os casos óbvios - só símbolo, teclado travado etc.), a pergunta do
paciente que PASSA por aquela checagem também é enviada normalmente pra
IA aqui, junto com o preparo - se o texto não for uma pergunta/comentário
coerente sobre o preparo (ex.: palavras reais em ordem sem sentido, ou um
texto sobre outro assunto qualquer que nem chega a ser uma pergunta), a IA
sinaliza isso respondendo EXATAMENTE com `MARCADOR_SEM_SENTIDO` (ver
PROMPT_SISTEMA abaixo) - diferente de `MARCADOR_NAO_SEI`, que é reservado
pra uma pergunta COERENTE que só não tem informação disponível pra
responder. Essa segunda camada não faz nenhuma chamada de API extra (é a
MESMA chamada que já ia ser feita pra tentar responder a pergunta) - só
existe quando pelo menos uma das IAs de chat está configurada; sem
nenhuma, o sistema continua dependendo só da checagem por regras fixas,
como sempre. Ver `responder_com_ia` (chave "sem_sentido" do retorno) e
app.whatsapp_conversa._responder_pergunta, quem decide o que fazer com
esse sinal.

Validador de pergunta dedicado (pedido do Silvan, 2026-09-24): o
julgamento de "isso faz sentido?" acima ficava embutido na MESMA chamada
que tenta responder a pergunta, usando as 2 IAs configuradas para
responder (`ia_chat_provedor_1/2`) - sem custo extra, mas sem
configuração própria. `validar_pergunta` (abaixo) é uma checagem NOVA e
SEPARADA (uma chamada de API dedicada, mais simples e mais barata: só
classifica, não responde), usando uma ÚNICA IA escolhida pelo dono
(`PlataformaConfig.ia_validador_pergunta`, independente de qual(is) IA(s)
respondem de verdade) - chamada ANTES de `responder_com_ia`, decidindo se
a pergunta segue para o fluxo normal (FAQ/alimento/medicamento/IA) ou é
rejeitada de cara. Além de "faz sentido?" (mesmo `MARCADOR_SEM_SENTIDO` de
antes), agora também julga "é sobre ESTE exame?" (`MARCADOR_FORA_DO_EXAME`,
abaixo) - pedido novo do Silvan: uma pergunta coerente mas claramente sem
nenhuma relação com exame médico nenhum (ex.: futebol, previsão do tempo,
assunto pessoal) não deveria nem chegar na fila do médico, do mesmo jeito
que uma mensagem sem sentido não chega. Uma pergunta sobre OUTRO assunto
da clínica (ex.: horário de atendimento, endereço) continua indo pro fluxo
normal (a IA de resposta pode não saber e sinalizar `MARCADOR_NAO_SEI`,
mas isso ainda é uma dúvida legítima de paciente de clínica, que o médico
deve ver) - `MARCADOR_FORA_DO_EXAME` é reservado para assunto que não tem
NADA a ver com exame médico. O sinal de `responder_com_ia` ("sem_sentido"
no retorno, ver docstring dela) continua existindo como segunda camada de
segurança (mesma chamada de resposta, sem custo extra) - as duas
checagens não são mutuamente exclusivas, só a nova (`validar_pergunta`)
roda PRIMEIRO e evita a chamada de resposta por completo quando já
rejeita a pergunta."""
import os
import re
import unicodedata

from flask import current_app

from app.custo_ia import registrar_chamada_ia

MARCADOR_NAO_SEI = "NAO_SEI_ENCAMINHAR"
# Ver docstring do módulo ("Julgamento de 'isso faz sentido?' pela própria
# IA") - diferente de MARCADOR_NAO_SEI: este marca uma mensagem que nem é
# uma pergunta/comentário coerente sobre o preparo (não que falte
# informação pra responder a uma pergunta coerente).
MARCADOR_SEM_SENTIDO = "SEM_SENTIDO_ENCAMINHAR"
# Ver docstring do módulo ("Validador de pergunta dedicado", 2026-09-24) -
# usado só por `validar_pergunta`, não pelas IAs de resposta
# (`_perguntar_claude`/`_perguntar_chatgpt`/`_perguntar_gemini`): pergunta
# coerente, mas sem nenhuma relação com exame médico nenhum.
MARCADOR_FORA_DO_EXAME = "FORA_DO_EXAME_ENCAMINHAR"

# Ver docstring do módulo ("Rede de segurança contra recusa disfarçada").
# Primeiro grupo: a IA declarando que não tem a informação. Segundo grupo:
# a IA recomendando falar com alguém da clínica. Só conta como recusa
# disfarçada quando as duas partes aparecem na mesma resposta - qualquer
# uma isolada é comum demais em respostas de verdade pra servir de sinal
# sozinha (ex.: uma resposta válida pode perfeitamente terminar com "fale
# com a secretaria em caso de dúvida" sem estar se recusando a responder).
#
# Bug corrigido (pedido do Silvan, 2026-09-24): o segundo grupo exigia a
# forma exata "entrar em contato" (infinitivo), mas a IA respondeu na
# prática com "entre em contato" (imperativo) - não batia, e uma recusa
# disfarçada real (pergunta sobre maconha, fora do preparo) passou como
# se fosse resposta de verdade e foi enviada direto ao paciente sem
# passar pelo médico (aprovação automática estava ativada para aquele
# médico/grupo - ver `exige_aprovacao_pergunta` em app.routes_paciente).
# Trocado "entrar em contato" por "entr\w* em contato" (cobre entrar/
# entre/entrei/entrando etc.) e adicionado "procur\w*" (ex.: "procure a
# secretaria") como mais uma forma comum de encaminhamento.
_PADROES_SEM_INFORMACAO = re.compile(
    r"nao (?:ha|tenho|temos|possuo|encontrei|consta) "
    r"(?:nenhuma |essa |esta |informa)|"
    r"nao (?:esta|foi) (?:especificad|informad|cadastrad)"
)
_PADROES_ENCAMINHA_PARA_CLINICA = re.compile(
    r"(?:confirm\w*|verifi\w*|entr\w* em contato|fal\w*|consult\w*|procur\w*).{0,30}"
    r"(?:secretaria|clinica|equipe|recepcao)"
)


def _normalizar_para_deteccao(texto):
    """Minúsculas e sem acento, só pra facilitar o casamento dos padrões
    acima contra variações de acentuação - não é exibido a ninguém."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return sem_acento.lower()


def _eh_recusa_generica_disfarcada(texto):
    """True quando o texto parece uma recusa em disfarce de resposta (ver
    docstring do módulo) - a IA deveria ter usado MARCADOR_NAO_SEI, mas
    preferiu formular a própria recusa em linguagem natural."""
    texto_normalizado = _normalizar_para_deteccao(texto)
    return bool(
        _PADROES_SEM_INFORMACAO.search(texto_normalizado)
        and _PADROES_ENCAMINHA_PARA_CLINICA.search(texto_normalizado)
    )

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
- Antes de aplicar qualquer regra acima, avalie se o texto do paciente é sequer uma pergunta ou comentário coerente (mesmo que informal, curto ou com erros de digitação/ortografia). Se o texto for palavras reais mas sem nenhum sentido coerente entre si, ou for sobre um assunto qualquer que nem chega a formar uma pergunta/comentário compreensível, responda EXATAMENTE com o texto SEM_SENTIDO_ENCAMINHAR, nada mais. NÃO use SEM_SENTIDO_ENCAMINHAR para uma pergunta coerente que só está fora do preparo ou que você não consegue identificar (esses casos usam NAO_SEI_ENCAMINHAR, acima) — a diferença é: NAO_SEI_ENCAMINHAR é "entendi a pergunta, mas não tenho a informação"; SEM_SENTIDO_ENCAMINHAR é "isso nem é uma pergunta/comentário que eu consiga entender". Na dúvida entre os dois, ou na dúvida entre usar SEM_SENTIDO_ENCAMINHAR e simplesmente responder, prefira responder normalmente ou usar NAO_SEI_ENCAMINHAR — evite usar SEM_SENTIDO_ENCAMINHAR para um texto que dá pra entender, mesmo que mal escrito.
- Nunca responda sobre assuntos fora do preparo deste exame específico (ex.: diagnósticos, tratamentos, outros exames).
- Quando houver um "Histórico recente desta conversa" listado antes da pergunta atual, use-o para entender o CONTEXTO da conversa em aberto com este paciente — principalmente perguntas de acompanhamento curtas que só fazem sentido em conjunto com a pergunta anterior (ex.: depois de "posso comer batata?", a pergunta seguinte "e frita?" deve ser entendida como "posso comer batata frita?", não como uma pergunta solta e incompleta). Sem esse histórico, trate a pergunta como isolada, do jeito de sempre."""


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


def _formatar_historico_conversa(historico):
    """Pedido do Silvan (2026-09-14) - "conceito de conversa": a IA deve
    entender que existe uma conversa em aberto com o paciente (ex.: depois
    de "posso comer batata?", a pergunta seguinte "e frita?" só faz
    sentido em conjunto com a anterior). `historico` é uma lista de
    tuplas `(pergunta, resposta)` em ordem CRONOLÓGICA (mais antiga
    primeiro) - ver app.routes_paciente._historico_recente_chat, quem
    monta essa lista a partir de `ChatMensagem` (mesmo paciente + mesmo
    exame, dentro de uma janela recente de tempo). Formata como um bloco
    de texto simples (não como múltiplos turnos de mensagem "de verdade"
    na API de cada provedor - Claude/OpenAI/Gemini têm formatos de
    histórico multi-turno ligeiramente diferentes entre si, e cada
    chamada aqui já é stateless/reconstruída do zero a cada pergunta; um
    bloco de texto simples, incluído dentro do mesmo "content" de sempre,
    é suficiente para a IA entender a continuidade e funciona igual nos
    3 provedores). Sem histórico (lista vazia/None - pergunta isolada, ou
    nenhuma pergunta anterior recente sobre este mesmo exame), devolve
    string vazia - quem chama simplesmente não inclui o bloco, mantendo o
    texto exatamente igual ao de antes desta funcionalidade existir.
    `resposta` pode ser None (pergunta anterior ainda pendente de
    aprovação do médico, ver ChatMensagem.resposta) - nesse caso mostra só
    a pergunta, sem inventar uma resposta que ainda não existe; mesmo
    assim vale como contexto para entender uma pergunta de continuação."""
    if not historico:
        return ""
    linhas = ["Histórico recente desta conversa com o paciente sobre este mesmo exame (mais antiga primeiro):"]
    for pergunta_anterior, resposta_anterior in historico:
        linhas.append(f'- Paciente perguntou antes: "{pergunta_anterior}"')
        if resposta_anterior:
            linhas.append(f'  Resposta que foi dada: "{resposta_anterior}"')
    return "\n".join(linhas) + "\n\n"


def _perguntar_claude(cliente, pergunta_usuario, contexto, paciente_id=None, historico=None):
    """Devolve uma tupla `(texto_ou_None, chamada_ou_None, sem_sentido_bool)`
    - `chamada` é o `ChamadaIA` já registrado (ver
    app.custo_ia.registrar_chamada_ia), para quem chamou poder marcar
    depois `.resposta_final_usada` assim que souber se esta resposta
    específica "venceu" (só é sabido depois que a(s) outra(s) IA(s)
    também já responderam - ver responder_com_ia). `sem_sentido_bool` é
    True quando esta IA respondeu com o marcador MARCADOR_SEM_SENTIDO -
    ver docstring do módulo ("Julgamento de 'isso faz sentido?'")."""
    try:
        mensagem = cliente.messages.create(
            model=MODELO_PADRAO,
            max_tokens=300,
            system=PROMPT_SISTEMA,
            messages=[{
                "role": "user",
                "content": (
                    f"Dados do preparo:\n{contexto}\n\n"
                    f"{_formatar_historico_conversa(historico)}"
                    f"Pergunta do paciente: {pergunta_usuario}"
                ),
            }],
        )
    except Exception:
        # Nunca chegou a receber resposta (rede, chave inválida, limite de
        # uso etc.) - sem custo real pra registrar, ver docstring de
        # app.custo_ia.registrar_chamada_ia. Registra no log só para dar
        # visibilidade (antes esse tipo de falha era totalmente
        # silencioso - nem aparecia no painel de custo do dono, já que
        # nenhuma chamada com custo chegou a acontecer) - não muda o
        # comportamento (a pergunta continua caindo pros outros
        # caminhos de sempre).
        current_app.logger.exception("Falha ao consultar a Claude para responder pergunta do paciente")
        return None, None, False
    uso = getattr(mensagem, "usage", None)
    chamada = registrar_chamada_ia(
        "chat_duvida_paciente", "Claude", getattr(mensagem, "model", MODELO_PADRAO),
        getattr(uso, "input_tokens", None), getattr(uso, "output_tokens", None),
        sucesso=True, paciente_id=paciente_id,
    )
    texto = "".join(getattr(bloco, "text", "") for bloco in mensagem.content).strip()
    if not texto or MARCADOR_NAO_SEI in texto or _eh_recusa_generica_disfarcada(texto):
        return None, chamada, False
    if MARCADOR_SEM_SENTIDO in texto:
        return None, chamada, True
    return texto, chamada, False


def _perguntar_chatgpt(cliente, pergunta_usuario, contexto, paciente_id=None, historico=None):
    """Ver docstring de `_perguntar_claude` acima - mesmo contrato de
    retorno `(texto_ou_None, chamada_ou_None, sem_sentido_bool)`."""
    try:
        resposta = cliente.chat.completions.create(
            model=MODELO_OPENAI_PADRAO,
            max_tokens=300,
            messages=[
                {"role": "system", "content": PROMPT_SISTEMA},
                {
                    "role": "user",
                    "content": (
                        f"Dados do preparo:\n{contexto}\n\n"
                        f"{_formatar_historico_conversa(historico)}"
                        f"Pergunta do paciente: {pergunta_usuario}"
                    ),
                },
            ],
        )
    except Exception:
        current_app.logger.exception("Falha ao consultar o ChatGPT para responder pergunta do paciente")
        return None, None, False
    uso = getattr(resposta, "usage", None)
    chamada = registrar_chamada_ia(
        "chat_duvida_paciente", "ChatGPT", getattr(resposta, "model", MODELO_OPENAI_PADRAO),
        getattr(uso, "prompt_tokens", None), getattr(uso, "completion_tokens", None),
        sucesso=True, paciente_id=paciente_id,
    )
    texto = (resposta.choices[0].message.content or "").strip()
    if not texto or MARCADOR_NAO_SEI in texto or _eh_recusa_generica_disfarcada(texto):
        return None, chamada, False
    if MARCADOR_SEM_SENTIDO in texto:
        return None, chamada, True
    return texto, chamada, False


def _perguntar_gemini(cliente, pergunta_usuario, contexto, paciente_id=None, historico=None):
    """Ver docstring de `_perguntar_claude` acima - mesmo contrato de
    retorno `(texto_ou_None, chamada_ou_None, sem_sentido_bool)`. Mesma lib/cliente do
    import de PDF (ver app.ia_pdf_preparo), mas aqui a chamada é bem mais
    simples (só texto, sem PDF em anexo, sem retry de sobrecarga - o
    volume de perguntas do chat é baixo, e um erro passageiro aqui
    simplesmente faz o Gemini "não responder" a esta pergunta, igual a
    qualquer outro erro de IA no chat)."""
    try:
        from google.genai import types as genai_types
        resposta = cliente.models.generate_content(
            model=MODELO_GEMINI_PADRAO,
            contents=(
                f"Dados do preparo:\n{contexto}\n\n"
                f"{_formatar_historico_conversa(historico)}"
                f"Pergunta do paciente: {pergunta_usuario}"
            ),
            config=genai_types.GenerateContentConfig(
                system_instruction=PROMPT_SISTEMA,
                max_output_tokens=300,
            ),
        )
    except Exception:
        current_app.logger.exception("Falha ao consultar o Gemini para responder pergunta do paciente")
        return None, None, False
    uso = getattr(resposta, "usage_metadata", None)
    chamada = registrar_chamada_ia(
        "chat_duvida_paciente", "Gemini", getattr(resposta, "model_version", None) or MODELO_GEMINI_PADRAO,
        getattr(uso, "prompt_token_count", None), getattr(uso, "candidates_token_count", None),
        sucesso=True, paciente_id=paciente_id,
    )
    texto = (getattr(resposta, "text", None) or "").strip()
    if not texto or MARCADOR_NAO_SEI in texto or _eh_recusa_generica_disfarcada(texto):
        return None, chamada, False
    if MARCADOR_SEM_SENTIDO in texto:
        return None, chamada, True
    return texto, chamada, False


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


def _tentar_provedor(nome_provedor, pergunta_usuario, contexto, paciente_id=None, historico=None):
    """Cria o cliente do provedor indicado (se a API key dele estiver
    configurada) e tenta obter uma resposta. Retorna
    `(texto_ou_None, chamada_ou_None, tentou_bool, sem_sentido_bool)` -
    `tentou_bool` distingue "provedor sem API key configurada" (False -
    nem tentou) de "tinha API key e a chamada foi feita" (True, mesmo que
    tenha falhado) - usado por responder_com_ia para decidir quando vale a
    pena acionar a reserva (ver logo abaixo). `sem_sentido_bool` (ver
    docstring do módulo, "Julgamento de 'isso faz sentido?'") só pode ser
    True quando `tentou_bool` também é True."""
    cliente_factory, perguntar = _PROVEDORES_CHAT[nome_provedor]
    cliente = cliente_factory()
    if not cliente:
        return None, None, False, False
    texto, chamada, sem_sentido = perguntar(cliente, pergunta_usuario, contexto, paciente_id, historico)
    return texto, chamada, True, sem_sentido


def responder_com_ia(pergunta_usuario, exame, paciente_id=None, historico=None):
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

    `historico` (opcional, pedido do Silvan, 2026-09-14 - "conceito de
    conversa"): lista de tuplas `(pergunta, resposta)` em ordem
    cronológica (mais antiga primeiro) das últimas perguntas deste mesmo
    paciente sobre este mesmo exame - ver
    app.routes_paciente._historico_recente_chat, quem monta essa lista.
    Passada a cada uma das IAs consultadas (ver
    _formatar_historico_conversa) para que uma pergunta de acompanhamento
    curta ("e frita?") seja entendida em conjunto com a pergunta anterior
    ("posso comer batata?"), em vez de tratada como uma pergunta solta e
    incompreensível sozinha. Omitido (None/lista vazia), o comportamento é
    idêntico a antes desta funcionalidade existir.

    Retorna um dicionário {"final": ..., "por_provedor": {"Claude": ...,
    "ChatGPT": ..., "Gemini": ...}, "falhas": [...], "sem_sentido": ...} —
    "por_provedor" tem a resposta crua
    de cada IA consultada (None para a que não foi escolhida, ou não
    respondeu a esta pergunta), usado por app.routes_paciente e
    app.whatsapp_conversa para preencher os 3 campos
    PerguntaPendente.resposta_bruta_<provedor> (ver
    medico/perguntas.html, que só mostra as colunas preenchidas). "final"
    é o rascunho que efetivamente vira o
    PerguntaPendente.resposta_sugerida_ia (já com a lógica de reforço
    mútuo acima aplicada) - vem None quando: nenhuma das duas IAs
    escolhidas está configurada; a(s) chamada(s) falharam (rede, limite
    de uso etc.) mesmo depois da reserva (ver abaixo); a(s) IA(s)
    sinalizaram que não têm certeza (com o marcador `MARCADOR_NAO_SEI`);
    ou a resposta bateu com o padrão de "recusa disfarçada de resposta"
    (`_eh_recusa_generica_disfarcada`, ver docstring do módulo -
    2026-09-14) - quando a IA, em vez de usar o marcador, escreve a
    própria recusa em texto livre ("não tenho essa informação, confirme
    com a secretaria"). Quando "final" é None, a pergunta segue para a
    correspondência por palavra-chave e, por fim, para a fila da
    secretaria — o comportamento de antes não muda.

    Reserva automática (2026-08-25): quando uma das duas IAs escolhidas
    pelo dono tem API key configurada mas a chamada falha de verdade (erro
    de rede/API - ex.: o 503 "high demand" que o Gemini apresenta em
    picos, ver `_tentar_provedor`), a terceira IA (a que o dono NÃO
    escolheu) é chamada como reserva para esta pergunta específica, desde
    que ela também tenha API key configurada. Se a reserva responder, a
    resposta dela ocupa o lugar da que falhou (inclusive no campo
    `por_provedor` correspondente, para o médico ver claramente qual IA
    respondeu de fato). Isso é diferente de "provedor não escolhido" (que
    nunca é chamado) - só entra em ação quando uma das duas escolhidas
    falha. Se as duas escolhidas falharem na mesma pergunta, a reserva só
    é acionada uma vez (evita gastar 2x a mesma chamada à toa); nesse caso
    o rascunho final acaba dependendo só da resposta da reserva, como se
    ela fosse a única IA disponível.

    "falhas" é a lista dos nomes das IAs que tiveram um erro de chamada de
    verdade nesta pergunta (0, 1, 2 ou os 3 nomes, na ordem em que
    falharam) — inclui tanto uma das duas escolhidas quanto a própria
    reserva, se ela também falhar. Pensado para persistir em
    PerguntaPendente.ias_com_erro (ver app.routes_paciente/
    app.whatsapp_conversa) e mostrar ao médico na tela de aprovação (ver
    medico/perguntas.html) que uma IA configurada deu erro nesta pergunta
    específica — mesmo quando a reserva "tapou o buraco" e o rascunho
    final saiu normal, sem nenhum outro sinal visível do problema.

    "sem_sentido" (pedido do Silvan, 2026-09-24 - ver docstring do módulo,
    "Julgamento de 'isso faz sentido?'") é True só quando "final" veio
    None E TODAS as IAs que efetivamente responderam a esta pergunta (pelo
    menos uma) sinalizaram com MARCADOR_SEM_SENTIDO - conservador de
    propósito (mesmo espírito do "reforço mútuo" acima): se uma IA achou
    sem sentido mas a outra respondeu normalmente, "final" já não é None
    (usa a que respondeu) e "sem_sentido" fica False. Quando nenhuma IA
    está configurada, ou nenhuma chegou a responder de verdade (só falha
    de chamada), também é False - só a checagem por regras fixas
    (app.whatsapp_conversa._eh_mensagem_sem_sentido_minimo) continua
    valendo nesses casos. Quem chamou (app.whatsapp_conversa.
    _responder_pergunta) usa esse sinal para devolver o mesmo aviso de
    "não consegui entender essa mensagem" de sempre, SEM criar
    PerguntaPendente nem ChatMensagem - mesmo comportamento da checagem
    por regras fixas, só que pega casos mais sutis (palavras reais em
    ordem sem sentido) que a checagem fixa não pega."""
    from app.models import PlataformaConfig

    config = PlataformaConfig.obter()
    provedor_a = config.ia_chat_provedor_1 or "Claude"
    provedor_b = config.ia_chat_provedor_2 or "ChatGPT"
    provedor_c = next(nome for nome in _PROVEDORES_CHAT if nome not in (provedor_a, provedor_b))

    respostas_por_provedor = {"Claude": None, "ChatGPT": None, "Gemini": None}
    contexto = _formatar_contexto_preparo(exame)

    resposta_a, chamada_a, tentou_a, sem_sentido_a = _tentar_provedor(provedor_a, pergunta_usuario, contexto, paciente_id, historico)
    resposta_b, chamada_b, tentou_b, sem_sentido_b = _tentar_provedor(provedor_b, pergunta_usuario, contexto, paciente_id, historico)

    if not tentou_a and not tentou_b:
        # Nenhuma das duas escolhidas tem API key configurada - não é
        # "falha", é "não configurada", não faz sentido acionar reserva.
        return {"final": None, "por_provedor": respostas_por_provedor, "falhas": [], "sem_sentido": False}

    nome_efetivo_a, nome_efetivo_b = provedor_a, provedor_b

    # Uma IA "falhou de verdade" quando tinha cliente (tentou_x=True) mas
    # não voltou nem resposta nem ChamadaIA registrado - ver
    # _perguntar_claude/_perguntar_chatgpt/_perguntar_gemini, que só
    # devolvem (None, None) nesse caso; (None, chamada) é "respondeu mas
    # sinalizou que não tem certeza", isso não é falha e não aciona
    # reserva.
    falhou_a = tentou_a and resposta_a is None and chamada_a is None
    falhou_b = tentou_b and resposta_b is None and chamada_b is None

    # Nomes das IAs que falharam de verdade nesta pergunta - devolvido pra
    # quem chamou persistir em PerguntaPendente.ias_com_erro (ver
    # app.routes_paciente/app.whatsapp_conversa) e mostrar ao médico na
    # tela de aprovação (ver medico/perguntas.html), mesmo quando uma
    # reserva "tapou o buraco" e o médico nem percebeu que uma das IAs
    # escolhidas deu erro nesta pergunta específica.
    falhas = []
    if falhou_a:
        falhas.append(provedor_a)
    if falhou_b:
        falhas.append(provedor_b)

    if falhou_a or falhou_b:
        current_app.logger.warning(
            "IA configurada (%s) falhou ao responder pergunta do paciente - tentando %s (não escolhida) como reserva",
            provedor_a if falhou_a else provedor_b, provedor_c,
        )
        resposta_c, chamada_c, tentou_c, sem_sentido_c = _tentar_provedor(provedor_c, pergunta_usuario, contexto, paciente_id, historico)
        reserva_respondeu = tentou_c and (resposta_c is not None or chamada_c is not None)
        if tentou_c and not reserva_respondeu:
            # A reserva também falhou de verdade - registra pra aparecer
            # na tela do médico igual às outras.
            falhas.append(provedor_c)
        if reserva_respondeu and falhou_a:
            resposta_a, chamada_a, nome_efetivo_a, sem_sentido_a = resposta_c, chamada_c, provedor_c, sem_sentido_c
        elif reserva_respondeu and falhou_b:
            resposta_b, chamada_b, nome_efetivo_b, sem_sentido_b = resposta_c, chamada_c, provedor_c, sem_sentido_c

    respostas_por_provedor[nome_efetivo_a] = resposta_a
    respostas_por_provedor[nome_efetivo_b] = resposta_b

    # Cliente da Claude para o papel de árbitro/conciliadora (ver docstring
    # acima) - construído à parte (independente de quem respondeu como
    # escolhida ou como reserva) porque `_tentar_provedor` não expõe o
    # cliente que criou por dentro; o custo de instanciar um segundo
    # cliente Anthropic quando Claude já respondeu é desprezível (não é
    # uma chamada de API, só a construção do objeto cliente).
    cliente_arbitro = _cliente_anthropic()

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
                    f"⚠️ As duas IAs consultadas ({nome_efetivo_a} e {nome_efetivo_b}) deram respostas "
                    "diferentes para esta pergunta — revise com atenção antes de "
                    "aprovar.\n\n"
                    f"Resposta do {nome_efetivo_a}:\n{resposta_a}\n\n"
                    f"Resposta do {nome_efetivo_b}:\n{resposta_b}"
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

    # Ver docstring acima ("sem_sentido") - só quando não sobrou nenhum
    # rascunho final E todas as IAs que de fato responderam (chamada
    # registrada) concordaram que o texto não fazia sentido.
    sem_sentido = False
    if not final:
        respondentes_sem_sentido = []
        if chamada_a is not None:
            respondentes_sem_sentido.append(sem_sentido_a)
        if chamada_b is not None:
            respondentes_sem_sentido.append(sem_sentido_b)
        sem_sentido = bool(respondentes_sem_sentido) and all(respondentes_sem_sentido)

    return {"final": final, "por_provedor": respostas_por_provedor, "falhas": falhas, "sem_sentido": sem_sentido}


# Ver docstring do módulo ("Validador de pergunta dedicado", 2026-09-24).
# Instruções BEM mais estreitas que PROMPT_SISTEMA acima - só classifica,
# nunca responde a pergunta em si (evita qualquer tentação de a IA
# "aproveitar" e já responder, o que quebraria o parsing do marcador).
PROMPT_SISTEMA_VALIDADOR = """Você é um validador de mensagens recebidas de pacientes no chat de dúvidas sobre preparo de um exame médico. Sua ÚNICA tarefa é CLASSIFICAR a mensagem do paciente - nunca respondê-la.

Classifique em exatamente uma das 3 categorias abaixo, e responda SOMENTE com o texto exato da categoria escolhida (nenhuma outra palavra, pontuação ou explicação):

- SEM_SENTIDO_ENCAMINHAR: o texto não é uma pergunta ou comentário coerente (ex.: palavras reais em ordem sem nenhum sentido entre si, teclado travado, texto que nem chega a formar uma ideia compreensível) - mesmo que informal, curto ou com erros de digitação/ortografia, se dá para entender a intenção, NÃO é este caso.
- FORA_DO_EXAME_ENCAMINHAR: o texto É coerente, mas não tem NENHUMA relação com exame médico nenhum (ex.: futebol, previsão do tempo, assunto pessoal, pedido sobre outro serviço qualquer sem nenhuma ligação com exames). Perguntas sobre outros assuntos da própria clínica (horário de atendimento, endereço, agendamento, valores) NÃO se encaixam aqui - são relacionadas à clínica/exame, mesmo que não sejam sobre o preparo específico deste exame.
- VALIDA: qualquer pergunta ou comentário coerente relacionado a exame médico ou à clínica, incluindo dúvidas sobre o preparo deste exame específico, mesmo que a resposta não esteja disponível nos dados fornecidos - a checagem de "temos a informação?" é feita depois, por outra parte do sistema, não é sua tarefa aqui.

Quando houver um "Histórico recente desta conversa" listado antes da mensagem atual, use-o para entender o CONTEXTO (ex.: uma mensagem de acompanhamento curta só faz sentido em conjunto com a anterior). Na dúvida entre duas categorias, prefira sempre VALIDA - é mais seguro deixar uma pergunta ambígua seguir o fluxo normal do que rejeitá-la por engano."""


def validar_pergunta(pergunta_usuario, exame, paciente_id=None, historico=None):
    """Checagem dedicada (pedido do Silvan, 2026-09-24 - ver docstring do
    módulo, "Validador de pergunta dedicado") que roda ANTES de
    `responder_com_ia`: usa uma ÚNICA IA (`PlataformaConfig.
    ia_validador_pergunta`, escolhida pelo dono em /dono/configuracoes,
    independente de `ia_chat_provedor_1/2`) para classificar a mensagem
    do paciente em sem_sentido / fora_do_exame / válida, numa chamada
    separada e mais barata (só classificação, sem tentar responder).

    Retorna um dicionário {"classificacao": ..., "chamada": ...} -
    "classificacao" é "sem_sentido", "fora_do_exame", "valida" ou None
    (sem julgamento possível: `exame` ausente, a IA escolhida não está
    configurada/disponível, a chamada falhou, ou a resposta não bateu com
    nenhum marcador esperado - nesses casos trata como "valida" na
    prática, já que quem chama só precisa agir sobre "sem_sentido"/
    "fora_do_exame" e deixa tudo o mais seguir o fluxo normal de sempre,
    mesmo espírito conservador do resto do módulo: na dúvida, não
    bloqueia). "chamada" é o `ChamadaIA` já registrado (ou None), para
    quem chamar decidir se quer fazer algo com ele (hoje, ninguém faz -
    só existe para manter o mesmo padrão das outras funções deste
    módulo, e para aparecer no painel de custo do dono como qualquer
    outra chamada de IA)."""
    from app.models import PlataformaConfig

    if not exame:
        return {"classificacao": None, "chamada": None}

    config = PlataformaConfig.obter()
    provedor = config.ia_validador_pergunta or "Claude"
    fabrica_cliente, _ = _PROVEDORES_CHAT.get(provedor, (None, None))
    cliente = fabrica_cliente() if fabrica_cliente else None
    if not cliente:
        # IA escolhida sem API key configurada - sem validador disponível,
        # segue o fluxo de sempre (checagem por regras fixas continua
        # valendo em app.whatsapp_conversa).
        return {"classificacao": None, "chamada": None}

    conteudo = (
        f"Nome do exame em foco nesta conversa: {exame.nome}\n\n"
        f"{_formatar_historico_conversa(historico)}"
        f"Mensagem do paciente: {pergunta_usuario}"
    )

    try:
        if provedor == "Claude":
            resposta = cliente.messages.create(
                model=MODELO_PADRAO, max_tokens=12, system=PROMPT_SISTEMA_VALIDADOR,
                messages=[{"role": "user", "content": conteudo}],
            )
            uso = getattr(resposta, "usage", None)
            chamada = registrar_chamada_ia(
                "validador_pergunta_paciente", "Claude", getattr(resposta, "model", MODELO_PADRAO),
                getattr(uso, "input_tokens", None), getattr(uso, "output_tokens", None),
                sucesso=True, paciente_id=paciente_id,
            )
            texto = "".join(getattr(bloco, "text", "") for bloco in resposta.content).strip()
        elif provedor == "ChatGPT":
            resposta = cliente.chat.completions.create(
                model=MODELO_OPENAI_PADRAO, max_tokens=12,
                messages=[
                    {"role": "system", "content": PROMPT_SISTEMA_VALIDADOR},
                    {"role": "user", "content": conteudo},
                ],
            )
            uso = getattr(resposta, "usage", None)
            chamada = registrar_chamada_ia(
                "validador_pergunta_paciente", "ChatGPT", getattr(resposta, "model", MODELO_OPENAI_PADRAO),
                getattr(uso, "prompt_tokens", None), getattr(uso, "completion_tokens", None),
                sucesso=True, paciente_id=paciente_id,
            )
            texto = (resposta.choices[0].message.content or "").strip()
        else:  # Gemini
            from google.genai import types as genai_types
            resposta = cliente.models.generate_content(
                model=MODELO_GEMINI_PADRAO, contents=conteudo,
                config=genai_types.GenerateContentConfig(
                    system_instruction=PROMPT_SISTEMA_VALIDADOR, max_output_tokens=12,
                ),
            )
            uso = getattr(resposta, "usage_metadata", None)
            chamada = registrar_chamada_ia(
                "validador_pergunta_paciente", "Gemini", getattr(resposta, "model_version", None) or MODELO_GEMINI_PADRAO,
                getattr(uso, "prompt_token_count", None), getattr(uso, "candidates_token_count", None),
                sucesso=True, paciente_id=paciente_id,
            )
            texto = (getattr(resposta, "text", None) or "").strip()
    except Exception:
        current_app.logger.exception(
            "Falha ao consultar %s para validar pergunta do paciente (ia_validador_pergunta)", provedor,
        )
        return {"classificacao": None, "chamada": None}

    texto_normalizado = texto.upper()
    if MARCADOR_SEM_SENTIDO in texto_normalizado:
        classificacao = "sem_sentido"
    elif MARCADOR_FORA_DO_EXAME in texto_normalizado:
        classificacao = "fora_do_exame"
    elif "VALIDA" in texto_normalizado:
        classificacao = "valida"
    else:
        # Resposta que não bateu com nenhum marcador esperado (a IA não
        # seguiu a instrução à risca) - mais seguro tratar como "valida" e
        # deixar a pergunta seguir o fluxo normal do que arriscar bloquear
        # uma pergunta legítima por um formato de resposta inesperado.
        classificacao = "valida"
    return {"classificacao": classificacao, "chamada": chamada}
