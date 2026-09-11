"""Testa os passos 3 e 4 do plano da área de WhatsApp (ver
PLANO_WHATSAPP.md e app/whatsapp_conversa.py): identificação do paciente
por CPF + data de nascimento (em duas mensagens separadas - primeiro o
CPF, depois a data), escolha do exame em foco quando há mais de um
ativo, o convite direto pra perguntar depois de identificado (o antigo
menu numerado "1) Ver informações do preparo / 2) Fazer uma pergunta /
3) Trocar de exame" foi removido a pedido do Silvan, 2026-09-11 - agora
o comando "trocar", em texto, substitui a opção "3") e expiração da
sessão de conversa por inatividade — direto na camada de lógica (sem
passar pelo webhook/Twilio, que já tem seu próprio teste de assinatura
(Meta Cloud API) em test_whatsapp_webhook_assinatura.py). O fluxo
completo da pergunta livre (IA/FAQ/alimento/medicamento/encaminhamento)
tem seu próprio teste em test_whatsapp_pergunta.py - aqui só confirma
que, depois de identificado, a mensagem já convida a perguntar direto."""
from datetime import datetime, timedelta

from app import create_app, db
from app.models import Agendamento, ConversaWhatsapp, Paciente
from app.whatsapp_conversa import normalizar_telefone_whatsapp, processar_mensagem

app = create_app()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


with app.app_context():
    checar(
        "normalizar_telefone_whatsapp acrescenta o \"+\" (a Meta manda só dígitos, sem prefixo)",
        normalizar_telefone_whatsapp("5527999998888") == "+5527999998888",
    )
    checar(
        "normalizar_telefone_whatsapp não duplica o \"+\" se já vier com ele",
        normalizar_telefone_whatsapp("+5527999998888") == "+5527999998888",
    )

    telefone_joao = "+5527900001111"

    # 1) Primeira mensagem, texto qualquer (não é um CPF): pede o CPF.
    resposta = processar_mensagem(telefone_joao, "Oi, bom dia")
    checar("Mensagem sem CPF pede o CPF primeiro", "CPF" in resposta)
    checar("Não cria paciente_id sem identificação", ConversaWhatsapp.query.filter_by(telefone=telefone_joao).first().paciente_id is None)

    # 1a) Texto que não é um CPF reconhecível (depois da primeira mensagem):
    # avisa que não reconheceu, sem falar em data de nascimento ainda.
    resposta = processar_mensagem(telefone_joao, "não sei meu cpf")
    checar("Texto que não é CPF avisa e pede de novo", "CPF" in resposta)
    checar("Ainda não pede data de nascimento (CPF não veio)", "data de nascimento" not in resposta.lower())

    # 2) CPF que não bate com nenhum cadastro: aceita o formato, guarda
    # como pendente e passa a pedir a data de nascimento.
    resposta = processar_mensagem(telefone_joao, "111.111.111-11")
    checar("CPF em formato válido: passa a pedir a data de nascimento", "data de nascimento" in resposta.lower())
    checar(
        "CPF pendente foi guardado na conversa",
        ConversaWhatsapp.query.filter_by(telefone=telefone_joao).first().cpf_pendente == "11111111111",
    )

    # 2a) Data que não bate com o CPF informado: mensagem genérica de não
    # encontrado, e volta a pedir o CPF do zero (não fica preso pedindo
    # só a data de um CPF que pode ter sido digitado errado).
    resposta = processar_mensagem(telefone_joao, "01/01/2000")
    checar("CPF/data inexistentes: mensagem genérica de não encontrado", "Não encontramos" in resposta)
    checar(
        "Depois de não encontrar, volta a exigir o CPF (limpa o pendente)",
        ConversaWhatsapp.query.filter_by(telefone=telefone_joao).first().cpf_pendente is None,
    )

    # 3) CPF do João (seed.py, sem máscara desta vez) - passa a pedir a
    # data de nascimento.
    resposta = processar_mensagem(telefone_joao, "12345678900")
    checar("CPF só com números também é aceito", "data de nascimento" in resposta.lower())

    # 3a) Data do João - um só exame ativo (a colonoscopia; a glicemia já
    # está encerrada) -> identifica e já convida a perguntar direto, sem
    # nenhum menu intermediário.
    resposta = processar_mensagem(telefone_joao, "12/04/1985")
    checar("CPF/data corretos identificam o paciente (nome no cumprimento)", "João" in resposta)
    checar("Já mostra o exame em foco (um só ativo)", "Colonoscopia" in resposta)
    checar("Já convida a perguntar direto, sem menu intermediário", "Pode digitar sua pergunta" in resposta)
    checar("Com um só exame ativo, não menciona o comando \"trocar\"", "trocar" not in resposta.lower())

    conversa = ConversaWhatsapp.query.filter_by(telefone=telefone_joao).first()
    joao = Paciente.query.filter_by(cpf="123.456.789-00").first()
    checar("ConversaWhatsapp ficou com o paciente_id certo", conversa.paciente_id == joao.id)
    checar("ConversaWhatsapp ficou com um agendamento_id (só havia um exame ativo)", conversa.agendamento_id is not None)
    checar("CPF pendente foi limpo depois de identificar", conversa.cpf_pendente is None)

    # 4) Mensagem vazia (sem texto nenhum): pede pra digitar a pergunta,
    # sem criar PerguntaPendente nem ChatMensagem nenhum.
    resposta = processar_mensagem(telefone_joao, "")
    checar("Mensagem vazia pede pra digitar a pergunta", "Não recebi nenhum texto" in resposta)

    # 4b) Correção do bug relatado pelo Silvan (2026-09-11, com print da
    # conversa e do painel do médico): um segundo exame passa a existir
    # DEPOIS que a conversa por WhatsApp já tinha fixado o primeiro (a
    # sessão ainda não expirou) - antes desta correção, a próxima pergunta
    # simplesmente respondia sobre o exame antigo, sem avisar que um novo
    # exame tinha aparecido (só um lembrete genérico do comando "trocar",
    # fácil de não notar - daí a impressão de "ficar logado" no primeiro
    # exame). Agora a mensagem depois de responder já NOMEIA o(s) outro(s)
    # exame(s) ativo(s), recalculados do banco a cada mensagem - sem
    # precisar pedir CPF/data de nascimento de novo (a sugestão do Silvan
    # de "sempre pedir CPF" foi descartada: quebraria a conveniência da
    # sessão já identificada só para resolver isso).
    agendamento_joao_original = Agendamento.query.filter_by(paciente_id=joao.id, encerrado_em=None).first()
    novo_agendamento_joao = Agendamento(
        grupo_id=agendamento_joao_original.grupo_id,
        paciente_id=joao.id,
        exame_id=agendamento_joao_original.exame_id,
        medico_id=agendamento_joao_original.medico_id,
        data_hora=datetime(2026, 9, 20, 9, 0),
    )
    db.session.add(novo_agendamento_joao)
    db.session.commit()

    resposta = processar_mensagem(telefone_joao, "Posso beber água durante o jejum?")
    checar("Novo exame surgido no meio da conversa NÃO volta a pedir CPF", "CPF" not in resposta)
    checar(
        "Avisa explicitamente (nomeando) sobre o novo exame surgido depois da identificação",
        "Você também tem agendado" in resposta and "20/09/2026" in resposta,
    )
    checar("Continua mencionando o comando \"trocar\"", "trocar" in resposta.lower())
    conversa = ConversaWhatsapp.query.filter_by(telefone=telefone_joao).first()
    checar(
        "agendamento_id continua o mesmo (exame antigo) até o paciente pedir para trocar",
        conversa.agendamento_id == agendamento_joao_original.id,
    )

    # 5) Paciente com múltiplos exames ativos: dá um segundo agendamento
    # ativo ao João (mesmo exame, data diferente) e simula uma conversa nova.
    agendamento_existente = Agendamento.query.filter_by(paciente_id=joao.id, encerrado_em=None).first()
    db.session.add(Agendamento(
        grupo_id=agendamento_existente.grupo_id,
        paciente_id=joao.id,
        exame_id=agendamento_existente.exame_id,
        medico_id=agendamento_existente.medico_id,
        data_hora=datetime(2026, 9, 1, 9, 0),
    ))
    db.session.commit()

    telefone_joao2 = "+5527900002222"
    processar_mensagem(telefone_joao2, "123.456.789-00")
    resposta = processar_mensagem(telefone_joao2, "12/04/1985")
    checar("Múltiplos exames ativos: mostra lista numerada", "1)" in resposta and "2)" in resposta)

    conversa2 = ConversaWhatsapp.query.filter_by(telefone=telefone_joao2).first()
    checar("Múltiplos exames ativos: ainda não fixou agendamento_id", conversa2.agendamento_id is None)
    checar("Múltiplos exames ativos: já fixou paciente_id", conversa2.paciente_id == joao.id)

    # 5a) Escolha inválida: repete a lista com aviso de "não entendi".
    resposta = processar_mensagem(telefone_joao2, "9")
    checar("Escolha fora da lista: avisa e repete as opções", "Não entendi" in resposta and "1)" in resposta)

    # 5b) Escolha válida: fixa o agendamento, confirma e já convida a
    # perguntar, mencionando o comando "trocar" (só faz sentido - e só
    # aparece - com mais de um exame ativo).
    resposta = processar_mensagem(telefone_joao2, "2")
    checar("Escolha válida: confirma o exame escolhido", "Colonoscopia" in resposta)
    checar("Escolha válida: já convida a perguntar direto", "Pode digitar sua pergunta" in resposta)
    checar("Com mais de um exame ativo, menciona o comando \"trocar\"", "trocar" in resposta.lower())
    conversa2 = ConversaWhatsapp.query.filter_by(telefone=telefone_joao2).first()
    agendamento_id_original = conversa2.agendamento_id
    checar("Escolha válida: agendamento_id foi fixado", agendamento_id_original is not None)

    # 5c) Comando "trocar" com mais de um exame ativo: volta a pedir a
    # escolha (mesma lista numerada de novo).
    resposta = processar_mensagem(telefone_joao2, "trocar")
    checar('Comando "trocar" com múltiplos exames ativos mostra a lista de novo', "1)" in resposta and "2)" in resposta)
    conversa2 = ConversaWhatsapp.query.filter_by(telefone=telefone_joao2).first()
    checar('"trocar": agendamento_id foi limpo, aguardando nova escolha', conversa2.agendamento_id is None)

    resposta = processar_mensagem(telefone_joao2, "1")
    conversa2 = ConversaWhatsapp.query.filter_by(telefone=telefone_joao2).first()
    checar("Nova escolha depois de trocar de exame fixou um agendamento_id", conversa2.agendamento_id is not None)

    # 5d) O comando também é reconhecido em maiúsculas ("TROCAR").
    resposta = processar_mensagem(telefone_joao2, "TROCAR")
    checar('Comando "TROCAR" (maiúsculo) também funciona', "1)" in resposta and "2)" in resposta)
    processar_mensagem(telefone_joao2, "1")

    # 6) Expiração: força a conversa do João a parecer inativa há muito
    # tempo - a próxima mensagem deve voltar a pedir CPF + data de nascimento.
    conversa.atualizado_em = datetime.utcnow() - timedelta(minutes=ConversaWhatsapp.MINUTOS_EXPIRACAO + 10)
    db.session.commit()
    resposta = processar_mensagem(telefone_joao, "oi de novo")
    checar("Conversa expirada volta a pedir identificação", "CPF" in resposta)
    conversa_expirada = ConversaWhatsapp.query.filter_by(telefone=telefone_joao).first()
    checar("Conversa expirada: paciente_id foi limpo", conversa_expirada.paciente_id is None)

    print("\nTodos os testes de identificação e convite direto à pergunta por WhatsApp (passos 3 e 4) passaram.")
