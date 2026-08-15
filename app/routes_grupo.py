"""Trabalho compartilhado (grupo) — BBP MedIA, seção 5.1.4 a 5.1.9.

Fatias (prova de conceito) da reformulação de escopo descrita no BBP:
- cadastro de usuário (já existente, auth.cadastro) -> login -> criar
  grupo -> convidar membro por CPF -> aprovar convite -> ver grupo na lista.
- cadastro/busca de paciente por CPF associado ao(s) grupo(s) do usuário.
Implementado como um blueprint novo, adicional ao modelo de Empresa/
Clínica já existente (ver app/routes_medico.py e app/clinica_utils.py) —
a migração completa do restante do sistema para o conceito de grupo é um
trabalho futuro maior, fora do escopo desta primeira entrega.
"""
import re
from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Usuario, Grupo, GrupoMembro, GrupoConvite, GrupoPaciente, Paciente, validar_cpf, cep_incompleto

grupo_bp = Blueprint("grupo", __name__, url_prefix="/grupos")


def _cpf_digitos(cpf):
    return re.sub(r"\D", "", cpf or "")


def _buscar_paciente_global_por_cpf(cpf):
    """Paciente "global" (cadastrado por este novo fluxo de grupo, sem
    empresa_id — ver Paciente.empresa_id) com este CPF, se existir. Não
    procura entre os cadastros antigos ligados a Empresa/Clínica — são
    dados de um modelo diferente (por enquanto) e ficam fora desta busca."""
    digitos = _cpf_digitos(cpf)
    if len(digitos) != 11:
        return None
    for p in Paciente.query.filter_by(empresa_id=None).filter(Paciente.cpf.isnot(None)).all():
        if _cpf_digitos(p.cpf) == digitos:
            return p
    return None


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

        grupo = Grupo(nome=nome)
        db.session.add(grupo)
        db.session.flush()  # garante grupo.id antes de criar o membro
        db.session.add(GrupoMembro(grupo_id=grupo.id, usuario_id=current_user.id, papel="dono"))
        db.session.commit()

        session["grupo_ativo_id"] = grupo.id
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


@grupo_bp.route("/<int:grupo_id>/convidar", methods=["GET", "POST"])
@login_required
def convidar(grupo_id):
    """Tela 5.1.5 — Convidar membros para o grupo. Convite só por CPF de
    um usuário já cadastrado (tela 5.1.1) — não existe cadastro de equipe
    aqui: não é possível criar uma conta nova a partir desta tela."""
    grupo = Grupo.query.get_or_404(grupo_id)
    membro_atual = grupo.membro_ativo(current_user.id)
    if not membro_atual or membro_atual.papel not in ("dono", "administrador"):
        flash("Somente um administrador do grupo pode convidar membros.", "danger")
        return redirect(url_for("grupo.meus_grupos"))

    if request.method == "POST":
        acao = request.form.get("acao", "convidar")

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

        candidato = None
        for u in Usuario.query.filter(Usuario.cpf.isnot(None)).all():
            if _cpf_digitos(u.cpf) == cpf_alvo and u.tipo in ("medico", "secretaria", "dono"):
                candidato = u
                break

        if not candidato:
            flash("Nenhum usuário cadastrado foi encontrado com esse CPF. O usuário precisa criar sua conta antes (tela de cadastro).", "danger")
            return redirect(url_for("grupo.convidar", grupo_id=grupo.id))

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
        db.session.commit()
        session["grupo_ativo_id"] = convite.grupo_id
        flash(f'Você agora faz parte do grupo "{convite.grupo.nome}".', "success")
    else:
        convite.status = "recusado"
        db.session.commit()
        flash("Convite recusado.", "success")

    return redirect(url_for("grupo.meus_convites"))


@grupo_bp.route("/<int:grupo_id>/pacientes")
@login_required
def pacientes_lista(grupo_id):
    """Tela 5.1.9 — Lista de pacientes (associados ao grupo)."""
    grupo = Grupo.query.get_or_404(grupo_id)
    if not grupo.membro_ativo(current_user.id):
        flash("Você não participa deste grupo.", "danger")
        return redirect(url_for("grupo.meus_grupos"))

    vinculos = GrupoPaciente.query.filter_by(grupo_id=grupo_id).join(Paciente).order_by(Paciente.nome).all()
    pacientes = [
        {"paciente": v.paciente, "pode_remover": grupo.paciente_pode_ser_removido(v.paciente_id)}
        for v in vinculos
    ]
    return render_template("grupo/pacientes_lista.html", grupo=grupo, pacientes=pacientes)


@grupo_bp.route("/<int:grupo_id>/pacientes/novo", methods=["GET", "POST"])
@login_required
def pacientes_novo(grupo_id):
    """Tela 5.1.8 — Cadastro/busca de paciente. Busca primeiro por CPF: se
    já existir (cadastrado por qualquer usuário, em qualquer grupo), só
    associa ao(s) grupo(s) escolhido(s) sem duplicar o cadastro; se não
    existir, cria o cadastro (com endereço obrigatório, BBP seção 7) e já
    associa. Quando o usuário participa de mais de um grupo, pode marcar a
    quais grupos associar (ou "todos")."""
    grupo = Grupo.query.get_or_404(grupo_id)
    if not grupo.membro_ativo(current_user.id):
        flash("Você não participa deste grupo.", "danger")
        return redirect(url_for("grupo.meus_grupos"))

    meus_grupos = [m.grupo for m in GrupoMembro.query.filter_by(usuario_id=current_user.id, ativo=True).all()]

    paciente_encontrado = None
    cpf_buscado = ""

    if request.method == "POST":
        etapa = request.form.get("etapa", "buscar")
        cpf_buscado = request.form.get("cpf", "")

        if etapa == "buscar":
            if not validar_cpf(cpf_buscado):
                flash("Informe um CPF válido para buscar.", "danger")
                return render_template("grupo/pacientes_novo.html", grupo=grupo, meus_grupos=meus_grupos,
                                       paciente_encontrado=None, cpf_buscado=cpf_buscado)
            paciente_encontrado = _buscar_paciente_global_por_cpf(cpf_buscado)
            return render_template("grupo/pacientes_novo.html", grupo=grupo, meus_grupos=meus_grupos,
                                    paciente_encontrado=paciente_encontrado, cpf_buscado=cpf_buscado,
                                    cpf_nao_encontrado=(paciente_encontrado is None))

        # etapa == "salvar": associa um paciente já encontrado, ou cadastra um novo.
        grupos_escolhidos_ids = request.form.getlist("grupos_ids")
        if request.form.get("associar_todos"):
            grupos_escolhidos_ids = [str(g.id) for g in meus_grupos]
        if not grupos_escolhidos_ids:
            grupos_escolhidos_ids = [str(grupo.id)]

        paciente_id = request.form.get("paciente_id")
        if paciente_id:
            paciente = Paciente.query.get(int(paciente_id))
        else:
            if not validar_cpf(cpf_buscado):
                flash("Informe um CPF válido.", "danger")
                return render_template("grupo/pacientes_novo.html", grupo=grupo, meus_grupos=meus_grupos,
                                        paciente_encontrado=None, cpf_buscado=cpf_buscado)
            nome = (request.form.get("nome") or "").strip()
            cep = (request.form.get("cep") or "").strip()
            rua = (request.form.get("rua") or "").strip()
            numero = (request.form.get("numero") or "").strip()
            bairro = (request.form.get("bairro") or "").strip()
            cidade = (request.form.get("cidade") or "").strip()
            uf = (request.form.get("uf") or "").strip()
            if not nome:
                flash("Informe o nome do paciente.", "danger")
                return render_template("grupo/pacientes_novo.html", grupo=grupo, meus_grupos=meus_grupos,
                                        paciente_encontrado=None, cpf_buscado=cpf_buscado)
            if cep_incompleto(cep) or not all([cep, rua, numero, bairro, cidade, uf]):
                flash("Endereço completo é obrigatório para o cadastro do paciente.", "danger")
                return render_template("grupo/pacientes_novo.html", grupo=grupo, meus_grupos=meus_grupos,
                                        paciente_encontrado=None, cpf_buscado=cpf_buscado)

            data_nascimento = None
            data_str = (request.form.get("data_nascimento") or "").strip()
            if data_str:
                try:
                    data_nascimento = datetime.strptime(data_str, "%d/%m/%Y").date()
                except ValueError:
                    pass

            paciente = Paciente(
                empresa_id=None, clinica_id=None,
                nome=nome, cpf=cpf_buscado, data_nascimento=data_nascimento,
                telefone=(request.form.get("telefone") or "").strip(),
                email=(request.form.get("email") or "").strip(),
                cep=cep, rua=rua, numero=numero,
                complemento=(request.form.get("complemento") or "").strip(),
                bairro=bairro, cidade=cidade, uf=uf,
            )
            db.session.add(paciente)
            db.session.flush()

        criados = 0
        for gid in grupos_escolhidos_ids:
            gid = int(gid)
            if not GrupoPaciente.query.filter_by(grupo_id=gid, paciente_id=paciente.id).first():
                db.session.add(GrupoPaciente(grupo_id=gid, paciente_id=paciente.id))
                criados += 1
        db.session.commit()

        if criados:
            flash(f"{paciente.nome} associado(a) a {criados} grupo(s).", "success")
        else:
            flash(f"{paciente.nome} já estava associado(a) ao(s) grupo(s) selecionado(s).", "success")
        return redirect(url_for("grupo.pacientes_lista", grupo_id=grupo.id))

    return render_template("grupo/pacientes_novo.html", grupo=grupo, meus_grupos=meus_grupos,
                            paciente_encontrado=None, cpf_buscado="")


@grupo_bp.route("/<int:grupo_id>/pacientes/<int:paciente_id>/remover", methods=["POST"])
@login_required
def pacientes_remover(grupo_id, paciente_id):
    """BBP seção 7: remove a associação do paciente com este grupo — só
    permitido se o paciente nunca teve consulta agendada por um médico
    deste grupo; caso contrário, a associação é definitiva."""
    grupo = Grupo.query.get_or_404(grupo_id)
    if not grupo.membro_ativo(current_user.id):
        flash("Você não participa deste grupo.", "danger")
        return redirect(url_for("grupo.meus_grupos"))

    vinculo = GrupoPaciente.query.filter_by(grupo_id=grupo_id, paciente_id=paciente_id).first()
    if not vinculo:
        flash("Este paciente não está associado a este grupo.", "danger")
    elif not grupo.paciente_pode_ser_removido(paciente_id):
        flash("Este paciente já teve consulta agendada neste grupo — a associação é definitiva e não pode ser removida.", "danger")
    else:
        db.session.delete(vinculo)
        db.session.commit()
        flash("Paciente removido do grupo.", "success")

    return redirect(url_for("grupo.pacientes_lista", grupo_id=grupo_id))
