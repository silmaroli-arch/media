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

  **Mitigação parcial adicionada (pedido do Silvan, 2026-09-24)**: com
  print de outro caso real - mandou ":(&;" por engano de digitação, e isso
  virou uma PerguntaPendente encaminhada pra equipe, sem fazer o menor
  sentido como pergunta. `_eh_mensagem_sem_sentido_minimo` (ver abaixo)
  filtra esse tipo de mensagem (símbolo/emoji solto, número colado,
  pontuação repetida) ANTES de virar pergunta - devolve
  `MENSAGEM_MENSAGEM_SEM_SENTIDO` pedindo pra reescrever, sem criar
  `PerguntaPendente`/`ChatMensagem` nem notificar ninguém. No mesmo dia,
  o Silvan perguntou se um teclado travado/preso (ex.: "eeeeeeeeeeee")
  também seria pego - a resposta era não (são só letras, sem símbolo
  nenhum), então a checagem ganhou mais uma condição: uma letra sozinha
  não pode responder por quase todas as letras da mensagem (ver
  `_PROPORCAO_MAXIMA_UMA_SO_LETRA`) - isso também passou a cobrir
  sequências de uma letra só repetida em geral (ex.: "kkkk"/"aaaaa"
  isolados, sem mais nenhuma letra na mensagem, também passam a ser
  tratados como sem sentido, não só o teclado travado). Importante: é só
  uma barreira de "isso nem é texto" - NÃO resolve o tradeoff aceito no
  parágrafo acima (uma saudação de verdade como "oi" tem "cara de texto"
  E variedade de letras suficiente pra passar por essa checagem, e
  continua virando pergunta encaminhada pra equipe, do mesmo jeito).
- Passo 5 (este arquivo): a pergunta livre reaproveita a MESMA lógica de
  app.routes_paciente.chat() (base de conhecimento/alimento/medicamento
  primeiro - pedido do Silvan, 2026-09-11; só quando nada bate a IA é
  consultada, com a resposta ficando pendente de aprovação do médico; sem
  IA, ou sem resposta dela, encaminhada pra equipe) - importa o helper
  `_resolver_ancora` de lá em vez de duplicar a regra de roteamento pra
  Grupo/dono pessoal.

  **Perguntas independentes com uma já pendente (pedido do Silvan,
  2026-09-24)**: existia um bloqueio (`_tem_pergunta_pendente`, checado no
  início de `processar_mensagem`) que impedia mandar QUALQUER mensagem
  nova - inclusive uma pergunta totalmente diferente, sobre outro assunto
  - enquanto uma pergunta anterior ainda não tinha resposta da equipe; a
  única coisa que o paciente via era o aviso de "sua pergunta ainda está
  sendo respondida", repetido pra cada mensagem nova. Removido: agora uma
  pergunta nova, mesmo com outra ainda pendente, cria sua PRÓPRIA
  `PerguntaPendente` independente (mesmo comportamento que o chat pela
  área web, `app.routes_paciente.chat`, já tinha - nunca teve esse
  bloqueio). `_tem_pergunta_pendente` continua existindo, mas só pra
  decidir qual mensagem de complemento mostrar depois de responder
  (`MENSAGEM_AGUARDANDO_RESPOSTA` em vez do convite de sempre, quando
  ainda sobra alguma pendência) - não bloqueia mais nada. "Trocar de
  exame" e o reconhecimento de intenção de remarcação/número errado
  (documento "Clara") também deixam de ficar bloqueados por uma pergunta
  pendente, como efeito colateral direto de remover esse bloqueio (eram
  checados depois dele).

  **Julgamento de "isso faz sentido?" pela própria IA (pedido do Silvan,
  2026-09-24)**: `_eh_mensagem_sem_sentido_minimo` (checagem por regras
  fixas, acima) só pega os casos óbvios - símbolo/emoji solto, teclado
  travado etc. - e não pega, por exemplo, palavras reais numa ordem sem
  sentido nenhum. Perguntado se dava pra usar de fato uma IA pra julgar
  isso em vez de só regras fixas, o Silvan escolheu a opção híbrida: a
  checagem por regras fixas continua sendo a primeira barreira (grátis,
  instantânea); quando o texto passa por ela mas ainda vai pra IA (porque
  não bateu com FAQ/alimento/medicamento, ver `_responder_pergunta`), a
  MESMA chamada que já ia ser feita pra tentar responder também serve pra
  julgar sentido - ver `app.ia_preparo.responder_com_ia` (chave
  "sem_sentido" do retorno) e `MARCADOR_SEM_SENTIDO` lá. Sem custo extra
  de chamada, mas só cobre esse julgamento quando pelo menos uma IA de
  chat está configurada (sem nenhuma, continua dependendo só da checagem
  por regras fixas). Quando a IA sinaliza sem sentido, `_responder_pergunta`
  devolve o MESMO aviso `MENSAGEM_MENSAGEM_SEM_SENTIDO` de sempre, sem
  criar `PerguntaPendente`/`ChatMensagem` - do ponto de vista do paciente
  e da equipe, é indistinguível de ter sido pego pela checagem por regras
  fixas, só que pega casos mais sutis.

  **Conversa social não é mais tratada como pergunta (pedido do Silvan,
  2026-09-24)**: até aqui, uma saudação de verdade como "oi" TINHA "cara
  de texto" o suficiente pra passar pela checagem de sem sentido acima -
  esse era exatamente o tradeoff aceito quando o gatilho "1" foi removido
  (ver parágrafo "Gatilho '1' removido de novo", mais acima: "uma
  saudação de verdade como 'oi' continua virando pergunta encaminhada à
  equipe"). Agora não mais: `_eh_apenas_conversa_social` reconhece quando
  a mensagem inteira é só saudação ("oi"/"olá"/"bom dia"/"boa tarde"/"boa
  noite"), despedida ("tchau") ou agradecimento ("obrigado"/"obrigada") -
  nesse caso responde com uma mensagem simpática de volta (variando por
  categoria, ver `_resposta_conversa_social`), sem criar
  `PerguntaPendente`/`ChatMensagem` nem notificar a equipe, do mesmo jeito
  que as checagens de sem sentido acima. Deliberadamente conservador: só
  entra em ação quando a mensagem é SÓ isso - "Oi, posso comer batata?"
  continua sendo tratada como pergunta normalmente, a saudação no início
  não desvia o fluxo.

  **Duas ou mais palavras desconhecidas pelo dicionário (pedido do
  Silvan, 2026-09-24)**: "se pelo menos duas palavras não existirem na
  frase, dê erro". `_eh_mensagem_sem_sentido_minimo` (acima) só olha
  proporção/repetição de caracteres, nunca se a palavra existe de
  verdade em português - uma mensagem como "asdkjf qwerty lorem" tem
  letras variadas o suficiente pra passar por ela. `_eh_mensagem_com_
  muitas_palavras_desconhecidas` usa o pacote `pyspellchecker` (ver
  requirements.txt) - que já vem com o dicionário de português embutido
  no próprio pacote, sem precisar instalar nada no sistema (inviável
  neste projeto - nem o ambiente de desenvolvimento nem a máquina do
  Silvan conseguem instalar `hunspell`/`enchant` via rede) - pra marcar
  como sem sentido quando 2+ palavras (de 3+ letras) não são reconhecidas
  pelo dicionário. Escrito pra nunca travar o chat enquanto o pacote não
  estiver instalado de verdade (só entra em vigor depois do próximo
  deploy no Render, que instala normalmente via `pip`): sem o pacote
  disponível, a checagem é um no-op silencioso, e a mensagem segue pro
  resto do fluxo de sempre.

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

Encerramento automático por inatividade (pedido do Silvan, 2026-09-12) -
REMOVIDO de novo a pedido dele em 2026-09-24 (ver HANDOFF_CHAT.md): existiu
um job em segundo plano (app.whatsapp_encerramento, hoje excluído) que
encerrava a conversa PROATIVAMENTE - com um aviso mandado ao paciente -
depois de 5 minutos sem nenhuma mensagem nova, em qualquer etapa
(aguardando CPF, data de nascimento, ou já identificada). Esse job não
existe mais. O único comportamento que resta é o `expirada()` usado
abaixo, que é passivo: só reseta a identificação (sem avisar nada, sem
apagar o registro) na PRÓXIMA mensagem que chegar, depois de
`ConversaWhatsapp.MINUTOS_EXPIRACAO` (4h) sem nenhuma mensagem nova."""
import re
import unicodedata
from collections import Counter
from datetime import date, datetime

from flask import current_app

from app.extensions import db
from app.faq_engine import (
    buscar_resposta,
    buscar_resposta_alimento,
    buscar_resposta_medicamento,
)
from app.ia_preparo import responder_com_ia, validar_pergunta
from app.models import (
    Agendamento, ChatMensagem, ContagemPerguntasDia, ConversaWhatsapp, Paciente,
    PerguntaPendente, PlataformaConfig, normalizar_telefone,
)
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
MENSAGEM_MENSAGEM_SEM_SENTIDO = (
    "Não consegui entender essa mensagem. Pode escrever sua pergunta sobre "
    "o preparo deste exame?"
)
MENSAGEM_PERGUNTA_ENCAMINHADA = (
    "Recebemos sua pergunta! Ela foi encaminhada para a equipe e você "
    "receberá a resposta assim que possível."
)
MENSAGEM_AGUARDANDO_RESPOSTA = (
    "Sua pergunta ainda está sendo respondida pela equipe. Assim que "
    "tivermos uma resposta, você a receberá por aqui."
)
# Pedido do Silvan (2026-09-24) - ver ContagemPerguntasDia/
# PlataformaConfig.limite_perguntas_dia_exame e
# _excedeu_limite_perguntas_dia abaixo.
MENSAGEM_LIMITE_PERGUNTAS_DIA = (
    "Você já atingiu o limite de mensagens de hoje sobre este exame. Pode "
    "escrever novamente a partir de amanhã. Se for urgente, entre em "
    "contato diretamente com a secretaria da clínica."
)
# Pedido do Silvan (2026-09-24) - ver app.ia_preparo.validar_pergunta
# ("fora_do_exame") e docstring do módulo dela ("Validador de pergunta
# dedicado"). Mesmo tratamento de MENSAGEM_MENSAGEM_SEM_SENTIDO (não cria
# PerguntaPendente nem ChatMensagem), só com um texto que orienta melhor
# quem escreveu sobre outro assunto sem relação com exame nenhum.
MENSAGEM_PERGUNTA_FORA_DO_EXAME = (
    "Este chat é só para dúvidas sobre o preparo do seu exame. Pode "
    "escrever sua pergunta sobre o preparo?"
)


def _tem_pergunta_pendente(paciente):
    """True se o paciente tem alguma PerguntaPendente ainda sem resposta
    (status "pendente" ou "aguardando_aprovacao").

    Usada só pra decidir a MENSAGEM de complemento depois de responder a
    uma pergunta nova (ver `processar_mensagem`) - mostra
    `MENSAGEM_AGUARDANDO_RESPOSTA` em vez do convite de sempre quando
    ainda sobra alguma pendência (a que acabou de ser criada, ou uma
    anterior). NÃO bloqueia mais mandar uma pergunta nova enquanto outra
    ainda está pendente (removido a pedido do Silvan, 2026-09-24 - ver
    docstring do módulo: "Perguntas independentes com uma já pendente").
    Cada pergunta vira sua própria `PerguntaPendente`, resolvida
    independentemente pelo médico - não há limite de quantas podem ficar
    pendentes ao mesmo tempo."""
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
    app.routes_medico.perguntas_responder). Devolve uma TRIPLA (texto de
    resposta a mandar de volta pro paciente agora, a PerguntaPendente
    criada - ou None se já foi respondida na hora, seja pela FAQ ou pela
    aprovação automática abaixo -, eh_sem_sentido) - o chamador usa o
    segundo item para avisar a equipe por notificação (push e/ou
    WhatsApp, ver app.push_notificacoes.notificar_equipe_nova_pergunta),
    só depois de commitar de verdade. O terceiro item (pedido do Silvan,
    2026-09-24, ver app.ia_preparo.responder_com_ia) é True quando a IA -
    não a checagem por regras fixas, feita antes desta função ser
    chamada, ver `_eh_mensagem_sem_sentido_minimo` - julgou que o texto
    do paciente nem chega a ser uma pergunta/comentário coerente; nesse
    caso NÃO cria PerguntaPendente nem ChatMensagem (mesmo comportamento
    da checagem por regras fixas), e quem chamou não deve colar o
    convite de "pode escrever sua próxima pergunta" na resposta.

    Validador de pergunta dedicado (pedido do Silvan, 2026-09-24 - ver
    app.ia_preparo.validar_pergunta): roda logo no INÍCIO desta função,
    antes até da FAQ. Reaproveita o mesmo terceiro item da tripla (True)
    tanto para "sem sentido" quanto para "sem relação com exame nenhum" -
    as duas encerram aqui, sem consultar nada mais, com a mensagem certa
    para cada caso (ver MENSAGEM_MENSAGEM_SEM_SENTIDO/MENSAGEM_PERGUNTA_
    FORA_DO_EXAME em app.whatsapp_conversa). Independente do sinal
    "sem_sentido" que `responder_com_ia` ainda pode emitir mais abaixo
    (segunda camada de segurança, sem custo extra - ver docstring dela) -
    as duas checagens convivem, só que o validador dedicado roda primeiro
    e evita a chamada de resposta por completo quando já rejeita.

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

    # Validador de pergunta dedicado (pedido do Silvan, 2026-09-24 - ver
    # app.ia_preparo.validar_pergunta e docstring do módulo dela): roda
    # ANTES de qualquer outra coisa (até antes da FAQ) - se a IA escolhida
    # pelo dono (PlataformaConfig.ia_validador_pergunta) classificar a
    # mensagem como sem sentido ou sem relação com exame nenhum, encerra
    # aqui, sem consultar FAQ/alimento/medicamento/IA de resposta, e sem
    # criar PerguntaPendente nem ChatMensagem (mesmo tratamento das outras
    # checagens de "isso nem é uma pergunta de verdade" já existentes em
    # app.whatsapp_conversa.processar_mensagem). Sem exame em foco, ou
    # sem IA validadora disponível, `classificacao` vem None e o fluxo
    # segue normalmente, sem nenhuma mudança de comportamento.
    resultado_validacao = validar_pergunta(
        pergunta_texto, exame, paciente_id=paciente.id,
        historico=_historico_recente_chat(paciente.id, exame.id) if exame else None,
    )
    if resultado_validacao["classificacao"] == "sem_sentido":
        return MENSAGEM_MENSAGEM_SEM_SENTIDO, None, True
    if resultado_validacao["classificacao"] == "fora_do_exame":
        return MENSAGEM_PERGUNTA_FORA_DO_EXAME, None, True

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
        if resultado_ia and resultado_ia.get("sem_sentido"):
            # Ver docstring desta função e de app.ia_preparo.
            # responder_com_ia ("sem_sentido") - a IA julgou que o texto
            # nem chega a ser uma pergunta/comentário coerente. Mesmo
            # tratamento da checagem por regras fixas em
            # `_eh_mensagem_sem_sentido_minimo`: devolve o mesmo aviso
            # pedindo pra reescrever, sem criar PerguntaPendente nem
            # ChatMensagem - não faz sentido registrar isso no histórico
            # nem na fila da equipe.
            return MENSAGEM_MENSAGEM_SEM_SENTIDO, None, True
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
    return texto_resposta, pergunta_pendente_criada, False


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


# Validação mínima de "isso parece um texto de verdade" (pedido do Silvan,
# 2026-09-24, com print de um caso real: mandou ":(&;" por engano de
# digitação - sem nenhuma barreira, ver docstring do módulo sobre a
# remoção do gatilho "1" - isso virou uma PerguntaPendente encaminhada pra
# equipe, sem fazer o menor sentido como pergunta). NÃO é uma correção
# ortográfica nem um julgamento de "faz sentido de verdade em português" -
# só filtra os casos mais óbvios: texto que não tem quase nenhuma letra
# (símbolos, emoji solto, número colado, pontuação repetida) OU texto que
# É só letras mas sem nenhuma variedade (uma tecla travada/presa, tipo
# "eeeeeeeeeeee" - pergunta do Silvan, 2026-09-24, depois desta correção:
# "e se o paciente digitar algo tipo eeeeeeeeeeee de um teclado preso?" -
# a versão anterior desta função aceitava esse caso, porque são só
# letras, sem nenhum símbolo). Erros de digitação/ortografia normais
# dentro de palavras de verdade continuam passando direto (ex.: "Posso
# comer batata frita?" com qualquer erro de digitação comum não é
# afetado) - a barreira é só contra "isso nem é texto".
_RE_LETRA = re.compile(r"[a-z]")
_RE_PALAVRA_MINIMA = re.compile(r"[a-z]{2,}")

# Proporção mínima de letras no texto (sem espaços) para considerar que
# "parece" um texto de verdade, e não uma sequência de símbolos/números
# com uma letra ou duas perdidas no meio.
_PROPORCAO_MINIMA_LETRAS = 0.5

# Teclado travado/preso: quando UMA letra sozinha responde por boa parte
# das letras da mensagem (ex.: "eeeeeeeeeeee", ou até "eeeeeaa" - maioria
# "e", só uma "sujeira" de outra tecla no meio) - só vale a partir de um
# mínimo de letras (`_MINIMO_LETRAS_PARA_CHECAR_REPETICAO`) pra não pegar
# à toa uma palavra curta de verdade com letra repetida (ex.: "certo",
# "carro" têm letra repetida, mas nenhuma delas domina a palavra).
_PROPORCAO_MAXIMA_UMA_SO_LETRA = 0.6
_MINIMO_LETRAS_PARA_CHECAR_REPETICAO = 4


def _eh_mensagem_sem_sentido_minimo(texto_normalizado):
    """True quando o texto não tem o mínimo de "cara de texto" pra ser
    tratado como uma pergunta de verdade (ver comentário acima) - exige
    pelo menos uma sequência de 2+ letras (uma "palavra", nem que seja
    "oi"/"ok"), que letras sejam pelo menos a metade dos caracteres (sem
    espaço) da mensagem, E que essas letras não sejam quase todas a
    MESMA letra repetida (teclado travado - ex.: "eeeeeeeeeeee"; isso
    também passa a cobrir uma sequência de UMA letra só repetida, tipo
    "kkkk"/"aaaaa" - sem outra letra na mensagem pra dar algum contexto,
    trata do mesmo jeito, pedindo pra reescrever). `texto_normalizado` já
    deve vir de `_normalizar_texto` (minúsculas, sem acento)."""
    sem_espaco = texto_normalizado.replace(" ", "")
    if not sem_espaco:
        return False  # mensagem vazia é tratada à parte (MENSAGEM_PERGUNTA_VAZIA)
    if not _RE_PALAVRA_MINIMA.search(texto_normalizado):
        return True
    letras = _RE_LETRA.findall(texto_normalizado)
    proporcao_letras = len(letras) / len(sem_espaco)
    if proporcao_letras < _PROPORCAO_MINIMA_LETRAS:
        return True
    contagem_por_letra = Counter(letras)
    if len(contagem_por_letra) < 2:
        return True  # uma única letra em toda a mensagem (ex.: "eeee", "kkkk")
    _letra_mais_comum, vezes_mais_comum = contagem_por_letra.most_common(1)[0]
    if (
        len(letras) >= _MINIMO_LETRAS_PARA_CHECAR_REPETICAO
        and vezes_mais_comum / len(letras) >= _PROPORCAO_MAXIMA_UMA_SO_LETRA
    ):
        return True  # uma letra domina quase todas as outras (teclado travado)
    return False


# Palavras/expressões de conversa social (pedido do Silvan, 2026-09-24:
# "obrigado, oi, tchau, bom dia, boa tarde, boa noite, olá devem ser
# apenas consideradas como conversa e não pergunta") - o problema que isso
# resolve é diferente do de `_eh_mensagem_sem_sentido_minimo` acima: uma
# saudação como "oi" TEM cara de texto de verdade (palavra real, letras
# variadas) e por isso passa direto pela checagem acima - o tradeoff
# aceito em 2026-09-14 (ver docstring do módulo) era justamente que "oi"
# continuava virando pergunta encaminhada pra equipe. Esta checagem nova
# resolve esse tradeoff especificamente pras palavras/expressões sociais
# mais comuns, sem reabrir o problema original (símbolo solto etc. -
# esse continua sendo pego pela checagem de sem sentido, que roda antes).
# Cada item é comparado à mensagem NORMALIZADA (ver `_normalizar_texto`)
# inteira (ignorando pontuação/espaços nas pontas) - de propósito
# conservador: só entra em ação quando a mensagem é SÓ a saudação/
# despedida/agradecimento (com ou sem combinações entre elas, ex.: "Oi,
# bom dia!" ou "Obrigado, tchau!"), nunca quando vem junto de uma
# pergunta de verdade (ex.: "Oi, posso comer batata?" continua indo
# direto pra `_responder_pergunta`, como uma pergunta - a saudação no
# início não desvia o fluxo).
_PALAVRAS_CONVERSA_SOCIAL = {
    "oi", "ola",
    "tchau",
    "obrigado", "obrigada",
    "bom", "boa", "dia", "tarde", "noite",
}
_RE_PALAVRA_CONVERSA_SOCIAL = re.compile(r"[a-z]+")

MENSAGEM_SAUDACAO_SOCIAL = "Oi! Se tiver alguma dúvida sobre o preparo deste exame, pode escrever aqui."
MENSAGEM_DESPEDIDA_SOCIAL = (
    "Tchau! Se surgir alguma dúvida sobre o preparo deste exame, pode "
    "voltar a escrever por aqui a qualquer momento."
)
MENSAGEM_AGRADECIMENTO_SOCIAL = "Por nada! Se tiver mais alguma dúvida sobre o preparo deste exame, pode escrever aqui."


def _eh_apenas_conversa_social(texto_normalizado):
    """True quando a mensagem inteira é composta só de saudação/despedida/
    agradecimento (ver `_PALAVRAS_CONVERSA_SOCIAL` acima) - nenhuma outra
    palavra sobrando. `texto_normalizado` já deve vir de `_normalizar_texto`
    (minúsculas, sem acento) - por isso "olá" é comparado como "ola"."""
    palavras = _RE_PALAVRA_CONVERSA_SOCIAL.findall(texto_normalizado)
    if not palavras:
        return False
    return all(palavra in _PALAVRAS_CONVERSA_SOCIAL for palavra in palavras)


def _resposta_conversa_social(texto_normalizado):
    """Escolhe a resposta certa dentre saudação/despedida/agradecimento -
    só chamar depois de confirmar `_eh_apenas_conversa_social`. Quando a
    mensagem combina mais de uma categoria (ex.: "Obrigado, tchau!"),
    despedida tem prioridade sobre agradecimento, que tem prioridade sobre
    saudação - a última coisa dita costuma ser a mais relevante pra
    resposta, e "tchau"/"obrigado" são despedidas mais definitivas do que
    uma saudação solta."""
    palavras = set(_RE_PALAVRA_CONVERSA_SOCIAL.findall(texto_normalizado))
    if "tchau" in palavras:
        return MENSAGEM_DESPEDIDA_SOCIAL
    if "obrigado" in palavras or "obrigada" in palavras:
        return MENSAGEM_AGRADECIMENTO_SOCIAL
    return MENSAGEM_SAUDACAO_SOCIAL


# Checagem por dicionário de português (pedido do Silvan, 2026-09-24: "se
# pelo menos duas palavras não existirem na frase, dê erro") - diferente
# de `_eh_mensagem_sem_sentido_minimo` acima (que só olha proporção/
# repetição de caracteres, nunca se a palavra existe de verdade), esta
# usa o pacote `pyspellchecker` (ver requirements.txt) - que já vem com o
# dicionário de português embutido nos próprios dados do pacote, sem
# precisar instalar nada no sistema (hunspell/enchant) - inviável neste
# projeto porque tanto o ambiente de nuvem usado nas sessões de
# desenvolvimento quanto a máquina do Silvan bloqueiam instalação via
# `apt-get`/`pip` fora do deploy normal do Render (ver HANDOFF_CHAT.md).
#
# Como esse pacote é uma dependência NOVA que só entra de verdade depois
# do próximo deploy no Render (`pip install -r requirements.txt` roda lá
# com internet normal), a função abaixo é escrita pra nunca quebrar o
# chat enquanto isso: se o import falhar (pacote ainda não instalado,
# ou qualquer outro erro ao carregar o dicionário), simplesmente não
# entra em ação - mesmo espírito de "nunca trava o chat do paciente" já
# usado pra IA (ver app/ia_preparo.py). `_verificador_ortografico_pt` é
# construído uma única vez por processo (é um dicionário carregado na
# memória, não uma chamada de rede) e reaproveitado nas próximas
# mensagens.
_verificador_ortografico_pt = None
_verificador_ortografico_indisponivel = False

# Só entra em ação com pelo menos duas palavras "checáveis" na mensagem
# (pedido literal do Silvan: "pelo menos duas") - uma mensagem com uma só
# palavra desconhecida (ex.: um nome próprio, uma marca de medicamento
# que a IA já sabe reconhecer, ver app.ia_preparo) não é motivo suficiente
# pra marcar como sem sentido.
_MINIMO_PALAVRAS_DESCONHECIDAS_SEM_SENTIDO = 2
# Palavras curtas (1-2 letras) são ignoradas nesta checagem específica -
# abreviações comuns de paciente (ex.: "vc", "pq", "tb", "oi", "ok") não
# costumam estar num dicionário formal e não devem ser penalizadas aqui;
# a checagem por regras fixas (`_eh_mensagem_sem_sentido_minimo`) e a
# conversa social (acima) já cobrem a maior parte do que interessa nesse
# tamanho.
_TAMANHO_MINIMO_PALAVRA_DICIONARIO = 3
_RE_PALAVRA_DICIONARIO = re.compile(r"[a-zà-ÿ]+", re.IGNORECASE)


def _obter_verificador_ortografico():
    """Constrói (uma única vez por processo) e devolve o SpellChecker de
    português, ou None se o pacote `pyspellchecker` não estiver
    disponível/instalado ainda, ou se algo der errado ao carregar o
    dicionário - nesse caso a checagem que usa esta função vira um no-op
    silencioso (ver docstring acima)."""
    global _verificador_ortografico_pt, _verificador_ortografico_indisponivel
    if _verificador_ortografico_indisponivel:
        return None
    if _verificador_ortografico_pt is not None:
        return _verificador_ortografico_pt
    try:
        from spellchecker import SpellChecker
        _verificador_ortografico_pt = SpellChecker(language="pt")
    except Exception:
        _verificador_ortografico_indisponivel = True
        current_app.logger.warning(
            "pyspellchecker indisponível (pacote não instalado ou erro ao carregar o "
            "dicionário de português) - a checagem de 'duas ou mais palavras "
            "desconhecidas' fica desligada até o próximo deploy que já inclua a "
            "dependência (ver requirements.txt)."
        )
        return None
    return _verificador_ortografico_pt


def _eh_mensagem_com_muitas_palavras_desconhecidas(texto_original):
    """True quando pelo menos `_MINIMO_PALAVRAS_DESCONHECIDAS_SEM_SENTIDO`
    palavras (de 3+ letras) da mensagem não são reconhecidas pelo
    dicionário de português (ver `_obter_verificador_ortografico`) -
    devolve False sem nenhum efeito quando o dicionário não está
    disponível (ver acima). Recebe o texto ORIGINAL da mensagem (não o
    `_normalizar_texto`, que tira os acentos) - o dicionário reconhece
    palavras acentuadas normalmente ("não", "é", "está"), e removê-los
    faria muita palavra de verdade parecer desconhecida por engano."""
    verificador = _obter_verificador_ortografico()
    if not verificador:
        return False
    palavras = [
        palavra for palavra in _RE_PALAVRA_DICIONARIO.findall((texto_original or "").lower())
        if len(palavra) >= _TAMANHO_MINIMO_PALAVRA_DICIONARIO
    ]
    if len(palavras) < _MINIMO_PALAVRAS_DESCONHECIDAS_SEM_SENTIDO:
        return False
    desconhecidas = verificador.unknown(palavras)
    return len(desconhecidas) >= _MINIMO_PALAVRAS_DESCONHECIDAS_SEM_SENTIDO


def _excedeu_limite_perguntas_dia(paciente, exame):
    """True quando o paciente já atingiu, HOJE, o limite diário de
    mensagens configurado pelo dono para este exame específico (pedido
    do Silvan, 2026-09-24 - ver PlataformaConfig.limite_perguntas_dia_
    exame e ContagemPerguntasDia em app.models). Sem limite configurado
    (None ou <= 0, o padrão) ou sem exame em foco, sempre False -
    comportamento idêntico a antes dessa funcionalidade existir, sem
    consultar a tabela à toa."""
    limite = PlataformaConfig.obter().limite_perguntas_dia_exame
    if not limite or not exame:
        return False
    contagem = ContagemPerguntasDia.query.filter_by(
        paciente_id=paciente.id, exame_id=exame.id, data=date.today(),
    ).first()
    return bool(contagem and contagem.quantidade >= limite)


def _registrar_mensagem_do_dia(paciente, exame):
    """Incrementa o contador do dia usado por `_excedeu_limite_perguntas_
    dia` (ver docstring dela) - só deve ser chamada DEPOIS de confirmar
    que o limite ainda não foi atingido (ver processar_mensagem), pra não
    incrementar sem parar depois que a conversa já está bloqueada pelo
    limite. Sem limite configurado, não faz nada - evita criar uma linha
    à toa quando essa funcionalidade nem está em uso."""
    limite = PlataformaConfig.obter().limite_perguntas_dia_exame
    if not limite or not exame:
        return
    hoje = date.today()
    contagem = ContagemPerguntasDia.query.filter_by(
        paciente_id=paciente.id, exame_id=exame.id, data=hoje,
    ).first()
    if not contagem:
        contagem = ContagemPerguntasDia(paciente_id=paciente.id, exame_id=exame.id, data=hoje, quantidade=0)
        db.session.add(contagem)
    contagem.quantidade += 1


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

    # Limite diário de mensagens por paciente x exame (pedido do Silvan,
    # 2026-09-24 - ver PlataformaConfig.limite_perguntas_dia_exame,
    # ContagemPerguntasDia e _excedeu_limite_perguntas_dia acima).
    # Checado ANTES de qualquer outra validação de conteúdo - conta TODA
    # mensagem que chega até aqui, mesmo sem sentido ou conversa social
    # (pedido explícito do Silvan), por isso incrementa incondicionalmente
    # depois de confirmar que ainda não excedeu. Sem limite configurado
    # (padrão), esta checagem não tem efeito nenhum.
    if _excedeu_limite_perguntas_dia(paciente, agendamento.exame if agendamento else None):
        db.session.commit()
        return MENSAGEM_LIMITE_PERGUNTAS_DIA
    _registrar_mensagem_do_dia(paciente, agendamento.exame if agendamento else None)

    # Validação mínima de "isso parece um texto de verdade" (pedido do
    # Silvan, 2026-09-24 - ver _eh_mensagem_sem_sentido_minimo acima):
    # símbolo/emoji solto, número colado, pontuação repetida etc. não
    # chega a virar PerguntaPendente - só pede pra reescrever. Erro de
    # digitação/ortografia normal dentro de palavras de verdade não é
    # afetado por isso.
    texto_normalizado = _normalizar_texto(texto)
    if _eh_mensagem_sem_sentido_minimo(texto_normalizado):
        db.session.commit()
        return MENSAGEM_MENSAGEM_SEM_SENTIDO

    # Duas ou mais palavras desconhecidas pelo dicionário de português
    # (pedido do Silvan, 2026-09-24 - ver `_eh_mensagem_com_muitas_
    # palavras_desconhecidas` acima) - pega o caso que a checagem por
    # regras fixas acima NÃO pega: palavras inventadas/digitação
    # aleatória que ainda têm "cara de texto" (letras variadas, sem
    # repetição excessiva). Vira um no-op silencioso enquanto o pacote
    # `pyspellchecker` não estiver instalado (ver requirements.txt e
    # HANDOFF_CHAT.md - só passa a valer depois do próximo deploy).
    # Usa o texto ORIGINAL (com acento), não o normalizado.
    if _eh_mensagem_com_muitas_palavras_desconhecidas(texto):
        db.session.commit()
        return MENSAGEM_MENSAGEM_SEM_SENTIDO

    # Conversa social (saudação/despedida/agradecimento - pedido do
    # Silvan, 2026-09-24: ver `_eh_apenas_conversa_social` acima) - só
    # entra em ação quando a mensagem é SÓ isso, sem nenhuma pergunta de
    # verdade junto. Mesmo tratamento das outras checagens acima: não
    # cria PerguntaPendente nem ChatMensagem, não notifica a equipe - só
    # responde com uma mensagem simpática e convida a perguntar.
    if _eh_apenas_conversa_social(texto_normalizado):
        db.session.commit()
        return _resposta_conversa_social(texto_normalizado)

    # Qualquer outro texto (que não seja "trocar", nem uma intenção de
    # remarcação já tratada acima, nem sem sentido nenhum, nem conversa
    # social) é a pergunta em si - direto, sem precisar digitar "1" antes
    # (ver docstring do módulo).
    resposta_pergunta, pergunta_criada, eh_sem_sentido = _responder_pergunta(paciente, agendamento, texto, telefone)
    if eh_sem_sentido:
        # Julgamento pela IA (pedido do Silvan, 2026-09-24 - ver docstring
        # do módulo e de `_responder_pergunta`): mesmo tratamento da
        # checagem por regras fixas, alguns parágrafos acima - devolve só
        # o aviso, sem colar o convite de "pode escrever sua próxima
        # pergunta" (não faz sentido convidar a reescrever E já convidar
        # a perguntar de novo na mesma resposta).
        db.session.commit()
        return resposta_pergunta
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
