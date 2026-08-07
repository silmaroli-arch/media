import os
import re
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import (
    Blueprint, render_template, redirect, url_for, request, flash, jsonify, session,
    send_from_directory, current_app,
)
from werkzeug.utils import secure_filename
from flask_login import login_required, current_user, logout_user
from sqlalchemy import or_, and_

from app.extensions import db
from app.models import (
    Paciente, Usuario, Exame, Agendamento, FaqItem,
    PerguntaPendente, ClinicaMembro, MedicoHorario, MedicoBloqueio, Clinica,
    PreparoModelo, PreparoCorte, PreparoMedicamentoSuspenso, PreparoInfoGeral, PreparoAlimento,
    PreparoExameAnterior, PreparoMedicamentoMantido, Medicamento, normalizar_telefone,
    ChatMensagem, ResultadoExame, DescontoConfig, Pagamento,
)
from app.clinica_utils import clinica_atual, clinicas_do_usuario, selecionar_clinica
from app.pdf_preparo import extrair_sugestao_de_pdf
from app.xlsx_preparo import extrair_sugestoes_de_xlsx
from app.agendamento_otimizador import sugerir_horarios, medico_tem_bloqueio
from app.cripto_fiscal import criptografar_bytes, criptografar_texto
from app.nfse_nacional import emitir_nfse, ErroEmissaoNfse
from cryptography.hazmat.primitives.serialization import pkcs12

# Dias da semana usados no formulário de horário de atendimento por médico.
# Índice = MedicoHorario.dia_semana (0=segunda ... 6=domingo).
DIAS_SEMANA = [
    "Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira",
    "Sexta-feira", "Sábado", "Domingo",
]

medico_bp = Blueprint("medico", __name__, url_prefix="/equipe")

# Pasta onde os PDFs de resultado de exame são salvos — ver `resultado_upload`.
# Aviso: em produção (Elastic Beanstalk) o disco da instância não é
# persistente entre deploys/reinícios; para um uso mais robusto no futuro,
# trocar por um armazenamento externo (ex.: S3).
PASTA_RESULTADOS = "resultados_exame"


def _parse_valor_decimal(valor_str):
    """Converte um valor digitado (aceita vírgula ou ponto decimal) para
    Decimal, ou None se vazio/inválido."""
    if not valor_str:
        return None
    valor_str = valor_str.strip().replace(".", "").replace(",", ".") if "," in valor_str else valor_str.strip()
    try:
        return Decimal(valor_str)
    except InvalidOperation:
        return None


def _parse_data_nascimento(valor_str):
    """Converte a data de nascimento digitada para um date, aceitando o
    formato brasileiro (DD/MM/AAAA, usado pelo campo com máscara no
    formulário) e, por compatibilidade, o formato ISO (AAAA-MM-DD, usado
    antes quando o campo era um <input type="date"> nativo)."""
    valor_str = (valor_str or "").strip()
    if not valor_str:
        return None
    for formato in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(valor_str, formato).date()
        except ValueError:
            continue
    return None


def _destino_pos_onboarding(endpoint_padrao, **kwargs):
    """Usado pelos formulários de cadastro que também são acessados a
    partir do assistente de configuração inicial (ver medico.onboarding).
    Quando a pessoa chegou à tela vindo do assistente (campo oculto
    "voltar_onboarding"), volta para lá em vez de ir para o destino normal
    daquele formulário — assim o fluxo guiado continua de onde parou."""
    if request.form.get("voltar_onboarding") == "1":
        return redirect(url_for("medico.onboarding"))
    return redirect(url_for(endpoint_padrao, **kwargs))


def staff_required(f):
    """Garante que o usuário é da equipe (médico/secretária) e que já tem
    uma clínica selecionada na sessão. Se tiver mais de uma clínica e
    nenhuma selecionada ainda, manda para a tela de escolha."""
    @wraps(f)
    def decorado(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_staff:
            flash("Acesso restrito à equipe médica/secretaria.", "danger")
            return redirect(url_for("auth.login"))

        if clinica_atual() is None:
            if not clinicas_do_usuario():
                # Precisa deslogar de verdade — senão a pessoa continua
                # autenticada e cai num loop (auth.login manda pra index,
                # que manda de volta pra uma view protegida por este decorator).
                logout_user()
                flash(
                    "Sua conta não está vinculada a nenhuma clínica ativa. "
                    "Fale com o administrador da sua clínica ou com o suporte.",
                    "danger",
                )
                return redirect(url_for("auth.login"))
            return redirect(url_for("medico.escolher_clinica"))

        return f(*args, **kwargs)
    return decorado


def permissao_required(campo):
    """Algumas ações administrativas (cadastrar paciente novo, gerenciar a
    equipe, filiais e dados da clínica) exigem uma permissão específica.
    Como nem toda clínica tem uma secretária, essa permissão não é fixa
    por papel ('secretaria' vs. 'medico') — cada pessoa da equipe tem um
    conjunto próprio de permissões, definido em Usuario.perm_*."""
    def decorator(f):
        @wraps(f)
        def decorado(*args, **kwargs):
            if not getattr(current_user, campo, False):
                flash(
                    "Você não tem permissão para acessar essa área. Fale com "
                    "quem administra sua clínica.",
                    "danger",
                )
                return redirect(url_for("medico.dashboard"))
            return f(*args, **kwargs)
        return decorado
    return decorator


def eh_medico():
    return current_user.is_authenticated and current_user.tipo == "medico"


def medicos_da_clinica(clinica):
    """Lista os médicos (usuários) vinculados e ativos nesta clínica."""
    return [m for m in clinica.medicos_e_secretarias if m.tipo == "medico"]


def status_configuracao_inicial(clinica):
    """Calcula, a partir dos dados que já existem no banco (sem guardar
    nenhum estado de "assistente" à parte), quais das etapas sugeridas na
    configuração inicial da clínica já foram feitas. Usado tanto pela tela
    do assistente (medico.onboarding) quanto pelo aviso mostrado no Painel
    enquanto a configuração não estiver completa — por ser calculado a
    partir dos dados reais, permanece correto mesmo que a pessoa pule
    etapas e preencha as coisas por fora do assistente, em outra ordem."""
    tem_medico = len(medicos_da_clinica(clinica)) > 0
    tem_horario = MedicoHorario.query.filter_by(clinica_id=clinica.id, ativo=True).first() is not None
    tem_mais_gente = ClinicaMembro.query.filter_by(clinica_id=clinica.id, ativo=True).count() > 1
    tem_modelo_preparo = PreparoModelo.query.filter_by(clinica_id=clinica.id).first() is not None
    tem_exame = Exame.query.filter_by(clinica_id=clinica.id).first() is not None

    etapas = [
        {
            "id": "dados_clinica",
            "titulo": "Dados da clínica",
            "descricao": "Endereço, CNPJ e telefone/e-mail de contato da clínica.",
            "concluida": bool(clinica.telefone and clinica.email_contato),
            "endpoint": "medico.clinica_configuracoes",
            "permissao": "perm_dados_clinica",
        },
        {
            "id": "equipe",
            "titulo": "Convidar mais gente para a equipe",
            "descricao": "Adicione outra secretária ou médico, se houver — esta etapa é totalmente opcional.",
            "concluida": tem_mais_gente,
            "endpoint": "medico.equipe_novo",
            "permissao": "perm_equipe",
            "opcional": True,
        },
        {
            "id": "horario",
            "titulo": "Horário de atendimento do médico",
            "descricao": "Necessário para o sistema sugerir horários automaticamente na hora de agendar.",
            "concluida": tem_horario,
            "endpoint": "medico.medico_horarios",
            "bloqueada": not tem_medico,
            "motivo_bloqueio": "Cadastre um médico na etapa \"Convidar mais gente para a equipe\" primeiro.",
        },
        {
            "id": "modelo_preparo",
            "titulo": "Primeiro modelo de preparo",
            "descricao": "As instruções (cortes de alimentação, medicamentos, etc.) que o paciente vai ver.",
            "concluida": tem_modelo_preparo,
            "endpoint": "medico.preparo_modelos_novo",
        },
        {
            "id": "exame",
            "titulo": "Primeiro exame",
            "descricao": "Vincula um exame a um modelo de preparo e a um médico responsável — sem isso, ainda não há nada para agendar.",
            "concluida": tem_exame,
            "endpoint": "medico.exames_novo",
            "bloqueada": not tem_modelo_preparo,
            "motivo_bloqueio": "Cadastre um modelo de preparo primeiro.",
        },
    ]
    return etapas


# ---------- Seleção de clínica ----------

@medico_bp.route("/clinica", methods=["GET", "POST"])
@login_required
def escolher_clinica():
    if not current_user.is_staff:
        return redirect(url_for("index"))

    clinicas = clinicas_do_usuario()

    if not clinicas:
        logout_user()
        flash(
            "Sua conta não está vinculada a nenhuma clínica ativa. "
            "Fale com o administrador da sua clínica ou com o suporte.",
            "danger",
        )
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        clinica_id = request.form.get("clinica_id", type=int)
        if selecionar_clinica(clinica_id):
            return redirect(url_for("medico.dashboard"))
        flash("Clínica inválida.", "danger")

    if len(clinicas) == 1:
        selecionar_clinica(clinicas[0].id)
        return redirect(url_for("medico.dashboard"))

    return render_template("medico/escolher_clinica.html", clinicas=clinicas)


@medico_bp.route("/")
@login_required
@staff_required
def dashboard():
    clinica = clinica_atual()

    agendamentos_q = Agendamento.query.filter_by(clinica_id=clinica.id)
    # "Pendente de resposta" no painel conta as duas situações que
    # aparecem na tela "Perguntas dos pacientes" (ver
    # medico.perguntas_pendentes) esperando alguma ação do médico: as que
    # ainda não têm nenhum rascunho (status "pendente") E as que a IA já
    # rascunhou mas ainda aguardam aprovação (status "aguardando_aprovacao")
    # — antes só a primeira era contada aqui, então uma pergunta com
    # rascunho da IA aparecia como card zerado mesmo tendo o que revisar.
    pendentes_q = PerguntaPendente.query.filter_by(clinica_id=clinica.id, status="pendente")
    aguardando_q = PerguntaPendente.query.filter_by(clinica_id=clinica.id, status="aguardando_aprovacao")
    solicitacoes_q = Agendamento.query.filter_by(clinica_id=clinica.id, status="solicitado")

    if eh_medico():
        total_pacientes = (
            db.session.query(Agendamento.paciente_id)
            .filter_by(clinica_id=clinica.id, medico_id=current_user.id)
            .distinct()
            .count()
        )
        agendamentos_q = agendamentos_q.filter(Agendamento.medico_id == current_user.id)
        # Perguntas pendentes só entram na conta do médico quando forem
        # sobre um exame de sua responsabilidade (perguntas gerais, sem
        # exame associado, ficam só para a secretaria responder).
        pendentes_q = pendentes_q.join(Exame, PerguntaPendente.exame_id == Exame.id).filter(
            or_(Exame.medico_id == current_user.id, Exame.medicos_extra.any(id=current_user.id))
        )
        aguardando_q = aguardando_q.join(Exame, PerguntaPendente.exame_id == Exame.id).filter(
            or_(Exame.medico_id == current_user.id, Exame.medicos_extra.any(id=current_user.id))
        )
        solicitacoes_q = solicitacoes_q.filter(Agendamento.medico_id == current_user.id)
    else:
        total_pacientes = Paciente.query.filter_by(clinica_id=clinica.id).count()

    proximos = (
        agendamentos_q.filter(Agendamento.data_hora >= datetime.utcnow())
        .order_by(Agendamento.data_hora.asc())
        .limit(5)
        .all()
    )
    pendentes = pendentes_q.count() + aguardando_q.count()
    solicitacoes_pendentes = solicitacoes_q.count()
    # A agenda completa (calendário + lista) foi incorporada ao painel — não
    # existe mais uma tela separada de "Agenda" no menu.
    agendamentos = agendamentos_q.order_by(Agendamento.data_hora.asc()).all()
    return render_template(
        "medico/dashboard.html",
        clinica=clinica,
        total_pacientes=total_pacientes,
        proximos=proximos,
        pendentes=pendentes,
        solicitacoes_pendentes=solicitacoes_pendentes,
        agendamentos=agendamentos,
        etapas_configuracao_inicial=[e for e in status_configuracao_inicial(clinica) if not e["concluida"] and not e.get("opcional")],
    )


@medico_bp.route("/configuracao-inicial")
@login_required
@staff_required
def onboarding():
    """Assistente de configuração inicial, mostrado logo após a empresa se
    cadastrar (ver auth.cadastro) e também acessível a qualquer momento
    pelo aviso no Painel — reúne, num só lugar, as etapas sugeridas para
    deixar a clínica pronta para uso. Nenhuma etapa é obrigatória: cada
    uma só linka para a tela real (Dados da clínica, Equipe, Horário de
    atendimento, Modelos de preparo, Exames), que continua funcionando
    normalmente por fora do assistente também."""
    clinica = clinica_atual()
    etapas = status_configuracao_inicial(clinica)
    concluidas = sum(1 for e in etapas if e["concluida"])
    return render_template(
        "medico/onboarding.html",
        clinica=clinica,
        etapas=etapas,
        concluidas=concluidas,
        total=len(etapas),
    )


# ---------- Pacientes ----------

@medico_bp.route("/pacientes")
@login_required
@staff_required
def pacientes_lista():
    clinica = clinica_atual()
    if eh_medico() and not current_user.perm_pacientes:
        # Médico sem a permissão administrativa de pacientes: só vê quem já
        # tem algum agendamento com ele mesmo — "acompanhar somente os seus
        # pacientes". Quem tem essa permissão (ex.: o médico fundador da
        # empresa, que também pode cadastrar pacientes novos) precisa ver
        # todos os pacientes da clínica, senão nem o paciente que ele mesmo
        # acabou de cadastrar apareceria na lista antes do 1º agendamento.
        pacientes = (
            Paciente.query.join(Agendamento, Agendamento.paciente_id == Paciente.id)
            .filter(Paciente.clinica_id == clinica.id, Agendamento.medico_id == current_user.id)
            .distinct()
            .order_by(Paciente.nome)
            .all()
        )
    else:
        pacientes = Paciente.query.filter_by(clinica_id=clinica.id).order_by(Paciente.nome).all()
    return render_template("medico/pacientes_lista.html", pacientes=pacientes)


def _preencher_endereco_emergencia(paciente, form):
    """Preenche os campos de endereço (obtidos via busca por CEP no
    formulário) e de contato de emergência a partir do form — usado tanto
    no cadastro quanto na edição do paciente."""
    paciente.cep = form.get("cep", "").strip()
    paciente.rua = form.get("rua", "").strip()
    paciente.numero = form.get("numero", "").strip()
    paciente.complemento = form.get("complemento", "").strip()
    paciente.bairro = form.get("bairro", "").strip()
    paciente.cidade = form.get("cidade", "").strip()
    paciente.uf = form.get("uf", "").strip().upper() or None
    paciente.contato_emergencia_nome = form.get("contato_emergencia_nome", "").strip()
    paciente.contato_emergencia_telefone = form.get("contato_emergencia_telefone", "").strip()


@medico_bp.route("/pacientes/novo", methods=["GET", "POST"])
@login_required
@staff_required
@permissao_required("perm_pacientes")
def pacientes_novo():
    clinica = clinica_atual()

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        cpf = request.form.get("cpf", "").strip()
        email = request.form.get("email", "").strip().lower()
        telefone_digitado = request.form.get("telefone", "").strip()
        data_nascimento_str = request.form.get("data_nascimento", "").strip()
        telefone = normalizar_telefone(telefone_digitado)

        if not nome or not cpf or not telefone or not data_nascimento_str:
            flash("Nome, CPF, telefone e data de nascimento são obrigatórios.", "danger")
            return render_template("medico/pacientes_form.html", paciente=None)

        data_nascimento = _parse_data_nascimento(data_nascimento_str)
        if not data_nascimento:
            flash("Data de nascimento inválida — use o formato DD/MM/AAAA.", "danger")
            return render_template("medico/pacientes_form.html", paciente=None)

        if Usuario.query.filter_by(telefone=telefone).first():
            flash("Já existe um paciente cadastrado com esse telefone.", "danger")
            return render_template("medico/pacientes_form.html", paciente=None)

        if email and Usuario.query.filter_by(email=email).first():
            flash("Já existe um usuário com esse e-mail.", "danger")
            return render_template("medico/pacientes_form.html", paciente=None)

        if Paciente.query.filter_by(clinica_id=clinica.id, cpf=cpf).first():
            flash("Já existe um paciente com esse CPF nesta clínica.", "danger")
            return render_template("medico/pacientes_form.html", paciente=None)

        # Paciente não usa e-mail/senha para entrar — o acesso é feito
        # informando telefone e data de nascimento (ver auth.login_paciente).
        usuario = Usuario(nome=nome, email=email or None, telefone=telefone, tipo="paciente")
        db.session.add(usuario)
        db.session.flush()

        paciente = Paciente(
            clinica_id=clinica.id,
            usuario_id=usuario.id,
            nome=nome,
            cpf=cpf,
            data_nascimento=data_nascimento,
            email=email or None,
            telefone=telefone,
        )
        _preencher_endereco_emergencia(paciente, request.form)
        db.session.add(paciente)
        db.session.commit()

        flash(
            "Paciente cadastrado. Ele(a) pode acessar o sistema informando o telefone "
            f"({telefone_digitado}) e a data de nascimento — não é necessário criar senha.",
            "success",
        )
        return redirect(url_for("medico.pacientes_lista"))

    return render_template("medico/pacientes_form.html", paciente=None)


@medico_bp.route("/pacientes/<int:paciente_id>/editar", methods=["GET", "POST"])
@login_required
@staff_required
@permissao_required("perm_pacientes")
def pacientes_editar(paciente_id):
    clinica = clinica_atual()
    paciente = Paciente.query.filter_by(id=paciente_id, clinica_id=clinica.id).first_or_404()

    if request.method == "POST":
        paciente.nome = request.form.get("nome", "").strip() or paciente.nome
        paciente.email = request.form.get("email", "").strip().lower() or None
        paciente.observacoes = request.form.get("observacoes", "").strip() or None
        _preencher_endereco_emergencia(paciente, request.form)
        db.session.commit()
        flash("Cadastro do paciente atualizado.", "success")
        return redirect(url_for("medico.pacientes_detalhe", paciente_id=paciente.id))

    return render_template("medico/pacientes_form.html", paciente=paciente)


@medico_bp.route("/pacientes/<int:paciente_id>")
@login_required
@staff_required
def pacientes_detalhe(paciente_id):
    clinica = clinica_atual()
    paciente = Paciente.query.filter_by(id=paciente_id, clinica_id=clinica.id).first_or_404()

    if eh_medico() and not current_user.perm_pacientes:
        tem_vinculo = Agendamento.query.filter_by(
            paciente_id=paciente.id, medico_id=current_user.id
        ).first()
        if not tem_vinculo:
            flash("Este paciente não tem agendamentos com você.", "danger")
            return redirect(url_for("medico.pacientes_lista"))

    return render_template("medico/pacientes_detalhe.html", paciente=paciente)


# ---------- Exames e preparo ----------

@medico_bp.route("/exames")
@login_required
@staff_required
def exames_lista():
    clinica = clinica_atual()
    query = Exame.query.filter_by(clinica_id=clinica.id)
    if eh_medico():
        query = query.filter(
            or_(Exame.medico_id == current_user.id, Exame.medicos_extra.any(id=current_user.id))
        )
    exames = query.order_by(Exame.nome).all()
    return render_template("medico/exames_lista.html", exames=exames, eh_medico=eh_medico())


@medico_bp.route("/exames/novo", methods=["GET", "POST"])
@login_required
@staff_required
def exames_novo():
    clinica = clinica_atual()
    medicos = medicos_da_clinica(clinica)
    modelos = PreparoModelo.query.filter_by(clinica_id=clinica.id).order_by(PreparoModelo.nome).all()

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        descricao = request.form.get("descricao", "").strip()
        preparo_modelo_id = request.form.get("preparo_modelo_id", type=int)
        duracao_minutos = request.form.get("duracao_minutos", type=int)
        preco = _parse_valor_decimal(request.form.get("preco", ""))
        precisa_acompanhante = request.form.get("precisa_acompanhante") == "on"

        if eh_medico():
            medico_id = current_user.id
        else:
            medico_id = request.form.get("medico_id", type=int)
            medico_valido = medico_id and any(m.id == medico_id for m in medicos)
            if not medico_valido:
                flash("Escolha o médico responsável pelo exame.", "danger")
                return render_template("medico/exames_form.html", exame=None, medicos=medicos, modelos=modelos)

        # Médicos adicionais, além do principal — qualquer um deles pode
        # atender esse exame; a escolha de quem atende de fato acontece no
        # momento do agendamento.
        medicos_extra_ids = {v for v in request.form.getlist("medicos_extra_ids", type=int) if v != medico_id}
        medicos_extra = [m for m in medicos if m.id in medicos_extra_ids]

        modelo = next((m for m in modelos if m.id == preparo_modelo_id), None)
        if not nome or not modelo:
            flash("Nome do exame e modelo de preparo são obrigatórios.", "danger")
            return render_template("medico/exames_form.html", exame=None, medicos=medicos, modelos=modelos)

        if Exame.query.filter_by(clinica_id=clinica.id, nome=nome).first():
            flash("Já existe um exame com esse nome nesta clínica.", "danger")
            return render_template("medico/exames_form.html", exame=None, medicos=medicos, modelos=modelos)

        exame = Exame(
            clinica_id=clinica.id, medico_id=medico_id, nome=nome, descricao=descricao,
            preparo_modelo_id=modelo.id, duracao_minutos=duracao_minutos, preco=preco,
            precisa_acompanhante=precisa_acompanhante, medicos_extra=medicos_extra,
        )
        db.session.add(exame)
        db.session.commit()

        flash("Exame cadastrado com sucesso.", "success")
        return _destino_pos_onboarding("medico.exames_lista")

    return render_template("medico/exames_form.html", exame=None, medicos=medicos, modelos=modelos)


@medico_bp.route("/exames/<int:exame_id>/editar", methods=["GET", "POST"])
@login_required
@staff_required
def exames_editar(exame_id):
    clinica = clinica_atual()
    medicos = medicos_da_clinica(clinica)
    modelos = PreparoModelo.query.filter_by(clinica_id=clinica.id).order_by(PreparoModelo.nome).all()
    query = Exame.query.filter_by(id=exame_id, clinica_id=clinica.id)
    if eh_medico():
        query = query.filter(
            or_(Exame.medico_id == current_user.id, Exame.medicos_extra.any(id=current_user.id))
        )
    exame = query.first_or_404()

    if request.method == "POST":
        exame.nome = request.form.get("nome", "").strip()
        exame.descricao = request.form.get("descricao", "").strip()
        exame.duracao_minutos = request.form.get("duracao_minutos", type=int)
        exame.preco = _parse_valor_decimal(request.form.get("preco", ""))
        exame.precisa_acompanhante = request.form.get("precisa_acompanhante") == "on"
        if not eh_medico():
            novo_medico_id = request.form.get("medico_id", type=int)
            if novo_medico_id and any(m.id == novo_medico_id for m in medicos):
                exame.medico_id = novo_medico_id
            medicos_extra_ids = {v for v in request.form.getlist("medicos_extra_ids", type=int) if v != exame.medico_id}
            exame.medicos_extra = [m for m in medicos if m.id in medicos_extra_ids]
        preparo_modelo_id = request.form.get("preparo_modelo_id", type=int)
        modelo = next((m for m in modelos if m.id == preparo_modelo_id), None)
        if not modelo:
            flash("Escolha um modelo de preparo válido.", "danger")
            return render_template("medico/exames_form.html", exame=exame, medicos=medicos, modelos=modelos)
        exame.preparo_modelo_id = modelo.id
        db.session.commit()
        flash("Exame atualizado.", "success")
        return redirect(url_for("medico.exames_lista"))

    return render_template("medico/exames_form.html", exame=exame, medicos=medicos, modelos=modelos)


# ---------- Modelos de preparo (reaproveitáveis entre exames) ----------

@medico_bp.route("/preparo-modelos")
@login_required
@staff_required
def preparo_modelos_lista():
    clinica = clinica_atual()
    modelos = PreparoModelo.query.filter_by(clinica_id=clinica.id).order_by(PreparoModelo.nome).all()
    return render_template("medico/preparo_modelos_lista.html", modelos=modelos)


def _salvar_cortes_e_medicamentos(modelo, form):
    """Substitui os cortes e medicamentos suspensos de um modelo pelos
    valores enviados no formulário (listas paralelas de campos
    repetidos — sem depender de JS framework, só de inputs com o mesmo
    name, na mesma ordem)."""
    for corte in list(modelo.cortes):
        db.session.delete(corte)
    descricoes = form.getlist("corte_descricao[]")
    horas = form.getlist("corte_horas[]")
    for descricao, hora_str in zip(descricoes, horas):
        descricao = descricao.strip()
        if not descricao or not hora_str.strip():
            continue
        try:
            horas_antes = int(hora_str)
        except ValueError:
            continue
        db.session.add(PreparoCorte(preparo_modelo=modelo, descricao=descricao, horas_antes=horas_antes))

    for medsusp in list(modelo.medicamentos_suspensos):
        db.session.delete(medsusp)
    nomes_medicamento = form.getlist("medicamento_nome[]")
    dias_lista = form.getlist("medicamento_dias[]")
    obs_lista = form.getlist("medicamento_obs[]")
    categoria_lista = form.getlist("medicamento_categoria[]")
    for nome_medicamento, dias_str, obs, categoria in zip(
        nomes_medicamento, dias_lista,
        obs_lista + [""] * len(dias_lista),
        categoria_lista + [""] * len(dias_lista),
    ):
        nome_medicamento = nome_medicamento.strip()
        if not nome_medicamento or not dias_str.strip():
            continue
        try:
            dias_antes = int(dias_str)
        except ValueError:
            continue
        categoria = categoria.strip()
        medicamento = Medicamento.query.filter(db.func.lower(Medicamento.nome) == nome_medicamento.lower()).first()
        if not medicamento:
            # Catálogo compartilhado da plataforma — se não existir ainda,
            # cria na hora, já com esse prazo como padrão sugerido.
            medicamento = Medicamento(nome=nome_medicamento, dias_padrao_suspensao=dias_antes, categoria=categoria or None)
            db.session.add(medicamento)
            db.session.flush()
        elif categoria:
            # Não apaga uma categoria já cadastrada por outra clínica só
            # porque esta tela deixou o campo em branco — só atualiza
            # quando uma categoria foi de fato informada.
            medicamento.categoria = categoria
        db.session.add(PreparoMedicamentoSuspenso(
            preparo_modelo=modelo, medicamento_id=medicamento.id, dias_antes=dias_antes,
            observacao=obs.strip() or None,
        ))

    for mantido in list(modelo.medicamentos_mantidos):
        db.session.delete(mantido)
    nomes_mantido = form.getlist("mantido_nome[]")
    obs_mantido = form.getlist("mantido_obs[]")
    for nome_mantido, obs in zip(nomes_mantido, obs_mantido + [""] * len(nomes_mantido)):
        nome_mantido = nome_mantido.strip()
        if not nome_mantido:
            continue
        db.session.add(PreparoMedicamentoMantido(
            preparo_modelo=modelo, nome=nome_mantido, observacao=obs.strip() or None,
        ))

    for info in list(modelo.informacoes_gerais):
        db.session.delete(info)
    textos_info = form.getlist("info_geral[]")
    horas_info = form.getlist("info_geral_horas[]")
    dias_info = form.getlist("info_geral_dias[]")
    hora_exata_info = form.getlist("info_geral_hora_exata[]")
    for texto, horas_str, dias_str, hora_exata_str in zip(
        textos_info,
        horas_info + [""] * len(textos_info),
        dias_info + [""] * len(textos_info),
        hora_exata_info + [""] * len(textos_info),
    ):
        texto = texto.strip()
        if not texto:
            continue
        horas_antes = None
        if horas_str.strip():
            try:
                horas_antes = int(horas_str)
            except ValueError:
                horas_antes = None
        dias_antes = None
        if dias_str.strip():
            try:
                dias_antes = int(dias_str)
            except ValueError:
                dias_antes = None
        hora_exata = None
        if hora_exata_str.strip():
            try:
                hora_exata = datetime.strptime(hora_exata_str.strip(), "%H:%M").time()
            except ValueError:
                hora_exata = None
        db.session.add(PreparoInfoGeral(
            preparo_modelo=modelo, texto=texto,
            horas_antes=horas_antes, dias_antes=dias_antes, hora_exata=hora_exata,
        ))

    for alimento in list(modelo.alimentos):
        db.session.delete(alimento)
    nomes_alimento = form.getlist("alimento_nome[]")
    tipos_alimento = form.getlist("alimento_tipo[]")
    horas_alimento = form.getlist("alimento_horas[]")
    dias_alimento = form.getlist("alimento_dias[]")
    for nome_alimento, tipo, horas_str, dias_str in zip(
        nomes_alimento, tipos_alimento,
        horas_alimento + [""] * len(nomes_alimento),
        dias_alimento + [""] * len(nomes_alimento),
    ):
        nome_alimento = nome_alimento.strip()
        if not nome_alimento:
            continue
        horas_str = horas_str.strip()
        horas_antes = None
        if horas_str:
            try:
                horas_antes = int(horas_str)
            except ValueError:
                horas_antes = None
        dias_str = dias_str.strip()
        dias_antes = None
        # Horas e dias são mutuamente excludentes — se as duas vierem
        # preenchidas (não deveria acontecer pela UI), prioriza horas.
        if dias_str and horas_antes is None:
            try:
                dias_antes = int(dias_str)
            except ValueError:
                dias_antes = None
        db.session.add(PreparoAlimento(
            preparo_modelo=modelo, nome=nome_alimento,
            permitido=(tipo == "permitido"), horas_antes=horas_antes, dias_antes=dias_antes,
        ))

    for exame_anterior in list(modelo.exames_anteriores_proibidos):
        db.session.delete(exame_anterior)
    nomes_exame_anterior = form.getlist("exame_anterior_nome[]")
    dias_exame_anterior = form.getlist("exame_anterior_dias[]")
    for nome_exame, dias_str in zip(
        nomes_exame_anterior, dias_exame_anterior + [""] * len(nomes_exame_anterior)
    ):
        nome_exame = nome_exame.strip()
        if not nome_exame:
            continue
        dias_str = dias_str.strip()
        dias_antes = None
        if dias_str:
            try:
                dias_antes = int(dias_str)
            except ValueError:
                dias_antes = None
        db.session.add(PreparoExameAnterior(
            preparo_modelo=modelo, nome=nome_exame, dias_antes=dias_antes,
        ))


@medico_bp.route("/preparo-modelos/novo", methods=["GET", "POST"])
@login_required
@staff_required
def preparo_modelos_novo():
    clinica = clinica_atual()
    sugestao = None
    if request.method == "GET" and request.args.get("de_importacao"):
        sugestao = session.pop("preparo_sugestao_importada", None)

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        instrucoes = request.form.get("instrucoes", "").strip()
        observacoes_medicamentos = request.form.get("observacoes_medicamentos", "").strip()

        if not nome or not instrucoes:
            flash("Nome do modelo e instruções são obrigatórios.", "danger")
            return render_template("medico/preparo_modelo_form.html", modelo=None, sugestao=None, medicamentos_catalogo=Medicamento.query.order_by(Medicamento.nome).all())

        if PreparoModelo.query.filter_by(clinica_id=clinica.id, nome=nome).first():
            flash("Já existe um modelo de preparo com esse nome nesta filial.", "danger")
            return render_template("medico/preparo_modelo_form.html", modelo=None, sugestao=None, medicamentos_catalogo=Medicamento.query.order_by(Medicamento.nome).all())

        modelo = PreparoModelo(
            clinica_id=clinica.id, nome=nome, instrucoes=instrucoes,
            observacoes_medicamentos=observacoes_medicamentos or None,
        )
        db.session.add(modelo)
        db.session.flush()
        _salvar_cortes_e_medicamentos(modelo, request.form)
        db.session.commit()

        flash("Modelo de preparo cadastrado com sucesso.", "success")
        return _destino_pos_onboarding("medico.preparo_modelos_lista")

    return render_template("medico/preparo_modelo_form.html", modelo=None, sugestao=sugestao, medicamentos_catalogo=Medicamento.query.order_by(Medicamento.nome).all())


@medico_bp.route("/preparo-modelos/<int:modelo_id>/editar", methods=["GET", "POST"])
@login_required
@staff_required
def preparo_modelos_editar(modelo_id):
    clinica = clinica_atual()
    modelo = PreparoModelo.query.filter_by(id=modelo_id, clinica_id=clinica.id).first_or_404()

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        if not nome:
            flash("Informe o nome do modelo.", "danger")
            return render_template("medico/preparo_modelo_form.html", modelo=modelo, sugestao=None, medicamentos_catalogo=Medicamento.query.order_by(Medicamento.nome).all())

        modelo.nome = nome
        modelo.instrucoes = request.form.get("instrucoes", "").strip()
        modelo.observacoes_medicamentos = request.form.get("observacoes_medicamentos", "").strip() or None
        _salvar_cortes_e_medicamentos(modelo, request.form)
        db.session.commit()

        flash("Modelo de preparo atualizado.", "success")
        return redirect(url_for("medico.preparo_modelos_lista"))

    return render_template("medico/preparo_modelo_form.html", modelo=modelo, sugestao=None, medicamentos_catalogo=Medicamento.query.order_by(Medicamento.nome).all())


@medico_bp.route("/preparo-modelos/<int:modelo_id>/remover", methods=["POST"])
@login_required
@staff_required
def preparo_modelos_remover(modelo_id):
    clinica = clinica_atual()
    modelo = PreparoModelo.query.filter_by(id=modelo_id, clinica_id=clinica.id).first_or_404()
    if modelo.exames:
        flash(
            f"Esse modelo está em uso por {len(modelo.exames)} exame(s) — troque o modelo desses "
            "exames antes de removê-lo.",
            "danger",
        )
        return redirect(url_for("medico.preparo_modelos_lista"))
    db.session.delete(modelo)
    db.session.commit()
    flash("Modelo de preparo removido.", "success")
    return redirect(url_for("medico.preparo_modelos_lista"))


@medico_bp.route("/preparo-modelos/importar-pdf", methods=["GET", "POST"])
@login_required
@staff_required
def preparo_modelos_importar_pdf():
    if request.method == "POST":
        arquivo = request.files.get("arquivo_pdf")
        if not arquivo or not arquivo.filename:
            flash("Selecione um arquivo PDF.", "danger")
            return render_template("medico/preparo_modelo_importar.html")

        try:
            sugestao = extrair_sugestao_de_pdf(arquivo.stream)
        except Exception:
            flash(
                "Não foi possível ler esse PDF. Ele pode estar corrompido, protegido por senha, ou ser "
                "uma imagem escaneada sem texto selecionável — nesse caso, cadastre o modelo manualmente.",
                "danger",
            )
            return render_template("medico/preparo_modelo_importar.html")

        # Guarda a sugestão na sessão só até a próxima tela (o formulário
        # de "novo modelo" é aberto já preenchido, mas nada é salvo até a
        # pessoa revisar e clicar em "Salvar").
        session["preparo_sugestao_importada"] = sugestao
        flash(
            "Texto extraído do PDF. Revise com cuidado antes de salvar — a extração é automática e pode "
            "ter interpretado algo errado.",
            "warning",
        )
        return redirect(url_for("medico.preparo_modelos_novo", de_importacao=1))

    return render_template("medico/preparo_modelo_importar.html")


@medico_bp.route("/preparo-modelos/importar-xlsx", methods=["GET", "POST"])
@login_required
@staff_required
def preparo_modelos_importar_xlsx():
    if request.method == "POST":
        arquivo = request.files.get("arquivo_xlsx")
        if not arquivo or not arquivo.filename:
            flash("Selecione um arquivo Excel (.xlsx).", "danger")
            return render_template("medico/preparo_modelo_importar_xlsx.html")

        try:
            sugestoes = extrair_sugestoes_de_xlsx(arquivo.stream)
        except Exception:
            flash(
                "Não foi possível ler essa planilha. Confira se é um arquivo .xlsx válido e se segue o "
                "formato com as colunas Tipo/Ação/Agrupador/Nome/Dias antes/Horas antes/Hora exata.",
                "danger",
            )
            return render_template("medico/preparo_modelo_importar_xlsx.html")

        if not sugestoes:
            flash("Essa planilha não tem nenhuma aba com dados.", "danger")
            return render_template("medico/preparo_modelo_importar_xlsx.html")

        if len(sugestoes) == 1:
            session["preparo_sugestao_importada"] = sugestoes[0]
            flash(
                "Dados extraídos da planilha. Revise com cuidado antes de salvar — a extração é "
                "automática e pode ter interpretado algo errado.",
                "warning",
            )
            return redirect(url_for("medico.preparo_modelos_novo", de_importacao=1))

        # Cada aba é o preparo de um exame diferente — guarda todas na
        # sessão e deixa a pessoa escolher qual importar primeiro (dá pra
        # voltar aqui depois para importar as outras abas também).
        session["preparo_xlsx_sugestoes"] = sugestoes
        return redirect(url_for("medico.preparo_modelos_importar_xlsx_escolher"))

    return render_template("medico/preparo_modelo_importar_xlsx.html")


@medico_bp.route("/preparo-modelos/importar-xlsx/escolher", methods=["GET", "POST"])
@login_required
@staff_required
def preparo_modelos_importar_xlsx_escolher():
    sugestoes = session.get("preparo_xlsx_sugestoes")
    if not sugestoes:
        flash("Nenhuma planilha importada ainda — envie o arquivo primeiro.", "danger")
        return redirect(url_for("medico.preparo_modelos_importar_xlsx"))

    if request.method == "POST":
        indice = request.form.get("indice", type=int)
        if indice is None or not (0 <= indice < len(sugestoes)):
            flash("Selecione uma aba válida.", "danger")
            return redirect(url_for("medico.preparo_modelos_importar_xlsx_escolher"))

        session["preparo_sugestao_importada"] = sugestoes[indice]
        flash(
            "Dados extraídos da planilha. Revise com cuidado antes de salvar — a extração é automática "
            "e pode ter interpretado algo errado.",
            "warning",
        )
        return redirect(url_for("medico.preparo_modelos_novo", de_importacao=1))

    return render_template("medico/preparo_modelo_importar_xlsx_escolher.html", sugestoes=sugestoes)


# ---------- Agenda ----------

# Cores por status (mesmas usadas nos badges do Bootstrap, em hexadecimal,
# para o componente de calendário pintar os eventos de forma consistente).
CORES_STATUS = {
    "agendado": "#6c757d",
    "confirmado": "#0dcaf0",
    "realizado": "#198754",
    "cancelado": "#dc3545",
}


@medico_bp.route("/agenda")
@login_required
@staff_required
def agenda():
    # A tela de agenda foi incorporada ao painel (não existe mais um item
    # de menu separado) — este redirecionamento mantém funcionando os
    # links/botões antigos que ainda apontam para cá.
    return redirect(url_for("medico.dashboard", _anchor="agenda-completa"))


@medico_bp.route("/agenda/eventos")
@login_required
@staff_required
def agenda_eventos():
    """Retorna os agendamentos no formato que o FullCalendar espera."""
    clinica = clinica_atual()
    query = Agendamento.query.filter_by(clinica_id=clinica.id)
    if eh_medico():
        query = query.filter_by(medico_id=current_user.id)
    agendamentos = query.all()
    eventos = [
        {
            "id": a.id,
            "title": f"{a.data_hora.strftime('%H:%M')} · {a.paciente.nome} · {a.exame.nome}",
            "start": a.data_hora.isoformat(),
            "color": CORES_STATUS.get(a.status, "#6c757d"),
            "extendedProps": {
                "paciente": a.paciente.nome,
                "exame": a.exame.nome,
                "status": a.status,
                "observacoes": a.observacoes or "",
            },
        }
        for a in agendamentos
    ]
    return jsonify(eventos)


@medico_bp.route("/agenda/novo", methods=["GET", "POST"])
@login_required
@staff_required
def agenda_novo():
    clinica = clinica_atual()
    filiais_disponiveis = clinicas_do_usuario()

    if request.method == "POST":
        filial_id = request.form.get("filial_id", type=int)
        paciente_id = request.form.get("paciente_id", type=int)
        exame_id = request.form.get("exame_id", type=int)
        data_hora_str = request.form.get("data_hora")
        observacoes = request.form.get("observacoes", "").strip()
        acompanhante_nome = request.form.get("acompanhante_nome", "").strip()
        acompanhante_telefone = request.form.get("acompanhante_telefone", "").strip()

        filial = next((f for f in filiais_disponiveis if f.id == filial_id), None)
        if not filial:
            flash("Escolha uma filial válida.", "danger")
            return redirect(url_for("medico.agenda_novo"))

        if not paciente_id or not exame_id:
            flash("Escolha um paciente e um exame válidos.", "danger")
            return redirect(url_for("medico.agenda_novo", filial_id=filial.id))

        # O médico só pode agendar para os seus próprios exames (principal
        # ou associado). Quando o exame tem mais de um médico associado,
        # é o médico escolhido no formulário (medico_id) que efetivamente
        # atende esse agendamento — não necessariamente o médico principal
        # do exame.
        medico_id_form = request.form.get("medico_id", type=int)

        # confirma que o paciente e o exame pertencem mesmo à filial escolhida
        paciente = Paciente.query.filter_by(id=paciente_id, clinica_id=filial.id).first()
        exame_query = Exame.query.filter_by(id=exame_id, clinica_id=filial.id)
        if eh_medico():
            exame_query = exame_query.filter(
                or_(Exame.medico_id == current_user.id, Exame.medicos_extra.any(id=current_user.id))
            )
        elif medico_id_form:
            exame_query = exame_query.filter(
                or_(Exame.medico_id == medico_id_form, Exame.medicos_extra.any(id=medico_id_form))
            )
        exame = exame_query.first()
        if not paciente or not exame:
            flash("Paciente ou exame inválido para a filial/médico escolhidos.", "danger")
            return redirect(url_for("medico.agenda_novo", filial_id=filial.id, medico_id=medico_id_form))

        if eh_medico():
            medico_atende_id = current_user.id
        elif medico_id_form and exame.medico_pode_atender(medico_id_form):
            medico_atende_id = medico_id_form
        else:
            medico_atende_id = exame.medico_id

        try:
            data_hora = datetime.strptime(data_hora_str, "%Y-%m-%dT%H:%M")
        except (ValueError, TypeError):
            flash("Data/hora inválida.", "danger")
            return redirect(url_for("medico.agenda_novo", filial_id=filial.id, medico_id=medico_id_form))

        if exame.precisa_acompanhante and not acompanhante_nome:
            flash(
                f"O exame '{exame.nome}' exige acompanhante — informe o nome de quem vai acompanhar "
                "o paciente no dia (pode ser alterado depois, se necessário).",
                "danger",
            )
            return redirect(url_for("medico.agenda_novo", filial_id=filial.id, medico_id=medico_id_form))

        if medico_tem_bloqueio(filial.id, medico_atende_id, data_hora):
            flash(
                "Esse médico bloqueou a agenda nesse horário (compromisso próprio) — escolha outro horário.",
                "danger",
            )
            return redirect(url_for("medico.agenda_novo", filial_id=filial.id, medico_id=medico_id_form))

        agendamento = Agendamento(
            clinica_id=filial.id,
            paciente_id=paciente.id,
            exame_id=exame.id,
            medico_id=medico_atende_id,
            data_hora=data_hora,
            observacoes=observacoes,
            acompanhante_nome=acompanhante_nome or None,
            acompanhante_telefone=acompanhante_telefone or None,
        )
        db.session.add(agendamento)
        db.session.commit()
        flash("Agendamento criado com sucesso.", "success")
        return redirect(url_for("medico.agenda"))

    # GET — monta os campos dependentes (filial -> médico -> exame/paciente)
    # a partir da query string, pra permitir trocar filial/médico sem perder
    # o restante do formulário (feito via um pequeno reload no template).
    filial_id_param = request.args.get("filial_id", type=int)
    filial_selecionada = next((f for f in filiais_disponiveis if f.id == filial_id_param), None) or clinica

    if eh_medico():
        medicos_disponiveis = []
        medico_selecionado_id = current_user.id
    else:
        medicos_disponiveis = medicos_da_clinica(filial_selecionada)
        medico_id_param = request.args.get("medico_id", type=int)
        medico_selecionado_id = medico_id_param if any(m.id == medico_id_param for m in medicos_disponiveis) else None
        if medico_selecionado_id is None and len(medicos_disponiveis) == 1:
            medico_selecionado_id = medicos_disponiveis[0].id

    pacientes = Paciente.query.filter_by(clinica_id=filial_selecionada.id).order_by(Paciente.nome).all()

    exames_query = Exame.query.filter_by(clinica_id=filial_selecionada.id)
    if eh_medico():
        exames_query = exames_query.filter(
            or_(Exame.medico_id == current_user.id, Exame.medicos_extra.any(id=current_user.id))
        )
    elif medico_selecionado_id:
        exames_query = exames_query.filter(
            or_(Exame.medico_id == medico_selecionado_id, Exame.medicos_extra.any(id=medico_selecionado_id))
        )
    exames = exames_query.order_by(Exame.nome).all()

    return render_template(
        "medico/agenda_form.html",
        pacientes=pacientes,
        exames=exames,
        filiais_disponiveis=filiais_disponiveis,
        filial_selecionada=filial_selecionada,
        medicos_disponiveis=medicos_disponiveis,
        medico_selecionado_id=medico_selecionado_id,
        eh_medico=eh_medico(),
    )


@medico_bp.route("/agenda/<int:agendamento_id>/status", methods=["POST"])
@login_required
@staff_required
def agenda_status(agendamento_id):
    clinica = clinica_atual()
    filtros = dict(id=agendamento_id, clinica_id=clinica.id)
    if eh_medico():
        filtros["medico_id"] = current_user.id
    agendamento = Agendamento.query.filter_by(**filtros).first_or_404()
    agendamento.status = request.form.get("status", agendamento.status)
    db.session.commit()
    flash("Status do agendamento atualizado.", "success")
    return redirect(url_for("medico.agenda"))


@medico_bp.route("/agenda/<int:agendamento_id>/acompanhante", methods=["POST"])
@login_required
@staff_required
def agenda_acompanhante(agendamento_id):
    """Indica/atualiza quem vai acompanhar o paciente no dia do exame —
    pode ser preenchido no momento do agendamento ou alterado depois, até
    o próprio dia do exame."""
    clinica = clinica_atual()
    filtros = dict(id=agendamento_id, clinica_id=clinica.id)
    if eh_medico():
        filtros["medico_id"] = current_user.id
    agendamento = Agendamento.query.filter_by(**filtros).first_or_404()
    agendamento.acompanhante_nome = request.form.get("acompanhante_nome", "").strip() or None
    agendamento.acompanhante_telefone = request.form.get("acompanhante_telefone", "").strip() or None
    db.session.commit()
    flash("Acompanhante atualizado.", "success")
    return redirect(url_for("medico.agenda"))


# ---------- Solicitações de agendamento feitas pelo paciente ----------

@medico_bp.route("/agenda/solicitacoes")
@login_required
@staff_required
def agenda_solicitacoes():
    clinica = clinica_atual()
    query = Agendamento.query.filter_by(clinica_id=clinica.id, status="solicitado")
    if eh_medico():
        query = query.filter_by(medico_id=current_user.id)
    solicitacoes = query.order_by(Agendamento.data_hora.asc()).all()
    return render_template("medico/agenda_solicitacoes.html", solicitacoes=solicitacoes)


@medico_bp.route("/agenda/<int:agendamento_id>/confirmar-solicitacao", methods=["POST"])
@login_required
@staff_required
def agenda_confirmar_solicitacao(agendamento_id):
    clinica = clinica_atual()
    filtros = dict(id=agendamento_id, clinica_id=clinica.id, status="solicitado")
    if eh_medico():
        filtros["medico_id"] = current_user.id
    agendamento = Agendamento.query.filter_by(**filtros).first_or_404()
    acao = request.form.get("acao")
    if acao == "recusar":
        agendamento.status = "cancelado"
        flash("Solicitação de agendamento recusada.", "success")
    else:
        agendamento.status = "agendado"
        flash("Agendamento confirmado.", "success")
    db.session.commit()
    return redirect(url_for("medico.agenda_solicitacoes"))


# ---------- Horário de atendimento do médico (por filial) ----------

@medico_bp.route("/medico-horarios", methods=["GET", "POST"])
@medico_bp.route("/medico-horarios/<int:medico_id>", methods=["GET", "POST"])
@login_required
@staff_required
def medico_horarios(medico_id=None):
    clinica = clinica_atual()

    # Um médico sem a permissão de gerir a equipe só configura o próprio
    # horário. Secretárias e médicos com "perm_equipe" (ex.: o médico
    # fundador da clínica) podem escolher qualquer médico da filial atual —
    # mesma regra usada nas outras telas administrativas.
    pode_escolher_medico, medico_alvo = _resolver_medico_alvo(clinica, medico_id)
    if not medico_alvo:
        flash("Nenhum médico cadastrado nesta filial ainda.", "danger")
        return redirect(url_for("medico.equipe_lista"))

    if request.method == "POST":
        horarios_existentes = {
            h.dia_semana: h
            for h in MedicoHorario.query.filter_by(clinica_id=clinica.id, medico_id=medico_alvo.id).all()
        }
        for dia_idx in range(7):
            ativo = request.form.get(f"dia_{dia_idx}_ativo") == "on"
            hora_inicio_str = request.form.get(f"dia_{dia_idx}_inicio", "").strip()
            hora_fim_str = request.form.get(f"dia_{dia_idx}_fim", "").strip()

            def parse_hora(valor):
                try:
                    return datetime.strptime(valor, "%H:%M").time() if valor else None
                except ValueError:
                    return None

            horario = horarios_existentes.get(dia_idx)
            if not horario:
                horario = MedicoHorario(clinica_id=clinica.id, medico_id=medico_alvo.id, dia_semana=dia_idx)
                db.session.add(horario)

            horario.ativo = ativo
            horario.hora_inicio = parse_hora(hora_inicio_str)
            horario.hora_fim = parse_hora(hora_fim_str)

        db.session.commit()
        flash(f"Horário de atendimento de {medico_alvo.nome} atualizado.", "success")
        return _destino_pos_onboarding("medico.medico_horarios", medico_id=medico_alvo.id)

    horarios_por_dia = {
        h.dia_semana: h
        for h in MedicoHorario.query.filter_by(clinica_id=clinica.id, medico_id=medico_alvo.id).all()
    }
    return render_template(
        "medico/medico_horarios.html",
        medico_alvo=medico_alvo,
        medicos=(medicos_da_clinica(clinica) if pode_escolher_medico else []),
        dias_semana=list(enumerate(DIAS_SEMANA)),
        horarios_por_dia=horarios_por_dia,
    )


def _resolver_medico_alvo(clinica, medico_id):
    """Mesma regra usada em toda tela "do médico": um médico sem
    perm_equipe só vê/edita os próprios dados; secretárias e médicos com
    perm_equipe podem escolher qualquer médico da filial atual. Retorna
    (pode_escolher_medico, medico_alvo) — medico_alvo é None só quando não
    há nenhum médico cadastrado na filial (caso em que o chamador deve
    redirecionar)."""
    pode_escolher_medico = current_user.perm_equipe or not eh_medico()
    if pode_escolher_medico:
        medicos = medicos_da_clinica(clinica)
        medico_alvo = next((m for m in medicos if m.id == medico_id), None)
        if not medico_alvo:
            medico_alvo = current_user if eh_medico() else (medicos[0] if medicos else None)
    else:
        medico_alvo = current_user
    return pode_escolher_medico, medico_alvo


# ---------- Meus exames agendados (agenda pessoal do médico) ----------

@medico_bp.route("/medico-agenda", methods=["GET"])
@medico_bp.route("/medico-agenda/<int:medico_id>", methods=["GET"])
@login_required
@staff_required
def medico_agenda_pessoal(medico_id=None):
    clinica = clinica_atual()
    pode_escolher_medico, medico_alvo = _resolver_medico_alvo(clinica, medico_id)
    if not medico_alvo:
        flash("Nenhum médico cadastrado nesta filial ainda.", "danger")
        return redirect(url_for("medico.equipe_lista"))

    # Só exames confirmados aparecem aqui — é a lista de trabalho do
    # médico para o que já está confirmado com o paciente, não uma agenda
    # geral (essa fica em "Agenda de exames", no Painel). Sem filtro de
    # data: um exame de hoje que já passou do horário mas ainda não foi
    # marcado como "realizado" continua precisando aparecer aqui.
    proximos = (
        Agendamento.query.filter_by(clinica_id=clinica.id, medico_id=medico_alvo.id, status="confirmado")
        .order_by(Agendamento.data_hora.asc())
        .all()
    )
    return render_template(
        "medico/medico_agenda_pessoal.html",
        medico_alvo=medico_alvo,
        medicos=(medicos_da_clinica(clinica) if pode_escolher_medico else []),
        proximos=proximos,
    )


# ---------- Bloqueio de agenda (compromisso próprio do médico) ----------

@medico_bp.route("/medico-bloqueios", methods=["GET", "POST"])
@medico_bp.route("/medico-bloqueios/<int:medico_id>", methods=["GET", "POST"])
@login_required
@staff_required
def medico_bloqueios(medico_id=None):
    clinica = clinica_atual()
    pode_escolher_medico, medico_alvo = _resolver_medico_alvo(clinica, medico_id)
    if not medico_alvo:
        flash("Nenhum médico cadastrado nesta filial ainda.", "danger")
        return redirect(url_for("medico.equipe_lista"))

    if request.method == "POST":
        dia_inteiro = request.form.get("dia_inteiro") == "on"
        data_inicio_str = request.form.get("data_inicio", "").strip()
        data_fim_str = request.form.get("data_fim", "").strip()
        hora_inicio_str = request.form.get("hora_inicio", "").strip()
        hora_fim_str = request.form.get("hora_fim", "").strip()
        motivo = request.form.get("motivo", "").strip()

        try:
            if dia_inteiro:
                data_inicio_dt = datetime.strptime(data_inicio_str, "%Y-%m-%d")
                data_fim_dt = datetime.strptime(data_fim_str or data_inicio_str, "%Y-%m-%d") + timedelta(
                    days=1, seconds=-1
                )
            else:
                data_inicio_dt = datetime.strptime(f"{data_inicio_str} {hora_inicio_str}", "%Y-%m-%d %H:%M")
                data_fim_dt = datetime.strptime(f"{data_fim_str} {hora_fim_str}", "%Y-%m-%d %H:%M")
        except ValueError:
            data_inicio_dt = data_fim_dt = None

        if not data_inicio_dt or not data_fim_dt or data_fim_dt <= data_inicio_dt:
            flash("Datas/horários inválidos — confira o período informado.", "danger")
        else:
            bloqueio = MedicoBloqueio(
                clinica_id=clinica.id, medico_id=medico_alvo.id,
                data_inicio=data_inicio_dt, data_fim=data_fim_dt,
                motivo=motivo or None, dia_inteiro=dia_inteiro,
            )
            db.session.add(bloqueio)
            db.session.commit()
            flash("Bloqueio de agenda cadastrado.", "success")
        return redirect(url_for("medico.medico_bloqueios", medico_id=medico_alvo.id))

    bloqueios = (
        MedicoBloqueio.query.filter_by(clinica_id=clinica.id, medico_id=medico_alvo.id)
        .order_by(MedicoBloqueio.data_inicio.desc())
        .all()
    )
    return render_template(
        "medico/medico_bloqueios.html",
        medico_alvo=medico_alvo,
        medicos=(medicos_da_clinica(clinica) if pode_escolher_medico else []),
        bloqueios=bloqueios,
    )


@medico_bp.route("/medico-bloqueios/<int:bloqueio_id>/remover", methods=["POST"])
@login_required
@staff_required
def medico_bloqueio_remover(bloqueio_id):
    clinica = clinica_atual()
    filtros = dict(id=bloqueio_id, clinica_id=clinica.id)
    if eh_medico() and not current_user.perm_equipe:
        filtros["medico_id"] = current_user.id
    bloqueio = MedicoBloqueio.query.filter_by(**filtros).first_or_404()
    medico_id = bloqueio.medico_id
    db.session.delete(bloqueio)
    db.session.commit()
    flash("Bloqueio removido.", "success")
    return redirect(url_for("medico.medico_bloqueios", medico_id=medico_id))


# ---------- Atendimento (continuidade/encerramento da consulta) ----------

@medico_bp.route("/agenda/<int:agendamento_id>/atendimento", methods=["GET", "POST"])
@login_required
@staff_required
def atendimento(agendamento_id):
    clinica = clinica_atual()
    filtros = dict(id=agendamento_id, clinica_id=clinica.id)
    if eh_medico():
        filtros["medico_id"] = current_user.id
    agendamento = Agendamento.query.filter_by(**filtros).first_or_404()

    if request.method == "POST":
        agendamento.notas_atendimento = request.form.get("notas_atendimento", "").strip() or None
        if request.form.get("encerrar") == "on":
            agendamento.encerrado_em = datetime.utcnow()
            agendamento.status = "realizado"
            flash("Atendimento encerrado.", "success")
        else:
            flash("Observações da consulta salvas.", "success")
        db.session.commit()
        return redirect(url_for("medico.atendimento", agendamento_id=agendamento.id))

    # Perguntas que o paciente fez pelo app sobre este exame — pra o
    # médico já chegar na consulta sabendo o que foi perguntado. Prioriza
    # o vínculo direto com este agendamento (ChatMensagem.agendamento_id);
    # o "or" com o filtro antigo por exame é só pra perguntas feitas antes
    # desse vínculo existir, que ficaram sem agendamento_id preenchido.
    mensagens_chat = (
        ChatMensagem.query.filter(
            ChatMensagem.paciente_id == agendamento.paciente_id,
            or_(
                ChatMensagem.agendamento_id == agendamento.id,
                and_(ChatMensagem.agendamento_id.is_(None), ChatMensagem.exame_id == agendamento.exame_id),
            ),
        )
        .order_by(ChatMensagem.criado_em.desc())
        .all()
    )
    # Notas de atendimentos anteriores do mesmo paciente (com qualquer
    # médico/exame) — reaproveitáveis como histórico de referência. Cada
    # um vira seu próprio expand panel na tela, mostrando ao abrir as
    # perguntas feitas especificamente para aquela consulta.
    atendimentos_anteriores_raw = (
        Agendamento.query.filter(
            Agendamento.paciente_id == agendamento.paciente_id,
            Agendamento.id != agendamento.id,
            Agendamento.notas_atendimento.isnot(None),
        )
        .order_by(Agendamento.data_hora.desc())
        .limit(10)
        .all()
    )
    atendimentos_anteriores = []
    for a in atendimentos_anteriores_raw:
        mensagens_da_consulta = (
            ChatMensagem.query.filter(
                ChatMensagem.paciente_id == agendamento.paciente_id,
                or_(
                    ChatMensagem.agendamento_id == a.id,
                    # Fallback para perguntas de antes do vínculo direto
                    # existir: aproxima pela data (mesmo exame, feitas até
                    # o dia daquela consulta).
                    and_(
                        ChatMensagem.agendamento_id.is_(None),
                        ChatMensagem.exame_id == a.exame_id,
                        ChatMensagem.criado_em <= a.data_hora,
                    ),
                ),
            )
            .order_by(ChatMensagem.criado_em.desc())
            .all()
        )
        atendimentos_anteriores.append((a, mensagens_da_consulta))

    return render_template(
        "medico/atendimento.html",
        agendamento=agendamento,
        mensagens_chat=mensagens_chat,
        atendimentos_anteriores=atendimentos_anteriores,
    )


# ---------- Resultado de exame (upload de PDF) ----------

def _pasta_resultados():
    pasta = os.path.join(current_app.instance_path, PASTA_RESULTADOS)
    os.makedirs(pasta, exist_ok=True)
    return pasta


@medico_bp.route("/agenda/<int:agendamento_id>/resultado", methods=["GET", "POST"])
@login_required
@staff_required
def resultado_upload(agendamento_id):
    clinica = clinica_atual()
    filtros = dict(id=agendamento_id, clinica_id=clinica.id)
    if eh_medico():
        filtros["medico_id"] = current_user.id
    agendamento = Agendamento.query.filter_by(**filtros).first_or_404()

    if request.method == "POST":
        arquivo = request.files.get("arquivo_pdf")
        if not arquivo or not arquivo.filename:
            flash("Selecione um arquivo PDF.", "danger")
            return redirect(url_for("medico.resultado_upload", agendamento_id=agendamento.id))
        if not arquivo.filename.lower().endswith(".pdf"):
            flash("Só é possível anexar arquivos PDF.", "danger")
            return redirect(url_for("medico.resultado_upload", agendamento_id=agendamento.id))

        nome_original = secure_filename(arquivo.filename)
        nome_salvo = f"{uuid.uuid4().hex}_{nome_original}"
        arquivo.save(os.path.join(_pasta_resultados(), nome_salvo))

        if agendamento.resultado:
            # Substitui o resultado anterior (mesmo agendamento não deveria
            # ter mais de um PDF de resultado).
            caminho_antigo = os.path.join(_pasta_resultados(), agendamento.resultado.caminho_arquivo)
            if os.path.exists(caminho_antigo):
                os.remove(caminho_antigo)
            agendamento.resultado.nome_arquivo = nome_original
            agendamento.resultado.caminho_arquivo = nome_salvo
            agendamento.resultado.enviado_por = current_user.nome
        else:
            db.session.add(ResultadoExame(
                agendamento_id=agendamento.id, nome_arquivo=nome_original,
                caminho_arquivo=nome_salvo, enviado_por=current_user.nome,
            ))
        db.session.commit()
        flash("Resultado anexado com sucesso. O paciente já pode baixá-lo pelo aplicativo.", "success")
        return redirect(url_for("medico.agenda"))

    return render_template("medico/resultado_upload.html", agendamento=agendamento)


# ---------- Pagamento e descontos ----------

@medico_bp.route("/financeiro/receber-pagamento", methods=["GET"])
@login_required
@staff_required
def financeiro_receber_pagamento():
    """Lista os agendamentos que ainda não têm pagamento registrado, pra
    quem cuida do financeiro dar baixa sem precisar navegar pela agenda —
    ver medico.pagamento_registrar para o registro em si."""
    clinica = clinica_atual()
    pendentes = (
        Agendamento.query
        .outerjoin(Pagamento, Pagamento.agendamento_id == Agendamento.id)
        .filter(
            Agendamento.clinica_id == clinica.id,
            Agendamento.status == "realizado",
            Pagamento.id.is_(None),
        )
        .order_by(Agendamento.data_hora.desc())
        .all()
    )
    return render_template("medico/financeiro_receber_pagamento.html", pendentes=pendentes)


@medico_bp.route("/descontos", methods=["GET", "POST"])
@login_required
@staff_required
@permissao_required("perm_dados_clinica")
def descontos_lista():
    clinica = clinica_atual()

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        percentual = _parse_valor_decimal(request.form.get("percentual", ""))
        if not nome or percentual is None:
            flash("Informe o nome e o percentual do desconto.", "danger")
        else:
            db.session.add(DescontoConfig(clinica_id=clinica.id, nome=nome, percentual=percentual, ativo=True))
            db.session.commit()
            flash("Desconto cadastrado.", "success")
        return redirect(url_for("medico.descontos_lista"))

    descontos = DescontoConfig.query.filter_by(clinica_id=clinica.id).order_by(DescontoConfig.nome).all()
    return render_template("medico/descontos_lista.html", descontos=descontos)


@medico_bp.route("/descontos/<int:desconto_id>/alternar", methods=["POST"])
@login_required
@staff_required
@permissao_required("perm_dados_clinica")
def descontos_alternar(desconto_id):
    clinica = clinica_atual()
    desconto = DescontoConfig.query.filter_by(id=desconto_id, clinica_id=clinica.id).first_or_404()
    desconto.ativo = not desconto.ativo
    db.session.commit()
    return redirect(url_for("medico.descontos_lista"))


@medico_bp.route("/agenda/<int:agendamento_id>/pagamento", methods=["GET", "POST"])
@login_required
@staff_required
def pagamento_registrar(agendamento_id):
    clinica = clinica_atual()
    filtros = dict(id=agendamento_id, clinica_id=clinica.id)
    if eh_medico():
        filtros["medico_id"] = current_user.id
    agendamento = Agendamento.query.filter_by(**filtros).first_or_404()
    descontos = DescontoConfig.query.filter_by(clinica_id=clinica.id, ativo=True).order_by(DescontoConfig.nome).all()

    if request.method == "POST":
        if not agendamento.exame.preco:
            flash("Este exame não tem preço cadastrado — cadastre o preço antes de registrar o pagamento.", "danger")
            return redirect(url_for("medico.pagamento_registrar", agendamento_id=agendamento.id))

        desconto_id = request.form.get("desconto_id", type=int)
        desconto = next((d for d in descontos if d.id == desconto_id), None)
        forma_pagamento = request.form.get("forma_pagamento", "").strip() or None

        valor_procedimento = agendamento.exame.preco
        percentual = desconto.percentual if desconto else Decimal("0")
        valor_final = (valor_procedimento * (Decimal("100") - percentual) / Decimal("100")).quantize(Decimal("0.01"))

        pagamento = agendamento.pagamento or Pagamento(agendamento_id=agendamento.id)
        pagamento.valor_procedimento = valor_procedimento
        pagamento.desconto_id = desconto.id if desconto else None
        pagamento.desconto_nome = desconto.nome if desconto else None
        pagamento.desconto_percentual = percentual
        pagamento.valor_final = valor_final
        pagamento.forma_pagamento = forma_pagamento
        pagamento.registrado_por = current_user.nome
        pagamento.pago_em = datetime.utcnow()
        db.session.add(pagamento)
        db.session.commit()
        flash("Pagamento registrado com sucesso.", "success")
        return redirect(url_for("medico.pagamento_comprovante", agendamento_id=agendamento.id))

    return render_template("medico/pagamento_form.html", agendamento=agendamento, descontos=descontos)


@medico_bp.route("/agenda/<int:agendamento_id>/pagamento/comprovante")
@login_required
@staff_required
def pagamento_comprovante(agendamento_id):
    clinica = clinica_atual()
    filtros = dict(id=agendamento_id, clinica_id=clinica.id)
    if eh_medico():
        filtros["medico_id"] = current_user.id
    agendamento = Agendamento.query.filter_by(**filtros).first_or_404()
    if not agendamento.pagamento:
        flash("Nenhum pagamento registrado para este agendamento ainda.", "danger")
        return redirect(url_for("medico.pagamento_registrar", agendamento_id=agendamento.id))
    return render_template("medico/pagamento_comprovante.html", agendamento=agendamento, clinica=clinica)


@medico_bp.route("/agenda/<int:agendamento_id>/pagamento/emitir-nfse", methods=["POST"])
@login_required
@staff_required
def pagamento_emitir_nfse(agendamento_id):
    """Emite a NFS-e do pagamento já registrado (ver app/nfse_nacional.py
    para o fluxo completo: monta o DPS, assina com o certificado da
    clínica e tenta enviar ao Ambiente de Dados Nacional). Em modo
    simulação, nada é assinado nem enviado — só marca a nota como
    simulada, sem valor fiscal, pra testar o fluxo de tela."""
    clinica = clinica_atual()
    filtros = dict(id=agendamento_id, clinica_id=clinica.id)
    if eh_medico():
        filtros["medico_id"] = current_user.id
    agendamento = Agendamento.query.filter_by(**filtros).first_or_404()
    pagamento = agendamento.pagamento
    if not pagamento:
        flash("Registre o pagamento antes de emitir a nota fiscal.", "danger")
        return redirect(url_for("medico.pagamento_registrar", agendamento_id=agendamento.id))

    try:
        resultado = emitir_nfse(clinica, agendamento.paciente, agendamento, pagamento)
    except ErroEmissaoNfse as erro:
        flash(str(erro), "danger")
        return redirect(url_for("medico.pagamento_comprovante", agendamento_id=agendamento.id))
    except Exception as erro:
        flash(f"Erro inesperado ao emitir a NFS-e: {erro}", "danger")
        return redirect(url_for("medico.pagamento_comprovante", agendamento_id=agendamento.id))

    pagamento.nfse_status = resultado["status"]
    pagamento.nfse_numero_dps = resultado["numero_dps"]
    pagamento.nfse_numero = resultado.get("numero_nfse")
    pagamento.nfse_codigo_verificacao = resultado.get("codigo_verificacao")
    pagamento.nfse_xml_assinado = resultado.get("xml_assinado")
    pagamento.nfse_erro = resultado.get("erro")
    pagamento.nfse_emitida_em = datetime.utcnow()
    clinica.fiscal_rps_proximo_numero = resultado["numero_dps"]
    db.session.commit()

    if resultado["status"] == "simulada":
        flash("NFS-e simulada com sucesso (modo simulação — sem valor fiscal).", "success")
    elif resultado["status"] == "enviada":
        flash("DPS assinado e enviado ao Ambiente de Dados Nacional com sucesso.", "success")
    else:
        flash(
            "DPS assinado com o certificado da clínica, mas o envio automático ao Ambiente de "
            "Dados Nacional falhou (" + (resultado.get("erro") or "motivo desconhecido") +
            "). O XML assinado foi salvo — copie-o abaixo se precisar enviar manualmente pelo "
            "emissor web da prefeitura/ADN.",
            "warning",
        )
    return redirect(url_for("medico.pagamento_comprovante", agendamento_id=agendamento.id))


# ---------- Perguntas pendentes (aprendizado da "IA") ----------

@medico_bp.route("/perguntas")
@login_required
@staff_required
def perguntas_pendentes():
    clinica = clinica_atual()
    pendentes_q = PerguntaPendente.query.filter_by(clinica_id=clinica.id, status="pendente")
    # Respostas que a IA já rascunhou e estão esperando o médico revisar,
    # editar se precisar, e aprovar antes de irem para o paciente.
    aguardando_q = PerguntaPendente.query.filter_by(clinica_id=clinica.id, status="aguardando_aprovacao")
    respondidas_q = PerguntaPendente.query.filter_by(clinica_id=clinica.id, status="respondida")

    if eh_medico():
        # O médico só acompanha perguntas sobre exames de sua
        # responsabilidade; perguntas gerais (sem exame associado) ficam
        # só para a secretária responder.
        pendentes_q = pendentes_q.join(Exame, PerguntaPendente.exame_id == Exame.id).filter(
            or_(Exame.medico_id == current_user.id, Exame.medicos_extra.any(id=current_user.id))
        )
        aguardando_q = aguardando_q.join(Exame, PerguntaPendente.exame_id == Exame.id).filter(
            or_(Exame.medico_id == current_user.id, Exame.medicos_extra.any(id=current_user.id))
        )
        respondidas_q = respondidas_q.join(Exame, PerguntaPendente.exame_id == Exame.id).filter(
            or_(Exame.medico_id == current_user.id, Exame.medicos_extra.any(id=current_user.id))
        )

    pendentes = pendentes_q.order_by(PerguntaPendente.criado_em.desc()).all()
    aguardando = aguardando_q.order_by(PerguntaPendente.criado_em.desc()).all()
    respondidas = respondidas_q.order_by(PerguntaPendente.respondida_em.desc()).limit(20).all()
    return render_template(
        "medico/perguntas.html", pendentes=pendentes, aguardando=aguardando, respondidas=respondidas,
    )


@medico_bp.route("/perguntas/<int:pergunta_id>/responder", methods=["POST"])
@login_required
@staff_required
def perguntas_responder(pergunta_id):
    clinica = clinica_atual()
    pergunta = PerguntaPendente.query.filter_by(id=pergunta_id, clinica_id=clinica.id).first_or_404()

    if eh_medico() and (not pergunta.exame or not pergunta.exame.medico_pode_atender(current_user.id)):
        flash("Você só pode responder perguntas sobre os seus próprios exames.", "danger")
        return redirect(url_for("medico.perguntas_pendentes"))

    resposta = request.form.get("resposta", "").strip()

    if not resposta:
        flash("Digite uma resposta antes de salvar.", "danger")
        return redirect(url_for("medico.perguntas_pendentes"))

    pergunta.resposta = resposta
    pergunta.status = "respondida"
    pergunta.respondida_por = current_user.nome
    pergunta.respondida_em = datetime.utcnow()

    # "Aprendizado": a pergunta+resposta entra na base de FAQ para uso futuro
    novo_faq = FaqItem(
        clinica_id=clinica.id,
        exame_id=pergunta.exame_id,
        pergunta=pergunta.pergunta,
        resposta=resposta,
        criado_por=current_user.nome,
    )
    db.session.add(novo_faq)
    db.session.commit()

    flash("Resposta salva e adicionada à base de conhecimento da IA.", "success")
    return redirect(url_for("medico.perguntas_pendentes"))


# ---------- Base de FAQ (consulta/gestão manual) ----------

@medico_bp.route("/faq")
@login_required
@staff_required
def faq_lista():
    clinica = clinica_atual()
    query = FaqItem.query.filter_by(clinica_id=clinica.id)
    if eh_medico():
        query = query.join(Exame, FaqItem.exame_id == Exame.id).filter(
            or_(Exame.medico_id == current_user.id, Exame.medicos_extra.any(id=current_user.id))
        )
    itens = query.order_by(FaqItem.criado_em.desc()).all()
    return render_template("medico/faq_lista.html", itens=itens)


@medico_bp.route("/faq/novo", methods=["GET", "POST"])
@login_required
@staff_required
def faq_novo():
    clinica = clinica_atual()
    exames_query = Exame.query.filter_by(clinica_id=clinica.id)
    if eh_medico():
        exames_query = exames_query.filter(
            or_(Exame.medico_id == current_user.id, Exame.medicos_extra.any(id=current_user.id))
        )
    exames = exames_query.order_by(Exame.nome).all()

    if request.method == "POST":
        exame_id = request.form.get("exame_id", type=int)

        if eh_medico():
            # O médico só pode criar itens de FAQ vinculados a um dos
            # seus próprios exames — não pode criar perguntas gerais.
            exame_valido = exame_id and any(e.id == exame_id for e in exames)
            if not exame_valido:
                flash("Escolha um dos seus exames para vincular a pergunta.", "danger")
                return render_template("medico/faq_form.html", exames=exames)

        pergunta = request.form.get("pergunta", "").strip()
        resposta = request.form.get("resposta", "").strip()

        if not pergunta or not resposta:
            flash("Pergunta e resposta são obrigatórias.", "danger")
            return render_template("medico/faq_form.html", exames=exames)

        item = FaqItem(
            clinica_id=clinica.id,
            exame_id=exame_id,
            pergunta=pergunta,
            resposta=resposta,
            criado_por=current_user.nome,
        )
        db.session.add(item)
        db.session.commit()
        flash("Item adicionado à base de conhecimento.", "success")
        return redirect(url_for("medico.faq_lista"))

    return render_template("medico/faq_form.html", exames=exames)


# ---------- Dados da clínica (gerais, endereço, fiscais) ----------

@medico_bp.route("/clinica/configuracoes", methods=["GET", "POST"])
@medico_bp.route("/clinica/configuracoes/<int:filial_id>", methods=["GET", "POST"])
@login_required
@staff_required
@permissao_required("perm_dados_clinica")
def clinica_configuracoes(filial_id=None):
    clinica_sessao = clinica_atual()
    if filial_id:
        # Permite editar qualquer filial da mesma empresa, não só a
        # selecionada na sessão — usado a partir da tela "Filiais".
        clinica = Clinica.query.filter_by(id=filial_id, empresa_id=clinica_sessao.empresa_id).first_or_404()
    else:
        clinica = clinica_sessao

    if request.method == "POST":
        # Dados gerais
        nome = request.form.get("nome", "").strip()
        if nome:
            clinica.nome = nome
        clinica.razao_social = request.form.get("razao_social", "").strip()
        clinica.cnpj = request.form.get("cnpj", "").strip()
        telefone = request.form.get("telefone", "").strip()
        if telefone:
            clinica.telefone = telefone
        email_contato = request.form.get("email_contato", "").strip()
        if email_contato:
            clinica.email_contato = email_contato

        # Endereço
        clinica.cep = request.form.get("cep", "").strip()
        clinica.rua = request.form.get("rua", "").strip()
        clinica.numero = request.form.get("numero", "").strip()
        clinica.complemento = request.form.get("complemento", "").strip()
        clinica.bairro = request.form.get("bairro", "").strip()
        clinica.cidade = request.form.get("cidade", "").strip()
        clinica.uf = request.form.get("uf", "").strip().upper() or None

        # Dados fiscais
        clinica.inscricao_estadual = request.form.get("inscricao_estadual", "").strip()
        clinica.regime_tributario = request.form.get("regime_tributario", "").strip()
        clinica.cnae = request.form.get("cnae", "").strip()
        clinica.codigo_ibge_municipio = request.form.get("codigo_ibge_municipio", "").strip()

        db.session.commit()
        flash("Dados da clínica atualizados com sucesso.", "success")
        return _destino_pos_onboarding("medico.clinica_configuracoes", filial_id=clinica.id)

    return render_template(
        "medico/clinica_configuracoes.html",
        clinica=clinica,
    )


@medico_bp.route("/clinica/emissao-fiscal", methods=["POST"])
@medico_bp.route("/clinica/emissao-fiscal/<int:filial_id>", methods=["POST"])
@login_required
@staff_required
@permissao_required("perm_dados_clinica")
def clinica_emissao_fiscal(filial_id=None):
    """Salva a configuração de emissão de NFS-e (ambiente, provedor,
    inscrição municipal, código de serviço, alíquota de ISS, série/número
    do RPS) — separado do formulário principal de "Dados da clínica"
    porque fica em outro <form> na mesma página (ver
    medico/clinica_configuracoes.html). O upload do certificado digital em
    si tem sua própria rota, `clinica_certificado_upload`, abaixo."""
    clinica_sessao = clinica_atual()
    if filial_id:
        clinica = Clinica.query.filter_by(id=filial_id, empresa_id=clinica_sessao.empresa_id).first_or_404()
    else:
        clinica = clinica_sessao

    clinica.fiscal_ambiente = request.form.get("fiscal_ambiente", "homologacao").strip() or "homologacao"
    clinica.fiscal_modo_simulacao = request.form.get("fiscal_modo_simulacao") == "on"
    clinica.fiscal_simular_falha_conexao = request.form.get("fiscal_simular_falha_conexao") == "on"

    clinica.fiscal_provedor_emissao = request.form.get("fiscal_provedor_emissao", "nenhum").strip() or "nenhum"

    # O campo abaixo é um segredo (token do provedor) — o formulário nunca
    # mostra o valor real de volta (só um placeholder com pontos), então
    # só regravamos quando a pessoa realmente digita algo novo. Deixar em
    # branco mantém o valor já salvo sem alteração.
    token_novo = request.form.get("fiscal_provedor_token_api", "").strip()
    if token_novo:
        clinica.fiscal_provedor_token_cripto = criptografar_texto(token_novo)

    clinica.fiscal_inscricao_municipal = request.form.get("fiscal_inscricao_municipal", "").strip()
    clinica.fiscal_codigo_servico = request.form.get("fiscal_codigo_servico", "").strip()

    aliquota = request.form.get("fiscal_aliquota_iss", "").strip().replace(",", ".")
    try:
        clinica.fiscal_aliquota_iss = round(float(aliquota), 2) if aliquota else None
    except ValueError:
        clinica.fiscal_aliquota_iss = None

    clinica.fiscal_rps_serie = request.form.get("fiscal_rps_serie", "").strip() or None

    proximo_numero = request.form.get("fiscal_rps_proximo_numero", "").strip()
    clinica.fiscal_rps_proximo_numero = int(proximo_numero) if proximo_numero.isdigit() else None

    db.session.commit()
    flash("Dados fiscais de emissão atualizados com sucesso.", "success")
    return _destino_pos_onboarding("medico.clinica_configuracoes", filial_id=clinica.id)


@medico_bp.route("/clinica/certificado", methods=["POST"])
@medico_bp.route("/clinica/certificado/<int:filial_id>", methods=["POST"])
@login_required
@staff_required
@permissao_required("perm_dados_clinica")
def clinica_certificado_upload(filial_id=None):
    """Recebe o certificado digital e-CNPJ (.pfx/.p12) e a senha dele,
    valida que o arquivo realmente abre com essa senha (usando a biblioteca
    `cryptography`) e só então salva — o arquivo e a senha ficam
    criptografados no banco (ver app/cripto_fiscal.py), nunca em texto
    puro nem em disco."""
    clinica_sessao = clinica_atual()
    if filial_id:
        clinica = Clinica.query.filter_by(id=filial_id, empresa_id=clinica_sessao.empresa_id).first_or_404()
    else:
        clinica = clinica_sessao

    arquivo = request.files.get("certificado_arquivo")
    senha = request.form.get("certificado_senha", "")

    if not arquivo or not arquivo.filename:
        flash("Selecione o arquivo do certificado (.pfx) antes de enviar.", "danger")
        return _destino_pos_onboarding("medico.clinica_configuracoes", filial_id=clinica.id)

    if not senha:
        flash("Informe a senha do certificado.", "danger")
        return _destino_pos_onboarding("medico.clinica_configuracoes", filial_id=clinica.id)

    conteudo = arquivo.read()
    # Um certificado .pfx/.p12 normal tem poucos KB — um arquivo muito
    # maior do que isso quase certamente não é um certificado válido, então
    # rejeitamos antes mesmo de tentar abrir (evita gastar memória com um
    # upload indevido).
    if len(conteudo) > 5 * 1024 * 1024:
        flash("Arquivo muito grande para ser um certificado válido.", "danger")
        return _destino_pos_onboarding("medico.clinica_configuracoes", filial_id=clinica.id)

    try:
        _chave_privada, certificado, _cadeia = pkcs12.load_key_and_certificates(
            conteudo, senha.encode("utf-8")
        )
    except Exception:
        flash(
            "Não foi possível abrir o certificado — verifique se o arquivo "
            "é um .pfx/.p12 válido e se a senha está correta.",
            "danger",
        )
        return _destino_pos_onboarding("medico.clinica_configuracoes", filial_id=clinica.id)

    if certificado is None:
        flash("O arquivo enviado não contém um certificado válido.", "danger")
        return _destino_pos_onboarding("medico.clinica_configuracoes", filial_id=clinica.id)

    # Tentativa (best-effort) de extrair o CNPJ a partir do "Common Name" do
    # certificado — certificados e-CNPJ da ICP-Brasil costumam trazer o
    # CNPJ (14 dígitos) ali, no formato "RAZAO SOCIAL:CNPJ". Serve só para
    # exibição na tela; se não conseguir extrair, o certificado é salvo do
    # mesmo jeito.
    cnpj_extraido = None
    try:
        cn = certificado.subject.rfc4514_string()
        digitos = re.findall(r"\d{14}", cn)
        if digitos:
            cnpj_extraido = digitos[0]
    except Exception:
        cnpj_extraido = None

    if hasattr(certificado, "not_valid_after_utc"):
        validade = certificado.not_valid_after_utc.date()
    else:
        validade = certificado.not_valid_after.date()

    clinica.fiscal_certificado_pfx = criptografar_bytes(conteudo)
    clinica.fiscal_certificado_senha_cripto = criptografar_texto(senha)
    clinica.fiscal_certificado_cnpj = cnpj_extraido
    clinica.fiscal_certificado_validade = validade
    db.session.commit()

    flash("Certificado digital validado e salvo com sucesso.", "success")
    return _destino_pos_onboarding("medico.clinica_configuracoes", filial_id=clinica.id)


# ---------- Filiais da empresa ----------

@medico_bp.route("/filiais")
@login_required
@staff_required
@permissao_required("perm_filiais")
def filiais_lista():
    clinica = clinica_atual()
    filiais = Clinica.query.filter_by(empresa_id=clinica.empresa_id).order_by(Clinica.nome).all()
    return render_template("medico/filiais_lista.html", filiais=filiais, clinica_atual_id=clinica.id)


@medico_bp.route("/filiais/nova", methods=["GET", "POST"])
@login_required
@staff_required
@permissao_required("perm_filiais")
def filiais_nova():
    clinica = clinica_atual()

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        if not nome:
            flash("Informe o nome da nova filial.", "danger")
            return render_template("medico/filiais_form.html")

        if Clinica.query.filter_by(empresa_id=clinica.empresa_id, nome=nome).first():
            flash("Já existe uma filial com esse nome nesta empresa.", "danger")
            return render_template("medico/filiais_form.html")

        nova_filial = Clinica(empresa_id=clinica.empresa_id, nome=nome)
        db.session.add(nova_filial)
        db.session.flush()

        # Quem cadastra a filial já fica vinculado a ela, pra poder
        # começar a trabalhar por lá imediatamente.
        vinculo = ClinicaMembro(clinica_id=nova_filial.id, usuario_id=current_user.id, ativo=True)
        db.session.add(vinculo)
        db.session.commit()

        flash(
            f"Filial '{nova_filial.nome}' cadastrada com sucesso. Use o link 'trocar' na barra de "
            "navegação para começar a trabalhar nela.",
            "success",
        )
        return redirect(url_for("medico.filiais_lista"))

    return render_template("medico/filiais_form.html")


# ---------- Equipe (médicos e secretárias da clínica atual) ----------

@medico_bp.route("/equipe-membros")
@login_required
@staff_required
@permissao_required("perm_equipe")
def equipe_lista():
    clinica = clinica_atual()
    filial_ids = [f.id for f in Clinica.query.filter_by(empresa_id=clinica.empresa_id).all()]
    membros = (
        ClinicaMembro.query.filter(ClinicaMembro.clinica_id.in_(filial_ids))
        .order_by(ClinicaMembro.clinica_id)
        .all()
    )
    return render_template("medico/equipe_lista.html", membros=membros)


@medico_bp.route("/equipe-membros/novo", methods=["GET", "POST"])
@login_required
@staff_required
@permissao_required("perm_equipe")
def equipe_novo():
    clinica = clinica_atual()
    filiais = Clinica.query.filter_by(empresa_id=clinica.empresa_id).order_by(Clinica.nome).all()

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        email = request.form.get("email", "").strip().lower()
        papel = request.form.get("papel", "secretaria")
        senha = request.form.get("senha", "").strip()
        filial_id = request.form.get("filial_id", type=int)

        filial = next((f for f in filiais if f.id == filial_id), None)
        if not filial:
            flash("Escolha em qual filial essa pessoa vai atuar.", "danger")
            return render_template("medico/equipe_form.html", filiais=filiais)

        if not email or papel not in ("medico", "secretaria"):
            flash("Preencha o e-mail e escolha o tipo (médico ou secretária).", "danger")
            return render_template("medico/equipe_form.html", filiais=filiais)

        usuario_existente = Usuario.query.filter_by(email=email).first()

        if usuario_existente:
            # A conta já existe na plataforma (pode ser de outra clínica) —
            # só criamos o vínculo dela com esta filial, sem duplicar a conta.
            if not usuario_existente.is_staff:
                flash("Esse e-mail já está cadastrado, mas não é uma conta de médico/secretária.", "danger")
                return render_template("medico/equipe_form.html", filiais=filiais)

            if usuario_existente.tipo != papel:
                flash(
                    f"Esse e-mail já está cadastrado como '{usuario_existente.tipo}' em outra clínica. "
                    "Não é possível vinculá-lo com um papel diferente.",
                    "danger",
                )
                return render_template("medico/equipe_form.html", filiais=filiais)

            vinculo_existente = ClinicaMembro.query.filter_by(
                clinica_id=filial.id, usuario_id=usuario_existente.id
            ).first()
            if vinculo_existente:
                flash("Esse usuário já faz parte dessa filial.", "warning")
                return _destino_pos_onboarding("medico.equipe_lista")

            vinculo = ClinicaMembro(clinica_id=filial.id, usuario_id=usuario_existente.id, ativo=True)
            db.session.add(vinculo)
            db.session.commit()
            flash(f"{usuario_existente.nome} foi vinculado(a) à filial '{filial.nome}'.", "success")
            return _destino_pos_onboarding("medico.equipe_lista")

        # Conta nova
        if not nome:
            flash("Informe o nome da pessoa.", "danger")
            return render_template("medico/equipe_form.html", filiais=filiais)

        senha_final = senha or "123456"
        usuario = Usuario(nome=nome, email=email, tipo=papel)
        usuario.set_senha(senha_final)
        # As permissões administrativas (pacientes, equipe, filiais, dados
        # da clínica) vêm dos checkboxes do formulário — nem toda clínica
        # tem secretária, então quem cadastra escolhe explicitamente quais
        # telas administrativas essa pessoa vai poder acessar.
        usuario.perm_pacientes = request.form.get("perm_pacientes") == "on"
        usuario.perm_equipe = request.form.get("perm_equipe") == "on"
        usuario.perm_filiais = request.form.get("perm_filiais") == "on"
        usuario.perm_dados_clinica = request.form.get("perm_dados_clinica") == "on"
        db.session.add(usuario)
        db.session.flush()

        vinculo = ClinicaMembro(clinica_id=filial.id, usuario_id=usuario.id, ativo=True)
        db.session.add(vinculo)
        db.session.commit()

        flash(
            f"{nome} cadastrado(a) como {papel} na filial '{filial.nome}'. Senha de acesso inicial: {senha_final}",
            "success",
        )
        return _destino_pos_onboarding("medico.equipe_lista")

    return render_template("medico/equipe_form.html", filiais=filiais)


@medico_bp.route("/equipe-membros/<int:usuario_id>/permissoes", methods=["GET", "POST"])
@login_required
@staff_required
@permissao_required("perm_equipe")
def equipe_permissoes(usuario_id):
    """Ajusta quais telas administrativas uma pessoa da equipe pode
    acessar. É uma permissão da conta (vale em todas as filiais em que a
    pessoa atua), não só desta filial."""
    clinica = clinica_atual()
    filial_ids = [f.id for f in Clinica.query.filter_by(empresa_id=clinica.empresa_id).all()]
    membro = ClinicaMembro.query.filter(
        ClinicaMembro.usuario_id == usuario_id, ClinicaMembro.clinica_id.in_(filial_ids)
    ).first_or_404()
    usuario_alvo = membro.usuario

    if request.method == "POST":
        usuario_alvo.perm_pacientes = request.form.get("perm_pacientes") == "on"
        usuario_alvo.perm_equipe = request.form.get("perm_equipe") == "on"
        usuario_alvo.perm_filiais = request.form.get("perm_filiais") == "on"
        usuario_alvo.perm_dados_clinica = request.form.get("perm_dados_clinica") == "on"
        db.session.commit()
        flash(f"Permissões de {usuario_alvo.nome} atualizadas.", "success")
        return redirect(url_for("medico.equipe_lista"))

    return render_template("medico/equipe_permissoes.html", usuario_alvo=usuario_alvo)


@medico_bp.route("/equipe-membros/<int:membro_id>/remover", methods=["POST"])
@login_required
@staff_required
@permissao_required("perm_equipe")
def equipe_remover(membro_id):
    clinica = clinica_atual()
    filial_ids = [f.id for f in Clinica.query.filter_by(empresa_id=clinica.empresa_id).all()]
    membro = ClinicaMembro.query.filter(
        ClinicaMembro.id == membro_id, ClinicaMembro.clinica_id.in_(filial_ids)
    ).first_or_404()

    if membro.usuario_id == current_user.id:
        flash("Você não pode remover a si mesmo da clínica.", "danger")
        return redirect(url_for("medico.equipe_lista"))

    db.session.delete(membro)
    db.session.commit()
    flash("Membro removido da clínica.", "success")
    return redirect(url_for("medico.equipe_lista"))
