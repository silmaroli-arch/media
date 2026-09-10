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
  o nome da pessoa, {{2}} um aviso extra que só o MÉDICO recebe no
  próprio cadastro (pedido do Silvan, 2026-09-10 - avisando que ele
  precisa cadastrar um modelo de preparo antes de testar); para um
  paciente de verdade, {{2}} chega vazio. Se este template já estava
  aprovado com só UMA variável (versão anterior a 2026-09-10), precisa
  ser editado/reaprovado na Meta para aceitar a segunda. É a PRIMEIRA
  mensagem que a clínica manda a essa pessoa, então está sempre fora da
  janela de 24h - sem este template configurado, o envio é só pulado
  (nada quebra, mesmo padrão de "falha aberta" do resto deste módulo).
- WHATSAPP_META_TEMPLATE_MEDICO_PREPARO_CADASTRADO (opcional, pedido do
  Silvan, 2026-09-10): nome do template aprovado usado para avisar o
  médico, no próprio WhatsApp, que um modelo de preparo foi cadastrado e
  ele já pode testar a IA (ver enviar_preparo_cadastrado_whatsapp mais
  abaixo, chamada em app.routes_medico.preparo_modelos_novo) - COM uma
  variável (o nome do médico). Também quase sempre fora da janela de
  24h, e também opcional (sem ele, o envio é só pulado).
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

from flask import current_app

GRAPH_API_BASE = "https://graph.facebook.com"


def _numero_para_graph_api(telefone_e164):
    """A Graph API espera o número como dígitos (código do país + DDD +
    número), sem "+", espaço, parêntese ou traço - `telefone_e164` chega
    aqui no formato usado no resto do sistema (ex.: "+5527999998888")."""
    return "".join(c for c in telefone_e164 if c.isdigit())


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
    fora da janela de 24h precisa do seu próprio template aprovado."""
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
                    "parameters": [{"type": "text", "text": str(v)} for v in content_variables],
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

    `aviso_extra` (pedido do Silvan, 2026-09-10): texto adicional que
    aparece só para o MÉDICO no momento do próprio cadastro (avisando que
    ele precisa cadastrar um modelo de preparo antes de poder testar) -
    fica em BRANCO (só um espaço, ver abaixo) para o paciente real, que
    não deveria ver esse aviso. É a 2ª variável do MESMO template
    WHATSAPP_META_TEMPLATE_BOAS_VINDAS (em vez de um template separado) -
    o template, ao ser (re)aprovado na Meta, precisa ter duas variáveis no
    corpo, cada uma com texto fixo antes/depois (a Meta recusa variável
    colada no início ou no fim do corpo): {{1}} o nome, {{2}} este aviso
    extra. Corpo aprovado (2026-09-10): "Olá {{1}}, tudo bem? Este é o
    WhatsApp da clínica - salve este número para tirar dúvidas sobre o
    preparo dos seus exames. {{2}} Qualquer coisa, estamos por aqui!".

    Importante: a Graph API rejeita variável de template como string
    vazia - por isso, quando não há aviso extra (paciente real), manda um
    espaço (" ") em vez de "" para {{2}}; como o corpo já tem texto fixo
    logo antes e logo depois dessa variável, um espaço a mais passa
    despercebido no resultado final.

    É sempre a PRIMEIRA mensagem trocada com esse número - nunca há uma
    janela de 24h aberta ainda -, então SEMPRE precisa do template
    aprovado (WHATSAPP_META_TEMPLATE_BOAS_VINDAS, ver docstring do
    módulo); sem ele configurado, o envio é só pulado (mesmo padrão de
    "falha aberta" do resto deste módulo) - o cadastro em si nunca falha
    por causa disso. `texto` aqui é só o rótulo do parâmetro obrigatório
    de enviar_mensagem_whatsapp; nunca chega a ser usado de fato, porque
    texto livre fora da janela de 24h a Meta sempre recusa."""
    return enviar_mensagem_whatsapp(
        paciente.telefone,
        texto=f"Olá, {paciente.nome}! Este é o WhatsApp da clínica — salve este número para tirar dúvidas sobre o preparo dos seus exames.",
        content_variables=[paciente.nome, aviso_extra.strip() or " "],
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
    nunca falha por causa disso."""
    return enviar_mensagem_whatsapp(
        medico.telefone,
        texto=(
            f"Boa notícia, {medico.nome}! Seu modelo de preparo foi cadastrado. "
            "Agora você já pode testar o assistente de IA fazendo uma pergunta de teste "
            'em "Testar IA nos meus preparos".'
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
