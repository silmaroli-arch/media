"""Testa o "conceito de conversa" pedido pelo Silvan (2026-09-14): a IA
que responde dúvidas do paciente (app.ia_preparo.responder_com_ia) deve
entender que existe uma conversa em aberto com aquele paciente sobre
aquele exame - uma pergunta de acompanhamento curta (ex.: "e frita?"
depois de "posso comer batata?") só faz sentido em conjunto com a
pergunta anterior, não isolada.

Como este ambiente de teste não tem nenhuma API key de IA configurada
(ver app/ia_preparo.py), não dá pra testar a IA respondendo de verdade
com o contexto - o que é testado aqui é a MECÂNICA que alimenta esse
contexto:
1) `app.ia_preparo._formatar_historico_conversa` - formata o histórico
   como texto, incluindo o caso de uma pergunta anterior ainda sem
   resposta (pendente de aprovação do médico).
2) `app.routes_paciente._historico_recente_chat` - busca esse histórico
   no banco (mesmo paciente + mesmo exame, dentro de uma janela recente
   de tempo, de qualquer canal).
3) Que tanto `app.routes_paciente.chat()` (área web) quanto
   `app.whatsapp_conversa._responder_pergunta` (WhatsApp) de fato PASSAM
   esse histórico para `responder_com_ia` a cada nova pergunta - via
   `unittest.mock.patch`, comparando o `historico` recebido pela chamada
   mockada com o que `_historico_recente_chat` calculava imediatamente
   antes daquela mensagem ser processada (evita depender de o banco
   estar "vazio" no início do teste - outros arquivos de teste desta
   mesma suíte podem ter deixado mensagens recentes sobre o mesmo
   exame)."""
from datetime import datetime, timedelta
from unittest.mock import patch

from app import create_app, db
from app.ia_preparo import _formatar_historico_conversa
from app.models import Agendamento, ChatMensagem, Exame, Grupo, Paciente, PerguntaPendente
from app.routes_paciente import _historico_recente_chat
from app.whatsapp_conversa import processar_mensagem

app = create_app()
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


def login_paciente(cpf, dn):
    return client.post("/login-paciente", data={"cpf": cpf, "data_nascimento": dn}, follow_redirects=True)


# ---------------------------------------------------------------------
# 1) _formatar_historico_conversa (função pura, sem banco/app_context)
# ---------------------------------------------------------------------
checar("Sem histórico, devolve string vazia (comportamento idêntico a antes)", _formatar_historico_conversa(None) == "")
checar("Lista vazia também devolve string vazia", _formatar_historico_conversa([]) == "")

bloco = _formatar_historico_conversa([("Posso comer batata?", "Sim, batata é permitida.")])
checar("Com histórico, menciona a pergunta anterior", "Posso comer batata?" in bloco)
checar("Com histórico, menciona a resposta dada", "Sim, batata é permitida." in bloco)

bloco_pendente = _formatar_historico_conversa([("Posso comer batata?", None)])
checar("Pergunta anterior ainda sem resposta: menciona a pergunta mesmo assim", "Posso comer batata?" in bloco_pendente)
checar("Pergunta anterior ainda sem resposta: não inventa nenhuma resposta", "Resposta que foi dada" not in bloco_pendente)


with app.app_context():
    joao = Paciente.query.filter_by(cpf="123.456.789-00").first()
    clinica_vitoria_id = Grupo.query.filter_by(nome="Clínica Vitória").first().id
    colonoscopia = Exame.query.filter_by(grupo_id=clinica_vitoria_id, nome="Colonoscopia").first()
    exame_id = colonoscopia.id

    # -------------------------------------------------------------
    # 2) _historico_recente_chat - filtragem por exame e por janela de
    #    tempo, e inclusão de pergunta ainda sem resposta.
    # -------------------------------------------------------------
    agora = datetime.utcnow()
    marca_dentro_da_janela = "[teste-conversa] pergunta recente sobre a colonoscopia"
    marca_fora_da_janela = "[teste-conversa] pergunta antiga demais, fora da janela"
    marca_outro_exame = "[teste-conversa] pergunta sobre outro exame, não deve aparecer"
    marca_pendente = "[teste-conversa] pergunta recente ainda pendente de aprovação"

    outro_exame = Exame.query.filter(Exame.id != exame_id).first()

    db.session.add_all([
        ChatMensagem(
            paciente_id=joao.id, exame_id=exame_id,
            pergunta=marca_dentro_da_janela, resposta="Resposta de teste.",
            criado_em=agora - timedelta(minutes=5),
        ),
        ChatMensagem(
            paciente_id=joao.id, exame_id=exame_id,
            pergunta=marca_fora_da_janela, resposta="Resposta de teste antiga.",
            criado_em=agora - timedelta(minutes=45),
        ),
        ChatMensagem(
            paciente_id=joao.id, exame_id=outro_exame.id if outro_exame else exame_id,
            pergunta=marca_outro_exame, resposta="Resposta de outro exame.",
            criado_em=agora - timedelta(minutes=2),
        ),
        ChatMensagem(
            paciente_id=joao.id, exame_id=exame_id,
            pergunta=marca_pendente, resposta=None,
            criado_em=agora - timedelta(minutes=1),
        ),
    ])
    db.session.commit()

    historico = _historico_recente_chat(joao.id, exame_id, limite=20)
    perguntas_no_historico = [p for p, r in historico]

    checar("Pergunta recente sobre o exame certo entra no histórico", marca_dentro_da_janela in perguntas_no_historico)
    checar("Pergunta fora da janela de tempo NÃO entra no histórico", marca_fora_da_janela not in perguntas_no_historico)
    if outro_exame:
        checar("Pergunta sobre OUTRO exame não entra no histórico deste exame", marca_outro_exame not in perguntas_no_historico)
    checar("Pergunta recente ainda sem resposta entra no histórico (com resposta=None)", marca_pendente in perguntas_no_historico)

    indice_dentro = perguntas_no_historico.index(marca_dentro_da_janela)
    indice_pendente = perguntas_no_historico.index(marca_pendente)
    checar(
        "Ordem cronológica (mais antiga primeiro): a de 5 min atrás vem antes da de 1 min atrás",
        indice_dentro < indice_pendente,
    )

    resposta_da_pendente = dict(historico)[marca_pendente]
    checar("A entrada pendente realmente guarda resposta=None (não inventa nada)", resposta_da_pendente is None)

    checar("exame_id vazio devolve lista vazia (não há como ter pergunta 'anterior' sem exame)", _historico_recente_chat(joao.id, None) == [])

    # -------------------------------------------------------------
    # 3a) app.routes_paciente.chat() (área web) passa o histórico certo
    #     para responder_com_ia a cada nova pergunta.
    # -------------------------------------------------------------
    login_paciente("123.456.789-00", "1985-04-12")

    pergunta_web_1 = "[teste-conversa-web] Esta pergunta não bate com nenhuma FAQ/alimento/medicamento cadastrado, parte 1"
    pergunta_web_2 = "[teste-conversa-web] E aqui a parte 2 desta mesma dúvida, também sem correspondência cadastrada?"

    esperado_antes_1 = _historico_recente_chat(joao.id, exame_id)
    with patch("app.routes_paciente.responder_com_ia", return_value=None) as ia_mock_web:
        client.post("/paciente/chat", data={"pergunta": pergunta_web_1, "exame_id": str(exame_id)}, follow_redirects=True)
    checar("Web: primeira pergunta chamou a IA (não bate com nada cadastrado)", ia_mock_web.called)
    historico_recebido_1 = ia_mock_web.call_args.kwargs.get("historico")
    checar("Web: histórico recebido na 1ª chamada é o que existia ANTES desta pergunta", historico_recebido_1 == esperado_antes_1)

    esperado_antes_2 = _historico_recente_chat(joao.id, exame_id)
    with patch("app.routes_paciente.responder_com_ia", return_value=None) as ia_mock_web2:
        client.post("/paciente/chat", data={"pergunta": pergunta_web_2, "exame_id": str(exame_id)}, follow_redirects=True)
    historico_recebido_2 = ia_mock_web2.call_args.kwargs.get("historico")
    checar("Web: histórico recebido na 2ª chamada é o que existia ANTES desta pergunta (já incluindo a 1ª)", historico_recebido_2 == esperado_antes_2)
    checar(
        "Web: a 1ª pergunta aparece como contexto da 2ª (é isso que permite entender uma continuação)",
        pergunta_web_1 in [p for p, r in historico_recebido_2],
    )

    client.get("/logout")

    # -------------------------------------------------------------
    # 3b) app.whatsapp_conversa._responder_pergunta passa o histórico
    #     certo para responder_com_ia a cada nova pergunta.
    #
    # Usa um paciente NOVO (não o João) de propósito: João já acumulou,
    # ao longo desta mesma suíte de testes, tanto mais de um agendamento
    # ativo (test_whatsapp_identificacao.py, test_smoke.py) quanto
    # perguntas pendentes nunca respondidas (test_whatsapp_pergunta.py) -
    # essa segunda parte bloquearia toda mensagem nova por WhatsApp com
    # "Sua pergunta ainda está sendo respondida" (ver
    # app.whatsapp_conversa._tem_pergunta_pendente), o que impediria este
    # teste de chegar a chamar a IA de verdade. Um paciente novo, com um
    # único agendamento e nenhuma pergunta pendente, garante um cenário
    # limpo e previsível, sem depender da ordem de execução dos outros
    # arquivos de teste.
    # -------------------------------------------------------------
    paciente_zap = Paciente(
        nome="Paciente Teste Conversa WhatsApp",
        cpf="000.111.222-33",
        data_nascimento=datetime(1990, 6, 15).date(),
    )
    db.session.add(paciente_zap)
    db.session.commit()
    db.session.add(Agendamento(
        grupo_id=clinica_vitoria_id, paciente_id=paciente_zap.id, exame_id=exame_id,
        medico_id=colonoscopia.medico_id, data_hora=datetime(2026, 10, 1, 8, 0),
    ))
    db.session.commit()

    telefone_conversa = "+5527900005555"
    processar_mensagem(telefone_conversa, "000.111.222-33")
    resposta_identificacao = processar_mensagem(telefone_conversa, "15/06/1990")
    checar("WhatsApp: paciente novo, com um único exame ativo, foca direto (sem lista de escolha)", "Pode escrever sua pergunta" in resposta_identificacao)

    pergunta_zap_1 = "[teste-conversa-zap] Esta pergunta não bate com nenhuma FAQ/alimento/medicamento cadastrado, parte 1"
    pergunta_zap_2 = "[teste-conversa-zap] E aqui a parte 2 desta mesma dúvida, também sem correspondência cadastrada?"

    esperado_antes_zap_1 = _historico_recente_chat(paciente_zap.id, exame_id)
    with patch("app.whatsapp_conversa.responder_com_ia", return_value=None) as ia_mock_zap:
        processar_mensagem(telefone_conversa, pergunta_zap_1)
    checar("WhatsApp: pergunta sem correspondência cadastrada chamou a IA", ia_mock_zap.called)
    historico_zap_1 = ia_mock_zap.call_args.kwargs.get("historico")
    checar("WhatsApp: histórico recebido na 1ª chamada é o que existia ANTES desta pergunta (vazio - paciente novo)", historico_zap_1 == esperado_antes_zap_1 == [])

    # Como a IA mockada devolveu None, a 1ª pergunta ficou como
    # PerguntaPendente "pendente" (encaminhada) - sem resolver isso, a
    # 2ª pergunta seria bloqueada por _tem_pergunta_pendente (mesmo
    # motivo pelo qual este teste usa um paciente novo, e não o João -
    # ver comentário acima). Simula o médico já tendo respondido, só
    # para poder seguir testando a continuidade da conversa.
    pendente_1 = (
        PerguntaPendente.query.filter_by(paciente_id=paciente_zap.id)
        .order_by(PerguntaPendente.id.desc())
        .first()
    )
    pendente_1.status = "respondida"
    db.session.commit()

    esperado_antes_zap_2 = _historico_recente_chat(paciente_zap.id, exame_id)
    with patch("app.whatsapp_conversa.responder_com_ia", return_value=None) as ia_mock_zap2:
        processar_mensagem(telefone_conversa, pergunta_zap_2)
    historico_zap_2 = ia_mock_zap2.call_args.kwargs.get("historico")
    checar("WhatsApp: histórico recebido na 2ª chamada é o que existia ANTES desta pergunta (já incluindo a 1ª)", historico_zap_2 == esperado_antes_zap_2)
    checar(
        "WhatsApp: a 1ª pergunta aparece como contexto da 2ª (é isso que permite entender uma continuação)",
        pergunta_zap_1 in [p for p, r in historico_zap_2],
    )

print("\nTodos os testes do 'conceito de conversa' (contexto de histórico para a IA) passaram.")
