"""Exclusão permanente de um médico/secretária pelo dono da plataforma —
substitui a antiga rota "Limpar dados de teste" (que apagava o banco
inteiro, sem login, e só existia pra facilitar teste em dev) por uma opção
de verdade, escopada a UMA pessoa, disponível em produção pra qualquer
dono da plataforma usar quando precisar (decisão do Silvan).

Regra de negócio (decisão do Silvan): ao excluir, apaga-se TUDO relacionado
àquela pessoa — conta, exames/agendamentos em que ela é a responsável,
calendário de licença, custo de IA, convites, vínculos de grupo etc.
Pacientes cadastrados por ela NÃO são apagados (só perdem essa atribuição
pessoal) — um cadastro de paciente não é "dado do médico", é um dado do
paciente que pode ter sido atendido por várias pessoas ao longo do tempo.

Duas situações ficam bloqueadas de propósito, ao invés de resolvidas
silenciosamente, porque afetariam dados de OUTRAS pessoas:
- Ser "dono" (título, não tipo de conta) de um Grupo que tem outras pessoas
  ativas nele — precisa transferir a titularidade antes.
- Ser o "médico responsável" de um Exame que já tem agendamento de OUTRO
  médico contra ele (exame compartilhado pela equipe) — precisa reatribuir
  o médico responsável desse exame antes.

Sempre chame `verificar_bloqueios_exclusao(usuario)` ANTES de
`excluir_usuario_e_dados(usuario)` e não prossiga se a lista vier não-vazia.
"""
from app.extensions import db
from app.models import (
    Agendamento,
    ChamadaIA,
    ChatMensagem,
    Exame,
    FaqItem,
    Grupo,
    GrupoConvite,
    GrupoMembro,
    GrupoPaciente,
    LicencaPagamento,
    Paciente,
    PerguntaPendente,
    PreparoModelo,
    PushSubscription,
    exame_medicos_associados,
)


def verificar_bloqueios_exclusao(usuario):
    """Retorna uma lista de mensagens (em português, prontas pra exibir num
    flash) explicando por que esta conta NÃO pode ser excluída agora. Lista
    vazia = pode prosseguir com `excluir_usuario_e_dados`."""
    bloqueios = []

    donos_de_grupo = GrupoMembro.query.filter_by(usuario_id=usuario.id, ativo=True, papel="dono").all()
    for gm in donos_de_grupo:
        outros_ativos = GrupoMembro.query.filter(
            GrupoMembro.grupo_id == gm.grupo_id,
            GrupoMembro.usuario_id != usuario.id,
            GrupoMembro.ativo.is_(True),
        ).count()
        if outros_ativos > 0:
            grupo = Grupo.query.get(gm.grupo_id)
            nome_grupo = grupo.nome if grupo else f"#{gm.grupo_id}"
            bloqueios.append(
                f'"{usuario.nome}" é o dono do grupo "{nome_grupo}", que tem outras pessoas ativas nele — '
                f"transfira a titularidade do grupo para outra pessoa antes de excluir esta conta."
            )

    exames_proprios = Exame.query.filter_by(medico_id=usuario.id).all()
    for exame in exames_proprios:
        usado_por_outro = Agendamento.query.filter(
            Agendamento.exame_id == exame.id,
            Agendamento.medico_id != usuario.id,
        ).first()
        if usado_por_outro:
            bloqueios.append(
                f'O exame "{exame.nome}" tem "{usuario.nome}" como médico responsável, mas já tem '
                f"agendamento de outro médico contra ele — reatribua o médico responsável desse exame "
                f'(tela "Exames") antes de excluir esta conta.'
            )

    return bloqueios


def excluir_usuario_e_dados(usuario):
    """Apaga PERMANENTEMENTE a conta e todos os dados de um médico ou
    secretária. NÃO faz commit — quem chamar decide quando salvar (e deve
    ter chamado `verificar_bloqueios_exclusao` antes e confirmado que a
    lista veio vazia)."""
    uid = usuario.id

    # 1. Histórico puramente pessoal, sem nada mais dependendo dele.
    PushSubscription.query.filter_by(usuario_id=uid).delete(synchronize_session=False)
    LicencaPagamento.query.filter_by(usuario_id=uid).delete(synchronize_session=False)
    ChamadaIA.query.filter_by(usuario_id=uid).update({"usuario_id": None}, synchronize_session=False)

    # 2. Convites de grupo — ele convidado, ou ele quem convidou.
    GrupoConvite.query.filter_by(usuario_convidado_id=uid).delete(synchronize_session=False)
    GrupoConvite.query.filter_by(convidado_por_id=uid).update({"convidado_por_id": None}, synchronize_session=False)

    # 3. Associação extra médico<->exame (tabela de associação simples).
    db.session.execute(exame_medicos_associados.delete().where(exame_medicos_associados.c.medico_id == uid))

    # 4. Exames em que ele é o médico responsável — o bloqueio acima já
    # garantiu que nenhum OUTRO médico tem agendamento contra eles, então
    # são exclusivamente dele: apaga o exame e tudo que aponta pra ele.
    exames_proprios_ids = [e.id for e in Exame.query.filter_by(medico_id=uid).all()]
    if exames_proprios_ids:
        for agendamento in Agendamento.query.filter(Agendamento.exame_id.in_(exames_proprios_ids)).all():
            db.session.delete(agendamento)  # cascade do ORM apaga o ResultadoExame junto
        ChatMensagem.query.filter(ChatMensagem.exame_id.in_(exames_proprios_ids)).update(
            {"exame_id": None}, synchronize_session=False
        )
        FaqItem.query.filter(FaqItem.exame_id.in_(exames_proprios_ids)).update(
            {"exame_id": None}, synchronize_session=False
        )
        PerguntaPendente.query.filter(PerguntaPendente.exame_id.in_(exames_proprios_ids)).update(
            {"exame_id": None}, synchronize_session=False
        )
        Exame.query.filter(Exame.id.in_(exames_proprios_ids)).delete(synchronize_session=False)

    # 5. Exames/modelos de preparo que ele só criou (outra pessoa é a
    # responsável) — perde só a atribuição de "criado por".
    Exame.query.filter_by(criado_por_id=uid).update({"criado_por_id": None}, synchronize_session=False)
    PreparoModelo.query.filter_by(criado_por_id=uid).update({"criado_por_id": None}, synchronize_session=False)

    # 6. Agendamentos em que ele é o médico responsável, mas o exame não
    # era dele (ex.: cobrindo um colega) — continuam sendo agendamentos
    # DELE, não têm como existir sem um médico responsável, então saem.
    for agendamento in Agendamento.query.filter_by(medico_id=uid).all():
        db.session.delete(agendamento)
    Agendamento.query.filter_by(criado_por_id=uid).update({"criado_por_id": None}, synchronize_session=False)

    # 7. Perguntas/FAQ que ele só criou/respondeu (atribuição, não é dado
    # clínico do paciente).
    FaqItem.query.filter_by(criado_por_id=uid).update({"criado_por_id": None}, synchronize_session=False)
    PerguntaPendente.query.filter_by(criado_por_id=uid).update({"criado_por_id": None}, synchronize_session=False)

    # 8. Pacientes que ele cadastrou (conta solo, sem Grupo) — o CADASTRO
    # do paciente continua existindo, só perde essa atribuição pessoal.
    Paciente.query.filter_by(cadastrado_por_id=uid).update({"cadastrado_por_id": None}, synchronize_session=False)

    # 9. Vínculos de grupo — remove a pessoa; se ela era a ÚNICA integrante
    # ativa de algum grupo (grupo pessoal/solo), apaga o grupo junto (não
    # sobra ninguém dependendo dele).
    membros = GrupoMembro.query.filter_by(usuario_id=uid).all()
    grupos_para_apagar = []
    for gm in membros:
        outros_ativos = GrupoMembro.query.filter(
            GrupoMembro.grupo_id == gm.grupo_id,
            GrupoMembro.usuario_id != uid,
            GrupoMembro.ativo.is_(True),
        ).count()
        if outros_ativos == 0:
            grupos_para_apagar.append(gm.grupo_id)
        db.session.delete(gm)

    for grupo_id in grupos_para_apagar:
        GrupoConvite.query.filter_by(grupo_id=grupo_id).delete(synchronize_session=False)
        GrupoPaciente.query.filter_by(grupo_id=grupo_id).delete(synchronize_session=False)
        Exame.query.filter_by(grupo_id=grupo_id).update({"grupo_id": None}, synchronize_session=False)
        PreparoModelo.query.filter_by(grupo_id=grupo_id).update({"grupo_id": None}, synchronize_session=False)
        Agendamento.query.filter_by(grupo_id=grupo_id).update({"grupo_id": None}, synchronize_session=False)
        FaqItem.query.filter_by(grupo_id=grupo_id).update({"grupo_id": None}, synchronize_session=False)
        PerguntaPendente.query.filter_by(grupo_id=grupo_id).update({"grupo_id": None}, synchronize_session=False)
        Grupo.query.filter_by(id=grupo_id).delete(synchronize_session=False)

    # 10. Por fim, a própria conta.
    db.session.delete(usuario)
