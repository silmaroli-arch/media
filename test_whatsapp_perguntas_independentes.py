"""Testa a remoção do bloqueio de "só uma pergunta por vez" por WhatsApp
(pedido do Silvan, 2026-09-24, com print de uma conversa real: mandou
"Ok" e depois "Hahaha" enquanto uma pergunta anterior ainda não tinha
resposta, e recebeu "Sua pergunta ainda está sendo respondida" pras duas
mensagens - mesmo sendo mensagens totalmente diferentes/novas, não uma
tentativa de repetir a pergunta pendente).

Antes desta correção, `app.whatsapp_conversa._tem_pergunta_pendente`
bloqueava QUALQUER mensagem nova (inclusive uma pergunta de verdade,
sobre outro assunto) enquanto uma pergunta anterior não tinha resposta da
equipe - só mostrava o aviso de espera, repetido pra cada mensagem. Ver
docstring do módulo ("Perguntas independentes com uma já pendente") -
agora uma pergunta nova cria sua PRÓPRIA PerguntaPendente independente,
mesmo com outra ainda pendente (mesmo comportamento que o chat pela área
web, app.routes_paciente.chat, já tinha - nunca bloqueou isso).

"Trocar de exame" e o reconhecimento de intenção de remarcação/número
errado (documento "Clara") também não ficam mais bloqueados por uma
pergunta pendente - eram checados depois do bloqueio removido, então
passam a funcionar normalmente mesmo com uma pendência em aberto."""
from app import create_app, db
from app.models import ChatMensagem, Paciente, PerguntaPendente
from app.whatsapp_conversa import MENSAGEM_AGUARDANDO_RESPOSTA, MENSAGEM_REAGENDAMENTO_AVISADO, processar_mensagem

app = create_app()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


with app.app_context():
    joao = Paciente.query.filter_by(cpf="123.456.789-00").first()
    telefone = "+5527900007654"

    processar_mensagem(telefone, "123.456.789-00")
    processar_mensagem(telefone, "12/04/1985")  # identifica o João (exame único: colonoscopia)

    pendentes_antes = PerguntaPendente.query.filter_by(paciente_id=joao.id).count()

    # 1ª pergunta - sem FAQ/alimento/medicamento cadastrado, sem IA
    # configurada neste ambiente de teste -> vira PerguntaPendente
    # "pendente" (encaminhada), sem resposta ainda.
    resposta_1 = processar_mensagem(
        telefone, "[teste-independente] Posso caminhar normalmente no dia anterior ao exame?"
    )
    checar("1ª pergunta é encaminhada normalmente", "encaminhada" in resposta_1.lower())
    pendentes_apos_1 = PerguntaPendente.query.filter_by(paciente_id=joao.id).count()
    checar("1ª pergunta cria uma PerguntaPendente", pendentes_apos_1 == pendentes_antes + 1)

    # 2ª pergunta, INDEPENDENTE (assunto diferente) - antes desta correção,
    # isso só devolvia o aviso de espera, sem criar nada novo. Agora deve
    # ser tratada como uma pergunta de verdade, criando sua própria
    # PerguntaPendente.
    resposta_2 = processar_mensagem(
        telefone, "[teste-independente] Posso usar batom no dia do exame?"
    )
    checar(
        "2ª pergunta, independente, com a 1ª ainda pendente, é encaminhada normalmente (não bloqueada)",
        "encaminhada" in resposta_2.lower(),
    )
    checar(
        "2ª pergunta NÃO devolve o aviso genérico de 'ainda está sendo respondida' no lugar da resposta",
        resposta_2 != MENSAGEM_AGUARDANDO_RESPOSTA,
    )
    pendentes_apos_2 = PerguntaPendente.query.filter_by(paciente_id=joao.id).count()
    checar(
        "2ª pergunta independente CRIA sua própria PerguntaPendente (não é bloqueada pela 1ª)",
        pendentes_apos_2 == pendentes_apos_1 + 1,
    )

    pendente_1 = (
        PerguntaPendente.query.filter_by(paciente_id=joao.id)
        .filter(PerguntaPendente.pergunta.like("%caminhar normalmente%"))
        .order_by(PerguntaPendente.id.desc())
        .first()
    )
    pendente_2 = (
        PerguntaPendente.query.filter_by(paciente_id=joao.id)
        .filter(PerguntaPendente.pergunta.like("%batom%"))
        .order_by(PerguntaPendente.id.desc())
        .first()
    )
    checar("As duas perguntas ficaram registradas como PerguntaPendente distintas", pendente_1.id != pendente_2.id)
    checar("As duas continuam sem resposta (nenhuma foi respondida por engano)", pendente_1.status != "respondida" and pendente_2.status != "respondida")

    checar(
        "As duas perguntas ficam no histórico (ChatMensagem), cada uma com seu próprio texto",
        ChatMensagem.query.filter_by(paciente_id=joao.id).filter(ChatMensagem.pergunta.like("%batom%")).count() == 1
        and ChatMensagem.query.filter_by(paciente_id=joao.id).filter(ChatMensagem.pergunta.like("%caminhar normalmente%")).count() == 1,
    )

    # Intenção de remarcação (documento "Clara") também não fica mais
    # bloqueada por uma pergunta pendente (era checada depois do bloqueio
    # removido, então dependia dele estar liberado pra sequer ser vista).
    resposta_reagendamento = processar_mensagem(telefone, "Quero remarcar, não vou conseguir ir")
    checar(
        "Pedido de remarcação funciona normalmente mesmo com perguntas pendentes",
        resposta_reagendamento == MENSAGEM_REAGENDAMENTO_AVISADO,
    )

print("\nTodos os testes de perguntas independentes com uma já pendente passaram.")
