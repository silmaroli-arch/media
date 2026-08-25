"""Fatia 7 (área de WhatsApp) — passos 3, 4 e 5 do plano:
- Passo 3: identificação do paciente por CPF + data de nascimento, com
  sessão de conversa que expira por inatividade (ver `ConversaWhatsapp`
  em app/models.py).
- Passo 4: uma vez identificado (e com um exame em foco escolhido), o
  menu de opções - "1) Ver informações do preparo" reaproveita
  app.faq_engine.texto_preparo_whatsapp (mesmos dados/cálculo de prazo
  que a tela paciente/preparo.html já usa) e "3) Trocar de exame"
  reaproveita a mesma lógica de seleção da identificação inicial.
- Passo 5 (este arquivo): "2) Fazer uma pergunta" reaproveita a MESMA
  lógica de app.routes_paciente.chat() (IA primeiro, com a resposta
  ficando pendente de aprovação do médico; sem IA, base de conhecimento/
  alimento/medicamento; sem nada disso, encaminhada pra equipe) - importa
  o helper `_resolver_ancora` de lá em vez de duplicar a regra de
  roteamento pra Grupo/dono pessoal.

Este módulo é só a LÓGICA de conversa (recebe telefone + texto da
mensagem, devolve o texto da resposta) — não sabe nada sobre Twilio nem
sobre HTTP, para poder ser testado sem precisar simular um webhook (ver
app/routes_whatsapp.py, que é a única coisa que fala com o provedor)."""
import re
from datetime import date, datetime

from app.extensions import db
from app.faq_engine import (
    buscar_resposta,
    buscar_resposta_alimento,
    buscar_resposta_medicamento,
    texto_preparo_whatsapp,
)
from app.ia_preparo import responder_com_ia
from app.models import Agendamento, ChatMensagem, ConversaWhatsapp, Paciente, PerguntaPendente
from app.push_notificacoes import notificar_equipe_nova_pergunta
from app.routes_paciente import _resolver_ancora


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


MENU_OPCOES_SEM_TROCAR = (
    "1) Ver informações do preparo\n"
    "2) Fazer uma pergunta"
)
MENU_OPCOES_COM_TROCAR = (
    "1) Ver informações do preparo\n"
    "2) Fazer uma pergunta\n"
    "3) Trocar de exame"
)


def _menu_opcoes(paciente):
    """"3) Trocar de exame" só faz sentido - e só aparece - quando o
    paciente tem mais de um exame ativo no momento; com um só, o menu fica
    só com as duas opções relevantes (a opção de trocar confundia quem só
    tinha um exame)."""
    tem_mais_de_um_exame = len(_agendamentos_ativos(paciente)) > 1
    return MENU_OPCOES_COM_TROCAR if tem_mais_de_um_exame else MENU_OPCOES_SEM_TROCAR


def _texto_menu(paciente, agendamento, saudacao=True):
    cabecalho = f"Olá, {paciente.nome.split(' ')[0]}! " if saudacao else ""
    return (
        f"{cabecalho}Exame em foco: *{agendamento.exame.nome}* — "
        f"{agendamento.data_hora.strftime('%d/%m/%Y')}.\n\n"
        f"{_menu_opcoes(paciente)}"
    )


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
MENSAGEM_SEM_EXAME_ATIVO = (
    "Não encontramos nenhum exame em preparo no momento. Se acha que isso é "
    "um engano, entre em contato com a clínica."
)
MENSAGEM_OPCAO_INVALIDA_EXAME = "Não entendi. Responda só com o número do exame na lista abaixo:"


def _mensagem_opcao_invalida_menu(paciente):
    return f"Não entendi. Escolha uma das opções abaixo:\n\n{_menu_opcoes(paciente)}"


MENSAGEM_PEDIR_PERGUNTA = (
    "Pode digitar sua pergunta sobre o preparo deste exame. "
    "(Ou responda 0 para cancelar e voltar ao menu.)"
)
MENSAGEM_PERGUNTA_VAZIA = "Não recebi nenhum texto. Digite sua pergunta, ou responda 0 para cancelar."
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
    verdade, o menu de opções fica escondido: a única coisa que faz
    sentido o paciente ver é o aviso de que a resposta está a caminho (ver
    pedido do Silvan - antes disso, o menu completo reaparecia mesmo com
    uma pergunta ainda pendente, o que dava a entender, por engano, que
    dava pra mandar outra pergunta ou trocar de exame livremente)."""
    return (
        PerguntaPendente.query.filter_by(paciente_id=paciente.id)
        .filter(PerguntaPendente.status != "respondida")
        .first()
        is not None
    )


def _resolver_exame_em_foco(conversa, paciente, agendamentos):
    """Decide o próximo passo depois de identificar o paciente (na
    entrada) ou depois de "3) Trocar de exame" (já identificado): com um
    só exame ativo, fixa ele direto e mostra o menu; com mais de um, pede
    pra escolher (a escolha em si é tratada por processar_mensagem, na
    próxima mensagem que chegar)."""
    if not agendamentos:
        conversa.agendamento_id = None
        return MENSAGEM_SEM_EXAME_ATIVO
    if len(agendamentos) == 1:
        conversa.agendamento_id = agendamentos[0].id
        return _texto_menu(paciente, agendamentos[0])
    conversa.agendamento_id = None
    return _texto_lista_exames(agendamentos)


def _responder_pergunta(paciente, agendamento, pergunta_texto, telefone):
    """Replica a lógica de app.routes_paciente.chat() (POST) para uma
    pergunta livre recebida por WhatsApp: a IA (quando configurada) é
    SEMPRE consultada primeiro, mas a resposta dela NUNCA vai direto pro
    paciente - fica como PerguntaPendente "aguardando_aprovacao" até o
    médico revisar; sem IA (ou sem resposta da IA), tenta a base de
    conhecimento (FAQ) e as respostas prontas de alimento/medicamento;
    sem nada disso, encaminha como pergunta pendente pra equipe responder
    manualmente. Sempre grava um ChatMensagem (canal="whatsapp") no mesmo
    histórico que a equipe já vê hoje (ver medico.atendimento). Toda
    PerguntaPendente criada aqui guarda `telefone` (o remetente desta
    conversa) - é o que permite ao sistema mandar a resposta de volta
    pelo WhatsApp automaticamente assim que o médico/equipe responder
    (ver app.routes_medico.perguntas_responder). Devolve uma tupla
    (texto de resposta a mandar de volta pro paciente agora, a
    PerguntaPendente criada - ou None se já foi respondida na hora por
    FAQ/alimento/medicamento) - o chamador usa o segundo item para
    avisar a equipe por notificação push (ver
    app.push_notificacoes.notificar_equipe_nova_pergunta), só depois de
    commitar de verdade."""
    exame = agendamento.exame if agendamento else None
    grupo_id_ancora, criado_por_id_ancora = _resolver_ancora(paciente, exame, agendamento)

    resultado_ia = responder_com_ia(pergunta_texto, exame, paciente_id=paciente.id) if exame else None
    resposta_final = None
    origem = None
    pergunta_pendente_criada = None

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
    else:
        faq_item, _score = buscar_resposta(
            pergunta_texto,
            grupo_id=grupo_id_ancora,
            exame_id=exame.id if exame else None,
            criado_por_id=criado_por_id_ancora,
        )
        if faq_item:
            faq_item.vezes_utilizada += 1
            resposta_final = faq_item.resposta
            origem = "faq"
        elif exame and (resposta_alimento := buscar_resposta_alimento(pergunta_texto, exame, paciente)):
            resposta_final, origem = resposta_alimento, "alimento"
        elif exame and (resposta_medicamento := buscar_resposta_medicamento(pergunta_texto, exame, paciente)):
            resposta_final, origem = resposta_medicamento, "medicamento"
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


def processar_mensagem(telefone, corpo_mensagem):
    """Ponto de entrada único usado pelo webhook (app/routes_whatsapp.py).
    Devolve o texto da resposta a enviar de volta pelo WhatsApp."""
    conversa = ConversaWhatsapp.query.filter_by(telefone=telefone).first()
    if conversa and conversa.expirada():
        # Sessão vencida: volta a exigir CPF + data de nascimento antes de
        # continuar - o WhatsApp de quem está escrevendo pode não ser mais
        # a mesma pessoa (ver PLANO_WHATSAPP.md).
        conversa.paciente_id = None
        conversa.agendamento_id = None
        conversa.cpf_pendente = None

    primeira_mensagem = conversa is None
    if not conversa:
        conversa = ConversaWhatsapp(telefone=telefone)
        db.session.add(conversa)

    # Toca "atualizado_em" a cada mensagem (mesmo quando nada mais muda no
    # registro) - senão o "onupdate" da coluna só dispararia se algum outro
    # campo fosse alterado, e uma conversa já identificada expiraria pela
    # data da ÚLTIMA MUDANÇA de estado, não da última mensagem trocada.
    conversa.atualizado_em = datetime.utcnow()

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
            db.session.commit()
            return MENSAGEM_NAO_ENCONTRADO

        conversa.paciente_id = paciente.id
        resposta = _resolver_exame_em_foco(conversa, paciente, _agendamentos_ativos(paciente))
        db.session.commit()
        return resposta

    # Já identificado - falta só escolher qual exame (paciente com mais
    # de um agendamento ativo, seja na identificação inicial ou depois de
    # "3) Trocar de exame").
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
        return _texto_menu(paciente, agendamento_escolhido)

    # Identificado e com exame em foco: menu de opções.
    paciente, agendamento = conversa.paciente, conversa.agendamento
    texto = (corpo_mensagem or "").strip()

    # Depois de escolher "2) Fazer uma pergunta", a PRÓXIMA mensagem é o
    # texto da pergunta em si, não uma opção do menu de novo.
    if conversa.aguardando_pergunta:
        pergunta_criada = None
        if texto == "0":
            conversa.aguardando_pergunta = False
            resposta = _texto_menu(paciente, agendamento, saudacao=False)
        elif not texto:
            resposta = MENSAGEM_PERGUNTA_VAZIA
        else:
            conversa.aguardando_pergunta = False
            resposta_pergunta, pergunta_criada = _responder_pergunta(paciente, agendamento, texto, telefone)
            # Se a pergunta acabou de ficar pendente/aguardando aprovação
            # (ver _tem_pergunta_pendente), não mostra o menu de novo -
            # só o aviso de que a resposta já foi encaminhada, sem dar a
            # entender que dá pra perguntar de novo ou trocar de exame
            # livremente enquanto isso.
            complemento = (
                MENSAGEM_AGUARDANDO_RESPOSTA
                if _tem_pergunta_pendente(paciente)
                else _menu_opcoes(paciente)
            )
            resposta = resposta_pergunta + "\n\n" + complemento
        db.session.commit()
        if pergunta_criada:
            # Só depois do commit acima - o push é melhor esforço (ver
            # push_notificacoes), não deve atrapalhar a resposta ao
            # paciente se falhar.
            notificar_equipe_nova_pergunta(pergunta_criada)
        return resposta

    # Enquanto houver uma pergunta pendente sem resposta da equipe, o
    # menu de opções fica escondido - a única coisa que faz sentido o
    # paciente ver é o aviso de que a resposta está a caminho.
    if _tem_pergunta_pendente(paciente):
        db.session.commit()
        return MENSAGEM_AGUARDANDO_RESPOSTA

    agendamentos_ativos = _agendamentos_ativos(paciente)
    tem_mais_de_um_exame = len(agendamentos_ativos) > 1

    if texto == "1":
        resposta = texto_preparo_whatsapp(agendamento) + "\n\n" + _menu_opcoes(paciente)
    elif texto == "2":
        conversa.aguardando_pergunta = True
        resposta = MENSAGEM_PEDIR_PERGUNTA
    elif texto == "3" and tem_mais_de_um_exame:
        resposta = _resolver_exame_em_foco(conversa, paciente, agendamentos_ativos)
    else:
        resposta = _mensagem_opcao_invalida_menu(paciente)

    db.session.commit()
    return resposta
