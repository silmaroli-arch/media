"""Fatia 7 (área de WhatsApp) — passos 3, 4 e 5 do plano:
- Passo 3: identificação do paciente por CPF + data de nascimento, com
  sessão de conversa que expira por inatividade (ver `ConversaWhatsapp`
  em app/models.py).
- Passo 4: uma vez identificado (e com um exame em foco escolhido), o
  paciente vê um convite pra perguntar - o menu antigo ("1) Ver
  informações do preparo" / "2) Fazer uma pergunta") foi removido a
  pedido do Silvan (2026-09-11). Nesse mesmo dia, mais tarde, o Silvan
  reparou (com print de conversa real) que isso tinha um efeito colateral
  ruim: SEM nenhuma barreira, qualquer mensagem solta do paciente
  enquanto não há pergunta pendente - uma saudação como "oi", um
  emoji, um comentário qualquer - era tratada como uma pergunta NOVA e
  encaminhada pra equipe (chegava até a avisar o médico por WhatsApp, ver
  app.push_notificacoes), poluindo a fila de perguntas pendentes sem
  necessidade. Correção (mesmo dia): reintroduzido um gatilho simples -
  o paciente precisava digitar **1** antes de cada pergunta; só a
  mensagem seguinte ao "1" era tratada como o texto da pergunta em si.

  **Gatilho "1" removido de novo (pedido do Silvan, 2026-09-14)**: "Vamos
  tirar o digite 1 para fazer uma pergunta" - agora qualquer texto (que
  não seja o comando "trocar", nem reconhecido como intenção de
  remarcação/número errado, ver mais abaixo) já é tratado direto como a
  pergunta em si, sem exigir digitar "1" antes. Isso reabre deliberadamente
  o problema descrito no parágrafo acima (uma saudação solta como "oi"
  volta a virar uma PerguntaPendente encaminhada à equipe, em vez de só
  repetir o convite) - decisão explícita do Silvan, não um descuido. O
  campo `ConversaWhatsapp.aguardando_pergunta` (usado pelo gatilho)
  continua existindo no banco só por compatibilidade com dados antigos,
  mas não é mais lido nem escrito por este módulo. "Trocar de exame"
  continua existindo (só quando há mais de um exame ativo), acionado
  pela palavra "trocar" em qualquer momento.
- Passo 5 (este arquivo): a pergunta livre reaproveita a MESMA lógica de
  app.routes_paciente.chat() (base de conhecimento/alimento/medicamento
  primeiro - pedido do Silvan, 2026-09-11; só quando nada bate a IA é
  consultada, com a resposta ficando pendente de aprovação do médico; sem
  IA, ou sem resposta dela, encaminhada pra equipe) - importa o helper
  `_resolver_ancora` de lá em vez de duplicar a regra de roteamento pra
  Grupo/dono pessoal.

Este módulo é só a LÓGICA de conversa (recebe telefone + texto da
mensagem, devolve o texto da resposta) — não sabe nada sobre Twilio nem
sobre HTTP, para poder ser testado sem precisar simular um webhook (ver
app/routes_whatsapp.py, que é a única coisa que fala com o provedor).

Documento "Clara" (2026-09-14) - três itens de baixo risco autorizados
pelo Silvan ("Pode começar", nenhum deles desfaz nada que já existia):
- Item 7: limite de tentativas de identificação. Cada vez que o par
  CPF + data de nascimento não bate com nenhum cadastro (ver
  `_localizar_paciente`), conta como uma tentativa
  (`ConversaWhatsapp.tentativas_identificacao`); ao chegar no limite
  (`ConversaWhatsapp.LIMITE_TENTATIVAS_IDENTIFICACAO`, hoje 3), a
  conversa é BLOQUEADA (ver `ConversaWhatsapp.bloqueada` e
  MENSAGEM_IDENTIFICACAO_BLOQUEADA) - proteção contra tentativa repetida
  de adivinhar dados de outra pessoa. Não conta tentativas de CPF em
  formato inválido (isso é só um erro de digitação, tratado à parte).
- Item 6: fluxo formal de "número errado". Reconhece frases como "número
  errado"/"não conheço essa pessoa" (ver `_eh_numero_errado`, em qualquer
  etapa da conversa) e BLOQUEIA a conversa (mesmo campo `bloqueada`
  acima) - já que a identificação normal é só por CPF/data de nascimento
  (nunca pelo número de WhatsApp em si), isso pode acontecer ANTES de
  identificar ninguém; nesse caso tenta achar, por aproximação de
  telefone, qual Paciente cadastrado é o "dono" esperado desse número
  (ver `_paciente_por_telefone_aproximado`) só para saber qual clínica
  avisar (ver app.push_notificacoes.notificar_equipe_numero_errado) - sem
  achar, o bloqueio acontece do mesmo jeito, só o aviso à equipe que fica
  sem destinatário certo.
- Item 9: reconhecimento de intenção de remarcação/cancelamento. Frases
  como "quero remarcar"/"não vou conseguir ir" (ver
  `_eh_pedido_reagendamento`, só depois de identificado e com exame em
  foco) avisam a equipe (app.push_notificacoes.
  notificar_equipe_reagendamento) - o sistema NUNCA confirma uma nova
  data por conta própria, só avisa quem vai combinar com o paciente.

Encerramento automático por inatividade (pedido do Silvan, 2026-09-12): a
conversa (`ConversaWhatsapp`, em qualquer etapa - aguardando CPF, data de
nascimento, ou já identificada) é encerrada PROATIVAMENTE - com um aviso
mandado ao paciente - depois de 5 minutos sem nenhuma mensagem nova, por
um job em segundo plano (ver app.whatsapp_encerramento, iniciado em
create_app). Diferente disso, o `expirada()` usado abaixo é passivo: só
reseta a identificação (sem avisar nada) na PRÓXIMA mensagem que chegar."""
import re
import unicodedata
from datetime import date, datetime

from app.extensions import db
from app.faq_engine import (
    buscar_resposta,
    buscar_resposta_alimento,
    buscar_resposta_medicamento,
)
from app.ia_preparo import responder_com_ia
from app.models import Agendamento, ChatMensagem, ConversaWhatsapp, Paciente, PerguntaPendente, normalizar_telefone
from app.push_notificacoes import (
    notificar_equipe_nova_pergunta,
    notificar_equipe_numero_errado,
    notificar_equipe_reagendamento,
)
from app.routes_paciente import (
    _historico_recente_chat,
    _resolver_ancora,
    aprovar_pergunta_automaticamente,
    exige_aprovacao_pergunta,
)


def normalizar_telefone_whatsapp(remetente_bruto):
    """A Meta (WhatsApp Cloud API) manda o remetente como dígitos apenas,
    com código do país, SEM o "+" na frente (ex.: "5527999998888", campo
    "from" de cada mensagem em value.messages[] - ver
    app/routes_whatsapp.py) - normaliza sempre para E.164 (com "+"), que é
    o formato usado no resto do sistema (Paciente.telefone,
    PerguntaPendente.telefone_whatsapp, ConversaWhatsapp.telefone)."""
    if not remetente_bruto:
        return None
    numero = remetente_bruto.strip()
    if not numero:
        return None
    return numero if numero.startswith("+") else f"+{numero}"


def _extrair_cpf(texto):
    """Lê um CPF de uma mensagem, aceitando só números (11 dígitos) ou com
    a máscara usual (000.000.000-00) — e mais nada além disso na mensagem,
    para não aceitar por engano um texto que só CONTÉM 11 dígitos em meio
    a outra coisa (ex.: uma data de nascimento digitada cedo demais).
    Retorna os dígitos do CPF, ou None se não reconhecer com confiança.
    Valida só o FORMATO (11 dígitos, com ou sem pontuação) - não o dígito
    verificador (ver validar_cpf em app/models.py), porque aqui é uma
    busca por um cadastro já existente, não uma validação de cadastro
    novo: um CPF de paciente já salvo (mesmo que digitado de forma
    inconsistente em algum cadastro antigo) precisa continuar sendo
    reconhecível por quem está tentando se identificar."""
    texto = (texto or "").strip()
    if not re.fullmatch(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}", texto):
        return None
    return re.sub(r"\D", "", texto)


def _extrair_data_nascimento(texto):
    """Lê uma data de nascimento em formato dd/mm/aaaa (aceita "-" no
    lugar de "/") de uma mensagem que contenha só a data. Retorna a data,
    ou None se não reconhecer ou se a data não existir de verdade (ex.:
    31/02/1990)."""
    texto = (texto or "").strip()
    data_match = re.fullmatch(r"(\d{1,2})\s*[/-]\s*(\d{1,2})\s*[/-]\s*(\d{4})", texto)
    if not data_match:
        return None
    dia, mes, ano = (int(x) for x in data_match.groups())
    try:
        return date(ano, mes, dia)
    except ValueError:
        return None


def _cpf_digitos(cpf):
    return re.sub(r"\D", "", cpf or "")


def _localizar_paciente(cpf_digitos, data_nascimento):
    """Busca o cadastro global (CPF é único desde a Fatia 5) cujo CPF e
    data de nascimento batem com o que foi informado. Comparação sempre
    pelos dígitos do CPF, porque o campo é guardado como foi digitado no
    cadastro (com ou sem pontuação) - mesmo critério já usado no login do
    paciente (ver app.routes_auth.login_paciente)."""
    for paciente in Paciente.query.filter(Paciente.cpf.isnot(None)).all():
        if _cpf_digitos(paciente.cpf) == cpf_digitos and paciente.data_nascimento == data_nascimento:
            return paciente
    return None


def _agendamentos_ativos(paciente):
    return (
        Agendamento.query.filter_by(paciente_id=paciente.id)
        .filter(Agendamento.encerrado_em.is_(None))
        .order_by(Agendamento.data_hora.desc())
        .all()
    )


def _texto_lista_exames(agendamentos, preambulo="Você tem mais de um exame em preparo. Sobre qual deles você quer falar?"):
    linhas = [
        f"{i}) {a.exame.nome} — {a.data_hora.strftime('%d/%m/%Y')}"
        for i, a in enumerate(agendamentos, start=1)
    ]
    return preambulo + "\n" + "\n".join(linhas)


def _texto_pedir_pergunta(paciente, agendamento, saudacao=True, outros_agendamentos=None):
    """Substitui o antigo menu numerado (ver docstring do módulo) - depois
    de identificado e com um exame em foco, a mensagem já convida a
    perguntar diretamente, sem precisar digitar nada antes (o gatilho
    "digite *1*", pedido em 2026-09-11 pra impedir que uma saudação solta
    como "oi" fosse tratada por engano como pergunta nova, foi removido a
    pedido do Silvan em 2026-09-14 - ver docstring do módulo sobre esse
    retrocesso deliberado e o que passa a acontecer com mensagens soltas
    agora). Quando há outro(s) exame(s) ativo(s) além do que está em
    foco, NOMEIA cada um deles aqui (em vez de só mencionar genericamente
    o comando "trocar") - correção pedida pelo Silvan (2026-09-11): antes
    disso, quando um segundo exame passava a existir DEPOIS que a
    conversa já tinha fixado o primeiro (ex.: paciente já identificado, e
    um novo agendamento é criado enquanto a sessão de WhatsApp ainda não
    expirou), o paciente ficava "logado" no exame antigo sem nenhum
    aviso claro de que havia outro - só um lembrete genérico de "trocar",
    fácil de não notar. Repetir aqui é seguro porque `outros_agendamentos`
    é sempre recalculado na hora (ver `processar_mensagem`), nunca
    guardado - qualquer novo agendamento aparece automaticamente na
    próxima mensagem, sem precisar pedir CPF/nascimento de novo."""
    cabecalho = f"Olá, {paciente.nome.split(' ')[0]}! " if saudacao else ""
    corpo = (
        f"{cabecalho}Exame em foco: *{agendamento.exame.nome}* — "
        f"{agendamento.data_hora.strftime('%d/%m/%Y')}.\n\n"
        "Pode escrever sua pergunta sobre o preparo deste exame."
    )
    if outros_agendamentos:
        nomes = "; ".join(
            f"{a.exame.nome} — {a.data_hora.strftime('%d/%m/%Y')}" for a in outros_agendamentos
        )
        corpo += f"\n\n(Você também tem agendado: {nomes}. Digite *trocar* para falar sobre outro exame.)"
    return corpo


MENSAGEM_PEDIR_CPF = (
    "Olá! Para começar, me envie seu CPF (só números ou com pontuação), "
    "assim: 000.000.000-00"
)
MENSAGEM_CPF_INVALIDO = (
    "Não reconheci um CPF. Envie só o CPF, com 11 números, com ou sem "
    "pontuação (ex.: 000.000.000-00)."
)
MENSAGEM_PEDIR_NASCIMENTO = "Certo! Agora me envie sua data de nascimento, assim: 01/01/1990"
MENSAGEM_NASCIMENTO_INVALIDA = (
    "Não reconheci a data. Envie no formato dia/mês/ano, assim: 01/01/1990"
)
MENSAGEM_NAO_ENCONTRADO = (
    "Não encontramos um cadastro com esses dados. Vamos tentar de novo — "
    "me envie seu CPF."
)
# Documento "Clara", item 7 (2026-09-14): mostrada em vez de
# MENSAGEM_NAO_ENCONTRADO quando a identificação já falhou
# `ConversaWhatsapp.LIMITE_TENTATIVAS_IDENTIFICACAO` vezes seguidas -
# ver `processar_mensagem`.
MENSAGEM_IDENTIFICACAO_BLOQUEADA = (
    "Não conseguimos confirmar seus dados depois de várias tentativas. "
    "Por segurança, vamos pausar as mensagens automáticas por aqui — "
    "entre em contato diretamente com a clínica para continuar."
)
# Documento "Clara", item 6 (2026-09-14): resposta única de confirmação
# quando o paciente avisa que é "número errado" - ver
# `_eh_numero_errado`. A partir daqui, qualquer mensagem nova recebida
# deste número recebe sempre a mesma resposta fixa (ver
# MENSAGEM_CONVERSA_BLOQUEADA), sem processar mais nada.
MENSAGEM_NUMERO_ERRADO_CONFIRMADO = (
    "Entendido! Vamos parar de enviar mensagens automáticas para este "
    "número. Avisamos a equipe da clínica para corrigir o cadastro."
)
# Documento "Clara", itens 6 e 7 (2026-09-14): resposta fixa pra qualquer
# mensagem recebida de uma conversa já bloqueada (ver
# `ConversaWhatsapp.bloqueada`) - checado antes de tudo, em
# `processar_mensagem`.
MENSAGEM_CONVERSA_BLOQUEADA = (
    "As mensagens automáticas para este número estão pausadas. Se "
    "precisar de algo, entre em contato diretamente com a clínica."
)
# Documento "Clara", item 9 (2026-09-14): resposta ao pedido de
# remarcação/cancelamento - ver `_eh_pedido_reagendamento`. O sistema
# nunca confirma uma nova data por conta própria, só avisa a equipe.
MENSAGEM_REAGENDAMENTO_AVISADO = (
    "Entendido! Avisamos a equipe da clínica sobre seu pedido de "
    "remarcação/cancelamento — em breve alguém vai entrar em contato "
    "para combinar uma nova data. Por enquanto, o exame continua "
    "agendado como está."
)
MENSAGEM_SEM_EXAME_ATIVO = (
    "Não encontramos nenhum exame em preparo no momento. Se acha que isso é "
    "um engano, entre em contato com a clínica."
)
MENSAGEM_OPCAO_INVALIDA_EXAME = "Não entendi. Responda só com o número do exame na lista abaixo:"
MENSAGEM_PERGUNTA_VAZIA = "Não recebi nenhum texto."
MENSAGEM_PERGUNTA_ENCAMINHADA = (
    "Recebemos sua pergunta! Ela foi encaminhada para a equipe e você "
    "receberá a resposta assim que possível."
)
MENSAGEM_AGUARDANDO_RESPOSTA = (
    "Sua pergunta ainda está sendo respondida pela equipe. Assim que "
    "tivermos uma resposta, você a receberá por aqui."
)


def _tem_pergunta_pendente(paciente):
    """True se o paciente tem alguma PerguntaPendente ainda sem resposta
    (status "pendente" ou "aguardando_aprovacao") - enquanto isso for
    verdade, o convite pra perguntar de novo fica escondido: a única
    coisa que faz sentido o paciente ver é o aviso de que a resposta está
    a caminho (ver pedido do Silvan - antes disso, dava a entender, por
    engano, que dava pra mandar outra pergunta ou trocar de exame
    livremente enquanto a anterior ainda não tinha resposta)."""
    return (
        PerguntaPendente.query.filter_by(paciente_id=paciente.id)
        .filter(PerguntaPendente.status != "respondida")
        .first()
        is not None
    )


def _resolver_exame_em_foco(conversa, paciente, agendamentos):
    """Decide o próximo passo depois de identificar o paciente (na
    entrada) ou depois de "trocar" (já identificado): com um só exame
    ativo, fixa ele direto e já convida a perguntar; com mais de um, pede
    pra escolher (a escolha em si é tratada por processar_mensagem, na
    próxima mensagem que chegar)."""
    if not agendamentos:
        conversa.agendamento_id = None
        return MENSAGEM_SEM_EXAME_ATIVO
    if len(agendamentos) == 1:
        conversa.agendamento_id = agendamentos[0].id
        return _texto_pedir_pergunta(paciente, agendamentos[0])
    conversa.agendamento_id = None
    return _texto_lista_exames(agendamentos)


def _responder_pergunta(paciente, agendamento, pergunta_texto, telefone):
    """Replica a lógica de app.routes_paciente.chat() (POST) para uma
    pergunta livre recebida por WhatsApp: a base de conhecimento (FAQ) é
    consultada PRIMEIRO (pedido do Silvan, 2026-09-11) - é a ÚNICA fonte
    que responde direto ao paciente sem passar pelo médico, porque já foi
    revisada e aprovada por alguém da equipe antes (ou é uma repetição
    exata de uma resposta de IA já aprovada, ver app.faq_engine.
    buscar_resposta). As respostas prontas de alimento/medicamento
    (calculadas na hora a partir do preparo cadastrado) NUNCA vão direto
    pro paciente (pedido do Silvan, 2026-09-11 - segurança do sistema:
    mesmo vindo do preparo, é uma resposta "nova" aos olhos do sistema e
    precisa de aprovação humana antes da primeira vez) - entram como
    PerguntaPendente "aguardando_aprovacao" com a resposta pronta já
    preenchida em `resposta_sugerida_ia` (mesmo campo usado pela IA),
    pronta pro médico só revisar e confirmar; depois de aprovada uma vez,
    a pergunta cai na base de FAQ e as próximas iguais/parecidas já
    respondem direto (via `faq_item` acima). Só quando nada disso bate é
    que a IA (quando configurada) é consultada - a resposta dela também
    NUNCA vai direto pro paciente, mesmo fluxo de aprovação. A IA recebe
    também o histórico recente da conversa deste paciente sobre este
    mesmo exame (`_historico_recente_chat`, pedido do Silvan, 2026-09-14 -
    "conceito de conversa") - permite entender uma pergunta de
    acompanhamento curta (ex.: "e frita?" depois de "posso comer
    batata?") em conjunto com a pergunta anterior, em vez de isolada
    (busca por FAQ/alimento/medicamento acima continua sendo feita só com
    o texto desta mensagem, sem esse histórico - só a IA recebe o
    contexto da conversa). Sempre grava
    um ChatMensagem (canal="whatsapp") no mesmo histórico que a equipe já
    vê hoje (ver medico.atendimento). Toda PerguntaPendente criada aqui
    guarda `telefone` (o remetente desta conversa) - é o que permite ao
    sistema mandar a resposta de volta pelo WhatsApp automaticamente
    assim que o médico/equipe responder (ver
    app.routes_medico.perguntas_responder). Devolve uma tupla (texto de
    resposta a mandar de volta pro paciente agora, a PerguntaPendente
    criada - ou None se já foi respondida na hora, seja pela FAQ ou pela
    aprovação automática abaixo) - o chamador usa o segundo item para
    avisar a equipe por notificação (push e/ou WhatsApp, ver
    app.push_notificacoes.notificar_equipe_nova_pergunta), só depois de
    commitar de verdade.

    Pedido do Silvan (2026-09-13): cada Grupo (ou médico/dono, numa conta
    solo sem Grupo) pode desativar a exigência de aprovação humana para
    essas respostas de alimento/medicamento/IA (ver
    Grupo.aprovacao_perguntas_paciente / Usuario.
    aprovacao_perguntas_paciente, e a tela medico.perguntas_configuracao) -
    nesse caso elas são aprovadas automaticamente
    (`aprovar_pergunta_automaticamente`, em app.routes_paciente) e vão
    direto pro paciente, sem passar pela fila do médico. O padrão (True)
    continua sendo o comportamento histórico, sem mudança nenhuma pra quem
    não tocar nesse parâmetro. A FAQ nunca passa por essa decisão - já é
    sempre direta, com ou sem esse parâmetro."""
    exame = agendamento.exame if agendamento else None
    grupo_id_ancora, criado_por_id_ancora = _resolver_ancora(paciente, exame, agendamento)
    exige_aprovacao = exige_aprovacao_pergunta(grupo_id_ancora, criado_por_id_ancora)

    resposta_final = None
    origem = None
    pergunta_pendente_criada = None

    faq_item, _score = buscar_resposta(
        pergunta_texto,
        grupo_id=grupo_id_ancora,
        exame_id=exame.id if exame else None,
        criado_por_id=criado_por_id_ancora,
    )
    resposta_alimento = buscar_resposta_alimento(pergunta_texto, exame, paciente) if not faq_item and exame else None
    resposta_medicamento = (
        buscar_resposta_medicamento(pergunta_texto, exame, paciente)
        if not faq_item and not resposta_alimento and exame else None
    )

    if faq_item:
        faq_item.vezes_utilizada += 1
        resposta_final = faq_item.resposta
        origem = "faq"
    elif resposta_alimento or resposta_medicamento:
        # Resposta pronta (alimento ou medicamento) - vira rascunho
        # aguardando aprovação do médico, igual à IA, em vez de ir direto
        # pro paciente (ver docstring desta função).
        resposta_pronta = resposta_alimento if resposta_alimento else resposta_medicamento
        # Nomes curtos de propósito: ChatMensagem.origem é String(20), e
        # "medicamento_aguardando" (22 caracteres) não caberia.
        origem = "alimento_aguard" if resposta_alimento else "medicamento_aguard"
        pergunta_pendente_criada = PerguntaPendente(
            grupo_id=grupo_id_ancora,
            criado_por_id=criado_por_id_ancora,
            paciente_id=paciente.id,
            exame_id=exame.id if exame else None,
            pergunta=pergunta_texto,
            status="aguardando_aprovacao",
            resposta_sugerida_ia=resposta_pronta,
            telefone_whatsapp=telefone,
        )
        db.session.add(pergunta_pendente_criada)
        if not exige_aprovacao:
            # Pedido do Silvan (2026-09-13): aprovação desativada para este
            # Grupo/médico (ver exige_aprovacao_pergunta) - responde direto
            # pelo WhatsApp, sem esperar o médico revisar.
            aprovar_pergunta_automaticamente(pergunta_pendente_criada, resposta_pronta)
            resposta_final = resposta_pronta
            origem = "alimento" if resposta_alimento else "medicamento"
            pergunta_pendente_criada = None
    else:
        resultado_ia = (
            responder_com_ia(
                pergunta_texto, exame, paciente_id=paciente.id,
                historico=_historico_recente_chat(paciente.id, exame.id),
            )
            if exame else None
        )
        if resultado_ia and resultado_ia["final"]:
            origem = "ia_aguardando"
            pergunta_pendente_criada = PerguntaPendente(
                grupo_id=grupo_id_ancora,
                criado_por_id=criado_por_id_ancora,
                paciente_id=paciente.id,
                exame_id=exame.id,
                pergunta=pergunta_texto,
                status="aguardando_aprovacao",
                resposta_sugerida_ia=resultado_ia["final"],
                resposta_bruta_claude=resultado_ia["por_provedor"]["Claude"],
                resposta_bruta_chatgpt=resultado_ia["por_provedor"]["ChatGPT"],
                resposta_bruta_gemini=resultado_ia["por_provedor"]["Gemini"],
                # Nomes das IAs que deram erro de chamada nesta pergunta (ver
                # app.ia_preparo.responder_com_ia) - mostrado como aviso na
                # tela de aprovação, mesmo quando a reserva "tapou o buraco"
                # e o rascunho final saiu normal (ver medico/perguntas.html).
                ias_com_erro=",".join(resultado_ia.get("falhas") or []) or None,
                telefone_whatsapp=telefone,
            )
            db.session.add(pergunta_pendente_criada)
            if not exige_aprovacao:
                # Pedido do Silvan (2026-09-13): aprovação desativada para
                # este Grupo/médico.
                aprovar_pergunta_automaticamente(pergunta_pendente_criada, resultado_ia["final"])
                resposta_final = resultado_ia["final"]
                origem = "ia"
                pergunta_pendente_criada = None
        else:
            origem = "pendente"
            pergunta_pendente_criada = PerguntaPendente(
                grupo_id=grupo_id_ancora,
                criado_por_id=criado_por_id_ancora,
                paciente_id=paciente.id,
                exame_id=exame.id if exame else None,
                pergunta=pergunta_texto,
                # Mesmo sem nenhum rascunho da IA, vale registrar se foi
                # porque alguma delas deu erro de chamada - ver
                # app.ia_preparo.responder_com_ia.
                ias_com_erro=(",".join(resultado_ia.get("falhas") or []) or None) if resultado_ia else None,
                telefone_whatsapp=telefone,
            )
            db.session.add(pergunta_pendente_criada)

    db.session.add(ChatMensagem(
        paciente_id=paciente.id,
        exame_id=exame.id if exame else None,
        agendamento_id=agendamento.id if agendamento else None,
        pergunta=pergunta_texto,
        # Igual à tela web: o histórico só grava uma resposta de verdade
        # quando já existe uma (faq/alimento/medicamento) - "ia_aguardando"
        # e "pendente" ainda não têm resposta nenhuma, só a mensagem de
        # "encaminhamos" que vai pro paciente agora.
        resposta=resposta_final,
        origem=origem,
        canal="whatsapp",
    ))

    texto_resposta = resposta_final if resposta_final else MENSAGEM_PERGUNTA_ENCAMINHADA
    return texto_resposta, pergunta_pendente_criada


def _normalizar_texto(texto):
    """Minúsculas e sem acentos, pra reconhecer frases (ver
    `_eh_numero_errado`/`_eh_pedido_reagendamento`) mesmo com variação de
    acentuação/caixa (ex.: "Número errado", "NUMERO ERRADO", "número
    érrado" por erro de digitação de acento não seriam batidos por um
    simples .lower())."""
    texto = (texto or "").strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in texto if not unicodedata.combining(c))


# Documento "Clara", item 6 (2026-09-14): frases que indicam que quem
# está respondendo não é a pessoa esperada pra esse número. Lista
# deliberadamente conservadora (frases mais específicas, não palavras
# soltas como "engano" isoladas) pra evitar bloquear por engano uma
# mensagem que só CONTÉM uma dessas palavras com outro sentido.
_FRASES_NUMERO_ERRADO = (
    "numero errado",
    "numero incorreto",
    "nao e meu numero",
    "esse numero nao e meu",
    "trocou de numero",
    "nao sou essa pessoa",
    "nao conheco essa pessoa",
    "voce esta enganado",
    "engano de numero",
)


def _eh_numero_errado(texto_normalizado):
    return any(frase in texto_normalizado for frase in _FRASES_NUMERO_ERRADO)


# Documento "Clara", item 9 (2026-09-14): frases que indicam pedido de
# remarcação/cancelamento - mesmo cuidado de usar frases específicas
# (não palavras soltas) pra reduzir falso positivo.
_FRASES_REAGENDAMENTO = (
    "quero remarcar",
    "preciso remarcar",
    "gostaria de remarcar",
    "quero reagendar",
    "preciso reagendar",
    "gostaria de reagendar",
    "nao vou conseguir ir",
    "nao poderei ir",
    "nao posso ir",
    "vou faltar",
    "preciso cancelar",
    "quero cancelar",
    "gostaria de cancelar",
)


def _eh_pedido_reagendamento(texto_normalizado):
    return any(frase in texto_normalizado for frase in _FRASES_REAGENDAMENTO)


def _paciente_por_telefone_aproximado(telefone_whatsapp):
    """Documento "Clara", item 6 (2026-09-14): acha, por aproximação,
    qual Paciente cadastrado tem esse número de WhatsApp como telefone de
    contato - usado só pro aviso de "número errado", pra saber qual
    clínica avisar quando isso acontece ANTES de qualquer identificação
    por CPF/data de nascimento (ver docstring do módulo). Compara só os
    últimos dígitos (o telefone cadastrado em Paciente pode não ter o
    código do país, diferente do formato E.164 usado aqui em
    ConversaWhatsapp.telefone) - até 9 dígitos finais, o suficiente pra
    não confundir números diferentes sem exigir bater o formato inteiro.
    Sem nenhum candidato, devolve None (o bloqueio da conversa acontece
    do mesmo jeito - só o aviso à equipe que fica sem destinatário
    certo)."""
    digitos_whatsapp = re.sub(r"\D", "", telefone_whatsapp or "")
    if len(digitos_whatsapp) < 8:
        return None
    for paciente in Paciente.query.filter(Paciente.telefone.isnot(None)).all():
        digitos_cadastro = normalizar_telefone(paciente.telefone)
        if not digitos_cadastro or len(digitos_cadastro) < 8:
            continue
        tamanho = min(len(digitos_whatsapp), len(digitos_cadastro), 9)
        if digitos_whatsapp[-tamanho:] == digitos_cadastro[-tamanho:]:
            return paciente
    return None


def processar_mensagem(telefone, corpo_mensagem):
    """Ponto de entrada único usado pelo webhook (app/routes_whatsapp.py).
    Devolve o texto da resposta a enviar de volta pelo WhatsApp."""
    conversa = ConversaWhatsapp.query.filter_by(telefone=telefone).first()

    # Documento "Clara", itens 6 e 7 (2026-09-14): conversa bloqueada
    # (número errado ou tentativas de identificação esgotadas, ver
    # ConversaWhatsapp.bloqueada) - checado ANTES de qualquer outra
    # coisa, inclusive antes de `expirada()` (o bloqueio não deve ser
    # contornado só esperando a sessão expirar).
    if conversa and conversa.bloqueada:
        return MENSAGEM_CONVERSA_BLOQUEADA

    if conversa and conversa.expirada():
        # Sessão vencida: volta a exigir CPF + data de nascimento antes de
        # continuar - o WhatsApp de quem está escrevendo pode não ser mais
        # a mesma pessoa (ver PLANO_WHATSAPP.md).
        conversa.paciente_id = None
        conversa.agendamento_id = None
        conversa.cpf_pendente = None
        conversa.aguardando_pergunta = False

    primeira_mensagem = conversa is None
    if not conversa:
        conversa = ConversaWhatsapp(telefone=telefone)
        db.session.add(conversa)

    # Toca "atualizado_em" a cada mensagem (mesmo quando nada mais muda no
    # registro) - senão o "onupdate" da coluna só dispararia se algum outro
    # campo fosse alterado, e uma conversa já identificada expiraria pela
    # data da ÚLTIMA MUDANÇA de estado, não da última mensagem trocada.
    conversa.atualizado_em = datetime.utcnow()

    # Documento "Clara", item 6 (2026-09-14): reconhecido em QUALQUER
    # etapa da conversa (mesmo antes de identificar ninguém) - ver
    # `_eh_numero_errado`/docstring do módulo.
    if _eh_numero_errado(_normalizar_texto(corpo_mensagem)):
        conversa.bloqueada = True
        conversa.motivo_bloqueio = "numero_errado"
        db.session.commit()
        paciente_aproximado = _paciente_por_telefone_aproximado(telefone)
        if paciente_aproximado:
            grupo_id_ancora, criado_por_id_ancora = _resolver_ancora(paciente_aproximado)
            notificar_equipe_numero_errado(
                grupo_id_ancora, criado_por_id_ancora, telefone, paciente_aproximado.nome
            )
        return MENSAGEM_NUMERO_ERRADO_CONFIRMADO

    # Identificação em duas mensagens separadas: primeiro só o CPF, depois
    # só a data de nascimento (mais fácil de digitar certo no WhatsApp do
    # que tudo numa mensagem só).
    if not conversa.paciente_id:
        if not conversa.cpf_pendente:
            cpf_digitos = _extrair_cpf(corpo_mensagem)
            if not cpf_digitos:
                db.session.commit()
                return MENSAGEM_PEDIR_CPF if primeira_mensagem else MENSAGEM_CPF_INVALIDO
            conversa.cpf_pendente = cpf_digitos
            db.session.commit()
            return MENSAGEM_PEDIR_NASCIMENTO

        data_nascimento = _extrair_data_nascimento(corpo_mensagem)
        if not data_nascimento:
            db.session.commit()
            return MENSAGEM_NASCIMENTO_INVALIDA

        paciente = _localizar_paciente(conversa.cpf_pendente, data_nascimento)
        conversa.cpf_pendente = None
        if not paciente:
            # Documento "Clara", item 7 (2026-09-14): conta mais uma
            # tentativa de identificação que não bateu; ao chegar no
            # limite, bloqueia a conversa em vez de convidar a tentar de
            # novo (ver ConversaWhatsapp.tentativas_identificacao/
            # LIMITE_TENTATIVAS_IDENTIFICACAO e docstring do módulo).
            conversa.tentativas_identificacao = (conversa.tentativas_identificacao or 0) + 1
            if conversa.tentativas_identificacao >= ConversaWhatsapp.LIMITE_TENTATIVAS_IDENTIFICACAO:
                conversa.bloqueada = True
                conversa.motivo_bloqueio = "tentativas_excedidas"
                db.session.commit()
                return MENSAGEM_IDENTIFICACAO_BLOQUEADA
            db.session.commit()
            return MENSAGEM_NAO_ENCONTRADO

        conversa.paciente_id = paciente.id
        conversa.tentativas_identificacao = 0
        resposta = _resolver_exame_em_foco(conversa, paciente, _agendamentos_ativos(paciente))
        db.session.commit()
        return resposta

    # Já identificado - falta só escolher qual exame (paciente com mais
    # de um agendamento ativo, seja na identificação inicial ou depois de
    # digitar "trocar").
    if not conversa.agendamento_id:
        paciente = conversa.paciente
        agendamentos = _agendamentos_ativos(paciente)
        if not agendamentos:
            db.session.commit()
            return MENSAGEM_SEM_EXAME_ATIVO

        escolha = corpo_mensagem.strip() if corpo_mensagem else ""
        indice = int(escolha) if escolha.isdigit() else None
        if not indice or not (1 <= indice <= len(agendamentos)):
            db.session.commit()
            return _texto_lista_exames(agendamentos, preambulo=MENSAGEM_OPCAO_INVALIDA_EXAME)

        agendamento_escolhido = agendamentos[indice - 1]
        conversa.agendamento_id = agendamento_escolhido.id
        db.session.commit()
        outros = [a for a in agendamentos if a.id != agendamento_escolhido.id]
        return _texto_pedir_pergunta(paciente, agendamento_escolhido, outros_agendamentos=outros)

    # Identificado e com exame em foco: qualquer texto já é tratado
    # direto como a pergunta em si (pedido do Silvan, 2026-09-14 - ver
    # docstring do módulo; o gatilho "digite *1* antes" que existia aqui
    # foi removido). Quem tem mais de um exame ativo pode digitar
    # "trocar" para escolher outro, em qualquer momento.
    paciente, agendamento = conversa.paciente, conversa.agendamento
    texto = (corpo_mensagem or "").strip()

    # Enquanto houver uma pergunta pendente sem resposta da equipe, a
    # única coisa que faz sentido o paciente ver é o aviso de que a
    # resposta está a caminho - não dá a entender que dá pra perguntar de
    # novo ou trocar de exame livremente enquanto isso.
    if _tem_pergunta_pendente(paciente):
        db.session.commit()
        return MENSAGEM_AGUARDANDO_RESPOSTA

    agendamentos_ativos = _agendamentos_ativos(paciente)
    tem_mais_de_um_exame = len(agendamentos_ativos) > 1
    outros_agendamentos = [a for a in agendamentos_ativos if a.id != agendamento.id] if tem_mais_de_um_exame else None

    # Documento "Clara", item 9 (2026-09-14): pedido de remarcação/
    # cancelamento - só avisa a equipe (nunca confirma uma nova data por
    # conta própria, ver _eh_pedido_reagendamento/docstring do módulo).
    # Checado antes do gatilho "trocar"/"1" - não precisa ter digitado
    # "1" antes pra isso valer, é uma intenção diferente de uma pergunta
    # sobre o preparo.
    if _eh_pedido_reagendamento(_normalizar_texto(texto)):
        grupo_id_ancora, criado_por_id_ancora = _resolver_ancora(
            paciente, agendamento.exame if agendamento else None, agendamento
        )
        notificar_equipe_reagendamento(
            grupo_id_ancora, criado_por_id_ancora, paciente, agendamento, telefone
        )
        db.session.commit()
        return MENSAGEM_REAGENDAMENTO_AVISADO

    if tem_mais_de_um_exame and texto.lower() == "trocar":
        resposta = _resolver_exame_em_foco(conversa, paciente, agendamentos_ativos)
        db.session.commit()
        return resposta

    if not texto:
        db.session.commit()
        return MENSAGEM_PERGUNTA_VAZIA

    # Qualquer outro texto (que não seja "trocar", nem uma intenção de
    # remarcação já tratada acima) é a pergunta em si - direto, sem
    # precisar digitar "1" antes (ver docstring do módulo).
    resposta_pergunta, pergunta_criada = _responder_pergunta(paciente, agendamento, texto, telefone)
    complemento = (
        MENSAGEM_AGUARDANDO_RESPOSTA
        if _tem_pergunta_pendente(paciente)
        else _texto_pedir_pergunta(paciente, agendamento, saudacao=False, outros_agendamentos=outros_agendamentos)
    )
    resposta = resposta_pergunta + "\n\n" + complemento
    db.session.commit()
    if pergunta_criada:
        # Só depois do commit acima - a notificação da equipe (push e/ou
        # WhatsApp, ver push_notificacoes) é melhor esforço, não deve
        # atrapalhar a resposta ao paciente se falhar.
        notificar_equipe_nova_pergunta(pergunta_criada)
    return resposta
