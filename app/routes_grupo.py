"""Administração de grupo de trabalho (BBP MedIA, seção 5.1.4 a 5.1.7 e
5.1.5/5.1.6) — criar grupo, listar/selecionar/sair, convidar membros por
CPF (ou criar conta nova), aceitar/recusar convite.

Fatia 5 (passo 5): este blueprint chegou a ter também as telas de dados
(pacientes/modelos de preparo/exames/agenda/perguntas) de um grupo
específico, por URL (`/grupos/<id>/...`) — eram a prova de conceito
original do BBP, anterior à decisão de migrar `routes_medico.py` inteiro
para operar sobre Grupo (ver Fatia 5 passo 4). Como `routes_medico.py`
(via `/equipe/...`, escopado pelo Grupo ativo da sessão) passou a cobrir
exatamente a mesma coisa, essas rotas duplicadas foram removidas daqui -
o que sobra neste arquivo é só ADMINISTRAÇÃO do grupo em si (criar,
listar, entrar, sair, convidar), que não tem equivalente em
`routes_medico.py` e virou o backbone canônico dessas telas (ver
`medico.equipe_lista`/`medico.filiais_lista`, que redirecionam pra cá).

Débito técnico conhecido (não migrado): a antiga `pacientes_remover`
(desassociar um paciente de um grupo específico) e `agenda_detalhe`
(ver o cronograma de preparo de uma consulta sem editar notas) não têm
equivalente direto em `routes_medico.py` - ficam pra uma iteração
futura, se fizerem falta na prática.
"""
import re
from datetime import date, datetime, timedelta

from flask import (
    Blueprint, render_template, redirect, url_for, request, flash, session,
)
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Usuario, Grupo, GrupoMembro, GrupoConvite, PlataformaConfig, validar_cpf
from app.clinica_utils import migrar_dados_pessoais_para_grupo

grupo_bp = Blueprint("grupo", __name__, url_prefix="/grupos")


def _cpf_digitos(cpf):
    return re.sub(r"\D", "", cpf or "")


def _grupo_ativo_id():
    return session.get("grupo_ativo_id")


def _meus_grupos_ativos():
    """Grupos em que o usuário logado é membro ativo, mais recentes primeiro."""
    if not current_user.is_authenticated:
        return []
    membros = (
        GrupoMembro.query.filter_by(usuario_id=current_user.id, ativo=True)
        .join(Grupo)
        .order_by(Grupo.id.desc())
        .all()
    )
    return [m.grupo for m in membros]


@grupo_bp.route("/novo", methods=["GET", "POST"])
@login_required
def novo():
    """Tela 5.1.4 — Criar trabalho compartilhado (grupo)."""
    if request.method == "POST":
        nome = (request.form.get("nome") or "").strip()
        if not nome:
            flash("Informe o nome do grupo.", "danger")
            return render_template("grupo/novo.html")

        # Fatia 6: este é hoje o ÚNICO lugar onde um Grupo nasce (o
        # cadastro público parou de criar um automaticamente - ver
        # routes_auth.py:cadastro()) - por isso é aqui que o trial começa
        # a contar, do mesmo jeito que o cadastro fazia antes.
        trial_dias = PlataformaConfig.obter().trial_dias
        grupo = Grupo(
            nome=nome,
            email_contato=current_user.email,
            data_vencimento=date.today() + timedelta(days=trial_dias),
        )
        db.session.add(grupo)
        db.session.flush()  # garante grupo.id antes de criar o membro
        db.session.add(GrupoMembro(grupo_id=grupo.id, usuario_id=current_user.id, papel="dono"))
        # Fatia 6: se quem está criando o grupo vinha trabalhando sozinho
        # (sem Grupo nenhum), todo o histórico pessoal dela (pacientes/
        # exames/agendamentos/etc. com escopo pessoal - ver
        # clinica_utils.filtro_escopo_atual()) migra pro grupo recém-criado
        # agora, antes do commit - sem isso, esse histórico "sumiria" da
        # vista assim que o Grupo passasse a ser o escopo de consulta.
        migrar_dados_pessoais_para_grupo(current_user, grupo)
        db.session.commit()

        session["grupo_ativo_id"] = grupo.id
        # empresa_atual() (clinica_utils) é quem de fato resolve o Grupo
        # ativo em toda a aplicação - sem isso, a pessoa continuaria "sem
        # Grupo selecionado" mesmo tendo acabado de criar/entrar num.
        session["empresa_id"] = grupo.id
        session.pop("clinica_id", None)
        flash(f'Grupo "{grupo.nome}" criado com sucesso.', "success")
        return redirect(url_for("grupo.meus_grupos"))

    return render_template("grupo/novo.html")


@grupo_bp.route("/")
@login_required
def meus_grupos():
    """Tela 5.1.7 — Meus grupos."""
    grupos = []
    for g in _meus_grupos_ativos():
        membro = g.membro_ativo(current_user.id)
        grupos.append({
            "grupo": g,
            "papel": membro.papel if membro else "membro",
            "n_membros": sum(1 for m in g.membros if m.ativo),
            "ativo": g.id == _grupo_ativo_id(),
        })
    # Quem trabalha sozinho só tem o próprio Grupo automático (nasce no
    # cadastro, ver routes_auth.py:cadastro) - pra essa pessoa, a palavra
    # "Grupo" deve ficar o mais invisível possível (decisão da Fatia 5,
    # passo 4): a lista de "Meus grupos" não agrega nada quando só tem uma
    # linha, então pula direto pra "Convidar membros" DAQUELE grupo -
    # mesmo destino que a extinta tela "Equipe" já levava. "Meus grupos"
    # continua acessível por trás (breadcrumb em grupo/convidar.html) pra
    # quem quiser criar um segundo grupo.
    # Só pula quando a pessoa é dona/administradora do único grupo -
    # sem isso, ela nem teria permissão de abrir "Convidar membros" (ver
    # checagem em grupo.convidar) e cairia num redirecionamento em loop.
    if len(grupos) == 1 and grupos[0]["papel"] in ("dono", "administrador"):
        return redirect(url_for("grupo.convidar", grupo_id=grupos[0]["grupo"].id))
    return render_template("grupo/lista.html", grupos=grupos)


@grupo_bp.route("/<int:grupo_id>/selecionar", methods=["POST"])
@login_required
def selecionar(grupo_id):
    grupo = Grupo.query.get_or_404(grupo_id)
    if not grupo.membro_ativo(current_user.id):
        flash("Você não participa deste grupo.", "danger")
        return redirect(url_for("grupo.meus_grupos"))
    session["grupo_ativo_id"] = grupo.id
    flash(f'Grupo ativo: "{grupo.nome}".', "success")
    return redirect(url_for("grupo.meus_grupos"))


@grupo_bp.route("/<int:grupo_id>/sair", methods=["POST"])
@login_required
def sair(grupo_id):
    grupo = Grupo.query.get_or_404(grupo_id)
    membro = grupo.membro_ativo(current_user.id)
    if not membro:
        flash("Você não participa deste grupo.", "danger")
    elif membro.papel == "dono":
        flash("O dono do grupo não pode sair — transfira a titularidade antes (fora do escopo desta versão).", "danger")
    else:
        membro.ativo = False
        db.session.commit()
        if _grupo_ativo_id() == grupo.id:
            session.pop("grupo_ativo_id", None)
        flash(f'Você saiu do grupo "{grupo.nome}".', "success")
    return redirect(url_for("grupo.meus_grupos"))


def _usuario_por_cpf(cpf_alvo, tipos=("medico", "secretaria", "dono")):
    """Acha uma conta (Usuario) de STAFF com este CPF - usada tanto para
    convidar (candidato precisa já ter conta) quanto para decidir, na tela
    de convite, se mostra o botão "enviar convite" ou o formulário de
    "criar conta nova" (ver convidar() abaixo)."""
    for u in Usuario.query.filter(Usuario.cpf.isnot(None)).all():
        if _cpf_digitos(u.cpf) == cpf_alvo and u.tipo in tipos:
            return u
    return None


@grupo_bp.route("/<int:grupo_id>/convidar", methods=["GET", "POST"])
@login_required
def convidar(grupo_id):
    """Tela 5.1.5 — Convidar membros para o grupo, por CPF. Fatia 5: se o
    CPF buscado não pertence a nenhuma conta existente, a própria tela
    permite criar a conta nova na hora (mesma capacidade que
    medico.equipe_novo tinha no modelo antigo - só que aqui a pessoa já
    entra ATIVA no grupo, sem passar por convite/aceite, já que quem está
    cadastrando é o próprio administrador do grupo)."""
    grupo = Grupo.query.get_or_404(grupo_id)
    membro_atual = grupo.membro_ativo(current_user.id)
    if not membro_atual or membro_atual.papel not in ("dono", "administrador"):
        flash("Somente um administrador do grupo pode convidar membros.", "danger")
        return redirect(url_for("grupo.meus_grupos"))

    cpf_busca = request.args.get("cpf_busca", "").strip()
    encontrado = None
    busca_feita = False
    if request.method == "GET" and cpf_busca:
        busca_feita = True
        cpf_alvo = _cpf_digitos(cpf_busca)
        if not cpf_alvo:
            flash("Informe um CPF válido para buscar.", "danger")
        else:
            encontrado = _usuario_por_cpf(cpf_alvo)

    if request.method == "POST":
        acao = request.form.get("acao", "convidar")

        if acao == "criar_conta":
            nome = request.form.get("nome", "").strip()
            email = request.form.get("email", "").strip().lower()
            cpf = request.form.get("cpf", "").strip()
            papel_conta = request.form.get("papel_conta", "secretaria")
            papel_grupo = request.form.get("papel_grupo", "membro")

            if papel_conta not in ("secretaria", "medico"):
                papel_conta = "secretaria"
            if papel_grupo not in ("administrador", "membro"):
                papel_grupo = "membro"

            if not nome or not email:
                flash("Nome e e-mail são obrigatórios para criar a conta.", "danger")
                return redirect(url_for("grupo.convidar", grupo_id=grupo.id, cpf_busca=cpf))
            if cpf and not validar_cpf(cpf):
                flash("CPF inválido — confira os números digitados.", "danger")
                return redirect(url_for("grupo.convidar", grupo_id=grupo.id))
            if cpf and _usuario_por_cpf(_cpf_digitos(cpf), tipos=("medico", "secretaria", "dono", "paciente")):
                flash("Já existe uma conta cadastrada com esse CPF.", "danger")
                return redirect(url_for("grupo.convidar", grupo_id=grupo.id))
            if Usuario.query.filter_by(email=email).first():
                flash("Já existe uma conta cadastrada com esse e-mail.", "danger")
                return redirect(url_for("grupo.convidar", grupo_id=grupo.id))

            senha_final = request.form.get("senha", "").strip() or "123456"
            usuario = Usuario(nome=nome, email=email, tipo=papel_conta, cpf=cpf or None)
            usuario.set_senha(senha_final)
            # Fatia 8 (licença individual): mesma regra de trial do cadastro
            # público (routes_auth.py:cadastro()) - vale a partir de agora,
            # independente de o médico já entrar direto num Grupo.
            if papel_conta == "medico":
                usuario.licenca_vencimento = date.today() + timedelta(days=PlataformaConfig.obter().trial_dias)
            db.session.add(usuario)
            db.session.flush()
            db.session.add(GrupoMembro(grupo_id=grupo.id, usuario_id=usuario.id, papel=papel_grupo, ativo=True))
            db.session.commit()
            flash(
                f"{nome} cadastrado(a) e já faz parte do grupo. Senha de acesso inicial: {senha_final}",
                "success",
            )
            return redirect(url_for("grupo.convidar", grupo_id=grupo.id))

        if acao == "cancelar_convite":
            convite = GrupoConvite.query.get(request.form.get("convite_id"))
            if convite and convite.grupo_id == grupo.id and convite.status == "pendente":
                convite.status = "cancelado"
                convite.decidido_em = datetime.utcnow()
                db.session.commit()
                flash("Convite cancelado.", "success")
            return redirect(url_for("grupo.convidar", grupo_id=grupo.id))

        if acao == "remover_membro":
            membro = GrupoMembro.query.get(request.form.get("membro_id"))
            if membro and membro.grupo_id == grupo.id and membro.papel != "dono":
                membro.ativo = False
                db.session.commit()
                flash("Membro removido do grupo.", "success")
            return redirect(url_for("grupo.convidar", grupo_id=grupo.id))

        if acao == "tornar_administrador":
            if membro_atual.papel != "dono":
                flash("Somente o dono do grupo pode conceder o papel de administrador.", "danger")
                return redirect(url_for("grupo.convidar", grupo_id=grupo.id))
            membro = GrupoMembro.query.get(request.form.get("membro_id"))
            if membro and membro.grupo_id == grupo.id:
                membro.papel = "administrador"
                db.session.commit()
                flash(f"{membro.usuario.nome} agora é administrador do grupo.", "success")
            return redirect(url_for("grupo.convidar", grupo_id=grupo.id))

        # Ação padrão: enviar convite por CPF.
        cpf_alvo = _cpf_digitos(request.form.get("cpf", ""))
        if not cpf_alvo:
            flash("Informe o CPF do usuário a convidar.", "danger")
            return redirect(url_for("grupo.convidar", grupo_id=grupo.id))

        candidato = _usuario_por_cpf(cpf_alvo)

        if not candidato:
            flash(
                "Nenhum usuário cadastrado foi encontrado com esse CPF — use \"criar conta nova\" "
                "abaixo da busca para cadastrá-lo direto no grupo.",
                "danger",
            )
            return redirect(url_for("grupo.convidar", grupo_id=grupo.id, cpf_busca=request.form.get("cpf", "")))

        if grupo.membro_ativo(candidato.id):
            flash(f"{candidato.nome} já é membro deste grupo.", "danger")
            return redirect(url_for("grupo.convidar", grupo_id=grupo.id))

        convite_existente = GrupoConvite.query.filter_by(
            grupo_id=grupo.id, usuario_convidado_id=candidato.id, status="pendente"
        ).first()
        if convite_existente:
            flash(f"Já existe um convite pendente para {candidato.nome}.", "danger")
            return redirect(url_for("grupo.convidar", grupo_id=grupo.id))

        db.session.add(GrupoConvite(
            grupo_id=grupo.id,
            usuario_convidado_id=candidato.id,
            convidado_por_id=current_user.id,
        ))
        db.session.commit()
        flash(f"Convite enviado para {candidato.nome}.", "success")
        return redirect(url_for("grupo.convidar", grupo_id=grupo.id))

    membros = [m for m in grupo.membros if m.ativo]
    convites_pendentes = [c for c in grupo.convites if c.status == "pendente"]
    return render_template(
        "grupo/convidar.html",
        grupo=grupo,
        membros=membros,
        convites_pendentes=convites_pendentes,
        sou_dono=membro_atual.papel == "dono",
        cpf_busca=cpf_busca,
        encontrado=encontrado,
        busca_feita=busca_feita,
    )


@grupo_bp.route("/convites")
@login_required
def meus_convites():
    """Tela 5.1.6 — Meus convites."""
    convites = (
        GrupoConvite.query.filter_by(usuario_convidado_id=current_user.id, status="pendente")
        .order_by(GrupoConvite.id.desc())
        .all()
    )
    return render_template("grupo/convites.html", convites=convites)


@grupo_bp.route("/convites/<int:convite_id>/responder", methods=["POST"])
@login_required
def responder_convite(convite_id):
    convite = GrupoConvite.query.get_or_404(convite_id)
    if convite.usuario_convidado_id != current_user.id or convite.status != "pendente":
        flash("Convite inválido.", "danger")
        return redirect(url_for("grupo.meus_convites"))

    decisao = request.form.get("decisao")
    convite.decidido_em = datetime.utcnow()

    if decisao == "aprovar":
        convite.status = "aceito"
        membro_existente = GrupoMembro.query.filter_by(
            grupo_id=convite.grupo_id, usuario_id=current_user.id
        ).first()
        if membro_existente:
            membro_existente.ativo = True
        else:
            db.session.add(GrupoMembro(grupo_id=convite.grupo_id, usuario_id=current_user.id, papel="membro"))
        # Fatia 6: quem aceita o convite pode ter vindo trabalhando sozinho
        # (sem Grupo nenhum) até agora - mesmo cuidado de grupo.novo() acima:
        # sem migrar aqui também, o histórico pessoal dela (pacientes/
        # exames/etc. já cadastrados) "sumiria" da vista assim que ela
        # passasse a ter um Grupo (ver filtro_escopo_atual()).
        migrar_dados_pessoais_para_grupo(current_user, convite.grupo)
        db.session.commit()
        session["grupo_ativo_id"] = convite.grupo_id
        flash(f'Você agora faz parte do grupo "{convite.grupo.nome}".', "success")
    else:
        convite.status = "recusado"
        db.session.commit()
        flash("Convite recusado.", "success")

    return redirect(url_for("grupo.meus_convites"))

