"""Testa os passos 3 e 4 do plano da área de WhatsApp (ver
PLANO_WHATSAPP.md e app/whatsapp_conversa.py): identificação do paciente
por CPF + data de nascimento, escolha do exame em foco quando há mais de
um ativo, o menu de opções (ver preparo / fazer pergunta / trocar de
exame) e expiração da sessão de conversa por inatividade — direto na
camada de lógica (sem passar pelo webhook/Twilio, que já tem seu próprio
teste de assinatura em test_whatsapp_webhook_assinatura.py)."""
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
        'normalizar_telefone_whatsapp remove o prefixo "whatsapp:"',
        normalizar_telefone_whatsapp("whatsapp:+5527999998888") == "+5527999998888",
    )

    telefone_joao = "+5527900001111"

    # 1) Primeira mensagem, texto qualquer (sem CPF nem data): pede identificação.
    resposta = processar_mensagem(telefone_joao, "Oi, bom dia")
    checar("Mensagem sem CPF/data pede identificação", "CPF" in resposta and "data de nascimento" in resposta)
    checar("Não cria paciente_id sem identificação", ConversaWhatsapp.query.filter_by(telefone=telefone_joao).first().paciente_id is None)

    # 2) CPF + data que não batem com nenhum cadastro: mensagem genérica de não encontrado.
    resposta = processar_mensagem(telefone_joao, "111.111.111-11, 01/01/2000")
    checar("CPF/data inexistentes: mensagem genérica de não encontrado", "Não encontramos" in resposta)

    # 3) CPF + data do João (seed.py) - um só exame ativo (a colonoscopia;
    # a glicemia já está encerrada) -> identifica e já mostra o exame em foco.
    resposta = processar_mensagem(telefone_joao, "123.456.789-00, 12/04/1985")
    checar("CPF/data corretos identificam o paciente (nome no cumprimento)", "João" in resposta)
    checar("Já mostra o exame em foco (um só ativo)", "Colonoscopia" in resposta)

    conversa = ConversaWhatsapp.query.filter_by(telefone=telefone_joao).first()
    joao = Paciente.query.filter_by(cpf="123.456.789-00").first()
    checar("ConversaWhatsapp ficou com o paciente_id certo", conversa.paciente_id == joao.id)
    checar("ConversaWhatsapp ficou com um agendamento_id (só havia um exame ativo)", conversa.agendamento_id is not None)

    # 4) Mensagem seguinte (já identificado): não pede CPF de novo, mostra
    # o menu de opções.
    resposta = processar_mensagem(telefone_joao, "quero saber sobre o preparo")
    checar("Já identificado: não pede CPF de novo", "CPF" not in resposta)
    checar("Já identificado: mensagem fora do menu (1/2/3) pede pra escolher uma opção", "Não entendi" in resposta)
    checar("Menu de opções aparece", "Ver informações do preparo" in resposta and "Fazer uma pergunta" in resposta and "Trocar de exame" in resposta)

    # 4a) Opção 1 - ver informações do preparo: reaproveita
    # app.faq_engine.texto_preparo_whatsapp (mesmos dados/cálculo de prazo
    # da tela paciente/preparo.html - a colonoscopia do seed.py tem cortes
    # de "Alimentos sólidos"/"Líquidos claros" e o alimento proibido
    # "Amendoim" cadastrados no modelo de preparo).
    resposta = processar_mensagem(telefone_joao, "1")
    checar("Opção 1 mostra o nome do exame", "Colonoscopia" in resposta)
    checar("Opção 1 mostra os cortes cadastrados no seed", "Alimentos sólidos" in resposta and "Líquidos claros" in resposta)
    checar("Opção 1 mostra um alimento proibido cadastrado no seed", "Amendoim" in resposta)
    checar("Opção 1 repete o menu no final", "Trocar de exame" in resposta)

    # 4b) Opção 2 - fazer pergunta: ainda não implementada (passo 5 do
    # plano), só avisa que está em construção.
    resposta = processar_mensagem(telefone_joao, "2")
    checar("Opção 2 avisa que ainda está sendo construída", "ainda está sendo construída" in resposta)

    # 4c) Opção fora do menu (nem 1, nem 2, nem 3): pede pra escolher de novo.
    resposta = processar_mensagem(telefone_joao, "9")
    checar("Opção inválida no menu pede pra escolher de novo", "Não entendi" in resposta and "Ver informações do preparo" in resposta)

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
    resposta = processar_mensagem(telefone_joao2, "123.456.789-00, 12/04/1985")
    checar("Múltiplos exames ativos: mostra lista numerada", "1)" in resposta and "2)" in resposta)

    conversa2 = ConversaWhatsapp.query.filter_by(telefone=telefone_joao2).first()
    checar("Múltiplos exames ativos: ainda não fixou agendamento_id", conversa2.agendamento_id is None)
    checar("Múltiplos exames ativos: já fixou paciente_id", conversa2.paciente_id == joao.id)

    # 5a) Escolha inválida: repete a lista com aviso de "não entendi".
    resposta = processar_mensagem(telefone_joao2, "9")
    checar("Escolha fora da lista: avisa e repete as opções", "Não entendi" in resposta and "1)" in resposta)

    # 5b) Escolha válida: fixa o agendamento e confirma.
    resposta = processar_mensagem(telefone_joao2, "2")
    checar("Escolha válida: confirma o exame escolhido", "Colonoscopia" in resposta)
    conversa2 = ConversaWhatsapp.query.filter_by(telefone=telefone_joao2).first()
    agendamento_id_original = conversa2.agendamento_id
    checar("Escolha válida: agendamento_id foi fixado", agendamento_id_original is not None)

    # 5c) "3) Trocar de exame" com mais de um exame ativo: volta a pedir a
    # escolha (mesma lista numerada de novo).
    resposta = processar_mensagem(telefone_joao2, "3")
    checar('"Trocar de exame" com múltiplos exames ativos mostra a lista de novo', "1)" in resposta and "2)" in resposta)
    conversa2 = ConversaWhatsapp.query.filter_by(telefone=telefone_joao2).first()
    checar('"Trocar de exame": agendamento_id foi limpo, aguardando nova escolha', conversa2.agendamento_id is None)

    resposta = processar_mensagem(telefone_joao2, "1")
    conversa2 = ConversaWhatsapp.query.filter_by(telefone=telefone_joao2).first()
    checar("Nova escolha depois de trocar de exame fixou um agendamento_id", conversa2.agendamento_id is not None)

    # 6) Expiração: força a conversa do João a parecer inativa há muito
    # tempo - a próxima mensagem deve voltar a pedir CPF + data de nascimento.
    conversa.atualizado_em = datetime.utcnow() - timedelta(minutes=ConversaWhatsapp.MINUTOS_EXPIRACAO + 10)
    db.session.commit()
    resposta = processar_mensagem(telefone_joao, "oi de novo")
    checar("Conversa expirada volta a pedir identificação", "CPF" in resposta)
    conversa_expirada = ConversaWhatsapp.query.filter_by(telefone=telefone_joao).first()
    checar("Conversa expirada: paciente_id foi limpo", conversa_expirada.paciente_id is None)

    print("\nTodos os testes de identificação e menu por WhatsApp (passos 3 e 4) passaram.")
