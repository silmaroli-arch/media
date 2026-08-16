"""Sincronização entre ClinicaMembro (equipe de uma filial legada) e
GrupoMembro (participação no Grupo pareado a essa filial) — parte da Fatia 4
da migração para Grupo (ver Clinica.grupo_pareado() em app/models.py).

O Grupo pareado é uma âncora TÉCNICA, invisível na navegação legada: a
autorização de verdade em todas as telas de app/routes_medico.py continua
100% baseada em Usuario.perm_* + ClinicaMembro, isso não muda aqui. Os
GrupoMembro deste módulo só precisam existir para (a) não violar os
invariantes do modelo Grupo (sempre ter um "dono") e (b) deixar
grupo.membro_ativo() correto caso alguém acabe batendo numa URL /grupos/<id>
do próprio grupo pareado.
"""
from app.extensions import db


def sincronizar_grupo_membro_pareado(clinica):
    """Recalcula os GrupoMembro do grupo pareado desta clínica a partir dos
    ClinicaMembro (ativos e inativos) dela. Chamar sempre que um
    ClinicaMembro for criado, reativado ou encerrado."""
    from app.models import GrupoMembro

    if not clinica.grupo_pareado_id:
        return
    grupo_id = clinica.grupo_pareado_id
    membros_ativos = [m for m in clinica.membros if m.ativo]
    dono_membro = next(
        (m for m in membros_ativos if m.usuario.empresa_fundadora_id == clinica.empresa_id),
        membros_ativos[0] if membros_ativos else None,
    )
    for cm in clinica.membros:
        gm = GrupoMembro.query.filter_by(grupo_id=grupo_id, usuario_id=cm.usuario_id).first()
        papel = "dono" if cm is dono_membro else "administrador"
        if gm:
            gm.ativo, gm.papel = cm.ativo, papel
        elif cm.ativo:
            db.session.add(GrupoMembro(grupo_id=grupo_id, usuario_id=cm.usuario_id, papel=papel, ativo=True))
