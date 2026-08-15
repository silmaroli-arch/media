"""Trabalho compartilhado (grupo) — BBP MedIA, seção 5.1.4 a 5.1.16.

Fatias (prova de conceito) da reformulação de escopo descrita no BBP:
- cadastro de usuário (já existente, auth.cadastro) -> login -> criar
  grupo -> convidar membro por CPF -> aprovar convite -> ver grupo na lista.
- cadastro/busca de paciente por CPF associado ao(s) grupo(s) do usuário.
- modelo de preparo e exame pertencentes ao médico, vinculados ao grupo.
Implementado como um blueprint novo, adicional ao modelo de Empresa/
Clínica já existente (ver app/routes_medico.py e app/clinica_utils.py) —
a migração completa do restante do sistema para o conceito de grupo é um
trabalho futuro maior, fora do escopo desta primeira entrega.
"""
import re
from datetime import datetime

from flask import (
    Blueprint, render_template, redirect, url_for, request, flash, session, send_file,
)
from flask_login import login_required, current_user

from app.extensions import db
from app.models import (
    Usuario, Grupo, GrupoMembro, GrupoConvite, GrupoPaciente, Paciente,
    PreparoModelo, Exame, Agendamento, PerguntaPendente, FaqItem, Medicamento,
    validar_cpf, cep_incompleto,
)
from app.pdf_preparo import extrair_sugestao_de_pdf, gerar_xlsx_da_sugestao
from app.xlsx_preparo import extrair_sugestoes_de_xlsx
# Reaproveita, sem duplicar, a lógica já existente e já testada do lado da
# equipe (Empresa/Clínica) para: (a) salvar cortes/medicamentos/alimentos/
# informações gerais/exames anteriores de um modelo de preparo a partir de
# um formulário com os mesmos names (ver medico/preparo_modelo_form.html) —
# nenhuma dessas funções depende de clínica/filial, então funcionam igual
# para a clínica interna do grupo; e (b) a regra de quem pode ver/responder
# uma PerguntaPendente quando o exame tem mais de um médico vinculado
# (BBP seção 8, decisão nº 5: "qualquer um dos médicos pode aprovar").
from app.routes_medico import _salvar_cortes_e_medicamentos, _restringir_perguntas_para_medico

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


# ===================== Modelo de preparo (tela 5.1.13/5.1.14) =====================
# BBP seção 4: "O modelo de preparo continuará pertencendo ao médico e
# somente ele poderá alterar." Exame/PreparoModelo (modelo legado) exigem
# uma Clinica de verdade — usamos a "clínica interna" do grupo (ver
# Grupo.clinica_interna em app/models.py) como âncora técnica, sem alterar
# o modelo antigo nem expor essa clínica em nenhuma tela do sistema atual.

@grupo_bp.route("/<int:grupo_id>/preparo-modelos")
@login_required
def preparo_modelos_lista(grupo_id):
    """Tela 5.1.14 — Lista de modelos de preparo do grupo."""
    grupo = Grupo.query.get_or_404(grupo_id)
    if not grupo.membro_ativo(current_user.id):
        flash("Você não participa deste grupo.", "danger")
        return redirect(url_for("grupo.meus_grupos"))

    modelos = []
    if grupo.clinica_interna_id:
        modelos = PreparoModelo.query.filter_by(clinica_id=grupo.clinica_interna_id).order_by(PreparoModelo.nome).all()
    return render_template("grupo/preparo_modelos_lista.html", grupo=grupo, modelos=modelos)


@grupo_bp.route("/<int:grupo_id>/preparo-modelos/novo", methods=["GET", "POST"])
@login_required
def preparo_modelos_novo(grupo_id):
    """Tela 5.1.13 — Cadastro de modelo de preparo. Disponível apenas para
    usuários do tipo Médico — o modelo pertence exclusivamente a quem o
    criou (BBP seção 7: "somente ele poderá alterar")."""
    grupo = Grupo.query.get_or_404(grupo_id)
    if not grupo.membro_ativo(current_user.id):
        flash("Você não participa deste grupo.", "danger")
        return redirect(url_for("grupo.meus_grupos"))
    if current_user.tipo != "medico":
        flash("Somente usuários do tipo Médico podem cadastrar modelos de preparo.", "danger")
        return redirect(url_for("grupo.preparo_modelos_lista", grupo_id=grupo_id))

    # Sugestão vinda da importação de Excel (ver preparo_modelos_importar_xlsx
    # / preparo_modelos_importar_xlsx_escolher, mais abaixo) — mesmo padrão
    # da tela equivalente da equipe (medico.preparo_modelos_novo): só fica
    # disponível uma vez (pop), e nada é salvo até o Salvar desta tela.
    sugestao = None
    if request.method == "GET" and request.args.get("de_importacao"):
        sugestao = session.pop("grupo_preparo_sugestao_importada", None)

    if request.method == "POST":
        nome = (request.form.get("nome") or "").strip()
        instrucoes = (request.form.get("instrucoes") or "").strip()
        if not nome:
            flash("Informe o nome do modelo.", "danger")
            return render_template("grupo/preparo_modelo_form.html", grupo=grupo, sugestao=None, medicamentos_catalogo=Medicamento.query.order_by(Medicamento.nome).all())

        clinica_interna = grupo.clinica_interna()
        ja_existe = PreparoModelo.query.filter_by(clinica_id=clinica_interna.id, nome=nome).first()
        if ja_existe:
            flash(f'Já existe um modelo de preparo chamado "{nome}" neste grupo.', "danger")
            db.session.rollback()
            return render_template("grupo/preparo_modelo_form.html", grupo=grupo, sugestao=None, medicamentos_catalogo=Medicamento.query.order_by(Medicamento.nome).all())

        modelo = PreparoModelo(
            clinica_id=clinica_interna.id, criado_por_id=current_user.id,
            nome=nome, instrucoes=instrucoes,
            observacoes_medicamentos=(request.form.get("observacoes_medicamentos") or "").strip() or None,
        )
        db.session.add(modelo)
        db.session.flush()

        # Cortes de alimentação/líquido, medicamentos a suspender/mantidos,
        # alimentos, informações gerais e exames anteriores proibidos — o
        # "cronograma" do preparo (tela 5.1.13) é calculado automaticamente
        # a partir daqui no momento do agendamento (5.1.17-5.1.19). Reaproveita
        # a mesma função já usada pelo lado da equipe (app.routes_medico) —
        # ela não depende de clínica/filial, só do modelo e do formulário.
        _salvar_cortes_e_medicamentos(modelo, request.form)

        db.session.commit()
        flash(f'Modelo de preparo "{nome}" cadastrado com sucesso.', "success")
        return redirect(url_for("grupo.preparo_modelos_lista", grupo_id=grupo_id))

    return render_template("grupo/preparo_modelo_form.html", grupo=grupo, sugestao=sugestao, medicamentos_catalogo=Medicamento.query.order_by(Medicamento.nome).all())


@grupo_bp.route("/<int:grupo_id>/preparo-modelos/pdf-para-excel", methods=["GET", "POST"])
@login_required
def preparo_pdf_para_excel(grupo_id):
    """Gera uma planilha Excel (.xlsx) pronta para revisão a partir de um PDF
    de preparo (tela 5.1.10) — mesma ferramenta de extração já usada pela
    equipe (app.pdf_preparo), só disponível também dentro do grupo. A pessoa
    revisa/ajusta no Excel com calma e importa o resultado pelo botão
    "Importar de um Excel" da tela de novo modelo de preparo (5.1.11/5.1.12).
    Exclusivo da versão web (BBP seção 8, decisão nº 6) — não existe no app."""
    grupo = Grupo.query.get_or_404(grupo_id)
    if not grupo.membro_ativo(current_user.id):
        flash("Você não participa deste grupo.", "danger")
        return redirect(url_for("grupo.meus_grupos"))
    if current_user.tipo != "medico":
        flash("Somente usuários do tipo Médico podem importar modelos de preparo.", "danger")
        return redirect(url_for("grupo.preparo_modelos_lista", grupo_id=grupo_id))

    if request.method == "POST":
        arquivo = request.files.get("arquivo_pdf")
        if not arquivo or not arquivo.filename:
            flash("Selecione um arquivo PDF.", "danger")
            return render_template("grupo/preparo_pdf_para_excel.html", grupo=grupo)

        try:
            sugestao = extrair_sugestao_de_pdf(arquivo.stream)
            planilha_buffer = gerar_xlsx_da_sugestao(sugestao)
        except Exception:
            flash(
                "Não foi possível ler esse PDF. Ele pode estar corrompido, protegido por senha, ou ser "
                "uma imagem escaneada sem texto selecionável — nesse caso, cadastre o modelo manualmente.",
                "danger",
            )
            return render_template("grupo/preparo_pdf_para_excel.html", grupo=grupo)

        return send_file(
            planilha_buffer,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name="preparo-extraido-do-pdf.xlsx",
        )

    return render_template("grupo/preparo_pdf_para_excel.html", grupo=grupo)


@grupo_bp.route("/<int:grupo_id>/preparo-modelos/importar-xlsx", methods=["GET", "POST"])
@login_required
def preparo_modelos_importar_xlsx(grupo_id):
    """Tela 5.1.11 — importar um modelo de preparo a partir de um Excel
    (gerado a partir de um PDF em preparo_pdf_para_excel, ou preenchido do
    zero seguindo o mesmo formato)."""
    grupo = Grupo.query.get_or_404(grupo_id)
    if not grupo.membro_ativo(current_user.id):
        flash("Você não participa deste grupo.", "danger")
        return redirect(url_for("grupo.meus_grupos"))
    if current_user.tipo != "medico":
        flash("Somente usuários do tipo Médico podem importar modelos de preparo.", "danger")
        return redirect(url_for("grupo.preparo_modelos_lista", grupo_id=grupo_id))

    if request.method == "POST":
        arquivo = request.files.get("arquivo_xlsx")
        if not arquivo or not arquivo.filename:
            flash("Selecione um arquivo Excel (.xlsx).", "danger")
            return render_template("grupo/preparo_modelo_importar_xlsx.html", grupo=grupo)

        try:
            sugestoes = extrair_sugestoes_de_xlsx(arquivo.stream)
        except Exception:
            flash(
                "Não foi possível ler essa planilha. Confira se é um arquivo .xlsx válido e se segue o "
                "formato com as colunas Tipo/Ação/Agrupador/Nome/Dias antes/Horas antes/Hora exata.",
                "danger",
            )
            return render_template("grupo/preparo_modelo_importar_xlsx.html", grupo=grupo)

        if not sugestoes:
            flash("Essa planilha não tem nenhuma aba com dados.", "danger")
            return render_template("grupo/preparo_modelo_importar_xlsx.html", grupo=grupo)

        if len(sugestoes) == 1:
            session["grupo_preparo_sugestao_importada"] = sugestoes[0]
            flash(
                "Dados extraídos da planilha. Revise com cuidado antes de salvar — a extração é "
                "automática e pode ter interpretado algo errado.",
                "warning",
            )
            return redirect(url_for("grupo.preparo_modelos_novo", grupo_id=grupo_id, de_importacao=1))

        # Cada aba é o preparo de um exame diferente — guarda todas na sessão
        # e deixa a pessoa escolher qual importar primeiro.
        session["grupo_preparo_xlsx_sugestoes"] = sugestoes
        return redirect(url_for("grupo.preparo_modelos_importar_xlsx_escolher", grupo_id=grupo_id))

    return render_template("grupo/preparo_modelo_importar_xlsx.html", grupo=grupo)


@grupo_bp.route("/<int:grupo_id>/preparo-modelos/importar-xlsx/escolher", methods=["GET", "POST"])
@login_required
def preparo_modelos_importar_xlsx_escolher(grupo_id):
    """Tela 5.1.12 — quando a planilha importada tem mais de uma aba (mais
    de um preparo), escolhe qual delas importar agora (as outras continuam
    disponíveis para importar depois, uma de cada vez)."""
    grupo = Grupo.query.get_or_404(grupo_id)
    if not grupo.membro_ativo(current_user.id):
        flash("Você não participa deste grupo.", "danger")
        return redirect(url_for("grupo.meus_grupos"))
    if current_user.tipo != "medico":
        flash("Somente usuários do tipo Médico podem importar modelos de preparo.", "danger")
        return redirect(url_for("grupo.preparo_modelos_lista", grupo_id=grupo_id))

    sugestoes = session.get("grupo_preparo_xlsx_sugestoes")
    if not sugestoes:
        flash("Nenhuma planilha importada ainda — envie o arquivo primeiro.", "danger")
        return redirect(url_for("grupo.preparo_modelos_importar_xlsx", grupo_id=grupo_id))

    if request.method == "POST":
        indice = request.form.get("indice", type=int)
        if indice is None or not (0 <= indice < len(sugestoes)):
            flash("Selecione uma aba válida.", "danger")
            return redirect(url_for("grupo.preparo_modelos_importar_xlsx_escolher", grupo_id=grupo_id))

        session["grupo_preparo_sugestao_importada"] = sugestoes[indice]
        flash(
            "Dados extraídos da planilha. Revise com cuidado antes de salvar — a extração é automática "
            "e pode ter interpretado algo errado.",
            "warning",
        )
        return redirect(url_for("grupo.preparo_modelos_novo", grupo_id=grupo_id, de_importacao=1))

    return render_template("grupo/preparo_modelo_importar_xlsx_escolher.html", grupo=grupo, sugestoes=sugestoes)


# ===================== Exame (tela 5.1.15/5.1.16) =====================

@grupo_bp.route("/<int:grupo_id>/exames")
@login_required
def exames_lista(grupo_id):
    """Tela 5.1.16 — Lista de exames do grupo."""
    grupo = Grupo.query.get_or_404(grupo_id)
    if not grupo.membro_ativo(current_user.id):
        flash("Você não participa deste grupo.", "danger")
        return redirect(url_for("grupo.meus_grupos"))

    exames = []
    if grupo.clinica_interna_id:
        exames = Exame.query.filter_by(clinica_id=grupo.clinica_interna_id).order_by(Exame.nome).all()
    return render_template("grupo/exames_lista.html", grupo=grupo, exames=exames)


@grupo_bp.route("/<int:grupo_id>/exames/novo", methods=["GET", "POST"])
@login_required
def exames_novo(grupo_id):
    """Tela 5.1.15 — Cadastro de exame, associado a um modelo de preparo
    do próprio médico. Disponível apenas para usuários do tipo Médico —
    "se o médico estiver no grupo de trabalho, os exames associados a ele
    aparecerão para agendamento de consulta" (BBP seção 4)."""
    grupo = Grupo.query.get_or_404(grupo_id)
    if not grupo.membro_ativo(current_user.id):
        flash("Você não participa deste grupo.", "danger")
        return redirect(url_for("grupo.meus_grupos"))
    if current_user.tipo != "medico":
        flash("Somente usuários do tipo Médico podem cadastrar exames.", "danger")
        return redirect(url_for("grupo.exames_lista", grupo_id=grupo_id))

    meus_modelos = []
    if grupo.clinica_interna_id:
        meus_modelos = PreparoModelo.query.filter_by(
            clinica_id=grupo.clinica_interna_id, criado_por_id=current_user.id
        ).order_by(PreparoModelo.nome).all()

    if request.method == "POST":
        if not meus_modelos:
            flash("Cadastre ao menos um modelo de preparo seu antes de cadastrar um exame (tela 5.1.13).", "danger")
            return redirect(url_for("grupo.preparo_modelos_novo", grupo_id=grupo_id))

        nome = (request.form.get("nome") or "").strip()
        preparo_modelo_id = request.form.get("preparo_modelo_id")
        modelo_escolhido = next((m for m in meus_modelos if str(m.id) == preparo_modelo_id), None)

        if not nome or not modelo_escolhido:
            flash("Informe o nome do exame e escolha um modelo de preparo seu.", "danger")
            return render_template("grupo/exame_form.html", grupo=grupo, meus_modelos=meus_modelos)

        clinica_interna = grupo.clinica_interna()
        if Exame.query.filter_by(clinica_id=clinica_interna.id, nome=nome).first():
            flash(f'Já existe um exame chamado "{nome}" neste grupo.', "danger")
            db.session.rollback()
            return render_template("grupo/exame_form.html", grupo=grupo, meus_modelos=meus_modelos)

        duracao = request.form.get("duracao_minutos") or None
        preco = request.form.get("preco") or None
        exame = Exame(
            clinica_id=clinica_interna.id, criado_por_id=current_user.id, medico_id=current_user.id,
            medico_confirmado=True, associado=True,
            nome=nome, descricao=(request.form.get("descricao") or "").strip() or None,
            preparo_modelo_id=modelo_escolhido.id,
            duracao_minutos=int(duracao) if duracao else None,
            preco=preco, precisa_acompanhante=bool(request.form.get("precisa_acompanhante")),
        )
        db.session.add(exame)
        db.session.commit()
        flash(f'Exame "{nome}" cadastrado com sucesso.', "success")
        return redirect(url_for("grupo.exames_lista", grupo_id=grupo_id))

    return render_template("grupo/exame_form.html", grupo=grupo, meus_modelos=meus_modelos)


# ===================== Agendamento de consulta (tela 5.1.17-5.1.19) =====================
#
# O cálculo do cronograma de preparo (cortes de alimentação/líquido,
# medicamentos a suspender, alimentos proibidos, exames anteriores etc.) já
# é feito automaticamente pelos próprios modelos (ver PreparoCorte.limite e
# equivalentes em app/models.py) a partir de Agendamento.data_hora — nenhum
# cálculo novo precisa ser feito aqui: basta criar o Agendamento vinculado
# ao exame certo que o cronograma "aparece pronto" tanto para a equipe
# (grupo.agenda_detalhe) quanto para o próprio paciente (a tela já existente
# paciente.preparo_exame, que não precisou de nenhuma mudança).

@grupo_bp.route("/<int:grupo_id>/agenda")
@login_required
def agenda_lista(grupo_id):
    """Tela 5.1.19 — Agenda de consultas do grupo."""
    grupo = Grupo.query.get_or_404(grupo_id)
    if not grupo.membro_ativo(current_user.id):
        flash("Você não participa deste grupo.", "danger")
        return redirect(url_for("grupo.meus_grupos"))

    agendamentos = []
    if grupo.clinica_interna_id:
        agendamentos = (
            Agendamento.query.filter_by(clinica_id=grupo.clinica_interna_id)
            .order_by(Agendamento.data_hora).all()
        )
    return render_template("grupo/agenda_lista.html", grupo=grupo, agendamentos=agendamentos)


@grupo_bp.route("/<int:grupo_id>/agenda/novo", methods=["GET", "POST"])
@login_required
def agenda_novo(grupo_id):
    """Tela 5.1.17/5.1.18 — Agendar uma consulta para um paciente do grupo,
    escolhendo um dos exames cadastrados no grupo. Qualquer membro ativo do
    grupo (médico ou secretaria) pode agendar — é um trabalho compartilhado."""
    grupo = Grupo.query.get_or_404(grupo_id)
    if not grupo.membro_ativo(current_user.id):
        flash("Você não participa deste grupo.", "danger")
        return redirect(url_for("grupo.meus_grupos"))

    exames = []
    if grupo.clinica_interna_id:
        exames = Exame.query.filter_by(clinica_id=grupo.clinica_interna_id).order_by(Exame.nome).all()
    pacientes = [
        v.paciente for v in
        GrupoPaciente.query.filter_by(grupo_id=grupo_id).join(Paciente).order_by(Paciente.nome).all()
    ]

    if request.method == "POST":
        paciente_id = request.form.get("paciente_id", type=int)
        exame_id = request.form.get("exame_id", type=int)
        data_hora_str = request.form.get("data_hora")

        paciente = next((p for p in pacientes if p.id == paciente_id), None)
        exame = next((e for e in exames if e.id == exame_id), None)
        if not paciente or not exame:
            flash("Escolha um paciente e um exame válidos deste grupo.", "danger")
            return render_template("grupo/agenda_form.html", grupo=grupo, pacientes=pacientes, exames=exames)

        try:
            data_hora = datetime.strptime(data_hora_str, "%Y-%m-%dT%H:%M")
        except (ValueError, TypeError):
            flash("Escolha uma data/hora válida para a consulta.", "danger")
            return render_template("grupo/agenda_form.html", grupo=grupo, pacientes=pacientes, exames=exames)

        agendamento = Agendamento(
            clinica_id=grupo.clinica_interna_id,
            paciente_id=paciente.id,
            exame_id=exame.id,
            medico_id=exame.medico_id,
            data_hora=data_hora,
            status="agendado",
        )
        db.session.add(agendamento)
        db.session.commit()
        flash(
            f"Consulta de {paciente.nome} agendada para {data_hora.strftime('%d/%m/%Y às %H:%M')} — "
            "o cronograma de preparo foi calculado automaticamente a partir deste horário.",
            "success",
        )
        return redirect(url_for("grupo.agenda_lista", grupo_id=grupo_id))

    return render_template("grupo/agenda_form.html", grupo=grupo, pacientes=pacientes, exames=exames)


@grupo_bp.route("/<int:grupo_id>/agenda/<int:agendamento_id>")
@login_required
def agenda_detalhe(grupo_id, agendamento_id):
    """Detalhe da consulta agendada, com o cronograma de preparo já
    calculado (mesma lógica usada na tela do paciente, sem duplicação)."""
    grupo = Grupo.query.get_or_404(grupo_id)
    if not grupo.membro_ativo(current_user.id):
        flash("Você não participa deste grupo.", "danger")
        return redirect(url_for("grupo.meus_grupos"))
    agendamento = Agendamento.query.filter_by(id=agendamento_id, clinica_id=grupo.clinica_interna_id).first_or_404()
    return render_template("grupo/agenda_detalhe.html", grupo=grupo, agendamento=agendamento)


# ===================== Perguntas dos pacientes (aprovação da IA) =====================
#
# BBP seção 8, decisão nº 5: quando um exame tem mais de um médico vinculado
# (Exame.medico_id + Exame.medicos_extra), QUALQUER um deles pode aprovar a
# resposta rascunhada pela IA antes dela ir para o paciente — essa regra já
# está pronta em Exame.medico_pode_atender (usada por
# _restringir_perguntas_para_medico, importada de app.routes_medico) e não
# precisou de nenhuma mudança; só faltava uma tela que buscasse as perguntas
# ancoradas na clínica interna do grupo, que a tela da equipe (que só olha
# as clínicas em que o usuário tem vínculo formal) nunca alcança.

@grupo_bp.route("/<int:grupo_id>/perguntas")
@login_required
def perguntas_pendentes(grupo_id):
    grupo = Grupo.query.get_or_404(grupo_id)
    if not grupo.membro_ativo(current_user.id):
        flash("Você não participa deste grupo.", "danger")
        return redirect(url_for("grupo.meus_grupos"))

    pendentes, aguardando, respondidas = [], [], []
    if grupo.clinica_interna_id:
        pendentes_q = PerguntaPendente.query.filter_by(clinica_id=grupo.clinica_interna_id, status="pendente")
        aguardando_q = PerguntaPendente.query.filter_by(clinica_id=grupo.clinica_interna_id, status="aguardando_aprovacao")
        respondidas_q = PerguntaPendente.query.filter_by(clinica_id=grupo.clinica_interna_id, status="respondida")

        # Mesma regra da equipe: um médico só acompanha perguntas dos seus
        # próprios exames (principal ou "extra"), mais as gerais (sem exame)
        # se também administrar pacientes — uma secretaria do grupo vê tudo.
        if current_user.tipo == "medico":
            pendentes_q = _restringir_perguntas_para_medico(pendentes_q)
            aguardando_q = _restringir_perguntas_para_medico(aguardando_q)
            respondidas_q = _restringir_perguntas_para_medico(respondidas_q)

        pendentes = pendentes_q.order_by(PerguntaPendente.criado_em.desc()).all()
        aguardando = aguardando_q.order_by(PerguntaPendente.criado_em.desc()).all()
        respondidas = respondidas_q.order_by(PerguntaPendente.respondida_em.desc()).limit(20).all()

    return render_template(
        "grupo/perguntas.html", grupo=grupo, pendentes=pendentes, aguardando=aguardando, respondidas=respondidas,
    )


@grupo_bp.route("/<int:grupo_id>/perguntas/<int:pergunta_id>/responder", methods=["POST"])
@login_required
def perguntas_responder(grupo_id, pergunta_id):
    grupo = Grupo.query.get_or_404(grupo_id)
    if not grupo.membro_ativo(current_user.id):
        flash("Você não participa deste grupo.", "danger")
        return redirect(url_for("grupo.meus_grupos"))

    pergunta = PerguntaPendente.query.filter_by(id=pergunta_id, clinica_id=grupo.clinica_interna_id).first_or_404()

    # Qualquer médico vinculado ao exame (principal ou extra) pode aprovar —
    # ver Exame.medico_pode_atender. Uma secretaria do grupo com permissão
    # de administrar pacientes também pode responder perguntas gerais (sem
    # exame associado).
    if current_user.tipo == "medico":
        exame_proprio = pergunta.exame is not None and pergunta.exame.medico_pode_atender(current_user.id)
        geral_administravel = pergunta.exame is None and current_user.perm_pacientes
        if not exame_proprio and not geral_administravel:
            flash("Você só pode responder perguntas sobre os seus próprios exames.", "danger")
            return redirect(url_for("grupo.perguntas_pendentes", grupo_id=grupo_id))

    resposta = (request.form.get("resposta") or "").strip()
    if not resposta:
        flash("Digite uma resposta antes de salvar.", "danger")
        return redirect(url_for("grupo.perguntas_pendentes", grupo_id=grupo_id))

    pergunta.resposta = resposta
    pergunta.status = "respondida"
    pergunta.respondida_por = current_user.nome
    pergunta.respondida_em = datetime.utcnow()

    # "Aprendizado": a pergunta+resposta entra na base de FAQ do grupo para uso futuro.
    db.session.add(FaqItem(
        clinica_id=pergunta.clinica_id,
        exame_id=pergunta.exame_id,
        pergunta=pergunta.pergunta,
        resposta=resposta,
        criado_por=current_user.nome,
    ))
    db.session.commit()

    flash("Resposta salva e adicionada à base de conhecimento da IA.", "success")
    return redirect(url_for("grupo.perguntas_pendentes", grupo_id=grupo_id))
