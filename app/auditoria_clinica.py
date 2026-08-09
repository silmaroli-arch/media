"""Registro da trilha de auditoria de acesso ao prontuário (ver
LogAcessoProntuario em app/models.py) — requisito técnico do CFM
1.821/2007 para os Níveis de Garantia de Segurança NGS2/NGS3: todo acesso
ao histórico clínico de um paciente precisa ficar registrado (quem, o quê,
quando), não só as alterações."""
from flask_login import current_user

from app.extensions import db
from app.models import LogAcessoProntuario


def registrar_acesso(paciente_id, acao, detalhe=None):
    """Grava uma entrada de auditoria. Não lança exceção em caso de falha
    de gravação (ex.: sessão de banco em estado inconsistente) — um
    problema no log de auditoria não deve nunca impedir o médico de
    atender o paciente; só registra o log em silêncio se não conseguir,
    sem quebrar o fluxo principal."""
    if not current_user or not current_user.is_authenticated:
        return
    try:
        log = LogAcessoProntuario(
            paciente_id=paciente_id,
            usuario_id=current_user.id,
            acao=acao,
            detalhe=detalhe,
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()
