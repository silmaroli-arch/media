import os
from datetime import datetime
from functools import wraps

from flask import (
    Blueprint, render_template, redirect, url_for, request, flash, send_from_directory,
    current_app, abort, session,
)
from flask_login import login_required, current_user, logout_user
from sqlalchemy import or_

from app.extensions import db
from app.models import Agendamento, Exame, PerguntaPendente, FaqItem, ChatMensagem, Usuario, MedicoHorario, Paciente, normalizar_telefone, encontrar_conta_paciente, formatar_nome_proprio, cep_incompleto, telefone_incompleto
from app.faq_engine import buscar_resposta, buscar_resposta_alimento, buscar_resposta_medicamento
from app.ia_preparo import responder_com_ia
from app.clinica_utils import verificar_vencimento_empresa
from app.agendamento_otimizador import sugerir_horarios

paciente_bp = Blueprint("paciente", __name__, url_prefix="/paciente")


def _exames_da_empresa(paciente):
    """Exames disponíveis para o paciente: os de TODAS as filiais da
    empresa dele (o paciente é da empresa, não de uma filial - a filial
    do atendimento é escolhida na hora de marcar, através do exame, que é
    por filial)."""
    empresa = paciente.empresa_efetiva
    if not empresa:
        return []
    filial_ids = [c.id for c in empresa.filiais]
    return (
        # Só exames ASSOCIADOS (exame + filial + médico + preço definidos
        # na tela "Associar exames") são ofertados ao paciente - item de
        # catálogo sem associação não aparece.
        Exame.query.filter(Exame.clinica_id.in_(filial_ids), Exame.associado.is_(True))
        .order_by(Exame.nome)
        .all()
    )


def _clinica_ancora(paciente, exame=None, agendamento=None):
    """Filial usada para ROTEAR registros do paciente que ainda são por
    filial no banco (PerguntaPendente/FAQ): a do agendamento em questão,
    senão a do exame em questão, senão a do agendamento mais recente do
    paciente, senão a primeira filial da empresa. É só um endereçamento
    para a equipe certa ver a pergunta - não é um vínculo do paciente."""
    if agendamento is not None:
        return agendamento.clinica_id
    if exame is not None:
        return exame.clinica_id
    ultimo = (
        Agendamento.query.filter_by(paciente_id=paciente.id)
        .order_by(Agendamento.data_hora.desc())
        .first()
    )
    if ultimo:
        return ultimo.clinica_id
    empresa = paciente.empresa_efetiva
    if empresa and empresa.filiais:
        return empresa.filiais[0].id
    return paciente.clinica_id


def _meus_cadastros_ids():
    """Ids de TODOS os cadastros (Paciente) da conta logada - um por
    empresa que a pessoa frequenta (conta única, ver
    encontrar_conta_paciente em app/models.py). A área do paciente é
    UNIFICADA: agendamentos/preparos/resultados de todas as clínicas
    aparecem juntos pro paciente (é tudo dado DELE); só as ações
    endereçadas a uma clínica específica (solicitar agendamento, tirar
    dúvida) usam o cadastro ativo da sessão (current_user.paciente)."""
    return [p.id for p in current_user.pacientes]


def paciente_required(f):
    @wraps(f)
    def decorado(*args, **kwargs):
        if not current_user.is_authenticated or current_user.tipo != "paciente":
            flash("Acesso restrito a pacientes.", "danger")
            return redirect(url_for("auth.login"))

        paciente = current_user.paciente
        empresa = paciente.empresa_efetiva if paciente else None
        if empresa:
            verificar_vencimento_empresa(empresa)
        if empresa and empresa.bloqueada:
            # Precisa deslogar de verdade aqui — senão a pessoa continua
            # autenticada e cai num loop (auth.login manda de volta pra
            # index, que manda de volta pra esta view).
            logout_user()
            flash(
                "O acesso da sua clínica à plataforma está temporariamente indisponível. "
                "Entre em contato com a clínica para mais informações.",
                "danger",
            )
            return redirect(url_for("auth.login"))

        return f(*args, **kwargs)
    return decorado


@paciente_bp.route("/")
@login_required
@paciente_required
def dashboard():
    # Painel UNIFICADO: mostra os exames do paciente em TODAS as clínicas
    # que ele frequenta (todos os cadastros da conta), com o local
    # identificado em cada um - os dados são do próprio paciente, então
    # ele vê tudo junto; cada clínica continua vendo só o que é dela.
    meus_ids = _meus_cadastros_ids()
    agora = datetime.utcnow()
    # "Próximos exames" só mostra o que ainda está de pé e no futuro.
    # Cancelado ou realizado vai para o Histórico independente da data —
    # mesmo um agendamento cancelado que estava marcado para o futuro não
    # é mais um "próximo exame" de verdade.
    proximos = (
        Agendamento.query.filter(Agendamento.paciente_id.in_(meus_ids))
        .filter(Agendamento.data_hora >= agora)
        .filter(Agendamento.status.in_(["solicitado", "agendado", "confirmado"]))
        .order_by(Agendamento.data_hora.asc())
        .all()
    )
    historico = (
        Agendamento.query.filter(Agendamento.paciente_id.in_(meus_ids))
        .filter(or_(Agendamento.status.in_(["cancelado", "realizado", "nao_compareceu"]), Agendamento.data_hora < agora))
        .order_by(Agendamento.data_hora.desc())
        .all()
    )
    return render_template(
        "paciente/dashboard.html", proximos=proximos, historico=historico,
        cadastros=current_user.pacientes,
    )


@paciente_bp.route("/trocar-clinica", methods=["POST"])
@login_required
@paciente_required
def trocar_clinica():
    """Troca o cadastro (clínica) ATIVO da sessão - usado pelas ações que
    são endereçadas a uma clínica específica (solicitar agendamento,
    tirar dúvida). Só aceita um cadastro que pertença à própria conta."""
    paciente_id = request.form.get("paciente_id", type=int)
    if paciente_id in _meus_cadastros_ids():
        session["paciente_id"] = paciente_id
        p = Paciente.query.get(paciente_id)
        empresa = p.empresa_efetiva
        flash(f"Agora você está usando o app como paciente de '{empresa.nome if empresa else p.clinica.nome}'.", "success")
    else:
        flash("Escolha inválida.", "danger")
    return redirect(request.form.get("proxima") or url_for("paciente.dashboard"))


@paciente_bp.route("/exame/<int:agendamento_id>")
@login_required
@paciente_required
def preparo_exame(agendamento_id):
    # Qualquer agendamento da CONTA (todas as clínicas) - é dado do paciente.
    agendamento = Agendamento.query.filter(
        Agendamento.id == agendamento_id, Agendamento.paciente_id.in_(_meus_cadastros_ids())
    ).first_or_404()
    return render_template("paciente/preparo.html", agendamento=agendamento)


@paciente_bp.route("/chat", methods=["GET", "POST"])
@login_required
@paciente_required
def chat():
    paciente = current_user.paciente
    # O seletor "sobre qual exame" só mostra exames agendados ou
    # confirmados — solicitado ainda não é certo, e cancelado/realizado
    # não fazem mais sentido como opção pra tirar dúvida sobre o preparo.
    agendamentos = (
        Agendamento.query.filter_by(paciente_id=paciente.id)
        .filter(Agendamento.status.in_(["agendado", "confirmado"]))
        .order_by(Agendamento.data_hora.desc())
        .all()
    )

    resposta_ia = None
    pergunta_enviada = None
    encaminhada = False
    origem = None
    agendamento_id_selecionado = request.form.get("agendamento_id") or (agendamentos[0].id if agendamentos else None)
    if agendamento_id_selecionado:
        agendamento_id_selecionado = int(agendamento_id_selecionado)

    if request.method == "POST":
        pergunta_enviada = request.form.get("pergunta", "").strip()
        agendamento_id_form = request.form.get("agendamento_id") or None
        exame_id_form = request.form.get("exame_id") or None  # compat: clientes antigos ainda mandam só isso

        # O seletor da tela (novo) guarda o agendamento específico, não só
        # o tipo de exame — assim dá pra vincular a pergunta a exatamente
        # qual consulta ela é sobre (ver ChatMensagem.agendamento_id),
        # mesmo quando o paciente tem mais de um agendamento do mesmo
        # exame.
        agendamento_selecionado = (
            Agendamento.query.filter_by(id=int(agendamento_id_form), paciente_id=paciente.id).first()
            if agendamento_id_form else None
        )
        exame_selecionado = agendamento_selecionado.exame if agendamento_selecionado else None

        if not agendamento_selecionado and exame_id_form:
            # Compat com quem ainda manda só "exame_id" — resolve o exame
            # normalmente e tenta achar, de forma best-effort, o
            # agendamento mais recente deste paciente para esse exame, só
            # pra ainda conseguir vincular a pergunta quando der.
            exame_selecionado = Exame.query.get(int(exame_id_form))
            if exame_selecionado:
                agendamento_selecionado = (
                    Agendamento.query.filter_by(paciente_id=paciente.id, exame_id=exame_selecionado.id)
                    .order_by(Agendamento.data_hora.desc())
                    .first()
                )

        exame_id_selecionado = exame_selecionado.id if exame_selecionado else None
        agendamento_id_selecionado = agendamento_selecionado.id if agendamento_selecionado else None

        if pergunta_enviada:

            # A IA (quando configurada — ver app.ia_preparo) é SEMPRE
            # consultada primeiro, e não a base de conhecimento (FAQ) — ela
            # interpreta o preparo com mais flexibilidade do que a
            # correspondência por palavra-chave abaixo. A resposta da IA,
            # porém, NÃO vai direto para o paciente: fica como um rascunho
            # (PerguntaPendente com status "aguardando_aprovacao") até o
            # médico revisar, editar se precisar, e aprovar — só nesse
            # momento ela é mostrada ao paciente e gravada na base de
            # conhecimento (FaqItem), igual a uma resposta manual. Cada
            # pergunta continua sendo encaminhada à IA de novo, mesmo que
            # pareça repetida — não há atalho pela FAQ aqui.
            resultado_ia = responder_com_ia(pergunta_enviada, exame_selecionado) if exame_selecionado else None

            if resultado_ia and resultado_ia["final"]:
                origem = "ia_aguardando"
                pendente = PerguntaPendente(
                    clinica_id=_clinica_ancora(paciente, exame_selecionado, agendamento_selecionado),
                    paciente_id=paciente.id,
                    exame_id=exame_selecionado.id,
                    pergunta=pergunta_enviada,
                    status="aguardando_aprovacao",
                    resposta_sugerida_ia=resultado_ia["final"],
                    # Guardadas à parte para a tela de aprovação mostrar a
                    # resposta de cada IA lado a lado, além da junção
                    # (ver medico/perguntas.html).
                    resposta_bruta_claude=resultado_ia["claude"],
                    resposta_bruta_chatgpt=resultado_ia["chatgpt"],
                )
                db.session.add(pendente)
                db.session.commit()
                # Mesma mensagem de "aguarde" usada quando ninguém sabe
                # responder ainda — o paciente só vê a resposta final depois
                # que o médico aprovar (ela aparece no histórico abaixo).
                encaminhada = True
            else:
                # A IA não respondeu (não está configurada, deu erro, ou não
                # há exame selecionado para dar contexto ao preparo) — só
                # nesse caso a base de conhecimento e as respostas prontas
                # de alimento/medicamento entram como alternativa.
                faq_item, score = buscar_resposta(
                    pergunta_enviada,
                    clinica_id=_clinica_ancora(paciente, exame_selecionado, agendamento_selecionado),
                    exame_id=int(exame_id_selecionado) if exame_id_selecionado else None,
                )
                if faq_item:
                    faq_item.vezes_utilizada += 1
                    db.session.commit()
                    resposta_ia = faq_item.resposta
                    origem = "faq"
                elif exame_selecionado and (resposta_alimento := buscar_resposta_alimento(
                    pergunta_enviada, exame_selecionado, paciente
                )):
                    resposta_ia = resposta_alimento
                    origem = "alimento"
                elif exame_selecionado and (resposta_medicamento := buscar_resposta_medicamento(
                    pergunta_enviada, exame_selecionado, paciente
                )):
                    resposta_ia = resposta_medicamento
                    origem = "medicamento"
                else:
                    pendente = PerguntaPendente(
                        clinica_id=_clinica_ancora(paciente, exame_selecionado, agendamento_selecionado),
                        paciente_id=paciente.id,
                        exame_id=exame_id_selecionado,
                        pergunta=pergunta_enviada,
                    )
                    db.session.add(pendente)
                    db.session.commit()
                    encaminhada = True
                    origem = "pendente"

            # Registra a pergunta+resposta no histórico do paciente — o
            # médico consegue ver essas dúvidas ao iniciar o atendimento
            # (ver medico.atendimento), já vinculadas ao agendamento certo.
            db.session.add(ChatMensagem(
                paciente_id=paciente.id,
                exame_id=exame_selecionado.id if exame_selecionado else None,
                agendamento_id=agendamento_selecionado.id if agendamento_selecionado else None,
                pergunta=pergunta_enviada,
                resposta=resposta_ia,
                origem=origem,
            ))
            db.session.commit()

    historico_pendentes = (
        PerguntaPendente.query.filter_by(paciente_id=paciente.id)
        .order_by(PerguntaPendente.criado_em.desc())
        .all()
    )

    return render_template(
        "paciente/chat.html",
        agendamentos=agendamentos,
        resposta_ia=resposta_ia,
        pergunta_enviada=pergunta_enviada,
        encaminhada=encaminhada,
        origem=origem,
        agendamento_id_selecionado=agendamento_id_selecionado,
        historico_pendentes=historico_pendentes,
    )


@paciente_bp.route("/perguntas/<int:pergunta_id>/remover", methods=["POST"])
@login_required
@paciente_required
def pergunta_remover(pergunta_id):
    paciente = current_user.paciente
    # Só remove se a pergunta for realmente do próprio paciente logado —
    # evita que alguém apague pergunta de outro paciente forjando o id na URL.
    pendente = PerguntaPendente.query.filter_by(id=pergunta_id, paciente_id=paciente.id).first_or_404()
    db.session.delete(pendente)
    db.session.commit()
    flash("Pergunta removida.", "success")
    return redirect(url_for("paciente.chat"))


# ---------- Solicitação de agendamento pelo próprio paciente ----------

@paciente_bp.route("/agendar", methods=["GET", "POST"])
@login_required
@paciente_required
def solicitar_agendamento():
    paciente = current_user.paciente
    if paciente.status_cadastro != "aprovado":
        # Conta única: se o cadastro ATIVO ainda não foi aprovado mas
        # outro cadastro da conta já foi, troca pra ele automaticamente
        # em vez de barrar a pessoa.
        aprovado = next((p for p in current_user.pacientes if p.status_cadastro == "aprovado"), None)
        if aprovado:
            session["paciente_id"] = aprovado.id
            paciente = aprovado
        else:
            flash(
                "Seu cadastro ainda está em análise pela clínica — assim que for aceito, "
                "você poderá solicitar agendamento de exames.",
                "warning",
            )
            return redirect(url_for("paciente.dashboard"))
    # Fluxo em etapas: o paciente escolhe primeiro O EXAME (só o nome,
    # sem local concatenado), depois EM QUAL LOCAL quer fazê-lo (só os
    # locais que oferecem aquele exame aparecem), e aí vê o endereço do
    # local, os médicos e os horários. Se o exame só é feito num local, o
    # local é selecionado direto.
    exames = _exames_da_empresa(paciente)
    nomes_exames = sorted({e.nome for e in exames}, key=str.lower)

    exame_id = request.form.get("exame_id", type=int) or request.args.get("exame_id", type=int)
    exame_selecionado = next((e for e in exames if e.id == exame_id), None) if exame_id else None

    exame_nome = (
        (request.form.get("exame_nome") or request.args.get("exame_nome") or "").strip()
        or (exame_selecionado.nome if exame_selecionado else "")
    )
    if exame_nome not in nomes_exames:
        exame_nome = ""

    # Locais em que ESTE exame é oferecido (uma associação por local).
    opcoes_locais = [e for e in exames if e.nome == exame_nome] if exame_nome else []

    # Trocou o exame no primeiro dropdown? A escolha antiga de local não
    # vale mais.
    if exame_selecionado and exame_selecionado.nome != exame_nome:
        exame_selecionado = None
    # Um local só oferece o exame -> seleciona direto, sem pedir mais um clique.
    if not exame_selecionado and len(opcoes_locais) == 1:
        exame_selecionado = opcoes_locais[0]

    medicos_disponiveis = []
    medico_selecionado = None
    if exame_selecionado:
        # Um exame pode ter mais de um médico associado (médico principal +
        # médicos "extra") — o paciente escolhe com qual deles prefere
        # agendar; sem escolha explícita, cai no médico principal.
        medicos_disponiveis = exame_selecionado.medicos
        medico_id_escolhido = request.form.get("medico_id", type=int) or request.args.get("medico_id", type=int)
        medico_selecionado = next(
            (m for m in medicos_disponiveis if m.id == medico_id_escolhido), None
        ) or exame_selecionado.medico

    sugestoes = []
    if exame_selecionado and medico_selecionado:
        sugestoes = sugerir_horarios(
            exame_selecionado, medico_selecionado, exame_selecionado.clinica,
        )

    if request.method == "POST":
        horario_escolhido = request.form.get("horario_escolhido")
        if not exame_selecionado or not medico_selecionado:
            flash("Escolha um exame e um médico válidos.", "danger")
        elif not horario_escolhido:
            flash("Escolha um dos horários sugeridos.", "danger")
        else:
            try:
                data_hora = datetime.strptime(horario_escolhido, "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                flash("Horário inválido — escolha novamente um dos horários sugeridos.", "danger")
                return redirect(url_for(
                    "paciente.solicitar_agendamento",
                    exame_id=exame_selecionado.id, medico_id=medico_selecionado.id,
                ))

            agendamento = Agendamento(
                # A filial do agendamento é a filial DO EXAME escolhido -
                # o paciente é da empresa, não de uma filial fixa.
                clinica_id=exame_selecionado.clinica_id,
                paciente_id=paciente.id,
                exame_id=exame_selecionado.id,
                medico_id=medico_selecionado.id,
                data_hora=data_hora,
                status="solicitado",
            )
            db.session.add(agendamento)
            db.session.commit()
            flash(
                "Solicitação de agendamento enviada! A clínica vai confirmar o horário em breve — "
                "você pode acompanhar o status pelo seu painel.",
                "success",
            )
            return redirect(url_for("paciente.dashboard"))

    return render_template(
        "paciente/solicitar_agendamento.html",
        exames=exames,
        nomes_exames=nomes_exames,
        exame_nome=exame_nome,
        opcoes_locais=opcoes_locais,
        medicos_disponiveis=medicos_disponiveis,
        medico_selecionado=medico_selecionado,
        exame_selecionado=exame_selecionado,
        sugestoes=sugestoes,
    )


# ---------- Meus dados (o próprio paciente atualiza, vale em todas as clínicas) ----------

@paciente_bp.route("/meus-dados", methods=["GET", "POST"])
@login_required
@paciente_required
def meus_dados():
    """O paciente edita os PRÓPRIOS dados de contato - telefone, e-mail,
    endereço e contato de emergência - e a mudança vale para TODOS os
    cadastros da conta (todas as clínicas que ele frequenta): são dados
    da pessoa, não da relação com uma clínica. Nome, CPF e data de
    nascimento NÃO são editáveis aqui - são a identidade verificada pela
    clínica (e nascimento é credencial de login); mudança neles é feita
    pela equipe da clínica. O login é por CPF + data de
    nascimento, então trocar telefone/e-mail não afeta o acesso."""
    paciente = current_user.paciente

    if request.method == "POST":
        telefone_digitado = request.form.get("telefone", "").strip()
        telefone = normalizar_telefone(telefone_digitado)
        email = request.form.get("email", "").strip().lower()

        if not telefone:
            flash("O telefone é obrigatório — é o contato que as clínicas usam com você.", "danger")
            return redirect(url_for("paciente.meus_dados"))

        # Telefone incompleto (ex.: "(27" digitado e enviado sem terminar)
        # não travava o envio - a máscara só formata o que foi digitado,
        # não garante que a pessoa terminou de digitar.
        if telefone_incompleto(telefone_digitado):
            flash("Telefone incompleto — digite o DDD e o número completos.", "danger")
            return redirect(url_for("paciente.meus_dados"))
        if telefone_incompleto(request.form.get("contato_emergencia_telefone", "")):
            flash("Telefone do contato de emergência incompleto — digite o DDD e o número completos.", "danger")
            return redirect(url_for("paciente.meus_dados"))

        # CEP incompleto (ex.: "29055") não bloqueava o envio e ficava
        # salvo pela metade, com rua/bairro/cidade/UF vazios.
        if cep_incompleto(request.form.get("cep", "")):
            flash("CEP incompleto — digite os 8 números.", "danger")
            return redirect(url_for("paciente.meus_dados"))

        # Aplica na CONTA (login) e em TODOS os cadastros (todas as clínicas).
        current_user.telefone = telefone
        current_user.email = email or None
        for p in current_user.pacientes:
            p.telefone = telefone
            p.email = email or None
            p.cep = request.form.get("cep", "").strip()
            p.rua = request.form.get("rua", "").strip()
            p.numero = request.form.get("numero", "").strip()
            p.complemento = request.form.get("complemento", "").strip()
            p.bairro = request.form.get("bairro", "").strip()
            p.cidade = request.form.get("cidade", "").strip()
            p.uf = request.form.get("uf", "").strip().upper() or None
            p.contato_emergencia_nome = formatar_nome_proprio(request.form.get("contato_emergencia_nome", ""))
            p.contato_emergencia_telefone = request.form.get("contato_emergencia_telefone", "").strip()
        db.session.commit()

        # O telefone deixou de ser credencial (o login é CPF + data de
        # nascimento) - trocar o telefone não afeta o acesso.
        flash("Seus dados foram atualizados em todas as clínicas que você frequenta.", "success")
        return redirect(url_for("paciente.meus_dados"))

    return render_template("paciente/meus_dados.html", paciente=paciente)


# ---------- Resultado de exame (download do PDF anexado pela clínica) ----------

@paciente_bp.route("/exame/<int:agendamento_id>/resultado")
@login_required
@paciente_required
def resultado_baixar(agendamento_id):
    # Qualquer resultado da CONTA (todas as clínicas) - é dado do paciente.
    agendamento = Agendamento.query.filter(
        Agendamento.id == agendamento_id, Agendamento.paciente_id.in_(_meus_cadastros_ids())
    ).first_or_404()
    if not agendamento.resultado:
        abort(404)
    pasta = os.path.join(current_app.instance_path, "resultados_exame")
    return send_from_directory(
        pasta, agendamento.resultado.caminho_arquivo,
        as_attachment=True, download_name=agendamento.resultado.nome_arquivo,
    )
