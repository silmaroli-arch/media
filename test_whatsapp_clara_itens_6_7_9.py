"""Testa os três itens de baixo risco do documento "Clara" (Fluxo de
Segurança e Atendimento, escrito pelo sócio do Silvan) autorizados em
2026-09-14 ("Pode começar" - nenhum deles desfaz nada que já existia, ver
HANDOFF_CHAT.md e a docstring de app.whatsapp_conversa):

- Item 7: limite de tentativas de identificação (CPF + data de
  nascimento que não batem com nenhum cadastro) - ao chegar no limite
  (ConversaWhatsapp.LIMITE_TENTATIVAS_IDENTIFICACAO, hoje 3), a conversa
  é bloqueada.
- Item 6: fluxo formal de "número errado" - a conversa é bloqueada assim
  que o paciente (ou quem responde por aquele número) avisa que não é a
  pessoa esperada, em qualquer etapa.
- Item 9: reconhecimento de intenção de remarcação/cancelamento - avisa a
  equipe, sem confirmar nenhuma data nova por conta própria.

Direto na camada de lógica (sem passar pelo webhook), mesmo estilo de
test_whatsapp_identificacao.py - depende do mesmo paciente de
demonstração (João, seed.py) já estar no banco."""
from app import create_app, db
from app.models import ConversaWhatsapp, PerguntaPendente
from app.whatsapp_conversa import processar_mensagem

app = create_app()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


with app.app_context():
    # ---------------------------------------------------------------
    # Item 7: limite de 3 tentativas de identificação.
    # ---------------------------------------------------------------
    telefone_item7 = "+5527900007777"

    processar_mensagem(telefone_item7, "111.111.111-11")  # CPF que não existe
    resposta = processar_mensagem(telefone_item7, "01/01/2000")  # 1ª falha
    checar("1ª falha de identificação: mensagem normal de não encontrado", "Não encontramos" in resposta)
    conversa7 = ConversaWhatsapp.query.filter_by(telefone=telefone_item7).first()
    checar("1ª falha: contou 1 tentativa", conversa7.tentativas_identificacao == 1)
    checar("1ª falha: ainda não bloqueou", conversa7.bloqueada is False)

    processar_mensagem(telefone_item7, "111.111.111-11")
    resposta = processar_mensagem(telefone_item7, "01/01/2000")  # 2ª falha
    conversa7 = ConversaWhatsapp.query.filter_by(telefone=telefone_item7).first()
    checar("2ª falha: contou 2 tentativas", conversa7.tentativas_identificacao == 2)
    checar("2ª falha: ainda não bloqueou (mensagem normal de novo)", "Não encontramos" in resposta)

    processar_mensagem(telefone_item7, "111.111.111-11")
    resposta = processar_mensagem(telefone_item7, "01/01/2000")  # 3ª falha: bloqueia
    checar("3ª falha: mensagem de bloqueio por tentativas esgotadas", "Não conseguimos confirmar" in resposta)
    conversa7 = ConversaWhatsapp.query.filter_by(telefone=telefone_item7).first()
    checar("3ª falha: conversa ficou bloqueada", conversa7.bloqueada is True)
    checar("3ª falha: motivo registrado", conversa7.motivo_bloqueio == "tentativas_excedidas")

    # Depois de bloqueada, QUALQUER mensagem nova - mesmo um CPF certo -
    # recebe sempre a mesma resposta fixa, sem processar mais nada.
    resposta = processar_mensagem(telefone_item7, "123.456.789-00")
    checar("Conversa bloqueada por tentativas: resposta fixa, não processa CPF novo", "pausadas" in resposta)
    conversa7 = ConversaWhatsapp.query.filter_by(telefone=telefone_item7).first()
    checar("Conversa bloqueada por tentativas: continua sem paciente_id", conversa7.paciente_id is None)

    # ---------------------------------------------------------------
    # Item 6: fluxo formal de "número errado" - reconhecido já na
    # primeira mensagem (antes de qualquer identificação).
    # ---------------------------------------------------------------
    telefone_item6 = "+5527900006666"

    resposta = processar_mensagem(telefone_item6, "Número errado, não conheço essa pessoa")
    checar("\"Número errado\" bloqueia direto, mesmo sem identificação prévia", "Avisamos a equipe" in resposta)
    conversa6 = ConversaWhatsapp.query.filter_by(telefone=telefone_item6).first()
    checar("\"Número errado\": conversa ficou bloqueada", conversa6.bloqueada is True)
    checar("\"Número errado\": motivo registrado", conversa6.motivo_bloqueio == "numero_errado")

    resposta = processar_mensagem(telefone_item6, "oi, tudo bem?")
    checar("Depois de bloqueada por número errado, resposta fixa pra qualquer mensagem", "pausadas" in resposta)

    # Reconhece a frase em qualquer caixa/acentuação.
    telefone_item6b = "+5527900006667"
    resposta = processar_mensagem(telefone_item6b, "NUMERO ERRADO")
    checar("\"NUMERO ERRADO\" (maiúsculo, sem acento) também bloqueia", "Avisamos a equipe" in resposta)

    # Uma mensagem comum (sem nenhuma das frases) NÃO bloqueia - só cai no
    # fluxo normal de pedir o CPF.
    telefone_item6_negativo = "+5527900006668"
    resposta = processar_mensagem(telefone_item6_negativo, "Oi, bom dia")
    checar("Mensagem comum não é confundida com \"número errado\"", "CPF" in resposta)
    conversa_negativa = ConversaWhatsapp.query.filter_by(telefone=telefone_item6_negativo).first()
    checar("Mensagem comum não bloqueia a conversa", conversa_negativa.bloqueada is False)

    # ---------------------------------------------------------------
    # Item 9: intenção de remarcação/cancelamento - só avisa a equipe,
    # nunca confirma uma data nova por conta própria.
    # ---------------------------------------------------------------
    telefone_item9 = "+5527900009999"
    processar_mensagem(telefone_item9, "123.456.789-00")  # CPF do João (seed.py)
    processar_mensagem(telefone_item9, "12/04/1985")  # identifica (exame único: colonoscopia)

    conversa9_antes = ConversaWhatsapp.query.filter_by(telefone=telefone_item9).first()
    agendamento_id_antes = conversa9_antes.agendamento_id
    checar("Item 9: paciente identificado com exame em foco antes do pedido", agendamento_id_antes is not None)

    total_perguntas_antes = PerguntaPendente.query.count()
    resposta = processar_mensagem(telefone_item9, "Quero remarcar, não vou conseguir ir")
    checar("Pedido de remarcação: avisa que a equipe foi notificada", "Avisamos a equipe" in resposta)
    checar(
        "Pedido de remarcação: não confirma nenhuma data nova por conta própria",
        "confirmado" not in resposta.lower() and "remarcado para" not in resposta.lower(),
    )

    conversa9_depois = ConversaWhatsapp.query.filter_by(telefone=telefone_item9).first()
    checar("Pedido de remarcação: agendamento em foco NÃO muda por conta própria", conversa9_depois.agendamento_id == agendamento_id_antes)
    checar("Pedido de remarcação: NÃO cria PerguntaPendente (não é uma dúvida de preparo)", PerguntaPendente.query.count() == total_perguntas_antes)
    checar("Pedido de remarcação: conversa não fica bloqueada", conversa9_depois.bloqueada is False)

    # Depois do aviso, a conversa continua normal - o paciente ainda
    # consegue fazer uma pergunta de preparo em seguida.
    resposta = processar_mensagem(telefone_item9, "1")
    checar("Depois do pedido de remarcação, ainda dá pra digitar \"1\" e perguntar normalmente", "Pode digitar sua pergunta" in resposta)

    print(
        "\nTodos os testes dos itens 6, 7 e 9 do documento Clara "
        "(número errado, limite de tentativas, pedido de remarcação) passaram."
    )
