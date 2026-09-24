"""Testa a validação mínima de "isso parece um texto de verdade" em
app.whatsapp_conversa (pedido do Silvan, 2026-09-24, com print de um caso
real: mandou ":(&;" por engano de digitação, sem ser uma pergunta de
verdade nenhuma, e isso virou uma PerguntaPendente encaminhada pra
equipe). `_eh_mensagem_sem_sentido_minimo` filtra esse tipo de mensagem
(símbolo/emoji solto, número colado, pontuação repetida) ANTES de virar
pergunta - ver docstring de app.whatsapp_conversa.

Importante: NÃO é uma correção ortográfica nem um julgamento de "faz
sentido de verdade em português" - só filtra o caso mais óbvio (texto sem
quase nenhuma letra). Erro de digitação normal dentro de palavras de
verdade continua passando direto, e uma saudação de verdade como "oi"
continua virando pergunta encaminhada pra equipe (tradeoff aceito em
2026-09-14, quando o gatilho "1" foi removido - esta mudança não resolve
isso, só o caso mais extremo de mensagem sem nenhuma cara de texto).

Duas partes: 1) `_eh_mensagem_sem_sentido_minimo` isolada (função pura de
texto, sem banco); 2) via `processar_mensagem`, confirmando que uma
mensagem sem sentido devolve o aviso pra reescrever SEM criar
PerguntaPendente/ChatMensagem nenhum, e que uma pergunta de verdade (até
com erro de digitação) continua funcionando normalmente depois disso, na
mesma conversa."""
from app.whatsapp_conversa import _eh_mensagem_sem_sentido_minimo, _normalizar_texto


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def sem_sentido(texto):
    return _eh_mensagem_sem_sentido_minimo(_normalizar_texto(texto))


# --- Parte 1: função pura, sem banco ---

checar("Caso real que motivou esta correção (\":(&;\") é sem sentido", sem_sentido(":(&;"))
checar("Só pontuação repetida (\"???\") é sem sentido", sem_sentido("???"))
checar("Só pontuação repetida (\"...\") é sem sentido", sem_sentido("..."))
checar("Emoji solto, sem nenhuma letra, é sem sentido", sem_sentido("😀👍"))
checar("Número colado (ex.: telefone digitado por engano) é sem sentido", sem_sentido("27999998888"))
checar("Uma letra só (\"k\") é sem sentido (não forma nem uma palavra mínima)", sem_sentido("k"))

checar("Saudação de verdade (\"oi\") NÃO é sem sentido (tradeoff já aceito em 2026-09-14)", not sem_sentido("oi"))
checar("Risada (\"kkkk\") NÃO é sem sentido (é uma palavra, mesmo informal)", not sem_sentido("kkkk"))
checar("Pergunta de verdade, sem erro nenhum, NÃO é sem sentido", not sem_sentido("Posso comer batata frita?"))
checar(
    "Pergunta de verdade COM erro de digitação comum continua passando",
    not sem_sentido("Posso tomar 2 comprimidos de Buscopan as 22h?"),
)
checar(
    "Pergunta de verdade com abreviação comum (\"vc\") continua passando",
    not sem_sentido("Vc pode midicar em jejum"),
)
checar(
    "Emoji + palavra de verdade (mais letra do que símbolo) NÃO é sem sentido",
    not sem_sentido("😊😊😊 obrigada"),
)
checar("Mensagem vazia não é tratada aqui (fica pra MENSAGEM_PERGUNTA_VAZIA)", not sem_sentido(""))


# --- Parte 2: via processar_mensagem, com banco ---
from app import create_app, db
from app.models import ChatMensagem, ConversaWhatsapp, Paciente, PerguntaPendente
from app.whatsapp_conversa import MENSAGEM_MENSAGEM_SEM_SENTIDO, processar_mensagem

app = create_app()

with app.app_context():
    joao = Paciente.query.filter_by(cpf="123.456.789-00").first()
    telefone = "+5527900008765"

    ConversaWhatsapp.query.filter_by(telefone=telefone).delete()
    db.session.commit()

    processar_mensagem(telefone, "123.456.789-00")
    processar_mensagem(telefone, "12/04/1985")  # identifica o João (exame único: colonoscopia)

    perguntas_antes = PerguntaPendente.query.filter_by(paciente_id=joao.id).count()
    mensagens_antes = ChatMensagem.query.filter_by(paciente_id=joao.id).count()

    resposta = processar_mensagem(telefone, ":(&;")
    checar(
        "Mensagem sem sentido devolve o aviso pra reescrever, não a resposta de uma pergunta",
        resposta == MENSAGEM_MENSAGEM_SEM_SENTIDO,
    )
    checar(
        "Mensagem sem sentido NÃO cria PerguntaPendente",
        PerguntaPendente.query.filter_by(paciente_id=joao.id).count() == perguntas_antes,
    )
    checar(
        "Mensagem sem sentido NÃO cria ChatMensagem (nem fica registrada como pergunta)",
        ChatMensagem.query.filter_by(paciente_id=joao.id).count() == mensagens_antes,
    )

    # Depois do aviso, uma pergunta de verdade (mesmo com erro de
    # digitação) continua funcionando normalmente, na mesma conversa.
    resposta = processar_mensagem(telefone, "Posso beber agua durante o jejum?")
    checar(
        "Pergunta de verdade depois do aviso funciona normalmente (bate com a FAQ do seed.py)",
        "água pura é permitida" in resposta,
    )

print("\nTodos os testes da validação mínima de 'isso parece um texto de verdade' passaram.")
