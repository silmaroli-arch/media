"""Testa o limite diário de mensagens por paciente x exame no WhatsApp
(pedido do Silvan, 2026-09-24 - ver PlataformaConfig.limite_perguntas_
dia_exame, app.models.ContagemPerguntasDia e app.whatsapp_conversa.
_excedeu_limite_perguntas_dia/_registrar_mensagem_do_dia). Sem limite
configurado (None, o padrão), nada muda - a Parte 1 confirma isso
explicitamente antes de configurar um limite baixo para o resto do
teste.

Conta TODA mensagem que chega na etapa de "pergunta" da conversa
(paciente já identificado, exame em foco), inclusive mensagens sem
sentido e de conversa social ("oi") - pedido EXPLÍCITO do Silvan (ver
docstring de ContagemPerguntasDia) - e bloqueia sem criar PerguntaPendente
nem ChatMensagem ao atingir o limite, mesmo tratamento das outras
checagens de "isso nem devia virar pergunta pra equipe" já existentes."""
from datetime import date

from app import create_app, db
from app.models import ChatMensagem, ContagemPerguntasDia, ConversaWhatsapp, Paciente, PerguntaPendente, PlataformaConfig
from app.whatsapp_conversa import (
    MENSAGEM_LIMITE_PERGUNTAS_DIA,
    _excedeu_limite_perguntas_dia,
    _registrar_mensagem_do_dia,
    processar_mensagem,
)


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


app = create_app()

with app.app_context():
    joao = Paciente.query.filter_by(cpf="123.456.789-00").first()
    exame = joao.agendamentos[0].exame if joao and joao.agendamentos else None
    checar("Paciente João e exame do seed.py disponíveis para o teste", bool(joao and exame))

    config = PlataformaConfig.obter()

    # --- Parte 1: funções puras, SEM limite configurado (padrão) ---
    config.limite_perguntas_dia_exame = None
    db.session.commit()

    ContagemPerguntasDia.query.filter_by(paciente_id=joao.id, exame_id=exame.id, data=date.today()).delete()
    db.session.commit()

    checar(
        "Sem limite configurado (None), _excedeu_limite_perguntas_dia é sempre False",
        not _excedeu_limite_perguntas_dia(joao, exame),
    )
    _registrar_mensagem_do_dia(joao, exame)
    checar(
        "Sem limite configurado, _registrar_mensagem_do_dia não cria linha nenhuma (evita gravar à toa)",
        ContagemPerguntasDia.query.filter_by(paciente_id=joao.id, exame_id=exame.id, data=date.today()).first() is None,
    )
    checar(
        "Sem exame em foco (None), nunca excede nem grava, mesmo com limite configurado",
        not _excedeu_limite_perguntas_dia(joao, None),
    )

    # --- Parte 2: funções puras, COM limite configurado ---
    config.limite_perguntas_dia_exame = 2
    db.session.commit()

    checar("Com limite 2 e contagem zerada, ainda não excede", not _excedeu_limite_perguntas_dia(joao, exame))
    _registrar_mensagem_do_dia(joao, exame)
    checar("Depois de 1 mensagem (limite 2), ainda não excede", not _excedeu_limite_perguntas_dia(joao, exame))
    _registrar_mensagem_do_dia(joao, exame)
    checar("Depois de 2 mensagens (limite 2), já excede", _excedeu_limite_perguntas_dia(joao, exame))

    # --- Parte 3: via processar_mensagem, de ponta a ponta ---
    telefone = "+5527900009999"
    ConversaWhatsapp.query.filter_by(telefone=telefone).delete()
    ContagemPerguntasDia.query.filter_by(paciente_id=joao.id, exame_id=exame.id, data=date.today()).delete()
    db.session.commit()

    config.limite_perguntas_dia_exame = 2
    db.session.commit()

    processar_mensagem(telefone, "123.456.789-00")
    processar_mensagem(telefone, "12/04/1985")  # identifica o João (exame único: colonoscopia)

    perguntas_antes = PerguntaPendente.query.filter_by(paciente_id=joao.id).count()
    mensagens_antes = ChatMensagem.query.filter_by(paciente_id=joao.id).count()

    # Mensagem 1 do dia (conta pra cota) - sem sentido de propósito, pra
    # confirmar que ISSO TAMBÉM conta (pedido explícito do Silvan).
    processar_mensagem(telefone, "asdkjf qwerty")
    # Mensagem 2 do dia (ainda dentro do limite de 2) - conversa social.
    processar_mensagem(telefone, "oi")
    # Mensagem 3 do dia - já deveria estar bloqueada pelo limite, mesmo
    # sendo uma pergunta de verdade.
    resposta = processar_mensagem(telefone, "Posso beber agua durante o jejum?")

    checar(
        "Mensagem 3 do dia (limite 2 já atingido pelas 2 mensagens anteriores) devolve o aviso de limite",
        resposta == MENSAGEM_LIMITE_PERGUNTAS_DIA,
    )
    checar(
        "NÃO cria PerguntaPendente pela pergunta bloqueada pelo limite",
        PerguntaPendente.query.filter_by(paciente_id=joao.id).count() == perguntas_antes,
    )
    checar(
        "NÃO cria ChatMensagem pela pergunta bloqueada pelo limite",
        ChatMensagem.query.filter_by(paciente_id=joao.id).count() == mensagens_antes,
    )

    # Limpeza: remove o limite configurado, pra não afetar outros testes
    # que rodem depois deste contra o mesmo banco.
    config.limite_perguntas_dia_exame = None
    db.session.commit()

print("\nTodos os testes do limite diário de mensagens por exame passaram.")
