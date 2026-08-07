import os
from datetime import datetime
from functools import wraps

from flask import (
    Blueprint, render_template, redirect, url_for, request, flash, send_from_directory,
    current_app, abort,
)
from flask_login import login_required, current_user, logout_user
from sqlalchemy import or_

from app.extensions import db
from app.models import Agendamento, Exame, PerguntaPendente, FaqItem, ChatMensagem, Usuario, MedicoHorario
from app.faq_engine import buscar_resposta, buscar_resposta_alimento, buscar_resposta_medicamento
from app.ia_preparo import responder_com_ia
from app.clinica_utils import verificar_vencimento
from app.agendamento_otimizador import sugerir_horarios

paciente_bp = Blueprint("paciente", __name__, url_prefix="/paciente")


def paciente_required(f):
    @wraps(f)
    def decorado(*args, **kwargs):
        if not current_user.is_authenticated or current_user.tipo != "paciente":
            flash("Acesso restrito a pacientes.", "danger")
            return redirect(url_for("auth.login"))

        paciente = current_user.paciente
        if paciente and paciente.clinica:
            verificar_vencimento(paciente.clinica)
        if paciente and paciente.clinica and paciente.clinica.bloqueada:
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
    paciente = current_user.paciente
    agora = datetime.utcnow()
    # "Próximos exames" só mostra o que ainda está de pé e no futuro.
    # Cancelado ou realizado vai para o Histórico independente da data —
    # mesmo um agendamento cancelado que estava marcado para o futuro não
    # é mais um "próximo exame" de verdade.
    proximos = (
        Agendamento.query.filter_by(paciente_id=paciente.id)
        .filter(Agendamento.data_hora >= agora)
        .filter(Agendamento.status.in_(["solicitado", "agendado", "confirmado"]))
        .order_by(Agendamento.data_hora.asc())
        .all()
    )
    historico = (
        Agendamento.query.filter_by(paciente_id=paciente.id)
        .filter(or_(Agendamento.status.in_(["cancelado", "realizado"]), Agendamento.data_hora < agora))
        .order_by(Agendamento.data_hora.desc())
        .all()
    )
    return render_template("paciente/dashboard.html", proximos=proximos, historico=historico)


@paciente_bp.route("/exame/<int:agendamento_id>")
@login_required
@paciente_required
def preparo_exame(agendamento_id):
    paciente = current_user.paciente
    agendamento = Agendamento.query.filter_by(id=agendamento_id, paciente_id=paciente.id).first_or_404()
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
                    clinica_id=paciente.clinica_id,
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
                    clinica_id=paciente.clinica_id,
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
                        clinica_id=paciente.clinica_id,
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
    exames = Exame.query.filter_by(clinica_id=paciente.clinica_id).order_by(Exame.nome).all()

    exame_id = request.form.get("exame_id", type=int) or request.args.get("exame_id", type=int)
    exame_selecionado = next((e for e in exames if e.id == exame_id), None) if exame_id else None

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
                clinica_id=paciente.clinica_id,
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
        medicos_disponiveis=medicos_disponiveis,
        medico_selecionado=medico_selecionado,
        exame_selecionado=exame_selecionado,
        sugestoes=sugestoes,
    )


# ---------- Resultado de exame (download do PDF anexado pela clínica) ----------

@paciente_bp.route("/exame/<int:agendamento_id>/resultado")
@login_required
@paciente_required
def resultado_baixar(agendamento_id):
    paciente = current_user.paciente
    agendamento = Agendamento.query.filter_by(id=agendamento_id, paciente_id=paciente.id).first_or_404()
    if not agendamento.resultado:
        abort(404)
    pasta = os.path.join(current_app.instance_path, "resultados_exame")
    return send_from_directory(
        pasta, agendamento.resultado.caminho_arquivo,
        as_attachment=True, download_name=agendamento.resultado.nome_arquivo,
    )
