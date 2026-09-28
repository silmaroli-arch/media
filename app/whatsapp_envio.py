"""Fatia 7 (migração): envio PROATIVO de mensagem pelo WhatsApp usando a
API direta da Meta (WhatsApp Cloud API), sem intermediário — substitui a
integração anterior via Twilio (ver PLANO_WHATSAPP.md, seção "Migração
para Meta Cloud API direta", para o histórico da decisão). Usado hoje só
para mandar de volta, automaticamente, a resposta de uma pergunta que o
paciente fez por esse canal (ver app.routes_medico.perguntas_responder),
assim que o médico/equipe aprovar/responder, e também para a resposta
imediata dentro do próprio webhook (ver app/routes_whatsapp.py) - é o
único lugar do projeto que fala com a Graph API para ENVIAR mensagem
(app/routes_whatsapp.py só RECEBE).

Por que precisa de um "template" fora da janela de 24h: a Meta só permite
texto livre (`type: "text"`) quando a mensagem está dentro da janela de
24h da ÚLTIMA mensagem que o paciente mandou - fora dela (o caso mais
comum aqui, já que a equipe pode demorar horas para responder), a API
recusa (erro 131047, "Re-engagement message") a menos que a mensagem use
um "template" (HSM) pré-aprovado pela Meta (WhatsApp Manager > Modelos de
mensagem). O template usado aqui tem duas variáveis no corpo: {{1}} a
pergunta original, {{2}} a resposta.

Configuração necessária (variáveis de ambiente — nunca em código nem no
repositório, ver .env.example):
- WHATSAPP_META_ACCESS_TOKEN: token de acesso permanente de um System
  User da Meta Business (WhatsApp Manager > Configuração da API > gerar
  token) - precisa da permissão "whatsapp_business_messaging".
- WHATSAPP_META_PHONE_NUMBER_ID: o ID do número de telefone da aplicação
  no WhatsApp Cloud API (não é o número em si, é o identificador interno
  que aparece em WhatsApp Manager > Números de telefone).
- WHATSAPP_META_TEMPLATE_RESPOSTA: o nome do template aprovado (ex.:
  "resposta_duvida_paciente") usado para mandar a resposta de uma
  pergunta - COM duas variáveis (pergunta e resposta, nessa ordem). Sem
  essa variável, tenta mandar como texto livre - funciona só dentro da
  janela de 24h da última mensagem do paciente; fora dela, a Meta recusa
  e o envio é só registrado como falha (a resposta continua disponível
  na área web).
- WHATSAPP_META_TEMPLATE_BOAS_VINDAS (opcional): nome do template
  aprovado usado para mandar a mensagem de boas-vindas quando um
  paciente é cadastrado (ver enviar_boas_vindas_whatsapp mais abaixo,
  chamada em app.routes_medico.pacientes_novo e
  app.routes_auth.cadastro_paciente_global) - COM DUAS variáveis: {{1}}
  o nome da pessoa, {{2}} um trecho que varia conforme quem recebe - o
  MÉDICO, no próprio cadastro, recebe um trecho explicando o fluxo de
  teste (cadastrar um modelo de preparo, importar PDF, criar um
  agendamento para o paciente de teste - pedido do Silvan, 2026-09-11);
  um paciente de verdade recebe um trecho mais simples (salvar o número
  para tirar dúvidas sobre o preparo). Corpo aprovado (2026-09-11, ver
  docstring de enviar_boas_vindas_whatsapp para o texto completo): "Olá
  {{1}}, tudo bem? Este é o WhatsApp da MedIA — {{2}} Qualquer coisa,
  estamos por aqui!". Se este template já estava aprovado com um corpo
  diferente (ex.: "Este é o WhatsApp da CLÍNICA...", versão anterior a
  2026-09-11), precisa ser editado/reaprovado na Meta com o novo corpo.
  É a PRIMEIRA mensagem que a clínica manda a essa pessoa, então está
  sempre fora da janela de 24h - sem este template configurado, o envio
  é só pulado (nada quebra, mesmo padrão de "falha aberta" do resto
  deste módulo).
- WHATSAPP_META_TEMPLATE_MEDICO_PREPARO_CADASTRADO (opcional, pedido do
  Silvan, 2026-09-10): nome do template aprovado usado para avisar o
  médico, no próprio WhatsApp, que um modelo de preparo foi cadastrado
  (ver enviar_preparo_cadastrado_whatsapp mais abaixo, chamada em
  app.routes_medico.preparo_modelos_novo) - COM uma variável (o nome do
  médico). Corpo aprovado (2026-09-11, reescrito a pedido do Silvan -
  antes orientava a "fazer uma pergunta de teste", agora orienta a
  "cadastrar um agendamento"): "Boa notícia, {{1}}! Seu modelo de
  preparo foi cadastrado com sucesso.\\nAgora você já pode continuar os
  seus testes: cadastre um agendamento para o paciente de teste (criado
  com o seu nome).". Também quase sempre fora da janela de 24h, e também
  opcional (sem ele, o envio é só pulado).
- WHATSAPP_META_TEMPLATE_AGENDAMENTO_CRIADO (opcional, pedido do Silvan,
  2026-09-10): nome do template aprovado usado para avisar o PACIENTE, no
  próprio WhatsApp, que um agendamento foi criado para ele (ver
  enviar_agendamento_criado_whatsapp mais abaixo, chamada em
  app.routes_medico.agenda_novo, só na criação inicial do agendamento -
  não em reagendamentos/edições) - COM TRÊS variáveis: {{1}} o nome do
  paciente, {{2}} o nome do exame, {{3}} a data/hora formatada
  (dd/mm/aaaa às HH:MM). A mensagem também avisa que aquele número é o
  canal para tirar dúvidas sobre o preparo. Quase sempre fora da janela
  de 24h (o paciente raramente acabou de mandar mensagem), e opcional:
  sem esse template configurado, o envio é só pulado (mesmo padrão de
  "falha aberta" do resto deste módulo) - o agendamento em si nunca falha
  por causa disso.
- WHATSAPP_META_TEMPLATE_IDIOMA (opcional, padrão "pt_BR"): o código de
  idioma cadastrado junto com o template na aprovação.
- WHATSAPP_META_API_VERSION (opcional, padrão "v22.0"): versão da Graph
  API usada nas chamadas - a Meta desativa versões antigas depois de um
  tempo, então pode ser preciso atualizar este valor eventualmente sem
  mexer em código.

Sem WHATSAPP_META_ACCESS_TOKEN/WHATSAPP_META_PHONE_NUMBER_ID configuradas,
o envio é apenas PULADO (a resposta continua salva normalmente no
sistema, só não sai pelo WhatsApp) - o paciente pode sempre ver a
resposta acessando a área web, então a ausência de configuração aqui
nunca impede o fluxo de responder perguntas."""
import os
import re

from flask import current_app

GRAPH_API_BASE = "https://graph.facebook.com"


def _numero_para_graph_api(telefone_e164):
    """A Graph API espera o número como dígitos (código do país + DDD +
    número), sem "+", espaço, parêntese ou traço - `telefone_e164` chega
    aqui no formato usado no resto do sistema (ex.: "+5527999998888")."""
    return "".join(c for c in telefone_e164 if c.isdigit())


def _sanitizar_variavel_template(valor):
    """Corrige (2026-09-12) o texto de uma variável {{n}} de template Meta
    para o formato que a Graph API aceita - ela recusa (erro #132018) uma
    variável com quebra de linha/tab ou 4+ espaços seguidos ("Param text
    cannot have new-line/tab characters or more than 4 consecutive
    spaces"). Descoberto porque a resposta livre do médico a uma pergunta
    do paciente (routes_medico.py:perguntas_responder) e o aviso extra do
    cadastro do médico (routes_auth.py:cadastro) frequentemente têm
    quebra de linha - sem esta função, o ENVIO INTEIRO era recusado pela
    Meta e a falha só ia para o log (ver enviar_mensagem_whatsapp), então
    o paciente/médico simplesmente nunca recebia a mensagem.

    Troca quebra de linha/tab por espaço e reduz qualquer sequência de
    espaços a um só - preserva o conteúdo (nada é cortado), só deixa de
    quebrar em várias linhas."""
    return re.sub(r"\s+", " ", str(valor)).strip()


def enviar_mensagem_whatsapp(telefone_destino, texto, content_variables=None, nome_template_env="WHATSAPP_META_TEMPLATE_RESPOSTA"):
    """Manda `texto` para `telefone_destino` (formato "+5527999998888")
    usando a WhatsApp Cloud API da Meta diretamente. Nunca levanta exceção
    para quem chama - qualquer falha (configuração ausente, erro de rede,
    número inválido, fora da janela de 24h sem template etc.) só é
    registrada no log, porque isso roda depois que a ação principal
    (responder a pergunta, cadastrar o paciente) já foi salva com
    sucesso: uma falha de envio não pode desfazer nem bloquear essa
    gravação, já que a informação continua acessível pela área web de
    qualquer forma.

    `content_variables`, se informado, é uma lista de strings usada para
    preencher as variáveis {{1}}, {{2}}... do template configurado na
    variável de ambiente indicada por `nome_template_env` (padrão
    WHATSAPP_META_TEMPLATE_RESPOSTA, ver docstring do módulo) - `texto`
    só é usado de fato se não houver template configurado (mensagem de
    texto livre, dentro da janela de 24h). `nome_template_env` existe
    para outros envios PROATIVOS (ex.: boas-vindas no cadastro, ver
    enviar_boas_vindas_whatsapp) usarem um template Meta DIFERENTE do de
    resposta de pergunta - cada tipo de mensagem iniciada pela clínica
    fora da janela de 24h precisa do seu próprio template aprovado.

    Correção (2026-09-12): cada item de `content_variables` passa por
    `_sanitizar_variavel_template` antes de ir para a Graph API - a Meta
    recusa (erro #132018) qualquer variável de template com quebra de
    linha/tab ou 4+ espaços seguidos, e vários chamadores passam texto
    livre digitado por alguém (ex.: a resposta do médico a uma pergunta
    do paciente, em routes_medico.py:perguntas_responder, ou o aviso
    extra do cadastro do médico, em routes_auth.py:cadastro) que pode
    perfeitamente ter isso - sem essa sanitização, o envio falhava por
    completo (e em silêncio, ver tratamento de falha abaixo) por causa
    de um caractere que quem chama nem sabia ser proibido."""
    access_token = os.environ.get("WHATSAPP_META_ACCESS_TOKEN")
    phone_number_id = os.environ.get("WHATSAPP_META_PHONE_NUMBER_ID")
    template_nome = os.environ.get(nome_template_env)
    template_idioma = os.environ.get("WHATSAPP_META_TEMPLATE_IDIOMA", "pt_BR")
    api_version = os.environ.get("WHATSAPP_META_API_VERSION", "v22.0")

    if not (access_token and phone_number_id):
        current_app.logger.info(
            "Envio de WhatsApp pulado (WHATSAPP_META_ACCESS_TOKEN/"
            "WHATSAPP_META_PHONE_NUMBER_ID não configurados) - a informação "
            "continua disponível na área web."
        )
        return False

    if not telefone_destino:
        current_app.logger.warning("Envio de WhatsApp pulado: sem telefone de destino.")
        return False

    numero_destino = _numero_para_graph_api(telefone_destino)

    if template_nome and content_variables:
        # Fora da janela de 24h, a Meta EXIGE um template aprovado (ver
        # docstring do módulo) - o texto livre (`texto`) é ignorado nesse
        # caso, o conteúdo mostrado ao paciente vem do template.
        payload = {
            "messaging_product": "whatsapp",
            "to": numero_destino,
            "type": "template",
            "template": {
                "name": template_nome,
                "language": {"code": template_idioma},
                "components": [{
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": _sanitizar_variavel_template(v)}
                        for v in content_variables
                    ],
                }],
            },
        }
    else:
        # Sem template configurado: só funciona dentro da janela de 24h
        # da última mensagem do paciente (ver docstring) - fora dela a
        # Meta recusa (erro 131047) e cai no tratamento de falha abaixo.
        payload = {
            "messaging_product": "whatsapp",
            "to": numero_destino,
            "type": "text",
            "text": {"body": texto},
        }

    try:
        import requests

        resposta_http = requests.post(
            f"{GRAPH_API_BASE}/{api_version}/{phone_number_id}/messages",
            json=payload,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if resposta_http.status_code >= 400:
            current_app.logger.warning(
                "Falha ao enviar mensagem de WhatsApp para %s: HTTP %s - %s",
                telefone_destino, resposta_http.status_code, resposta_http.text,
            )
            return False
        return True
    except Exception:
        # Falha de rede/API/número inválido etc. - não propaga (ver
        # docstring), só registra para investigação.
        current_app.logger.exception("Falha ao enviar mensagem de WhatsApp para %s", telefone_destino)
        return False


_AVISO_PADRAO_PACIENTE = "Salve este número para tirar dúvidas sobre o preparo dos seus exames."


def enviar_boas_vindas_whatsapp(paciente, aviso_extra=""):
    """Pedido do Silvan (2026-09-06): mandar uma mensagem de WhatsApp para
    o paciente assim que ele é cadastrado, para que ele já tenha o número
    da clínica salvo e saiba que pode mandar dúvidas por lá (ver
    app.whatsapp_conversa para o fluxo de identificação por CPF + data de
    nascimento que essa mensagem inaugura).

    Chamada logo depois do cadastro em app.routes_medico.pacientes_novo
    (cadastro feito pela equipe) e app.routes_auth.cadastro_paciente_global
    (autocadastro do próprio paciente) - e também em
    app.routes_medico._paciente_teste_do_medico, para o médico poder
    testar esse mesmo fluxo no próprio WhatsApp (ver medico.testar_ia), e
    diretamente em auth.cadastro (pedido do Silvan, 2026-09-10: mandar
    essa mensagem já no CADASTRO do médico, não só quando ele abre
    "Testar IA" pela primeira vez).

    `aviso_extra` é a 2ª variável do MESMO template
    WHATSAPP_META_TEMPLATE_BOAS_VINDAS (em vez de um template separado
    por audiência) - o template, ao ser (re)aprovado na Meta, precisa ter
    duas variáveis no corpo, cada uma com texto fixo antes/depois (a Meta
    recusa variável colada no início ou no fim do corpo): {{1}} o nome,
    {{2}} este trecho (pode ter várias linhas/parágrafos - a Meta aceita
    quebra de linha dentro do corpo do template). Corpo aprovado
    (2026-09-11, reescrito a pedido do Silvan - antes falava
    genericamente "da clínica"):
    "Olá, {{1}}! Tudo bem?\\nEste é o WhatsApp da MedIA — {{2}}\\n\\nQualquer dúvida, estamos por aqui!".

    Quem chama decide o conteúdo de `aviso_extra` conforme a audiência
    (pedido do Silvan, 2026-09-11 - antes disso só o médico tinha um
    trecho extra, e o paciente real recebia {{2}} em branco):
    - **Médico, no próprio cadastro** (ver app.routes_auth.cadastro):
      passa um trecho mais elaborado (com lista numerada) explicando o
      fluxo de teste (cadastrar modelo de preparo, importar PDF, criar
      agendamento para o paciente de teste) - ver a chamada em
      app.routes_auth.cadastro para o texto exato.
    - **Paciente real** (chamadores que não passam `aviso_extra`): cai no
      trecho padrão abaixo (`_AVISO_PADRAO_PACIENTE`), sobre salvar o
      número para tirar dúvidas sobre o preparo - antes disso ({{2}} em
      branco) o corpo aprovado tinha esse texto FIXO em vez de variável,
      então o comportamento visto pelo paciente não muda.

    Importante: a Graph API rejeita variável de template como string
    vazia - por isso, mesmo com `aviso_extra` vazio, sempre cai no
    trecho padrão do paciente em vez de mandar um espaço em branco.

    É sempre a PRIMEIRA mensagem trocada com esse número - nunca há uma
    janela de 24h aberta ainda -, então SEMPRE precisa do template
    aprovado (WHATSAPP_META_TEMPLATE_BOAS_VINDAS, ver docstring do
    módulo); sem ele configurado, o envio é só pulado (mesmo padrão de
    "falha aberta" do resto deste módulo) - o cadastro em si nunca falha
    por causa disso. `texto` aqui é só o rótulo do parâmetro obrigatório
    de enviar_mensagem_whatsapp; nunca chega a ser usado de fato, porque
    texto livre fora da janela de 24h a Meta sempre recusa."""
    aviso = aviso_extra.strip() if aviso_extra else _AVISO_PADRAO_PACIENTE
    return enviar_mensagem_whatsapp(
        paciente.telefone,
        texto=(
            f"Olá, {paciente.nome}! Tudo bem?\n"
            f"Este é o WhatsApp da MedIA — {aviso}\n\n"
            "Qualquer dúvida, estamos por aqui!"
        ),
        content_variables=[paciente.nome, aviso],
        nome_template_env="WHATSAPP_META_TEMPLATE_BOAS_VINDAS",
    )


def enviar_preparo_cadastrado_whatsapp(medico):
    """Pedido do Silvan (2026-09-10): avisar o médico, no PRÓPRIO
    WhatsApp, assim que ele cadastra um modelo de preparo - "agora você já
    pode testar fazendo uma pergunta" (ver medico.testar_ia). Chamada em
    app.routes_medico.preparo_modelos_novo, uma vez a cada preparo
    cadastrado (não só no primeiro - decisão do Silvan).

    Vai para Usuario.telefone (o telefone do próprio médico, informado no
    cadastro - mesmo número usado pelo paciente de teste dele, ver
    routes_medico._paciente_teste_do_medico) - nunca para um paciente de
    verdade, então não precisa (nem deve) da variável de aviso extra que
    enviar_boas_vindas_whatsapp usa.

    Mensagem iniciada pela clínica, quase sempre fora da janela de 24h (o
    médico raramente vai ter mandado mensagem havia pouco) - por isso usa
    seu PRÓPRIO template (WHATSAPP_META_TEMPLATE_MEDICO_PREPARO_CADASTRADO,
    com uma variável: o nome do médico), separado do de boas-vindas. Sem
    esse template configurado, o envio é só pulado (mesmo padrão de
    "falha aberta" do resto deste módulo) - o cadastro do preparo em si
    nunca falha por causa disso.

    Texto reescrito a pedido do Silvan (2026-09-11 - a orientação de
    próximo passo mudou de "faça uma pergunta de teste" para "cadastre um
    agendamento", já que agora é isso que falta para o fluxo de teste
    ficar completo, ver enviar_boas_vindas_whatsapp). Corpo aprovado:
    "Boa notícia, {{1}}! Seu modelo de preparo foi cadastrado com
    sucesso.\\nAgora você já pode continuar os seus testes: cadastre um
    agendamento para o paciente de teste (criado com o seu nome)."."""
    return enviar_mensagem_whatsapp(
        medico.telefone,
        texto=(
            f"Boa notícia, {medico.nome}! Seu modelo de preparo foi cadastrado com sucesso.\n"
            "Agora você já pode continuar os seus testes: cadastre um agendamento para o "
            "paciente de teste (criado com o seu nome)."
        ),
        content_variables=[medico.nome],
        nome_template_env="WHATSAPP_META_TEMPLATE_MEDICO_PREPARO_CADASTRADO",
    )


def enviar_agendamento_criado_whatsapp(agendamento):
    """Pedido do Silvan (2026-09-10): avisar o PACIENTE, no próprio
    WhatsApp, assim que um agendamento é criado para ele - a mensagem traz
    a data/hora do exame e reforça que aquele número é o canal certo para
    tirar dúvidas sobre o preparo. Chamada em
    app.routes_medico.agenda_novo, uma única vez, logo após o commit da
    CRIAÇÃO do agendamento (decisão do Silvan: reagendamentos/edições não
    reenviam este aviso - só a criação inicial dispara).

    Importante: NÃO deve ser chamada para o agendamento sintético que
    app.routes_medico._garantir_agendamento_teste cria/atualiza para a
    tela "Testar IA nos meus preparos" - aquele não é um agendamento real
    experimentado por um paciente de verdade, então não deve gerar este
    aviso.

    `agendamento` é a instância recém-criada de Agendamento, já com
    `paciente` e `exame` carregáveis via relacionamento (ver Agendamento
    em app/models.py) - usamos paciente.telefone como destino.

    Mensagem iniciada pela clínica, quase sempre fora da janela de 24h -
    por isso usa seu PRÓPRIO template
    (WHATSAPP_META_TEMPLATE_AGENDAMENTO_CRIADO, com três variáveis: nome
    do paciente, nome do exame, data/hora formatada), separado dos demais.
    Sem esse template configurado, o envio é só pulado (mesmo padrão de
    "falha aberta" do resto deste módulo) - a criação do agendamento em si
    nunca falha por causa disso."""
    paciente = agendamento.paciente
    exame = agendamento.exame
    data_hora_formatada = agendamento.data_hora.strftime("%d/%m/%Y às %H:%M")
    return enviar_mensagem_whatsapp(
        paciente.telefone,
        texto=(
            f"Olá, {paciente.nome}! Seu exame {exame.nome} foi agendado para "
            f"{data_hora_formatada}. Salve este número: é por aqui que você tira "
            "dúvidas sobre o preparo do exame."
        ),
        content_variables=[paciente.nome, exame.nome, data_hora_formatada],
        nome_template_env="WHATSAPP_META_TEMPLATE_AGENDAMENTO_CRIADO",
    )
