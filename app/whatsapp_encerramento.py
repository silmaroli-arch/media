"""Encerramento automático de conversas de WhatsApp por inatividade
(pedido do Silvan, 2026-09-12): hoje, uma vez iniciada (mesmo que ainda
aguardando CPF ou data de nascimento), a conversa nunca é encerrada de
verdade - ela só "expira" em silêncio (ver
app.models.ConversaWhatsapp.expirada, 4h) na PRÓXIMA mensagem que chegar,
sem avisar nada e sem apagar a linha se ninguém escrever de novo.

Este módulo roda em SEGUNDO PLANO (thread própria, iniciada uma vez em
create_app - ver `iniciar_encerramento_automatico`, chamada em
app/__init__.py) para encerrar PROATIVAMENTE qualquer conversa - em
qualquer etapa (aguardando CPF, aguardando data de nascimento, ou já
identificada) - depois de
app.models.ConversaWhatsapp.MINUTOS_INATIVIDADE_ENCERRAR (5) minutos sem
nenhuma mensagem nova, mandando um aviso de encerramento e então apagando
o registro - a próxima mensagem que chegar depois disso começa do zero
(pede CPF de novo), como se fosse uma conversa nova.

Por que este aviso NÃO precisa de template Meta aprovado: como o paciente
mandou a ÚLTIMA mensagem há só ~5 minutos, a conversa está bem dentro da
janela de 24h de "reengajamento" da Meta - dá pra mandar como texto livre
(ver app.whatsapp_envio.enviar_mensagem_whatsapp chamada sem
`nome_template_env`/`content_variables`), sem precisar cadastrar e aprovar
mais um modelo de mensagem (diferente dos avisos PROATIVOS de boas-vindas/
agendamento, que só acontecem fora dessa janela).

Importante (limite conhecido, aceitável no ambiente de dev/teste atual -
plano free do Render, um único worker do gunicorn, ver render.yaml): se um
dia o app passar a rodar com mais de um worker/processo, cada um teria sua
própria thread e poderia, em tese, encerrar (e avisar) a mesma conversa
duas vezes numa janela de corrida rara. Não é um problema hoje porque o
startCommand do render.yaml não define múltiplos workers."""
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

MENSAGEM_CONVERSA_ENCERRADA = (
    "Encerrando esta conversa por inatividade. Se precisar de algo, é só "
    "mandar uma nova mensagem por aqui que a gente recomeça."
)

# Intervalo entre verificações (segundos) - não precisa ser fino: a
# conversa só é encerrada depois de 5 minutos INTEIROS de silêncio (ver
# ConversaWhatsapp.MINUTOS_INATIVIDADE_ENCERRAR), então checar a cada 30s
# dá folga de sobra (encerra entre 5:00 e 5:30 de inatividade) sem
# sobrecarregar o banco com consultas a cada poucos segundos.
INTERVALO_VERIFICACAO_SEGUNDOS = 30


def _encerrar_conversas_vencidas():
    """Um "tick" da verificação - roda dentro de um app_context próprio
    (ver `_loop_encerramento`). Import local dos módulos da aplicação (não
    no topo do arquivo) para não criar um ciclo de import com app/__init__.py,
    que importa este módulo (mesmo padrão já usado em
    app/__init__.py:_registrar_deploy_atual)."""
    from app.extensions import db
    from app.models import ConversaWhatsapp
    from app.whatsapp_envio import enviar_mensagem_whatsapp

    conversas_vencidas = [c for c in ConversaWhatsapp.query.all() if c.pronta_para_encerrar()]
    for conversa in conversas_vencidas:
        # Mesmo padrão de "falha aberta" do resto do módulo de WhatsApp -
        # se o envio falhar (sem crédito, número inválido etc.), a
        # conversa é encerrada (apagada) do mesmo jeito: o objetivo aqui é
        # não deixar conversas velhas acumulando, o aviso é só uma
        # cortesia por cima disso.
        enviar_mensagem_whatsapp(conversa.telefone, MENSAGEM_CONVERSA_ENCERRADA)
        db.session.delete(conversa)
    if conversas_vencidas:
        db.session.commit()
        logger.info("Encerradas %d conversa(s) de WhatsApp por inatividade.", len(conversas_vencidas))


def _loop_encerramento(app):
    while True:
        time.sleep(INTERVALO_VERIFICACAO_SEGUNDOS)
        try:
            with app.app_context():
                _encerrar_conversas_vencidas()
        except Exception:
            # Nunca deixa a thread morrer por um erro pontual (ex.: banco
            # momentaneamente fora do ar) - só registra e tenta de novo no
            # próximo ciclo, 30s depois.
            logger.exception("Falha ao encerrar conversas de WhatsApp por inatividade.")


def iniciar_encerramento_automatico(app):
    """Chamada uma vez em create_app() - inicia a thread de verificação em
    segundo plano (`daemon=True`: nunca impede o processo de terminar).

    Não inicia a thread (nem entra no loop) quando
    WHATSAPP_META_ACCESS_TOKEN não está configurado - sem essa variável
    nenhuma mensagem de WhatsApp é enviada pela aplicação (ver
    app.whatsapp_envio), então não existe paciente algum conversando por
    esse canal para encerrar. Isso também é o que mantém a suíte de testes
    (que nunca configura essa variável) livre desta thread."""
    if not os.environ.get("WHATSAPP_META_ACCESS_TOKEN"):
        return
    threading.Thread(
        target=_loop_encerramento, args=(app,), daemon=True, name="encerramento-whatsapp",
    ).start()
