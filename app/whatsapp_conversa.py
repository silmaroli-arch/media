"""Fatia 7 (área de WhatsApp) — passo 3 do plano: fluxo de identificação
do paciente por CPF + data de nascimento, com sessão de conversa que
expira por inatividade (ver `ConversaWhatsapp` em app/models.py).

Este módulo é só a LÓGICA de conversa (recebe telefone + texto da
mensagem, devolve o texto da resposta) — não sabe nada sobre Twilio nem
sobre HTTP, para poder ser testado sem precisar simular um webhook (ver
app/routes_whatsapp.py, que é a única coisa que fala com o provedor).

O que ainda NÃO está implementado aqui (próximos passos do plano):
- "Ver informações do preparo" (reaproveitar app/faq_engine.py e o
  cálculo de prazos de app/models.py:Agendamento.limite()).
- "Fazer uma pergunta" (reaproveitar app/ia_preparo.py).
Por ora, depois de identificado (e com um exame em foco escolhido), a
conversa só confirma a identificação — o menu de opções de verdade entra
no próximo passo."""
import re
from datetime import date, datetime

from app.extensions import db
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


def _texto_identificado(paciente, agendamento):
    return (
        f"Olá, {paciente.nome.split(' ')[0]}! Encontrei seu cadastro.\n"
        f"Exame em foco: {agendamento.exame.nome} — {agendamento.data_hora.strftime('%d/%m/%Y')}.\n"
        "(As opções de ver o preparo e fazer perguntas chegam no próximo passo desta área de WhatsApp.)"
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
MENSAGEM_OPCAO_INVALIDA = "Não entendi. Responda só com o número do exame na lista abaixo:"


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
        agendamentos = _agendamentos_ativos(paciente)
        if not agendamentos:
            db.session.commit()
            return MENSAGEM_SEM_EXAME_ATIVO
        if len(agendamentos) == 1:
            conversa.agendamento_id = agendamentos[0].id
            db.session.commit()
            return _texto_identificado(paciente, agendamentos[0])

        db.session.commit()
        return _texto_lista_exames(agendamentos)

    # Já identificado - falta só escolher qual exame (paciente com mais
    # de um agendamento ativo).
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
            return _texto_lista_exames(agendamentos, preambulo=MENSAGEM_OPCAO_INVALIDA)

        agendamento_escolhido = agendamentos[indice - 1]
        conversa.agendamento_id = agendamento_escolhido.id
        db.session.commit()
        return _texto_identificado(paciente, agendamento_escolhido)

    # Identificado e com exame em foco: o menu de opções de verdade
    # (ver preparo / fazer pergunta) é o próximo passo do plano.
    db.session.commit()
    return _texto_identificado(conversa.paciente, conversa.agendamento)
