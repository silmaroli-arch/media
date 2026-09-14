"""Testa o parâmetro de aprovação configurável (pedido do Silvan,
2026-09-13): cada Grupo (ou médico/dono, numa conta solo sem Grupo) pode
desativar a exigência de revisão humana antes de uma resposta de
alimento/medicamento (calculada a partir do preparo cadastrado) ou da IA
ir para o paciente — ver Grupo.aprovacao_perguntas_paciente / Usuario.
aprovacao_perguntas_paciente (app/models.py), app.routes_paciente.
exige_aprovacao_pergunta / aprovar_pergunta_automaticamente (usadas tanto
pelo chat web quanto pelo WhatsApp) e a tela medico.perguntas_configuracao
(app/routes_medico.py + medico/perguntas.html).

Reaproveita o mesmo cenário do Caminho 2 de test_whatsapp_pergunta.py
(pergunta sobre "Amendoim", alimento proibido cadastrado no preparo do
João, ver seed.py) - com o padrão de fábrica (Grupo recém-semeado, sem
tocar no parâmetro) o comportamento continua IGUAL a sempre (aguardando
aprovação); só muda depois que este teste desativa explicitamente o
parâmetro no Grupo do João. Descobre o Grupo do João via GrupoPaciente (em
vez de fixar o nome do Grupo no teste) para não depender dos dados exatos
do seed.py mudarem no futuro."""
from app import create_app, db
from app.models import FaqItem, Grupo, GrupoPaciente, Paciente, PerguntaPendente
from app.whatsapp_conversa import processar_mensagem

app = create_app()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


with app.app_context():
    telefone = "+5527900004444"

    # Identifica o João (mesmo paciente/preparo do Caminho 2 de
    # test_whatsapp_pergunta.py) - um número de telefone diferente daquele
    # teste, para não reaproveitar (e ficar preso a) a ConversaWhatsapp que
    # ele deixa por lá.
    processar_mensagem(telefone, "123.456.789-00")
    processar_mensagem(telefone, "12/04/1985")
    joao = Paciente.query.filter_by(cpf="123.456.789-00").first()

    grupo_paciente = GrupoPaciente.query.filter_by(paciente_id=joao.id).first()
    checar("João está associado a um Grupo (seed.py)", grupo_paciente is not None)
    grupo_joao = Grupo.query.get(grupo_paciente.grupo_id)
    checar(
        "Por padrão (sem tocar no parâmetro), o Grupo nasce com aprovação ATIVADA",
        grupo_joao.aprovacao_perguntas_paciente is True,
    )

    # --- Com o padrão de fábrica (aprovação ativada): comportamento igual
    # a sempre, já coberto em detalhe por test_whatsapp_pergunta.py - aqui
    # só confirma que a EXISTÊNCIA do novo campo não mudou nada por si só. ---
    processar_mensagem(telefone, "1")
    resposta = processar_mensagem(telefone, "Posso comer amendoim antes do exame?")
    checar(
        "Com aprovação ativada, pergunta sobre alimento NÃO devolve a resposta direto",
        "encaminhada" in resposta.lower(),
    )
    pendente = PerguntaPendente.query.filter_by(paciente_id=joao.id).order_by(PerguntaPendente.id.desc()).first()
    checar('Fica com status "aguardando_aprovacao", como sempre', pendente.status == "aguardando_aprovacao")
    # Libera manualmente (simula o médico aprovando) só para poder seguir
    # testando o próximo cenário nesta mesma conversa.
    pendente.status = "respondida"
    db.session.commit()

    # --- Desativa a aprovação no Grupo do João (equivalente a desmarcar o
    # botão em "Perguntas pendentes" - ver medico.perguntas_configuracao) ---
    grupo_joao.aprovacao_perguntas_paciente = False
    db.session.commit()

    pendentes_antes = PerguntaPendente.query.filter_by(paciente_id=joao.id).count()
    faqs_antes = FaqItem.query.filter_by(grupo_id=grupo_joao.id).count()

    processar_mensagem(telefone, "1")
    resposta = processar_mensagem(telefone, "Posso comer amendoim antes do exame?")
    checar(
        "Com aprovação DESATIVADA, a resposta sobre o alimento já vem direto pelo WhatsApp",
        "amendoim" in resposta.lower() and "proibid" in resposta.lower(),
    )
    checar("Não mostra mais o aviso de encaminhamento", "encaminhada" not in resposta.lower())

    pendentes_depois = PerguntaPendente.query.filter_by(paciente_id=joao.id).count()
    checar(
        "Ainda registra uma PerguntaPendente (auditoria/histórico) - só que já nasce respondida",
        pendentes_depois == pendentes_antes + 1,
    )
    pendente_auto = PerguntaPendente.query.filter_by(paciente_id=joao.id).order_by(PerguntaPendente.id.desc()).first()
    checar('PerguntaPendente da aprovação automática já fica com status "respondida"', pendente_auto.status == "respondida")
    checar(
        "PerguntaPendente da aprovação automática guarda a resposta de verdade",
        pendente_auto.resposta and "proibid" in pendente_auto.resposta.lower(),
    )
    checar(
        'PerguntaPendente da aprovação automática identifica quem respondeu como o sistema (não um nome de médico de verdade)',
        pendente_auto.respondida_por and "automática" in pendente_auto.respondida_por.lower(),
    )

    faqs_depois = FaqItem.query.filter_by(grupo_id=grupo_joao.id).count()
    checar(
        "Aprovação automática também alimenta a base de FAQ, igual a uma aprovação manual do médico",
        faqs_depois == faqs_antes + 1,
    )

    # Como a pergunta já ficou "respondida" (mesmo que automaticamente), a
    # conversa não trava esperando o médico - a próxima mensagem já mostra
    # o convite normal pra perguntar de novo.
    resposta_convite = processar_mensagem(telefone, "1")
    checar(
        'Depois da aprovação automática, o próximo "1" já convida a perguntar de novo (não trava em "aguardando resposta")',
        "Pode digitar sua pergunta" in resposta_convite,
    )

    # --- Reativa a aprovação no Grupo do João, para não deixar efeito
    # colateral em outros testes que rodem depois neste mesmo banco
    # (ex.: test_whatsapp_pergunta.py, que espera o comportamento padrão). ---
    grupo_joao.aprovacao_perguntas_paciente = True
    db.session.commit()

    print("\nTodos os testes do parâmetro de aprovação configurável passaram.")
