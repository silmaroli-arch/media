"""Testa o encerramento automático de conversas de WhatsApp por
inatividade (ver app/whatsapp_encerramento.py - pedido do Silvan,
2026-09-12): depois de `ConversaWhatsapp.MINUTOS_INATIVIDADE_ENCERRAR` (5)
minutos sem nenhuma mensagem nova, a conversa é apagada e um aviso é
mandado ao número - em QUALQUER etapa (ainda aguardando CPF/data de
nascimento, ou já identificada), não só depois de identificado.

Testa só a função de verificação (`_encerrar_conversas_vencidas`)
diretamente - o `iniciar_encerramento_automatico` (a thread em si, com o
`time.sleep` de produção) não tem teste automatizado aqui, porque testar
uma thread que dorme de verdade tornaria este teste lento e não muito
mais confiável; a garantia de que ela só inicia com
WHATSAPP_META_ACCESS_TOKEN configurado é conferida abaixo chamando
`iniciar_encerramento_automatico` diretamente, sem essa variável, e
confirmando que nenhuma thread nova aparece."""
import threading
from datetime import datetime, timedelta
from unittest.mock import patch

from app import create_app, db
from app.models import ConversaWhatsapp
from app.whatsapp_encerramento import _encerrar_conversas_vencidas, iniciar_encerramento_automatico
import app.whatsapp_envio as whatsapp_envio_mod

app = create_app()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


with app.app_context():
    telefone_vencida = "+5527900002222"
    telefone_recente = "+5527900003333"

    # Limpa qualquer resquício de execuções anteriores deste mesmo teste.
    ConversaWhatsapp.query.filter(ConversaWhatsapp.telefone.in_([telefone_vencida, telefone_recente])).delete()
    db.session.commit()

    # Conversa "vencida": última mensagem há 6 minutos (> 5 minutos de
    # ConversaWhatsapp.MINUTOS_INATIVIDADE_ENCERRAR) - ainda aguardando o
    # CPF (nem chegou a ser identificada), justamente para confirmar que o
    # encerramento automático vale em QUALQUER etapa da conversa, não só
    # depois de identificada.
    conversa_vencida = ConversaWhatsapp(telefone=telefone_vencida)
    db.session.add(conversa_vencida)
    db.session.commit()
    conversa_vencida.atualizado_em = datetime.utcnow() - timedelta(minutes=6)
    db.session.commit()

    # Conversa "recente": última mensagem há 1 minuto - não deve ser
    # encerrada ainda.
    conversa_recente = ConversaWhatsapp(telefone=telefone_recente)
    db.session.add(conversa_recente)
    db.session.commit()
    conversa_recente.atualizado_em = datetime.utcnow() - timedelta(minutes=1)
    db.session.commit()

    checar(
        "Conversa parada há 6 minutos: pronta_para_encerrar() é True",
        conversa_vencida.pronta_para_encerrar(),
    )
    checar(
        "Conversa parada há 1 minuto: pronta_para_encerrar() é False",
        not conversa_recente.pronta_para_encerrar(),
    )

    with patch.object(whatsapp_envio_mod, "enviar_mensagem_whatsapp") as mock_envio:
        _encerrar_conversas_vencidas()

    checar(
        "Mandou exatamente um aviso de encerramento (só para a conversa vencida)",
        mock_envio.call_count == 1,
    )
    telefone_avisado, texto_avisado = mock_envio.call_args[0]
    checar("O aviso foi para o telefone da conversa vencida", telefone_avisado == telefone_vencida)
    checar("O texto do aviso menciona o encerramento", "encerr" in texto_avisado.lower())

    checar(
        "Conversa vencida foi apagada do banco",
        ConversaWhatsapp.query.filter_by(telefone=telefone_vencida).first() is None,
    )
    checar(
        "Conversa recente NÃO foi apagada",
        ConversaWhatsapp.query.filter_by(telefone=telefone_recente).first() is not None,
    )

    # Limpeza final.
    ConversaWhatsapp.query.filter_by(telefone=telefone_recente).delete()
    db.session.commit()

    # iniciar_encerramento_automatico: sem WHATSAPP_META_ACCESS_TOKEN
    # configurado (não configuramos nada aqui), não deve criar nenhuma
    # thread nova - mesmo padrão de "falha aberta"/recurso desligado
    # sozinho do resto do módulo de WhatsApp (ver docstring da função).
    threads_antes = set(threading.enumerate())
    iniciar_encerramento_automatico(app)
    threads_depois = set(threading.enumerate())
    checar(
        "Sem WHATSAPP_META_ACCESS_TOKEN: iniciar_encerramento_automatico não cria thread nova",
        threads_depois == threads_antes,
    )

print("\nTodos os testes de encerramento automático de conversas de WhatsApp passaram.")
