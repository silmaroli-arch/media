"""Fatia 7 (área de WhatsApp) — passos 3 e 4 do plano:
- Passo 3: identificação do paciente por CPF + data de nascimento, com
  sessão de conversa que expira por inatividade (ver `ConversaWhatsapp`
  em app/models.py).
- Passo 4 (este arquivo): uma vez identificado (e com um exame em foco
  escolhido), o menu de opções de verdade - "1) Ver informações do
  preparo" reaproveita app.faq_engine.texto_preparo_whatsapp (mesmos
  dados/cálculo de prazo que a tela paciente/preparo.html já usa) e
  "3) Trocar de exame" reaproveita a mesma lógica de seleção da
  identificação inicial.

Este módulo é só a LÓGICA de conversa (recebe telefone + texto da
mensagem, devolve o texto da resposta) — não sabe nada sobre Twilio nem
sobre HTTP, para poder ser testado sem precisar simular um webhook (ver
app/routes_whatsapp.py, que é a única coisa que fala com o provedor).

O que ainda NÃO está implementado aqui (próximo passo do plano):
- "2) Fazer uma pergunta" de verdade (reaproveitar app/ia_preparo.py) -
  por ora essa opção só avisa que ainda está sendo construída."""
import re
from datetime import date, datetime

from app.extensions import db
from app.faq_engine import texto_preparo_whatsapp
from app.models import Agendamento, ConversaWhatsapp, Paciente


def normalizar_telefone_whatsapp(remetente_bruto):
    """A Twilio manda o remetente como "whatsapp:+5527999998888" - guarda
    só o número em E.164, sem o prefixo do canal (que é sempre "whatsapp"
    neste projeto, já que não há outro canal por trás do mesmo webhook)."""
    if not remetente_bruto:
        return None
    return remetente_bruto.replace("whatsapp:", "").strip()


def _extrair_cpf_e_nascimento(texto):
    """Lê CPF + data de nascimento de uma mensagem de texto livre, em
    qualquer ordem e com ou sem pontuação (ex.: "111.222.333-44,
    01/01/1990" ou "01/01/1990 11122233344"). Retorna (cpf_digitos,
    data_nascimento) — qualquer um dos dois pode vir None se não for
    possível reconhecer com confiança."""
    texto = texto or ""

    data_nascimento = None
    data_match = re.search(r"(\d{1,2})\s*[/-]\s*(\d{1,2})\s*[/-]\s*(\d{4})", texto)
    resto = texto
    if data_match:
        dia, mes, ano = (int(x) for x in data_match.groups())
        try:
            data_nascimento = date(ano, mes, dia)
        except ValueError:
            data_nascimento = None
        # Remove o trecho da data antes de procurar o CPF, para os dígitos
        # da data não se misturarem com os do CPF.
        resto = texto[:data_match.start()] + texto[data_match.end():]

    cpf_digitos = re.sub(r"\D", "", resto)
    if len(cpf_digitos) != 11:
        cpf_digitos = None

    return cpf_digitos, data_nascimento


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


MENU_OPCOES = (
    "1) Ver informações do preparo\n"
    "2) Fazer uma pergunta\n"
    "3) Trocar de exame"
)


def _texto_menu(paciente, agendamento, saudacao=True):
    cabecalho = f"Olá, {paciente.nome.split(' ')[0]}! " if saudacao else ""
    return (
        f"{cabecalho}Exame em foco: *{agendamento.exame.nome}* — "
        f"{agendamento.data_hora.strftime('%d/%m/%Y')}.\n\n"
        f"{MENU_OPCOES}"
    )


MENSAGEM_PEDIR_IDENTIFICACAO = (
    "Olá! Para começar, me envie seu CPF e sua data de nascimento, assim: "
    "000.000.000-00, 01/01/1990"
)
MENSAGEM_NAO_ENCONTRADO = (
    "Não encontramos um cadastro com esses dados. Confira o CPF e a data de "
    "nascimento e tente novamente."
)
MENSAGEM_SEM_EXAME_ATIVO = (
    "Não encontramos nenhum exame em preparo no momento. Se acha que isso é "
    "um engano, entre em contato com a clínica."
)
MENSAGEM_OPCAO_INVALIDA_EXAME = "Não entendi. Responda só com o número do exame na lista abaixo:"
MENSAGEM_OPCAO_INVALIDA_MENU = f"Não entendi. Escolha uma das opções abaixo:\n\n{MENU_OPCOES}"
MENSAGEM_PERGUNTA_EM_BREVE = (
    "Essa opção (fazer uma pergunta) ainda está sendo construída nesta área "
    "de WhatsApp. Por enquanto, entre em contato com a secretaria da clínica "
    "para tirar dúvidas sobre o preparo.\n\n" + MENU_OPCOES
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

    if not conversa:
        conversa = ConversaWhatsapp(telefone=telefone)
        db.session.add(conversa)

    # Toca "atualizado_em" a cada mensagem (mesmo quando nada mais muda no
    # registro) - senão o "onupdate" da coluna só dispararia se algum outro
    # campo fosse alterado, e uma conversa já identificada expiraria pela
    # data da ÚLTIMA MUDANÇA de estado, não da última mensagem trocada.
    conversa.atualizado_em = datetime.utcnow()

    if not conversa.paciente_id:
        cpf_digitos, data_nascimento = _extrair_cpf_e_nascimento(corpo_mensagem)
        if not cpf_digitos or not data_nascimento:
            db.session.commit()
            return MENSAGEM_PEDIR_IDENTIFICACAO

        paciente = _localizar_paciente(cpf_digitos, data_nascimento)
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
    opcao = (corpo_mensagem or "").strip()

    if opcao == "1":
        resposta = texto_preparo_whatsapp(agendamento) + "\n\n" + MENU_OPCOES
    elif opcao == "2":
        resposta = MENSAGEM_PERGUNTA_EM_BREVE
    elif opcao == "3":
        resposta = _resolver_exame_em_foco(conversa, paciente, _agendamentos_ativos(paciente))
    else:
        resposta = MENSAGEM_OPCAO_INVALIDA_MENU

    db.session.commit()
    return resposta
