"""WhatsApp: paciente identificado sem exame em preparo não fica preso
(relatado pelo Silvan, 2026-10-21). Antes, depois de "Não encontramos
nenhum exame em preparo", a conversa ficava identificada e respondia a
mesma coisa a qualquer mensagem, inclusive a outro CPF. Agora a
identificação é desfeita e a próxima mensagem recomeça pelo CPF."""
from datetime import datetime

from app import create_app, db
from app.models import Agendamento, ConversaWhatsapp, Paciente
from app.whatsapp_conversa import MENSAGEM_SEM_EXAME_ATIVO, processar_mensagem

app = create_app()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


with app.app_context():
    joao = Paciente.query.filter_by(nome="João Pereira").first()
    for ag in Agendamento.query.filter_by(paciente_id=joao.id).all():
        ag.encerrado_em = ag.encerrado_em or datetime.utcnow()
    db.session.commit()

    telefone = "+5527900007777"
    processar_mensagem(telefone, "oi")
    processar_mensagem(telefone, "123.456.789-00")
    resposta = processar_mensagem(telefone, "12041985")
    checar("Sem exame em preparo: avisa e diz que pode enviar outro CPF",
           resposta == MENSAGEM_SEM_EXAME_ATIVO and "outro CPF" in resposta)
    conversa = ConversaWhatsapp.query.filter_by(telefone=telefone).first()
    checar("... e desfaz a identificação", conversa.paciente_id is None and conversa.cpf_pendente is None)

    resposta = processar_mensagem(telefone, "ok")
    checar("Mensagem qualquer depois disso não repete o aviso: pede o CPF", resposta != MENSAGEM_SEM_EXAME_ATIVO and "CPF" in resposta)

    resposta = processar_mensagem(telefone, "111.222.333-00")
    checar("Outro CPF: segue para a data de nascimento", "data de nascimento" in resposta.lower())
    resposta = processar_mensagem(telefone, "25121978")
    checar("... e identifica o outro paciente, com o exame dele", "Hemograma" in resposta)
    checar("... sem contar as tentativas como erro",
           ConversaWhatsapp.query.filter_by(telefone=telefone).first().tentativas_identificacao == 0)

    # Já identificado e com exame em foco, mas o exame some (cancelado):
    # também não fica preso.
    pedro = Paciente.query.filter_by(nome="Pedro Souza").first()
    for ag in Agendamento.query.filter_by(paciente_id=pedro.id).all():
        ag.encerrado_em = datetime.utcnow()
    conversa = ConversaWhatsapp.query.filter_by(telefone=telefone).first()
    conversa.agendamento_id = None
    db.session.commit()
    resposta = processar_mensagem(telefone, "posso tomar café?")
    checar("Exame encerrado/cancelado depois: avisa uma vez", resposta == MENSAGEM_SEM_EXAME_ATIVO)
    resposta = processar_mensagem(telefone, "sair")
    checar("... e a próxima mensagem já pede o CPF", resposta != MENSAGEM_SEM_EXAME_ATIVO and "CPF" in resposta)

print("\nTodos os testes de WhatsApp sem exame ativo passaram.")
