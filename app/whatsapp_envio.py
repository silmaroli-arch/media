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
  app.routes_auth.cadastro_paciente_global) - COM uma variável (o nome
  do paciente). É a PRIMEIRA mensagem que a clínica manda a essa pessoa,
  então está sempre fora da janela de 24h - sem este template
  configurado, o envio é só pulado (nada quebra, mesmo padrão de
  "falha aberta" do resto deste módulo); o Silvan precisa criar e
  aprovar este template na Meta separadamente do de resposta (são dois
  templates diferentes, cada mensagem iniciada pela clínica precisa do
  seu próprio).
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


def enviar_boas_vindas_whatsapp(paciente):
    """Pedido do Silvan (2026-09-06): mandar uma mensagem de WhatsApp para
    o paciente assim que ele é cadastrado, para que ele já tenha o número
    da clínica salvo e saiba que pode mandar dúvidas por lá (ver
    app.whatsapp_conversa para o fluxo de identificação por CPF + data de
    nascimento que essa mensagem inaugura).

    Chamada logo depois do cadastro em app.routes_medico.pacientes_novo
    (cadastro feito pela equipe) e app.routes_auth.cadastro_paciente_global
    (autocadastro do próprio paciente) - e também em
    app.routes_medico._paciente_teste_do_medico, para o médico poder
    testar esse mesmo fluxo no próprio WhatsApp (ver medico.testar_ia).

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
        content_variables=[paciente.nome],
        nome_template_env="WHATSAPP_META_TEMPLATE_BOAS_VINDAS",
    )
