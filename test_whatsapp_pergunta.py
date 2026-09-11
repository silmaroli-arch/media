"""Testa o passo 5 do plano da área de WhatsApp (ver PLANO_WHATSAPP.md e
app/whatsapp_conversa.py:_responder_pergunta): a pergunta livre digitada
direto (sem passo intermediário de menu - o antigo "2) Fazer uma
pergunta" foi removido a pedido do Silvan, 2026-09-11) reaproveita a
MESMA lógica de app.routes_paciente.chat() — sem ANTHROPIC_API_KEY/
OPENAI_API_KEY configuradas neste ambiente de teste, a IA nunca responde
(ver app/ia_preparo.py), então os três caminhos testáveis aqui são: base
de conhecimento (FAQ já cadastrada no seed.py), resposta pronta de
alimento (a partir do preparo cadastrado) e encaminhamento como pergunta
pendente (quando nada bate)."""
from app import create_app
from app.models import ChatMensagem, ConversaWhatsapp, FaqItem, Paciente, PerguntaPendente
from app.whatsapp_conversa import processar_mensagem

app = create_app()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


with app.app_context():
    telefone = "+5527900003333"

    # Identifica o João (seed.py) - um só exame ativo (colonoscopia). CPF
    # e data de nascimento são pedidos em duas mensagens separadas (ver
    # app/whatsapp_conversa.py). Depois de identificado, já pode
    # perguntar direto, sem nenhum passo intermediário de menu.
    processar_mensagem(telefone, "123.456.789-00")
    processar_mensagem(telefone, "12/04/1985")
    joao = Paciente.query.filter_by(cpf="123.456.789-00").first()

    # --- Caminho 1: bate com uma FAQ já cadastrada no seed.py ---
    faq_agua = FaqItem.query.filter_by(pergunta="Posso beber água durante o jejum?").first()
    vezes_usada_antes = faq_agua.vezes_utilizada

    resposta = processar_mensagem(telefone, "Posso beber água durante o jejum?")
    checar("Pergunta que bate com FAQ devolve a resposta cadastrada", "água pura é permitida" in resposta)
    checar("Depois de responder, convida a perguntar de novo", "Pode digitar sua pergunta" in resposta)

    faq_agua_depois = FaqItem.query.get(faq_agua.id)
    checar("FAQ usada tem o contador de uso incrementado", faq_agua_depois.vezes_utilizada == vezes_usada_antes + 1)

    ultima_mensagem = ChatMensagem.query.filter_by(paciente_id=joao.id).order_by(ChatMensagem.id.desc()).first()
    checar("Pergunta respondida por FAQ fica no histórico com canal whatsapp", ultima_mensagem.canal == "whatsapp")
    checar("Pergunta respondida por FAQ fica no histórico com origem faq", ultima_mensagem.origem == "faq")
    checar("Histórico guarda a resposta de verdade (não só o aviso de encaminhamento)", "água pura é permitida" in ultima_mensagem.resposta)

    # --- Caminho 2: sem FAQ, mas bate com um alimento proibido cadastrado
    # no preparo (Amendoim, ver seed.py) ---
    resposta = processar_mensagem(telefone, "Posso comer amendoim antes do exame?")
    checar("Pergunta sobre alimento cadastrado devolve resposta pronta", "Amendoim" in resposta and "proibid" in resposta)

    ultima_mensagem = ChatMensagem.query.filter_by(paciente_id=joao.id).order_by(ChatMensagem.id.desc()).first()
    checar("Pergunta respondida por alimento fica no histórico com origem alimento", ultima_mensagem.origem == "alimento")

    # --- Caminho 3: não bate com nada -> encaminhada como pendente ---
    pendentes_antes = PerguntaPendente.query.filter_by(paciente_id=joao.id).count()
    resposta = processar_mensagem(telefone, "Posso dirigir sozinho depois do exame de colonoscopia?")
    checar("Pergunta sem correspondência avisa que foi encaminhada", "encaminhada" in resposta.lower())

    pendentes_depois = PerguntaPendente.query.filter_by(paciente_id=joao.id).count()
    checar("Pergunta sem correspondência cria uma PerguntaPendente nova", pendentes_depois == pendentes_antes + 1)

    ultima_pendente = PerguntaPendente.query.filter_by(paciente_id=joao.id).order_by(PerguntaPendente.id.desc()).first()
    checar("PerguntaPendente criada com o texto certo", "dirigir sozinho" in ultima_pendente.pergunta)
    checar('PerguntaPendente criada com status "pendente" (sem IA disponível)', ultima_pendente.status in (None, "pendente"))

    ultima_mensagem = ChatMensagem.query.filter_by(paciente_id=joao.id).order_by(ChatMensagem.id.desc()).first()
    checar("Pergunta encaminhada fica no histórico sem resposta ainda", ultima_mensagem.resposta is None)
    checar("Pergunta encaminhada fica no histórico com origem pendente", ultima_mensagem.origem == "pendente")

    # --- Enquanto a pergunta acima está pendente, o convite a perguntar
    # de novo fica escondido - só aparece o aviso de que a resposta está
    # a caminho, e nenhuma mensagem nova cria PerguntaPendente/ChatMensagem.
    resposta = processar_mensagem(telefone, "Posso comer batata?")
    checar("Com pergunta pendente, nova mensagem só mostra o aviso de aguardando resposta", "ainda está sendo respondida" in resposta)
    pendentes_depois2 = PerguntaPendente.query.filter_by(paciente_id=joao.id).count()
    checar("Mensagem durante pendência não cria PerguntaPendente nova", pendentes_depois2 == pendentes_depois)

    print("\nTodos os testes de pergunta livre por WhatsApp (passo 5) passaram.")
