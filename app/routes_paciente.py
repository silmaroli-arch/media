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
from app.models import Agendamento, Exame, PerguntaPendente, ChatMensagem, Paciente, GrupoPaciente, normalizar_telefone, formatar_nome_proprio, cep_incompleto, telefone_incompleto
from app.faq_engine import buscar_resposta, buscar_resposta_alimento, buscar_resposta_medicamento
from app.ia_preparo import responder_com_ia
from app.clinica_utils import verificar_vencimento_grupo
from app.push_notificacoes import notificar_equipe_nova_pergunta

paciente_bp = Blueprint("paciente", __name__, url_prefix="/paciente")


def _resolver_ancora(paciente, exame=None, agendamento=None):
    """(grupo_id, criado_por_id) usado para ROTEAR PerguntaPendente/FAQ: o
    do agendamento em questão, senão o do exame em questão, senão o do
    agendamento mais recente do paciente, senão a primeira associação
    (GrupoPaciente) do próprio paciente. É só um endereçamento para a
    equipe certa ver a pergunta - não é um vínculo de acesso do paciente.

    Fatia 6: quando não há Grupo nenhum envolvido (agendamento/exame de uma
    conta solo), o endereçamento passa a ser pelo dono pessoal
    (`criado_por_id` de quem cadastrou o exame/agendamento) em vez de por
    Grupo - só assim a pergunta chega até quem vai respondê-la."""
    grupo_id = None
    criado_por_id = None
    if agendamento is not None:
        grupo_id = agendamento.grupo_id
        criado_por_id = agendamento.criado_por_id
    elif exame is not None:
        grupo_id = exame.grupo_id
        criado_por_id = exame.criado_por_id
    else:
        ultimo = (
            Agendamento.query.filter_by(paciente_id=paciente.id)
            .order_by(Agendamento.data_hora.desc())
            .first()
        )
        if ultimo:
            grupo_id = ultimo.grupo_id
            criado_por_id = ultimo.criado_por_id

    if grupo_id is None and criado_por_id is None:
        grupos = _grupos_do_paciente(paciente)
        if grupos:
            grupo_id = grupos[0].id
        else:
            # Cadastro pessoal (sem Grupo nenhum) de uma conta solo - o
            # dono do cadastro (ver Paciente.cadastrado_por_id) é quem
            # deve receber a pergunta.
            criado_por_id = paciente.cadastrado_por_id

    return grupo_id, criado_por_id


def _meus_cadastros_ids():
    """Ids de TODOS os cadastros (Paciente) da conta logada - um por
    empresa que a pessoa frequenta (conta única, ver
    encontrar_conta_paciente em app/models.py). A área do paciente é
    UNIFICADA: agendamentos/preparos/resultados de todas as clínicas
    aparecem juntos pro paciente (é tudo dado DELE); só as ações
    endereçadas a uma clínica específica (solicitar agendamento, tirar
    dúvida) usam o cadastro ativo da sessão (current_user.paciente)."""
    return [p.id for p in current_user.pacientes]


def _grupos_do_paciente(paciente):
    """Grupos aos quais este cadastro (Paciente) está associado - ver
    GrupoPaciente em app/models.py. Fatia 5: cadastros criados a partir de
    agora são globais (empresa_id vazio) e a associação com cada clínica é
    só por aqui - é a partir daqui que o bloqueio por inadimplência
    (Grupo.bloqueada) é checado para esses cadastros (ver
    paciente_required abaixo)."""
    return [gp.grupo for gp in GrupoPaciente.query.filter_by(paciente_id=paciente.id).all()]


def paciente_required(f):
    @wraps(f)
    def decorado(*args, **kwargs):
        if not current_user.is_authenticated or current_user.tipo != "paciente":
            flash("Acesso restrito a pacientes.", "danger")
            return redirect(url_for("auth.login"))

        paciente = current_user.paciente
        bloqueado = False
        if paciente:
            # O bloqueio por inadimplência é checado nos Grupos associados
            # (GrupoPaciente). Basta UM grupo bloqueado pra barrar o
            # acesso (mesmo critério conservador de sempre: qualquer
            # clínica/grupo em atraso barra o app pra aquela conta).
            grupos = _grupos_do_paciente(paciente)
            for g in grupos:
                verificar_vencimento_grupo(g)
            bloqueado = any(g.bloqueada for g in grupos)

        if bloqueado:
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
    # "Próximos exames" mostra o que ainda está no futuro e não foi
    # encerrado; o resto (passado ou já encerrado pelo médico) vai para o
    # Histórico.
    proximos = (
        Agendamento.query.filter(Agendamento.paciente_id.in_(meus_ids))
        .filter(Agendamento.data_hora >= agora)
        .filter(Agendamento.encerrado_em.is_(None))
        .order_by(Agendamento.data_hora.asc())
        .all()
    )
    historico = (
        Agendamento.query.filter(Agendamento.paciente_id.in_(meus_ids))
        .filter(or_(Agendamento.encerrado_em.isnot(None), Agendamento.data_hora < agora))
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
        # Identifica pelo(s) Grupo(s) associados (GrupoPaciente) - um
        # cadastro pode estar em mais de um grupo ao mesmo tempo; usa o
        # primeiro pra compor a mensagem, com o nome do cadastro como
        # fallback caso não haja nenhuma associação ainda.
        grupos = _grupos_do_paciente(p)
        nome_contexto = grupos[0].nome if grupos else p.nome
        flash(f"Agora você está usando o app como paciente de '{nome_contexto}'.", "success")
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
    # O seletor "sobre qual exame" mostra os agendamentos ainda não
    # encerrados pelo médico.
    agendamentos = (
        Agendamento.query.filter_by(paciente_id=paciente.id)
        .filter(Agendamento.encerrado_em.is_(None))
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
            resultado_ia = (
                responder_com_ia(pergunta_enviada, exame_selecionado, paciente_id=paciente.id)
                if exame_selecionado else None
            )
            grupo_id_ancora, criado_por_id_ancora = _resolver_ancora(paciente, exame_selecionado, agendamento_selecionado)

            if resultado_ia and resultado_ia["final"]:
                origem = "ia_aguardando"
                pendente = PerguntaPendente(
                    grupo_id=grupo_id_ancora,
                    criado_por_id=criado_por_id_ancora,
                    paciente_id=paciente.id,
                    exame_id=exame_selecionado.id,
                    pergunta=pergunta_enviada,
                    status="aguardando_aprovacao",
                    resposta_sugerida_ia=resultado_ia["final"],
                    # Guardadas à parte para a tela de aprovação mostrar a
                    # resposta de cada IA lado a lado, além da junção
                    # (ver medico/perguntas.html) - só as 2 escolhidas pelo
                    # dono vêm preenchidas (ver app.ia_preparo.responder_com_ia).
                    resposta_bruta_claude=resultado_ia["por_provedor"]["Claude"],
                    resposta_bruta_chatgpt=resultado_ia["por_provedor"]["ChatGPT"],
                    resposta_bruta_gemini=resultado_ia["por_provedor"]["Gemini"],
                )
                db.session.add(pendente)
                db.session.commit()
                notificar_equipe_nova_pergunta(pendente)
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
                    grupo_id=grupo_id_ancora,
                    exame_id=int(exame_id_selecionado) if exame_id_selecionado else None,
                    criado_por_id=criado_por_id_ancora,
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
                        grupo_id=grupo_id_ancora,
                        criado_por_id=criado_por_id_ancora,
                        paciente_id=paciente.id,
                        exame_id=exame_id_selecionado,
                        pergunta=pergunta_enviada,
                    )
                    db.session.add(pendente)
                    db.session.commit()
                    notificar_equipe_nova_pergunta(pendente)
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
