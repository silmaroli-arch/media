import os
import re
import secrets
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import (
    Blueprint, render_template, redirect, url_for, request, flash, jsonify, session,
    send_from_directory, send_file, current_app,
)
from werkzeug.utils import secure_filename
from flask_login import login_required, current_user, logout_user
from sqlalchemy import or_, and_, func

from app.extensions import db
from app.models import (
    Paciente, Usuario, Exame, Agendamento, FaqItem, Empresa,
    PerguntaPendente, ClinicaMembro, MedicoHorario, MedicoBloqueio, Clinica,
    PreparoModelo, PreparoCorte, PreparoMedicamentoSuspenso, PreparoInfoGeral, PreparoAlimento,
    PreparoExameAnterior, PreparoMedicamentoMantido, Medicamento, normalizar_telefone,
    ChatMensagem, ResultadoExame, DescontoConfig, Pagamento, EvolucaoClinica,
    ProcedimentoGastro, ProcedimentoPolipo,
    ConviteVinculo, gerar_codigo_mestre_medico, encontrar_conta_paciente, encontrar_conta_paciente_por_cpf, formatar_nome_proprio,
    cep_incompleto, telefone_incompleto, validar_cpf,
)
from app.clinica_utils import (
    clinica_atual, clinicas_do_usuario, selecionar_clinica,
    empresa_atual, empresas_do_usuario, selecionar_empresa,
    filiais_atuais, filiais_atuais_ids,
)
from app.pdf_preparo import extrair_sugestao_de_pdf, gerar_xlsx_da_sugestao
from app.xlsx_preparo import extrair_sugestoes_de_xlsx
from app.agendamento_otimizador import sugerir_horarios, medico_tem_bloqueio, conflito_de_agenda
from app.cripto_fiscal import criptografar_bytes, criptografar_texto
from app.cripto_clinico import criptografar_bytes as criptografar_bytes_clinico, criptografar_texto as criptografar_texto_clinico
from app.nfse_nacional import emitir_nfse, reenviar_nfse_pendente, gerar_pdf_contingencia, ErroEmissaoNfse
from app.assinatura_clinica import assinar_evolucao_se_possivel, ErroAssinatura
from app.auditoria_clinica import registrar_acesso
from app.prontuario_pdf import gerar_pdf_prontuario
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


def _pessoa_da_empresa(usuario_id, empresa, filial_ids):
    """O usuário faz parte desta empresa? Ou por vínculo de filial
    (ClinicaMembro), ou por ser quem FUNDOU a empresa
    (Usuario.empresa_fundadora_id) - o fundador faz parte da equipe desde
    o cadastro público, mesmo antes de (ou sem nunca) se vincular a um
    local de atendimento."""
    if ClinicaMembro.query.filter(
        ClinicaMembro.usuario_id == usuario_id, ClinicaMembro.clinica_id.in_(filial_ids),
        ClinicaMembro.ativo.is_(True),
    ).first():
        return True
    u = Usuario.query.get(usuario_id)
    return bool(u and u.empresa_fundadora_id == empresa.id)


def _agendamentos_futuros_do_vinculo(clinica_id, usuario_id):
    """Agendamentos ainda por acontecer deste médico nesta filial - usados
    como TRAVA antes de encerrar um vínculo: encerrar com consultas
    marcadas deixaria pacientes com hora marcada e médico que "não existe
    mais" ali. A equipe cancela/transfere primeiro, depois encerra."""
    return Agendamento.query.filter(
        Agendamento.clinica_id == clinica_id,
        Agendamento.medico_id == usuario_id,
        Agendamento.data_hora >= datetime.utcnow(),
        Agendamento.status.in_(("solicitado", "agendado", "confirmado")),
    ).count()


def _filiais_da_empresa():
    """TODAS as filiais da EMPRESA atual, independente de o usuário estar
    vinculado a elas. Usado nas telas de CONFIGURAÇÃO (modelos de preparo,
    exames), que são dados da empresa: quem está configurando o sistema
    (ex.: o fundador, ou um "configurador") não precisa estar vinculado a
    local de atendimento nenhum pra cadastrar esses dados - o vínculo
    ("você atua aqui") só diz respeito a atuação/agenda, não à
    configuração."""
    empresa = empresa_atual()
    return Clinica.query.filter_by(empresa_id=empresa.id).order_by(Clinica.nome).all()


def _filiais_da_empresa_ids():
    return [f.id for f in _filiais_da_empresa()]


def _filtro_pacientes_da_empresa():
    """Filtro SQLAlchemy para "pacientes da EMPRESA atual". O paciente
    pertence à empresa, não a uma filial ("o cliente é só cliente" - a
    filial só é escolhida na hora de marcar cada consulta, ver
    Agendamento.clinica_id). Cobre também cadastros antigos, de antes
    dessa mudança, que só têm a filial legada (Paciente.clinica_id) e
    ainda não passaram pela migração que preenche empresa_id."""
    empresa = empresa_atual()
    filiais_da_empresa = db.session.query(Clinica.id).filter(Clinica.empresa_id == empresa.id)
    return or_(
        Paciente.empresa_id == empresa.id,
        Paciente.clinica_id.in_(filiais_da_empresa),
    )


def _gerar_codigo_cadastro_paciente():
    """Gera um código curto e único para o link público de auto-cadastro
    de paciente (ver auth.cadastro_paciente). O código agora vive na
    EMPRESA (o paciente é da empresa) - a checagem inclui também os
    códigos legados por filial, que continuam válidos em links antigos."""
    for _ in range(10):
        codigo = secrets.token_urlsafe(6).replace("_", "").replace("-", "")[:8]
        if (
            not Empresa.query.filter_by(codigo_cadastro_paciente=codigo).first()
            and not Clinica.query.filter_by(codigo_cadastro_paciente=codigo).first()
        ):
            return codigo
    # Praticamente impossível de cair aqui (espaço de códigos é enorme),
    # mas por segurança nunca deixa a função sem devolver um código.
    return secrets.token_hex(8)


def staff_required(f):
    """Garante que o usuário é da equipe (médico/secretária) e que já tem
    uma EMPRESA (tenant) definida na sessão. Como quase todo mundo só tem
    vínculo em uma empresa, ela é escolhida automaticamente; só quem atua em
    empresas diferentes cai na tela de escolha."""
    @wraps(f)
    def decorado(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_staff:
            flash("Acesso restrito à equipe médica/secretaria.", "danger")
            return redirect(url_for("auth.login"))

        if empresa_atual() is None:
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


def _restringir_perguntas_para_medico(query):
    """Restringe uma query de PerguntaPendente às perguntas que ESTE médico
    logado pode ver: as de exames dos quais ele é responsável (principal ou
    "extra" - ver Exame.medico_pode_atender), mais as perguntas GERAIS (sem
    exame associado) quando ele também tiver perm_pacientes - caso do médico
    fundador de uma clínica sem secretária, que acumula o papel de quem
    administra pacientes. Vale pra QUALQUER médico, mesmo com perm_pacientes:
    ter permissão administrativa não deve fazer um médico ver perguntas de
    exames de OUTRO médico da mesma clínica - só a secretária/dono, que não
    são "donos" de exame nenhum, veem tudo."""
    condicoes = [Exame.medico_id == current_user.id, Exame.medicos_extra.any(id=current_user.id)]
    if current_user.perm_pacientes:
        condicoes.append(PerguntaPendente.exame_id.is_(None))
    return query.outerjoin(Exame, PerguntaPendente.exame_id == Exame.id).filter(or_(*condicoes))


def medicos_da_clinica(clinica):
    """Lista os médicos (usuários) vinculados e ativos nesta clínica."""
    return [m for m in clinica.medicos_e_secretarias if m.tipo == "medico"]


def medicos_das_filiais(filiais):
    """Médicos distintos vinculados a qualquer uma das filiais informadas —
    uma pessoa que atende em duas filiais da empresa aparece uma única vez."""
    vistos = {}
    for f in filiais:
        for m in f.medicos_e_secretarias:
            if m.tipo == "medico":
                vistos.setdefault(m.id, m)
    return sorted(vistos.values(), key=lambda m: (m.nome or "").lower())


def _filial_do_form(filiais, campo="clinica_id"):
    """Filial escolhida num formulário de cadastro, sempre validada contra
    as filiais acessíveis do usuário (fronteira de acesso). Quando a pessoa
    só atua numa filial, o campo nem aparece na tela e essa única filial é
    usada direto — mesmo comportamento de antes."""
    if len(filiais) == 1:
        return filiais[0]
    filial_id = request.form.get(campo, type=int)
    return next((f for f in filiais if f.id == filial_id), None)


def status_configuracao_inicial(filiais):
    """Calcula, a partir dos dados que já existem no banco (sem guardar
    nenhum estado de "assistente" à parte), quais das etapas sugeridas na
    configuração inicial da clínica já foram feitas. Usado tanto pela tela
    do assistente (medico.onboarding) quanto pelo aviso mostrado no Painel
    enquanto a configuração não estiver completa — por ser calculado a
    partir dos dados reais, permanece correto mesmo que a pessoa pule
    etapas e preencha as coisas por fora do assistente, em outra ordem."""
    filial_ids = [f.id for f in filiais]
    tem_medico = len(medicos_das_filiais(filiais)) > 0
    tem_horario = MedicoHorario.query.filter(
        MedicoHorario.clinica_id.in_(filial_ids), MedicoHorario.ativo.is_(True)
    ).first() is not None
    tem_mais_gente = (
        db.session.query(ClinicaMembro.usuario_id)
        .filter(ClinicaMembro.clinica_id.in_(filial_ids), ClinicaMembro.ativo.is_(True))
        .distinct().count() > 1
    )
    tem_modelo_preparo = PreparoModelo.query.filter(PreparoModelo.clinica_id.in_(filial_ids)).first() is not None
    tem_exame = Exame.query.filter(Exame.clinica_id.in_(filial_ids)).first() is not None

    etapas = [
        {
            "id": "locais_atendimento",
            "titulo": "Meus Locais de Atendimento",
            "descricao": (
                "Endereço, CNPJ e telefone/e-mail de contato da clínica — e, se atender em mais de um "
                "endereço/consultório, cadastre cada local separadamente aqui também (opcional)."
            ),
            "concluida": bool(filiais) and all(f.telefone and f.email_contato for f in filiais),
            "endpoint": "medico.filiais_lista",
            "permissao": "perm_filiais",
        },
        {
            "id": "equipe",
            "titulo": "Convidar mais gente para a equipe",
            "descricao": "Adicione outra secretária ou médico, se houver — esta etapa é totalmente opcional.",
            "concluida": tem_mais_gente,
            "endpoint": "medico.equipe_lista",
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


# ---------- Seleção de empresa (tenant) ----------

@medico_bp.route("/clinica", methods=["GET", "POST"])
@login_required
def escolher_clinica():
    """Escolha da EMPRESA em que a pessoa vai trabalhar agora. Não existe
    mais troca de filial: dentro da empresa, os dados de todas as filiais em
    que a pessoa atua aparecem juntos, com a filial indicada em cada
    registro. Esta tela só aparece no caso raro de a pessoa ter vínculo em
    mais de uma empresa (tenants diferentes); com uma só, é automático.

    O nome da rota (e a URL) foi mantido para não quebrar links antigos."""
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

    empresas = empresas_do_usuario()

    if request.method == "POST":
        empresa_id = request.form.get("empresa_id", type=int)
        if empresa_id and selecionar_empresa(empresa_id):
            return redirect(url_for("medico.dashboard"))
        # Compatibilidade com links/formulários antigos que mandavam uma
        # filial: passa a valer só como filial padrão de formulário (e
        # define a empresa dela) — não filtra mais nada.
        clinica_id = request.form.get("clinica_id", type=int)
        if clinica_id and selecionar_clinica(clinica_id):
            return redirect(url_for("medico.dashboard"))
        flash("Empresa inválida.", "danger")

    if len(empresas) == 1:
        selecionar_empresa(empresas[0].id)
        return redirect(url_for("medico.dashboard"))

    return render_template("medico/escolher_clinica.html", empresas=empresas)


@medico_bp.route("/")
@login_required
@staff_required
def dashboard():
    filiais = filiais_atuais()
    filial_ids = [f.id for f in filiais]

    agendamentos_q = Agendamento.query.filter(Agendamento.clinica_id.in_(filial_ids))
    # Médico logado com o próprio login vê no painel APENAS a agenda DELE -
    # independente das permissões administrativas que tenha (um médico
    # fundador com todas as permissões continua vendo as telas
    # administrativas, mas a agenda do painel é a dele, não a da clínica
    # inteira). Secretária/configurador continuam vendo a agenda de todos.
    if eh_medico():
        agendamentos_q = agendamentos_q.filter(Agendamento.medico_id == current_user.id)
    # "Pendente de resposta" no painel conta as duas situações que
    # aparecem na tela "Perguntas dos pacientes" (ver
    # medico.perguntas_pendentes) esperando alguma ação do médico: as que
    # ainda não têm nenhum rascunho (status "pendente") E as que a IA já
    # rascunhou mas ainda aguardam aprovação (status "aguardando_aprovacao")
    # — antes só a primeira era contada aqui, então uma pergunta com
    # rascunho da IA aparecia como card zerado mesmo tendo o que revisar.
    pendentes_q = PerguntaPendente.query.filter(
        PerguntaPendente.clinica_id.in_(filial_ids), PerguntaPendente.status == "pendente"
    )
    aguardando_q = PerguntaPendente.query.filter(
        PerguntaPendente.clinica_id.in_(filial_ids), PerguntaPendente.status == "aguardando_aprovacao"
    )
    solicitacoes_q = Agendamento.query.filter(
        Agendamento.clinica_id.in_(filial_ids), Agendamento.status == "solicitado"
    )
    # Pacientes que se cadastraram sozinhos pelo app (ver
    # auth.cadastro_paciente) e aguardam a equipe aceitar o cadastro antes
    # de poder solicitar agendamento — qualquer um da equipe pode ver e
    # decidir, não depende de perm_pacientes.
    cadastros_pendentes_count = Paciente.query.filter(
        _filtro_pacientes_da_empresa(), Paciente.status_cadastro == "pendente"
    ).count()

    if eh_medico() and not current_user.perm_pacientes:
        total_pacientes = (
            db.session.query(Agendamento.paciente_id)
            .filter(Agendamento.clinica_id.in_(filial_ids), Agendamento.medico_id == current_user.id)
            .distinct()
            .count()
        )
        # (a agenda do painel já vem filtrada pro médico logo acima,
        # independente de permissão - aqui só o contador de pacientes/solicitações)
        solicitacoes_q = solicitacoes_q.filter(Agendamento.medico_id == current_user.id)
    else:
        total_pacientes = Paciente.query.filter(_filtro_pacientes_da_empresa()).count()

    # Perguntas pendentes: um médico só vê (e conta) as de exames dos quais é
    # responsável, mais as gerais quando também administra pacientes - vale
    # mesmo pra médico com perm_pacientes (ver _restringir_perguntas_para_medico).
    if eh_medico():
        pendentes_q = _restringir_perguntas_para_medico(pendentes_q)
        aguardando_q = _restringir_perguntas_para_medico(aguardando_q)

    # Link de auto-cadastro de paciente é da EMPRESA e fica aqui no
    # Painel - gera o código na primeira vez que o painel é aberto.
    empresa = empresa_atual()
    if not empresa.codigo_cadastro_paciente:
        empresa.codigo_cadastro_paciente = _gerar_codigo_cadastro_paciente()
        db.session.commit()

    # Código mestre do médico (ver Usuario.codigo_mestre): contas de médico
    # criadas antes do código ganham um na primeira visita ao painel.
    convites_pendentes = []
    if eh_medico():
        if not current_user.codigo_mestre:
            current_user.codigo_mestre = gerar_codigo_mestre_medico()
            db.session.commit()
        # Convites de clínicas querendo vincular este médico - ele decide
        # aqui no painel (aceitar cria o vínculo, ver convite_decidir).
        convites_pendentes = (
            ConviteVinculo.query.filter_by(medico_id=current_user.id, status="pendente")
            .order_by(ConviteVinculo.criado_em.asc())
            .all()
        )

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
        clinica=clinica_atual(),
        empresa=empresa,
        filiais=filiais,
        total_pacientes=total_pacientes,
        proximos=proximos,
        pendentes=pendentes,
        solicitacoes_pendentes=solicitacoes_pendentes,
        cadastros_pendentes=cadastros_pendentes_count,
        convites_pendentes=convites_pendentes,
        agendamentos=agendamentos,
        etapas_configuracao_inicial=[e for e in status_configuracao_inicial(_filiais_da_empresa()) if not e["concluida"] and not e.get("opcional")],
    )


@medico_bp.route("/configuracao-inicial")
@login_required
@staff_required
def onboarding():
    """Assistente de configuração inicial, mostrado logo após a empresa se
    cadastrar (ver auth.cadastro) e também acessível a qualquer momento
    pelo aviso no Painel — reúne, num só lugar, as etapas sugeridas para
    deixar a clínica pronta para uso. Nenhuma etapa é obrigatória: cada
    uma só linka para a tela real (Dados Cadastrais, Equipe, Horário de
    atendimento, Modelos de preparo, Exames), que continua funcionando
    normalmente por fora do assistente também."""
    # As etapas medem a configuração da EMPRESA (todas as filiais), não só
    # dos locais em que o usuário atua - um fundador/configurador sem
    # vínculo nenhum precisa ver o progresso real do que já cadastrou.
    filiais = _filiais_da_empresa()
    etapas = status_configuracao_inicial(filiais)
    concluidas = sum(1 for e in etapas if e["concluida"])
    return render_template(
        "medico/onboarding.html",
        clinica=clinica_atual(),
        etapas=etapas,
        concluidas=concluidas,
        total=len(etapas),
    )


# ---------- Pacientes ----------

@medico_bp.route("/pacientes")
@login_required
@staff_required
def pacientes_lista():
    filial_ids = filiais_atuais_ids()
    if eh_medico() and not current_user.perm_pacientes:
        # Médico sem a permissão administrativa de pacientes: só vê quem já
        # tem algum agendamento com ele mesmo — "acompanhar somente os seus
        # pacientes". Quem tem essa permissão (ex.: o médico fundador da
        # empresa, que também pode cadastrar pacientes novos) precisa ver
        # todos os pacientes da clínica, senão nem o paciente que ele mesmo
        # acabou de cadastrar apareceria na lista antes do 1º agendamento.
        pacientes = (
            Paciente.query.join(Agendamento, Agendamento.paciente_id == Paciente.id)
            .filter(_filtro_pacientes_da_empresa(), Agendamento.medico_id == current_user.id)
            .distinct()
            .order_by(Paciente.nome)
            .all()
        )
    else:
        pacientes = Paciente.query.filter(_filtro_pacientes_da_empresa()).order_by(Paciente.nome).all()
    return render_template("medico/pacientes_lista.html", pacientes=pacientes)


@medico_bp.route("/pacientes/solicitacoes")
@login_required
@staff_required
def pacientes_solicitacoes():
    """Cadastros de paciente feitos pelo próprio app (ver
    auth.cadastro_paciente), ainda aguardando a equipe aceitar. Qualquer
    membro da equipe pode ver e decidir — não é restrito por perm_pacientes,
    já que aceitar/recusar um cadastro não é a mesma coisa que gerenciar o
    cadastro completo do paciente."""
    filial_ids = filiais_atuais_ids()
    pendentes = (
        Paciente.query.filter(_filtro_pacientes_da_empresa(), Paciente.status_cadastro == "pendente")
        .order_by(Paciente.criado_em.asc())
        .all()
    )
    return render_template("medico/pacientes_solicitacoes.html", pendentes=pendentes)


@medico_bp.route("/pacientes/<int:paciente_id>/cadastro/decidir", methods=["POST"])
@login_required
@staff_required
def pacientes_cadastro_decidir(paciente_id):
    """Aceita ou rejeita o cadastro de um paciente. Usada tanto na fila de
    solicitações pendentes (medico.pacientes_solicitacoes) quanto na tela
    de detalhe do paciente (medico.pacientes_detalhe) - essa segunda
    permite reverter uma decisão a qualquer momento (ex.: rejeitou por
    engano, ou o paciente resolveu a pendência e agora pode ser aprovado),
    por isso não exige mais que o status atual seja "pendente"."""
    paciente = Paciente.query.filter(
        Paciente.id == paciente_id, _filtro_pacientes_da_empresa()
    ).first_or_404()
    acao = request.form.get("acao")
    if acao == "aceitar":
        paciente.status_cadastro = "aprovado"
        db.session.commit()
        flash(f"Cadastro de {paciente.nome} aceito — já pode solicitar agendamento.", "success")
    elif acao == "rejeitar":
        paciente.status_cadastro = "rejeitado"
        db.session.commit()
        flash(f"Cadastro de {paciente.nome} rejeitado.", "success")
    else:
        flash("Ação inválida.", "danger")

    # Vem da tela de detalhe do paciente -> volta pra lá; vem da fila de
    # solicitações pendentes -> continua lá (fluxo de processar vários).
    if request.form.get("origem") == "detalhe":
        return redirect(url_for("medico.pacientes_detalhe", paciente_id=paciente.id))
    return redirect(url_for("medico.pacientes_solicitacoes"))


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
    paciente.contato_emergencia_nome = formatar_nome_proprio(form.get("contato_emergencia_nome", ""))
    paciente.contato_emergencia_telefone = form.get("contato_emergencia_telefone", "").strip()


def _buscar_paciente_por_cpf_plataforma(cpf):
    """Acha o cadastro mais recente de um paciente na PLATAFORMA inteira
    pelo CPF (comparado só nos dígitos - o CPF é guardado como digitado).
    É a base do fluxo "importar paciente": o paciente se cadastra uma vez,
    independente de clínica (ver auth.cadastro_paciente_global), e cada
    clínica o importa pelo CPF na recepção."""
    digitos = re.sub(r"\D", "", cpf or "")
    if len(digitos) < 11:
        return None
    expr = func.replace(func.replace(func.replace(Paciente.cpf, ".", ""), "-", ""), " ", "")
    return (
        Paciente.query.filter(expr == digitos)
        .order_by(Paciente.id.desc())
        .first()
    )


@medico_bp.route("/pacientes/novo", methods=["GET", "POST"])
@login_required
@staff_required
@permissao_required("perm_pacientes")
def pacientes_novo():
    filiais = filiais_atuais()

    if not filiais:
        flash("Cadastre seu primeiro local de atendimento antes de cadastrar pacientes.", "info")
        return redirect(url_for("medico.filiais_lista"))

    empresa = empresa_atual()

    # Busca por CPF ("importar paciente da plataforma"): antes de digitar
    # tudo de novo, a secretária consulta o CPF - se o paciente já tem
    # cadastro (global ou em outra clínica), é só importar.
    cpf_busca = request.args.get("cpf_busca", "").strip()
    encontrado = None
    busca_feita = False
    if request.method == "GET" and cpf_busca:
        busca_feita = True
        encontrado = _buscar_paciente_por_cpf_plataforma(cpf_busca)
        if encontrado and Paciente.query.filter(
            _filtro_pacientes_da_empresa(), Paciente.id.in_(
                [pp.id for pp in Paciente.query.filter(Paciente.cpf == encontrado.cpf).all()]
            )
        ).first():
            flash(f"{encontrado.nome} já é paciente desta empresa.", "warning")
            return redirect(url_for("medico.pacientes_lista"))
        if not encontrado:
            flash("CPF não encontrado na plataforma — preencha o cadastro completo abaixo.", "info")

    if request.method == "POST":
        # O paciente é cadastrado na EMPRESA - não se escolhe filial aqui
        # ("o cliente é só cliente"). A filial só é escolhida na hora de
        # marcar cada consulta (medico.agenda_novo /
        # paciente.solicitar_agendamento).
        nome = formatar_nome_proprio(request.form.get("nome", ""))
        cpf = request.form.get("cpf", "").strip()
        email = request.form.get("email", "").strip().lower()
        telefone_digitado = request.form.get("telefone", "").strip()
        data_nascimento_str = request.form.get("data_nascimento", "").strip()
        telefone = normalizar_telefone(telefone_digitado)

        if not nome or not cpf or not telefone or not data_nascimento_str:
            flash("Nome, CPF, telefone e data de nascimento são obrigatórios.", "danger")
            return render_template("medico/pacientes_form.html", paciente=None)

        # Telefone incompleto (ex.: "(27" digitado e enviado sem terminar)
        # não travava o envio - a máscara só formata o que foi digitado,
        # não garante que a pessoa terminou de digitar.
        if telefone_incompleto(telefone_digitado):
            flash("Telefone incompleto — digite o DDD e o número completos.", "danger")
            return render_template(
                "medico/pacientes_form.html", paciente=None,
                cpf_busca=cpf_busca, encontrado=encontrado, busca_feita=busca_feita,
            )
        if telefone_incompleto(request.form.get("contato_emergencia_telefone", "")):
            flash("Telefone do contato de emergência incompleto — digite o DDD e o número completos.", "danger")
            return render_template(
                "medico/pacientes_form.html", paciente=None,
                cpf_busca=cpf_busca, encontrado=encontrado, busca_feita=busca_feita,
            )

        data_nascimento = _parse_data_nascimento(data_nascimento_str)
        if not data_nascimento:
            flash("Data de nascimento inválida — use o formato DD/MM/AAAA.", "danger")
            return render_template("medico/pacientes_form.html", paciente=None)

        # CEP incompleto (ex.: "29055") não bloqueava o envio e ficava
        # salvo pela metade, com rua/bairro/cidade/UF vazios.
        if cep_incompleto(request.form.get("cep", "")):
            flash("CEP incompleto — digite os 8 números.", "danger")
            return render_template(
                "medico/pacientes_form.html", paciente=None,
                cpf_busca=cpf_busca, encontrado=encontrado, busca_feita=busca_feita,
            )

        # Telefone não é único por pessoa - é normal uma família inteira
        # (pais e filhos, por exemplo) compartilhar o mesmo telefone de
        # contato, cada um com seu próprio cadastro de paciente. O login
        # de paciente (auth.login_paciente) já identifica a conta certa
        # por telefone + data de nascimento, não só telefone - então só
        # bloqueamos aqui se as duas coisas baterem ao mesmo tempo (mesma
        # pessoa cadastrada de novo). A unicidade que de fato importa
        # (garantida pelo banco) é por CPF, verificada logo abaixo.
        if (
            Paciente.query.join(Usuario, Paciente.usuario_id == Usuario.id)
            .filter(
                _filtro_pacientes_da_empresa(),
                Usuario.telefone == telefone,
                Paciente.data_nascimento == data_nascimento,
            )
            .first()
        ):
            flash("Já existe um paciente cadastrado com esse telefone e data de nascimento nesta empresa.", "danger")
            return render_template("medico/pacientes_form.html", paciente=None)

        if email and (
            Paciente.query.join(Usuario, Paciente.usuario_id == Usuario.id)
            .filter(_filtro_pacientes_da_empresa(), Usuario.email == email)
            .first()
        ):
            flash("Já existe um paciente com esse e-mail nesta empresa.", "danger")
            return render_template("medico/pacientes_form.html", paciente=None)

        if Paciente.query.filter(_filtro_pacientes_da_empresa(), Paciente.cpf == cpf).first():
            flash("Já existe um paciente com esse CPF nesta empresa.", "danger")
            return render_template("medico/pacientes_form.html", paciente=None)

        # Paciente não usa e-mail/senha para entrar — o acesso é feito
        # informando telefone e data de nascimento (ver auth.login_paciente).
        # CONTA ÚNICA: se essa pessoa já usa o app por outra empresa
        # (mesmo telefone + data de nascimento), reaproveita a conta dela
        # em vez de criar uma segunda - só o cadastro (Paciente) desta
        # empresa é novo. Ver encontrar_conta_paciente em app/models.py.
        usuario = encontrar_conta_paciente_por_cpf(cpf) or encontrar_conta_paciente(telefone, data_nascimento)
        if not usuario:
            usuario = Usuario(nome=nome, email=email or None, telefone=telefone, tipo="paciente")
            db.session.add(usuario)
            db.session.flush()

        paciente = Paciente(
            empresa_id=empresa.id,
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
            "Paciente cadastrado. Ele(a) pode acessar o sistema informando o CPF e a data de "
            "nascimento — não é necessário criar senha.",
            "success",
        )
        return redirect(url_for("medico.pacientes_lista"))

    return render_template(
        "medico/pacientes_form.html", paciente=None,
        cpf_busca=cpf_busca, encontrado=encontrado, busca_feita=busca_feita,
    )


@medico_bp.route("/pacientes/importar", methods=["POST"])
@login_required
@staff_required
@permissao_required("perm_pacientes")
def pacientes_importar():
    """Importa pra ESTA empresa um paciente que já existe na plataforma
    (cadastro global ou de outra clínica), achado pelo CPF em
    pacientes_novo. Cria o cadastro (Paciente) desta empresa copiando os
    dados do cadastro mais recente - a conta de login é a MESMA (conta
    única), então o paciente passa a ver esta clínica no app dele. Os
    dados clínicos de outras clínicas NÃO vêm junto (cada clínica só vê
    o que é dela)."""
    empresa = empresa_atual()
    origem = _buscar_paciente_por_cpf_plataforma(request.form.get("cpf", ""))
    if not origem:
        flash("CPF não encontrado na plataforma.", "danger")
        return redirect(url_for("medico.pacientes_novo"))

    ja_daqui = Paciente.query.filter(_filtro_pacientes_da_empresa(), Paciente.cpf == origem.cpf).first()
    if ja_daqui:
        flash(f"{origem.nome} já é paciente desta empresa.", "warning")
        return redirect(url_for("medico.pacientes_lista"))

    novo = Paciente(
        empresa_id=empresa.id,
        usuario_id=origem.usuario_id,
        nome=origem.nome,
        cpf=origem.cpf,
        data_nascimento=origem.data_nascimento,
        email=origem.email,
        telefone=origem.telefone,
        # Importado pela própria equipe = aceito por definição (não passa
        # pela fila de "cadastros pendentes").
        status_cadastro="aprovado",
        cep=origem.cep, rua=origem.rua, numero=origem.numero,
        complemento=origem.complemento, bairro=origem.bairro,
        cidade=origem.cidade, uf=origem.uf,
        contato_emergencia_nome=origem.contato_emergencia_nome,
        contato_emergencia_telefone=origem.contato_emergencia_telefone,
    )
    db.session.add(novo)
    db.session.commit()
    flash(
        f"{origem.nome} foi importado(a) da plataforma para esta empresa - dados de contato e "
        "endereço vieram junto; o histórico dele(a) em outras clínicas continua lá (cada clínica "
        "vê só o que é dela).",
        "success",
    )
    return redirect(url_for("medico.pacientes_lista"))


@medico_bp.route("/pacientes/<int:paciente_id>/editar", methods=["GET", "POST"])
@login_required
@staff_required
@permissao_required("perm_pacientes")
def pacientes_editar(paciente_id):
    paciente = Paciente.query.filter(
        Paciente.id == paciente_id, _filtro_pacientes_da_empresa()
    ).first_or_404()

    if request.method == "POST":
        # CEP incompleto (ex.: "29055") não bloqueava o envio e ficava
        # salvo pela metade, com rua/bairro/cidade/UF vazios.
        if cep_incompleto(request.form.get("cep", "")):
            flash("CEP incompleto — digite os 8 números.", "danger")
            return render_template("medico/pacientes_form.html", paciente=paciente)
        if telefone_incompleto(request.form.get("contato_emergencia_telefone", "")):
            flash("Telefone do contato de emergência incompleto — digite o DDD e o número completos.", "danger")
            return render_template("medico/pacientes_form.html", paciente=paciente)

        paciente.nome = formatar_nome_proprio(request.form.get("nome", "")) or paciente.nome
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
    paciente = Paciente.query.filter(
        Paciente.id == paciente_id, _filtro_pacientes_da_empresa()
    ).first_or_404()

    if eh_medico() and not current_user.perm_pacientes:
        tem_vinculo = Agendamento.query.filter_by(
            paciente_id=paciente.id, medico_id=current_user.id
        ).first()
        if not tem_vinculo:
            flash("Este paciente não tem agendamentos com você.", "danger")
            return redirect(url_for("medico.pacientes_lista"))

    return render_template("medico/pacientes_detalhe.html", paciente=paciente)


@medico_bp.route("/pacientes/<int:paciente_id>/prontuario/exportar")
@login_required
@staff_required
def pacientes_prontuario_exportar(paciente_id):
    """Exporta o prontuário (histórico de evolução clínica) do paciente em
    PDF — requisito técnico do processo sem papel (NGS2/NGS3, CFM
    1.821/2007): o sistema precisa conseguir exportar num formato aberto.
    Mesmas checagens de acesso de pacientes_detalhe(), e o próprio acesso
    de exportação também fica registrado na trilha de auditoria."""
    paciente = Paciente.query.filter(
        Paciente.id == paciente_id, _filtro_pacientes_da_empresa()
    ).first_or_404()

    if eh_medico() and not current_user.perm_pacientes:
        tem_vinculo = Agendamento.query.filter_by(
            paciente_id=paciente.id, medico_id=current_user.id
        ).first()
        if not tem_vinculo:
            flash("Este paciente não tem agendamentos com você.", "danger")
            return redirect(url_for("medico.pacientes_lista"))

    evolucoes = list(paciente.evolucoes_clinicas)
    pdf_buffer = gerar_pdf_prontuario(paciente, evolucoes)

    registrar_acesso(paciente.id, "exportar_prontuario", detalhe=f"total_evolucoes={len(evolucoes)}")

    nome_arquivo = f"prontuario_{paciente.nome.replace(' ', '_')}.pdf"
    return send_file(
        pdf_buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=nome_arquivo,
    )


# ---------- Exames e preparo ----------

@medico_bp.route("/exames")
@login_required
@staff_required
def exames_lista():
    # Exames são dados de CONFIGURAÇÃO da empresa - a lista mostra os de
    # todas as filiais, mesmo pra quem não está vinculado a local nenhum.
    query = Exame.query.filter(Exame.clinica_id.in_(_filiais_da_empresa_ids()))
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
    # Cadastro de exame é CONFIGURAÇÃO da empresa - não depende de o
    # usuário estar vinculado a alguma filial (ver _filiais_da_empresa).
    filiais = _filiais_da_empresa()
    filial_ids = [f.id for f in filiais]
    medicos = medicos_das_filiais(filiais)
    modelos = PreparoModelo.query.filter(PreparoModelo.clinica_id.in_(filial_ids)).order_by(PreparoModelo.nome).all()
    # Mesmo padrão de dono de conteúdo clínico: o médico só pode escolher
    # (e, portanto, só vê no dropdown) os SEUS modelos de preparo ou os sem
    # dono registrado - não os de outro médico da empresa.
    if eh_medico():
        modelos = [m for m in modelos if m.dono_medico is None or m.dono_medico.id == current_user.id]

    if request.method == "POST":
        # O cadastro de exame é genérico - só define nome/descrição/duração/
        # preparo, sem escolher filial nem médico responsável. Quem atende
        # esse exame em cada local (e com qual médico) é decidido depois, na
        # tela "Exames por filial" (medico.exames_por_filial), que é onde
        # médico e preço realmente variam por local de atendimento.
        if not filiais:
            flash("A empresa ainda não tem nenhum local de atendimento cadastrado.", "danger")
            return redirect(url_for("medico.filiais_lista"))
        filial = filiais[0]

        nome = request.form.get("nome", "").strip()
        descricao = request.form.get("descricao", "").strip()
        preparo_modelo_raw = request.form.get("preparo_modelo_id", "").strip()
        duracao_minutos = request.form.get("duracao_minutos", type=int)
        precisa_acompanhante = request.form.get("precisa_acompanhante") == "on"

        # medico_id continua obrigatório no banco (é quem aparece como
        # titular do exame) - se quem está cadastrando é médico, ele mesmo é
        # o responsável inicial; se é secretária, usamos o primeiro médico
        # disponível na empresa como responsável provisório, e a tela
        # "Exames por filial" (ou "editar exame") serve para corrigir isso
        # exame a exame, sem travar o cadastro pedindo essa escolha aqui.
        if eh_medico():
            medico_id = current_user.id
        elif medicos:
            medico_id = medicos[0].id
        else:
            flash("Cadastre um médico na equipe antes de criar exames.", "danger")
            return render_template("medico/exames_form.html", exame=None, medicos=medicos, modelos=modelos)

        # É obrigatório escolher uma opção no cadastro - mas a opção pode ser
        # "nenhum" (procedimento simples, sem instrução prévia, ex.: uma
        # consulta). O que não pode é deixar sem escolher nada.
        modelo = None
        if preparo_modelo_raw == "nenhum":
            modelo = None
        elif preparo_modelo_raw:
            # Modelo de preparo é genérico (vale para a empresa toda, não só
            # para uma filial) - qualquer modelo acessível ao usuário serve.
            modelo = next((m for m in modelos if str(m.id) == preparo_modelo_raw), None)
            if not modelo:
                flash("Escolha um modelo de preparo válido, ou \"Nenhum\".", "danger")
                return render_template("medico/exames_form.html", exame=None, medicos=medicos, modelos=modelos)
        else:
            flash("Escolha uma opção de modelo de preparo (pode ser \"Nenhum\").", "danger")
            return render_template("medico/exames_form.html", exame=None, medicos=medicos, modelos=modelos)

        if not nome:
            flash("Nome do exame é obrigatório.", "danger")
            return render_template("medico/exames_form.html", exame=None, medicos=medicos, modelos=modelos)

        if Exame.query.filter(Exame.clinica_id.in_(filial_ids), Exame.nome == nome).first():
            flash("Já existe um exame com esse nome.", "danger")
            return render_template("medico/exames_form.html", exame=None, medicos=medicos, modelos=modelos)

        # Preço fica de fora do cadastro genérico - é definido depois, por
        # local de atendimento, em "Exames por filial" (mesmo esquema que já
        # vale pra médico e pra associar o exame a mais de uma filial).
        exame = Exame(
            clinica_id=filial.id, medico_id=medico_id, nome=nome, descricao=descricao,
            preparo_modelo_id=modelo.id if modelo else None, duracao_minutos=duracao_minutos,
            precisa_acompanhante=precisa_acompanhante,
            # medico_id acima é só um valor técnico/provisório pra passar
            # pela constraint do banco - não foi uma escolha de verdade
            # (nem quando é o próprio médico logado cadastrando, já que
            # não existe "médico principal" assumido automaticamente).
            # Só vira confirmado quando alguém escolhe de propósito em
            # "Exames por filial" (ver exames_por_filial_associar /
            # exames_por_filial_atualizar).
            medico_confirmado=False,
            # Cadastrar exame NÃO cria associação nenhuma: nasce só como
            # item de catálogo. A associação (exame + filial + médico +
            # preço) é criada de propósito na tela "Associar exames".
            associado=False,
            # DONO do exame: quem criou. Se for um médico, só ele edita o
            # cadastro e só ele pode ser associado a este exame (ver
            # Exame.pode_ser_editado_por / _dono_medico_do_exame).
            criado_por_id=current_user.id,
        )
        db.session.add(exame)
        db.session.commit()

        flash(
            "Exame cadastrado com sucesso. Defina o médico responsável e o preço em "
            '"Exames por filial", antes de agendar.',
            "success",
        )
        return _destino_pos_onboarding("medico.exames_lista")

    return render_template("medico/exames_form.html", exame=None, medicos=medicos, modelos=modelos)


@medico_bp.route("/exames/<int:exame_id>/editar", methods=["GET", "POST"])
@login_required
@staff_required
def exames_editar(exame_id):
    filial_ids = _filiais_da_empresa_ids()
    query = Exame.query.filter(Exame.id == exame_id, Exame.clinica_id.in_(filial_ids))
    if eh_medico():
        query = query.filter(
            or_(Exame.medico_id == current_user.id, Exame.medicos_extra.any(id=current_user.id))
        )
    exame = query.first_or_404()
    # DONO do conteúdo clínico: exame criado por um médico só é editado
    # POR ELE (nem secretária, nem outro médico). Exames sem dono
    # registrado (antigos) seguem o comportamento antigo.
    if not exame.pode_ser_editado_por(current_user):
        flash(
            f"Só {exame.dono_medico.nome}, que criou este exame, pode editá-lo.",
            "danger",
        )
        return redirect(url_for("medico.exames_lista"))
    # Médico responsável é da filial DO EXAME, mas modelo de preparo é
    # genérico - vale qualquer modelo acessível ao usuário na empresa.
    medicos = medicos_da_clinica(exame.clinica)
    modelos = PreparoModelo.query.filter(PreparoModelo.clinica_id.in_(filial_ids)).order_by(PreparoModelo.nome).all()
    if eh_medico():
        modelos = [m for m in modelos if m.dono_medico is None or m.dono_medico.id == current_user.id]

    if request.method == "POST":
        exame.nome = request.form.get("nome", "").strip()
        exame.descricao = request.form.get("descricao", "").strip()
        exame.duracao_minutos = request.form.get("duracao_minutos", type=int)
        # O preço NÃO é editado aqui (de propósito - ver exames_form.html):
        # ele é específico de cada filial e passou a ser ajustado só pela
        # tela "Exames por filial" (medico.exames_por_filial_atualizar),
        # que deixa claro que é um valor por local, não do cadastro do
        # exame em si.
        exame.precisa_acompanhante = request.form.get("precisa_acompanhante") == "on"
        # Reatribuir o médico RESPONSÁVEL principal saiu desta tela (fica
        # para uma funcionalidade própria, futura) - por ora só os médicos
        # EXTRAS continuam editáveis abaixo.
        # Já os médicos EXTRAS (outros médicos que também atendem este
        # exame) qualquer pessoa da equipe pode ajustar - inclusive um
        # médico editando o próprio exame - já que clínicas sem secretária
        # (só médicos) também precisam conseguir compartilhar um exame
        # entre colegas.
        medicos_extra_ids = {v for v in request.form.getlist("medicos_extra_ids", type=int) if v != exame.medico_id}
        if exame.dono_medico and medicos_extra_ids:
            # Exame com dono médico é SÓ dele - outros médicos não podem
            # ser associados (nem como extras).
            flash(
                f"Este exame pertence a {exame.dono_medico.nome} — outros médicos não podem ser "
                "associados a ele.",
                "danger",
            )
            return render_template("medico/exames_form.html", exame=exame, medicos=medicos, modelos=modelos)
        exame.medicos_extra = [m for m in medicos if m.id in medicos_extra_ids]
        preparo_modelo_id = request.form.get("preparo_modelo_id", type=int)
        if preparo_modelo_id:
            modelo = next((m for m in modelos if m.id == preparo_modelo_id), None)
            if not modelo:
                flash("Escolha um modelo de preparo válido, ou deixe em branco se este procedimento não precisa de preparo.", "danger")
                return render_template("medico/exames_form.html", exame=exame, medicos=medicos, modelos=modelos)
            exame.preparo_modelo_id = modelo.id
        else:
            exame.preparo_modelo_id = None
        db.session.commit()
        flash("Exame atualizado.", "success")
        return redirect(url_for("medico.exames_lista"))

    return render_template("medico/exames_form.html", exame=exame, medicos=medicos, modelos=modelos)


@medico_bp.route("/exames/por-filial")
@login_required
@staff_required
def exames_por_filial():
    """Tela "Associar exames" - um cadastro BÁSICO de associações. Uma
    associação = um exame disponível numa filial, com um médico
    responsável e um preço. A tela é uma lista simples (Exame, Filial,
    Médico, Preço) com um botão "Adicionar" que abre um formulário só com
    esses 4 campos: preencheu, salvou, pronto. Cada linha tem um "Editar"
    que reaproveita o mesmo formulário pra trocar médico/preço daquela
    associação. (As antigas grades "Exame × Filial"/"Exame × Médico" com
    combobox de tipo foram substituídas por isso - não eram intuitivas.)

    Não existe "médico principal": todo mundo (médico ou secretária)
    segue o mesmo fluxo, escolhendo o médico responsável numa lista.
    Enquanto o médico de uma associação for só o valor técnico/provisório
    do cadastro genérico do exame (ver exames_novo), a linha mostra um
    aviso de "não confirmado" - escolher e salvar o médico aqui é o que
    confirma."""
    empresa = empresa_atual()
    filiais = Clinica.query.filter_by(empresa_id=empresa.id).order_by(Clinica.nome).all()

    # A lista mostra só ASSOCIAÇÕES de verdade (associado=True) - o
    # cadastro genérico de exame cria só um item de catálogo
    # (associado=False), que não aparece aqui até alguém associar.
    exames_da_empresa = (
        Exame.query.join(Clinica, Exame.clinica_id == Clinica.id)
        .filter(Clinica.empresa_id == empresa.id)
        .all()
    )
    associacoes = sorted(
        (e for e in exames_da_empresa if e.associado),
        key=lambda e: (e.nome.lower(), (e.clinica.nome or "").lower()),
    )

    # O dropdown "Exame" do formulário lista os exames cadastrados
    # (inclusive os que ainda são só catálogo, sem nenhuma associação) -
    # mas quando quem está logado é médico, só aparecem os exames que são
    # DELE (ver Exame.dono_medico) ou que não têm dono médico registrado
    # (cadastro antigo/criado pela secretária). Isso evita o médico
    # escolher na lista um exame de outro médico e só descobrir depois,
    # ao tentar salvar, que a associação é rejeitada (ver
    # _dono_medico_do_exame, chamada em exames_por_filial_associar).
    # Secretária/dono continuam vendo todos, já que não são "donos" de
    # exame nenhum.
    if current_user.tipo == "medico":
        exames_visiveis = [
            e for e in exames_da_empresa
            if e.dono_medico is None or e.dono_medico.id == current_user.id
        ]
    else:
        exames_visiveis = exames_da_empresa
    nomes = sorted({e.nome for e in exames_visiveis})
    # O dropdown "Médico" segue a mesma lógica do "Exame" acima: quando
    # quem está logado é médico, só aparece ele mesmo — um médico só pode
    # ser o responsável pelos SEUS próprios exames (ver dono_medico), então
    # listar os colegas da filial ali só confunde. Secretária/dono
    # continuam vendo todos, já que são quem de fato define qual médico
    # atende cada exame.
    if current_user.tipo == "medico":
        medicos_por_filial = {
            f.id: [m for m in medicos_da_clinica(f) if m.id == current_user.id] for f in filiais
        }
    else:
        medicos_por_filial = {f.id: medicos_da_clinica(f) for f in filiais}

    # "Editar" de uma linha reaproveita o mesmo formulário, pré-preenchido
    # com a associação escolhida (exame+filial ficam fixos; só médico e
    # preço são editáveis).
    editar_id = request.args.get("editar", type=int)
    editar_exame = next((e for e in associacoes if e.id == editar_id), None)

    return render_template(
        "medico/exames_por_filial.html",
        filiais=filiais,
        associacoes=associacoes,
        nomes=nomes,
        medicos_por_filial=medicos_por_filial,
        editar_exame=editar_exame,
    )


def _dono_medico_do_exame(nome, empresa):
    """O médico DONO do exame com esse nome na empresa - quem criou o
    cadastro (ver Exame.criado_por_id). Um médico não pode ser associado
    a um exame do qual não é o dono. Retorna None quando o exame não tem
    dono médico registrado (cadastro antigo ou criado pela secretária) -
    nesse caso vale o comportamento antigo (qualquer médico da filial)."""
    filial_ids = db.session.query(Clinica.id).filter(Clinica.empresa_id == empresa.id)
    for e in Exame.query.filter(Exame.clinica_id.in_(filial_ids), Exame.nome == nome).all():
        dono = e.dono_medico
        if dono:
            return dono
    return None


@medico_bp.route("/exames/por-filial/associar", methods=["POST"])
@login_required
@staff_required
def exames_por_filial_associar():
    empresa = empresa_atual()
    nome = request.form.get("nome", "").strip()
    clinica_destino = Clinica.query.filter_by(
        id=request.form.get("clinica_destino_id", type=int), empresa_id=empresa.id
    ).first()

    if not nome or not clinica_destino:
        flash("Escolha um exame e uma filial válidos.", "danger")
        return redirect(url_for("medico.exames_por_filial"))

    # Reforço no servidor da mesma regra do dropdown (ver exames_por_filial
    # acima): um médico só pode se escolher a si mesmo como responsável,
    # nunca um colega — mesmo que o exame não tenha dono registrado. Sem
    # isso, dava pra contornar a restrição do dropdown só editando o HTML.
    medico_escolhido_id = request.form.get("medico_id", type=int)
    if current_user.tipo == "medico" and medico_escolhido_id and medico_escolhido_id != current_user.id:
        flash("Você só pode se associar como responsável pelos seus próprios exames.", "danger")
        return redirect(url_for("medico.exames_por_filial"))

    # DONO do exame: se o exame foi criado por um médico, SÓ ELE pode ser
    # associado a este exame - vale tanto pra associação nova quanto pra
    # tentar adicionar outro médico a uma associação existente.
    dono = _dono_medico_do_exame(nome, empresa)
    if dono and medico_escolhido_id and medico_escolhido_id != dono.id:
        flash(
            f"O exame \"{nome}\" pertence a {dono.nome} (quem o criou) — só ele pode ser "
            "associado a este exame.",
            "danger",
        )
        return redirect(url_for("medico.exames_por_filial"))

    existente = Exame.query.filter_by(clinica_id=clinica_destino.id, nome=nome).first()
    if existente and existente.associado:
        # A associação exame+filial já existe - mas um exame pode ter MAIS
        # de um médico na mesma filial (o responsável + outros que também
        # o atendem). Se o médico escolhido for novo, ele é ADICIONADO à
        # associação existente como médico extra, em vez de rejeitar.
        medico_novo_id = request.form.get("medico_id", type=int)
        medicos_destino = medicos_da_clinica(clinica_destino)
        medico_novo = next((m for m in medicos_destino if m.id == medico_novo_id), None)
        if not medico_novo:
            flash("Escolha um médico válido, vinculado a essa filial.", "danger")
            return redirect(url_for("medico.exames_por_filial"))

        if medico_novo.id == existente.medico_id or medico_novo in existente.medicos_extra:
            flash(
                f"\"{nome}\" já está associado à filial {clinica_destino.nome} com {medico_novo.nome}.",
                "warning",
            )
            return redirect(url_for("medico.exames_por_filial"))

        existente.medicos_extra = list(existente.medicos_extra) + [medico_novo]
        db.session.commit()
        flash(
            f"{medico_novo.nome} foi adicionado(a) como médico que também atende \"{nome}\" na filial "
            f"{clinica_destino.nome} (responsável: {existente.medico.nome}). O preço continua o já "
            "definido para essa associação — ajuste pelo Editar, se precisar.",
            "success",
        )
        return redirect(url_for("medico.exames_por_filial"))

    origem = (
        Exame.query.join(Clinica, Exame.clinica_id == Clinica.id)
        .filter(Clinica.empresa_id == empresa.id, Exame.nome == nome)
        .first()
    )
    if not origem:
        flash("Exame não encontrado.", "danger")
        return redirect(url_for("medico.exames_por_filial"))

    medicos_destino = medicos_da_clinica(clinica_destino)

    # Não existe "médico principal" - médico e secretária seguem o mesmo
    # fluxo aqui, escolhendo o médico responsável numa lista (que inclui
    # o próprio médico logado, se ele atender nessa filial).
    medico_id = request.form.get("medico_id", type=int)
    if not medico_id or not any(m.id == medico_id for m in medicos_destino):
        flash("Escolha um médico válido, vinculado a essa filial.", "danger")
        return redirect(url_for("medico.exames_por_filial"))

    # O preço é específico de cada filial (pode variar de local pra local)
    # e por isso é informado aqui, na hora da associação — não vem mais
    # copiado silenciosamente do exame de origem nem editável no cadastro
    # do exame (ver exames_editar).
    preco = _parse_valor_decimal(request.form.get("preco", ""))
    if preco is None:
        flash("Informe o preço deste exame nessa filial.", "danger")
        return redirect(url_for("medico.exames_por_filial"))

    if existente:
        # O exame já existia nessa filial como item de CATÁLOGO (cadastro
        # genérico, sem associação) - a associação promove esse mesmo
        # registro em vez de criar outro.
        existente.medico_id = medico_id
        existente.preco = preco
        existente.medico_confirmado = True
        existente.associado = True
        db.session.commit()
        flash(
            f"\"{nome}\" associado à filial {clinica_destino.nome} com {existente.medico.nome} como responsável.",
            "success",
        )
        return redirect(url_for("medico.exames_por_filial"))

    novo_exame = Exame(
        clinica_id=clinica_destino.id,
        medico_id=medico_id,
        nome=origem.nome,
        descricao=origem.descricao,
        duracao_minutos=origem.duracao_minutos,
        preco=preco,
        precisa_acompanhante=origem.precisa_acompanhante,
        # O modelo de preparo é específico de cada filial (PreparoModelo é
        # por clínica) - não dá pra copiar o id de uma filial pra outra,
        # por isso fica em branco aqui, pra revisar/escolher depois na
        # tela de editar o exame recém-criado.
        preparo_modelo_id=None,
        # Médico escolhido de propósito aqui, na tela de associação - vale
        # como confirmado (diferente do valor técnico/provisório do
        # cadastro genérico do exame, ver exames_novo).
        medico_confirmado=True,
        associado=True,
    )
    db.session.add(novo_exame)
    db.session.commit()

    flash(
        f"\"{nome}\" associado à filial {clinica_destino.nome} com {novo_exame.medico.nome} como responsável. "
        "Revise a duração e o modelo de preparo dessa filial, se for diferente.",
        "success",
    )
    return redirect(url_for("medico.exames_por_filial"))


@medico_bp.route("/exames/por-filial/<int:exame_id>/atualizar", methods=["POST"])
@login_required
@staff_required
def exames_por_filial_atualizar(exame_id):
    """Atualiza uma associação já existente (um exame já criado numa
    filial) - usada pelo "Editar" da tela de associações
    (medico.exames_por_filial). TODOS os campos são editáveis: exame,
    filial, médico e preço. Também aceita ajustar os médicos extras
    quando o chamador manda atualizar_extras=1."""
    empresa = empresa_atual()
    exame = Exame.query.join(Clinica, Exame.clinica_id == Clinica.id).filter(
        Exame.id == exame_id, Clinica.empresa_id == empresa.id,
    ).first_or_404()

    # Trocar o EXAME e/ou a FILIAL da associação: só quando esses campos
    # vêm no formulário e mudaram de valor. Se a associação já tem
    # agendamento marcado, exame/filial ficam travados (o agendamento
    # aponta pra esta associação; mudar por baixo bagunçaria a agenda e o
    # preparo dos pacientes) - médico e preço continuam editáveis.
    nome_novo = request.form.get("nome", "").strip() if "nome" in request.form else None
    filial_nova_id = request.form.get("clinica_destino_id", type=int)
    mudou_exame = bool(nome_novo) and nome_novo != exame.nome
    mudou_filial = bool(filial_nova_id) and filial_nova_id != exame.clinica_id
    if mudou_exame or mudou_filial:
        if exame.agendamentos:
            flash(
                "Esta associação já tem agendamentos - não dá pra trocar o exame ou a filial dela. "
                "Troque só médico/preço, ou exclua a associação (após tratar os agendamentos) e crie outra.",
                "danger",
            )
            return redirect(url_for("medico.exames_por_filial", editar=exame.id))

        filial_destino = exame.clinica
        if mudou_filial:
            filial_destino = Clinica.query.filter_by(id=filial_nova_id, empresa_id=empresa.id).first()
            if not filial_destino:
                flash("Escolha uma filial válida.", "danger")
                return redirect(url_for("medico.exames_por_filial", editar=exame.id))

        nome_final = nome_novo if mudou_exame else exame.nome
        ja_existe = Exame.query.filter(
            Exame.clinica_id == filial_destino.id, Exame.nome == nome_final,
            Exame.id != exame.id, Exame.associado.is_(True),
        ).first()
        if ja_existe:
            flash(f"\"{nome_final}\" já está associado à filial {filial_destino.nome}.", "warning")
            return redirect(url_for("medico.exames_por_filial", editar=exame.id))

        if mudou_exame:
            # Os dados do exame (descrição/duração/acompanhante) vêm do
            # cadastro dele em outra filial da empresa - a associação passa
            # a ser DESSE exame, não é só uma troca de rótulo.
            origem = (
                Exame.query.join(Clinica, Exame.clinica_id == Clinica.id)
                .filter(Clinica.empresa_id == empresa.id, Exame.nome == nome_final, Exame.id != exame.id)
                .first()
            )
            if not origem:
                flash("Exame não encontrado.", "danger")
                return redirect(url_for("medico.exames_por_filial", editar=exame.id))
            exame.nome = origem.nome
            exame.descricao = origem.descricao
            exame.duracao_minutos = origem.duracao_minutos
            exame.precisa_acompanhante = origem.precisa_acompanhante
            # O modelo de preparo é por filial e por exame - com a
            # associação apontando pra outro exame, o modelo antigo não
            # vale mais; fica pra revisar na edição do exame.
            exame.preparo_modelo_id = None

        if mudou_filial:
            exame.clinica_id = filial_destino.id
            # Modelo de preparo e médicos extras eram da filial antiga.
            exame.preparo_modelo_id = None
            exame.medicos_extra = []

    # Só valida/atualiza o preço quando ele veio no formulário - chamadas
    # que só mexem em médico/extras não mandam o campo.
    if "preco" in request.form:
        preco = _parse_valor_decimal(request.form.get("preco", ""))
        if preco is None:
            flash("Informe um preço válido.", "danger")
            return redirect(url_for("medico.exames_por_filial", editar=exame.id))
        exame.preco = preco

    # Não existe "médico principal" - qualquer pessoa da equipe (médico
    # ou secretária) pode reatribuir o médico responsável aqui, escolhendo
    # numa lista igual a qualquer outro campo do formulário. Escolher (ou
    # confirmar) o médico aqui é o que torna esse valor "confirmado" -
    # antes disso, pode ter sido só um valor técnico/provisório do
    # cadastro genérico do exame (ver exames_novo).
    medico_id = request.form.get("medico_id", type=int)
    # DONO do exame: se o exame foi criado por um médico, só ELE pode ser
    # o médico da associação - mesma regra do associar.
    dono = _dono_medico_do_exame(exame.nome, empresa)
    if dono and medico_id and medico_id != dono.id:
        flash(
            f"O exame \"{exame.nome}\" pertence a {dono.nome} (quem o criou) — só ele pode ser "
            "associado a este exame.",
            "danger",
        )
        db.session.rollback()
        return redirect(url_for("medico.exames_por_filial", editar=exame.id))
    filial_do_exame = Clinica.query.get(exame.clinica_id)
    medicos_da_filial = medicos_da_clinica(filial_do_exame)
    if medico_id and any(m.id == medico_id for m in medicos_da_filial):
        exame.medico_id = medico_id
        exame.medico_confirmado = True
    elif not any(m.id == exame.medico_id for m in medicos_da_filial) and medicos_da_filial:
        # A filial mudou e o médico antigo não atua nela - sem um médico
        # válido escolhido, não dá pra salvar (medico_id é obrigatório).
        flash("Escolha um médico que atue na filial escolhida.", "danger")
        db.session.rollback()
        return redirect(url_for("medico.exames_por_filial", editar=exame.id))

    if request.form.get("atualizar_extras") == "1":
        # Quem manda esse campo explicitamente pode ajustar os médicos
        # extras junto - as chamadas normais da tela de associação não
        # mandam, pra não mexer nos extras sem querer.
        medicos_da_filial = medicos_da_clinica(exame.clinica)
        medicos_extra_ids = {v for v in request.form.getlist("medicos_extra_ids", type=int) if v != exame.medico_id}
        if dono and medicos_extra_ids:
            # Exame com dono médico não aceita outros médicos associados.
            flash(
                f"O exame \"{exame.nome}\" pertence a {dono.nome} — outros médicos não podem "
                "ser associados a ele.",
                "danger",
            )
            db.session.rollback()
            return redirect(url_for("medico.exames_por_filial", editar=exame.id))
        exame.medicos_extra = [m for m in medicos_da_filial if m.id in medicos_extra_ids]

    db.session.commit()
    flash(f"\"{exame.nome}\" atualizado na filial {exame.clinica.nome}.", "success")
    return redirect(url_for("medico.exames_por_filial"))


@medico_bp.route("/exames/por-filial/<int:exame_id>/excluir", methods=["POST"])
@login_required
@staff_required
def exames_por_filial_excluir(exame_id):
    """Exclui uma associação (o exame naquela filial) - botão "Excluir"
    do Editar na tela de associações. Associação com agendamento marcado
    não pode ser excluída (o agendamento aponta pra ela); trate os
    agendamentos primeiro. Perguntas e mensagens de chat antigas que
    citavam este exame são mantidas, só perdem o vínculo com ele."""
    empresa = empresa_atual()
    exame = Exame.query.join(Clinica, Exame.clinica_id == Clinica.id).filter(
        Exame.id == exame_id, Clinica.empresa_id == empresa.id,
    ).first_or_404()

    if exame.agendamentos:
        flash(
            f"\"{exame.nome}\" tem agendamento(s) na filial {exame.clinica.nome} - "
            "cancele/realize esses agendamentos antes de excluir a associação.",
            "danger",
        )
        return redirect(url_for("medico.exames_por_filial", editar=exame.id))

    # Referências opcionais (histórico) só perdem o vínculo - nada é apagado.
    PerguntaPendente.query.filter_by(exame_id=exame.id).update({"exame_id": None})
    ChatMensagem.query.filter_by(exame_id=exame.id).update({"exame_id": None})

    nome, filial_nome = exame.nome, exame.clinica.nome
    db.session.delete(exame)
    db.session.commit()
    flash(f"Associação de \"{nome}\" com a filial {filial_nome} excluída.", "success")
    return redirect(url_for("medico.exames_por_filial"))


# ---------- Modelos de preparo (reaproveitáveis entre exames) ----------

@medico_bp.route("/preparo-modelos")
@login_required
@staff_required
def preparo_modelos_lista():
    modelos = (
        PreparoModelo.query.filter(PreparoModelo.clinica_id.in_(_filiais_da_empresa_ids()))
        .order_by(PreparoModelo.nome).all()
    )
    # Mesmo padrão do dono de conteúdo clínico usado em "Exames"/"Associar
    # exames" (ver Exame.dono_medico): um médico só ENXERGA os modelos de
    # preparo que são dele ou que não têm dono médico registrado (legado/
    # criado pela secretária) - não só a edição já era bloqueada, a lista
    # inteira não deveria nem mostrar o conteúdo clínico de outro médico.
    # Secretária/dono continuam vendo todos.
    if eh_medico():
        modelos = [m for m in modelos if m.dono_medico is None or m.dono_medico.id == current_user.id]
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
    # Modelo de preparo é CONFIGURAÇÃO da empresa - quem configura não
    # precisa estar vinculado a local nenhum (era exatamente esse o bug:
    # o fundador sem vínculo perdia o formulário inteiro com um aviso de
    # "nenhum local cadastrado", mesmo com a empresa tendo locais).
    filiais = _filiais_da_empresa()
    sugestao = None
    if request.method == "GET" and request.args.get("de_importacao"):
        sugestao = session.pop("preparo_sugestao_importada", None)

    if request.method == "POST":
        # Modelo de preparo é genérico - não pertence a uma filial específica,
        # e sim à empresa como um todo (fica disponível em qualquer exame de
        # qualquer local de atendimento). O campo clinica_id continua
        # existindo no banco só por exigência técnica da FK/constraint atual;
        # usamos a primeira filial acessível do usuário sem expor essa
        # escolha na tela.
        if not filiais:
            flash("A empresa ainda não tem nenhum local de atendimento cadastrado.", "danger")
            return redirect(url_for("medico.filiais_lista"))
        filial = filiais[0]

        nome = request.form.get("nome", "").strip()
        instrucoes = request.form.get("instrucoes", "").strip()
        observacoes_medicamentos = request.form.get("observacoes_medicamentos", "").strip()

        if not nome or not instrucoes:
            flash("Nome do modelo e instruções são obrigatórios.", "danger")
            return render_template("medico/preparo_modelo_form.html", modelo=None, sugestao=None, medicamentos_catalogo=Medicamento.query.order_by(Medicamento.nome).all())

        if PreparoModelo.query.filter(PreparoModelo.clinica_id.in_(_filiais_da_empresa_ids()), PreparoModelo.nome == nome).first():
            flash("Já existe um modelo de preparo com esse nome.", "danger")
            return render_template("medico/preparo_modelo_form.html", modelo=None, sugestao=None, medicamentos_catalogo=Medicamento.query.order_by(Medicamento.nome).all())

        modelo = PreparoModelo(
            clinica_id=filial.id, nome=nome, instrucoes=instrucoes,
            observacoes_medicamentos=observacoes_medicamentos or None,
            # DONO do modelo: quem criou. Se for um médico, só ele
            # edita/remove (ver PreparoModelo.pode_ser_editado_por).
            criado_por_id=current_user.id,
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
    modelo = PreparoModelo.query.filter(
        PreparoModelo.id == modelo_id, PreparoModelo.clinica_id.in_(_filiais_da_empresa_ids())
    ).first_or_404()
    # DONO do conteúdo clínico: modelo criado por um médico só é editado
    # POR ELE. Modelos sem dono registrado (antigos) seguem como antes.
    if not modelo.pode_ser_editado_por(current_user):
        flash(
            f"Só {modelo.dono_medico.nome}, que criou este modelo de preparo, pode editá-lo.",
            "danger",
        )
        return redirect(url_for("medico.preparo_modelos_lista"))

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
    modelo = PreparoModelo.query.filter(
        PreparoModelo.id == modelo_id, PreparoModelo.clinica_id.in_(_filiais_da_empresa_ids())
    ).first_or_404()
    # Mesma regra da edição: só o médico dono remove.
    if not modelo.pode_ser_editado_por(current_user):
        flash(
            f"Só {modelo.dono_medico.nome}, que criou este modelo de preparo, pode removê-lo.",
            "danger",
        )
        return redirect(url_for("medico.preparo_modelos_lista"))
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


@medico_bp.route("/preparo-modelos/pdf-para-excel", methods=["GET", "POST"])
@login_required
@staff_required
def preparo_pdf_para_excel():
    """Gera uma planilha Excel (.xlsx) pronta pra revisão a partir de um PDF
    de preparo - substitui a antiga importação direta de PDF pro formulário
    de modelo (preparo_modelos_importar_pdf, removida): a extração
    heurística de PDF nunca foi tão confiável quanto a de Excel (ver
    app.xlsx_preparo), então agora ela só gera a planilha - a pessoa revisa/
    ajusta no Excel com calma e importa o resultado pelo botão "Importar de
    um Excel" já existente na tela de novo modelo de preparo."""
    if request.method == "POST":
        arquivo = request.files.get("arquivo_pdf")
        if not arquivo or not arquivo.filename:
            flash("Selecione um arquivo PDF.", "danger")
            return render_template("medico/preparo_pdf_para_excel.html")

        try:
            sugestao = extrair_sugestao_de_pdf(arquivo.stream)
            planilha_buffer = gerar_xlsx_da_sugestao(sugestao)
        except Exception:
            flash(
                "Não foi possível ler esse PDF. Ele pode estar corrompido, protegido por senha, ou ser "
                "uma imagem escaneada sem texto selecionável — nesse caso, cadastre o modelo manualmente.",
                "danger",
            )
            return render_template("medico/preparo_pdf_para_excel.html")

        return send_file(
            planilha_buffer,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name="preparo-extraido-do-pdf.xlsx",
        )

    return render_template("medico/preparo_pdf_para_excel.html")


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
    "nao_compareceu": "#fd7e14",
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
    """Retorna os agendamentos no formato que o FullCalendar espera. Só
    mostra o que ainda está de pé (solicitado/agendado/confirmado) —
    cancelado e realizado não aparecem mais no calendário do painel."""
    query = Agendamento.query.filter(
        Agendamento.clinica_id.in_(filiais_atuais_ids()),
        Agendamento.status.in_(["solicitado", "agendado", "confirmado"]),
    )
    if eh_medico():
        query = query.filter_by(medico_id=current_user.id)
    agendamentos = query.all()
    eventos = [
        {
            "id": a.id,
            "title": f"{a.data_hora.strftime('%H:%M')} · {a.paciente.nome} · {a.exame.nome}",
            "filial": a.clinica.nome,
            "start": a.data_hora.isoformat(),
            "color": CORES_STATUS.get(a.status, "#6c757d"),
            "extendedProps": {
                "paciente": a.paciente.nome,
                "exame": a.exame.nome,
                "status": a.status,
                "filial": a.clinica.nome,
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
    filiais_disponiveis = filiais_atuais()

    if request.method == "POST":
        filial_id = request.form.get("filial_id", type=int)
        paciente_id = request.form.get("paciente_id", type=int)
        exame_id = request.form.get("exame_id", type=int)
        data_hora_str = request.form.get("data_hora")
        observacoes = request.form.get("observacoes", "").strip()
        acompanhante_nome = request.form.get("acompanhante_nome", "").strip()
        acompanhante_telefone = request.form.get("acompanhante_telefone", "").strip()

        if telefone_incompleto(acompanhante_telefone):
            flash("Telefone do acompanhante incompleto — digite o DDD e o número completos.", "danger")
            return redirect(url_for("medico.agenda_novo", filial_id=filial_id))

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

        # O exame precisa pertencer à filial escolhida; o paciente é da
        # EMPRESA (qualquer paciente da empresa pode ser agendado em
        # qualquer filial - a filial do atendimento é a deste agendamento).
        paciente = Paciente.query.filter(Paciente.id == paciente_id, _filtro_pacientes_da_empresa()).first()
        # Só exame ASSOCIADO à filial pode ser agendado (item de catálogo
        # sem associação não é ofertado em lugar nenhum).
        exame_query = Exame.query.filter_by(id=exame_id, clinica_id=filial.id, associado=True)
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

        # Choque de horário do médico multi-clínica: se ele já tem um
        # agendamento nesse intervalo em OUTRO local (mesmo de outra
        # empresa), não deixa marcar - a agenda do médico é uma só. A
        # mensagem não expõe dados da outra empresa: só diz que o horário
        # está tomado em outro local de atuação do médico.
        conflito = conflito_de_agenda(medico_atende_id, data_hora, exame.duracao_minutos)
        if conflito and conflito.clinica_id != filial.id:
            if conflito.clinica.empresa_id == filial.empresa_id:
                onde = f"na filial '{conflito.clinica.nome}'"
            else:
                onde = "em outro local em que ele atende (fora desta empresa)"
            flash(
                f"Esse médico já tem um agendamento nesse horário {onde} — escolha outro horário.",
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
    filial_selecionada = (
        next((f for f in filiais_disponiveis if f.id == filial_id_param), None)
        or clinica_atual()
    )

    if eh_medico():
        medicos_disponiveis = []
        medico_selecionado_id = current_user.id
    else:
        medicos_disponiveis = medicos_da_clinica(filial_selecionada)
        medico_id_param = request.args.get("medico_id", type=int)
        medico_selecionado_id = medico_id_param if any(m.id == medico_id_param for m in medicos_disponiveis) else None
        if medico_selecionado_id is None and len(medicos_disponiveis) == 1:
            medico_selecionado_id = medicos_disponiveis[0].id

    # Pacientes são da EMPRESA - a lista não depende da filial escolhida
    # (a filial vale só para o exame e para onde o atendimento acontece).
    pacientes = Paciente.query.filter(_filtro_pacientes_da_empresa()).order_by(Paciente.nome).all()

    exames_query = Exame.query.filter_by(clinica_id=filial_selecionada.id, associado=True)
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
    query = Agendamento.query.filter(
        Agendamento.id == agendamento_id,
        Agendamento.clinica_id.in_(filiais_atuais_ids()),
    )
    if eh_medico():
        query = query.filter(Agendamento.medico_id == current_user.id)
    agendamento = query.first_or_404()
    novo_status = request.form.get("status", agendamento.status)
    status_validos = {"solicitado", "agendado", "confirmado", "realizado", "cancelado", "nao_compareceu"}
    if novo_status in status_validos:
        agendamento.status = novo_status
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
    query = Agendamento.query.filter(
        Agendamento.id == agendamento_id,
        Agendamento.clinica_id.in_(filiais_atuais_ids()),
    )
    if eh_medico():
        query = query.filter(Agendamento.medico_id == current_user.id)
    agendamento = query.first_or_404()
    acompanhante_telefone = request.form.get("acompanhante_telefone", "").strip()
    if telefone_incompleto(acompanhante_telefone):
        flash("Telefone do acompanhante incompleto — digite o DDD e o número completos.", "danger")
        return redirect(url_for("medico.agenda"))
    agendamento.acompanhante_nome = request.form.get("acompanhante_nome", "").strip() or None
    agendamento.acompanhante_telefone = acompanhante_telefone or None
    db.session.commit()
    flash("Acompanhante atualizado.", "success")
    return redirect(url_for("medico.agenda"))


# ---------- Solicitações de agendamento feitas pelo paciente ----------

@medico_bp.route("/agenda/solicitacoes")
@login_required
@staff_required
def agenda_solicitacoes():
    """Solicitações de agendamento feitas pelos pacientes, aguardando a
    equipe confirmar/recusar. MESMA regra do contador do Painel (ver
    dashboard): médico COM a permissão administrativa de pacientes (ex.:
    o fundador) vê e decide todas as solicitações das filiais dele; só o
    médico SEM essa permissão fica restrito às solicitações endereçadas a
    ele mesmo - antes a lista filtrava sempre pelo médico logado, e o
    Painel mostrava "1" enquanto esta tela vinha vazia."""
    query = Agendamento.query.filter(
        Agendamento.clinica_id.in_(filiais_atuais_ids()), Agendamento.status == "solicitado"
    )
    if eh_medico() and not current_user.perm_pacientes:
        query = query.filter(Agendamento.medico_id == current_user.id)
    solicitacoes = query.order_by(Agendamento.data_hora.asc()).all()
    return render_template("medico/agenda_solicitacoes.html", solicitacoes=solicitacoes)


@medico_bp.route("/agenda/<int:agendamento_id>/confirmar-solicitacao", methods=["POST"])
@login_required
@staff_required
def agenda_confirmar_solicitacao(agendamento_id):
    query = Agendamento.query.filter(
        Agendamento.id == agendamento_id,
        Agendamento.clinica_id.in_(filiais_atuais_ids()), Agendamento.status == "solicitado",
    )
    # Mesma regra da listagem (agenda_solicitacoes): médico sem a
    # permissão administrativa só decide as solicitações dele.
    if eh_medico() and not current_user.perm_pacientes:
        query = query.filter(Agendamento.medico_id == current_user.id)
    agendamento = query.first_or_404()
    acao = request.form.get("acao")
    if acao == "recusar":
        agendamento.status = "cancelado"
        flash("Solicitação de agendamento recusada.", "success")
    else:
        # Antes de confirmar, checa o choque de horário do médico em
        # QUALQUER local em que ele atenda (a agenda dele é uma só) - a
        # solicitação pode ter sido feita antes de outro agendamento
        # ocupar o horário.
        conflito = conflito_de_agenda(
            agendamento.medico_id, agendamento.data_hora,
            agendamento.exame.duracao_minutos if agendamento.exame else None,
            ignorar_agendamento_id=agendamento.id,
        )
        if conflito:
            if conflito.clinica_id == agendamento.clinica_id:
                onde = "nesta filial"
            elif conflito.clinica.empresa_id == agendamento.clinica.empresa_id:
                onde = f"na filial '{conflito.clinica.nome}'"
            else:
                onde = "em outro local em que ele atende (fora desta empresa)"
            flash(
                f"O médico já tem um agendamento nesse horário {onde} — combine outro horário "
                "com o paciente (recuse esta solicitação e crie um novo agendamento).",
                "danger",
            )
            return redirect(url_for("medico.agenda_solicitacoes"))
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
    filiais = filiais_atuais()
    empresa = empresa_atual()

    # Um médico sem a permissão de gerir a equipe só configura o próprio
    # horário. Secretárias e médicos com "perm_equipe" (ex.: o médico
    # fundador da clínica) podem escolher qualquer médico dos locais em que
    # a pessoa atua — mesma regra usada nas outras telas administrativas.
    pode_escolher_medico, medico_alvo = _resolver_medico_alvo(filiais, medico_id)
    if not medico_alvo:
        flash("Nenhum médico cadastrado ainda.", "danger")
        return redirect(url_for("medico.equipe_lista"))

    # Locais (filiais da mesma empresa) em que esse médico atende - permite
    # escolher o horário de qualquer um deles direto nesta tela, sem
    # precisar trocar de filial no menu superior primeiro. Cada local tem
    # seu próprio horário independente (ver MedicoHorario.clinica_id).
    locais_do_medico = (
        Clinica.query.join(ClinicaMembro, ClinicaMembro.clinica_id == Clinica.id)
        .filter(
            ClinicaMembro.usuario_id == medico_alvo.id, Clinica.empresa_id == empresa.id,
            ClinicaMembro.ativo.is_(True),
        )
        .order_by(Clinica.nome)
        .all()
    )
    if not locais_do_medico:
        locais_do_medico = list(filiais)

    clinica_id_escolhida = request.values.get("clinica_id", type=int)
    clinica_alvo = (
        next((c for c in locais_do_medico if c.id == clinica_id_escolhida), None)
        or next((c for c in locais_do_medico if c in filiais), locais_do_medico[0])
    )

    if request.method == "POST":
        horarios_existentes = {
            h.dia_semana: h
            for h in MedicoHorario.query.filter_by(clinica_id=clinica_alvo.id, medico_id=medico_alvo.id).all()
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
                horario = MedicoHorario(clinica_id=clinica_alvo.id, medico_id=medico_alvo.id, dia_semana=dia_idx)
                db.session.add(horario)

            horario.ativo = ativo
            horario.hora_inicio = parse_hora(hora_inicio_str)
            horario.hora_fim = parse_hora(hora_fim_str)

        db.session.commit()
        flash(f"Horário de atendimento de {medico_alvo.nome} em {clinica_alvo.nome} atualizado.", "success")
        # Salvar CONTINUA na tela de horário (mesmo médico/local) - mesmo
        # quando a pessoa chegou aqui pelo assistente de configuração
        # inicial. É comum salvar e continuar ajustando (outro local do
        # mesmo médico, outro médico); o assistente marca a etapa como
        # concluída sozinho (o status é calculado dos dados reais) e segue
        # acessível pelo aviso no Painel.
        return redirect(url_for("medico.medico_horarios", medico_id=medico_alvo.id, clinica_id=clinica_alvo.id))

    horarios_por_dia = {
        h.dia_semana: h
        for h in MedicoHorario.query.filter_by(clinica_id=clinica_alvo.id, medico_id=medico_alvo.id).all()
    }
    return render_template(
        "medico/medico_horarios.html",
        medico_alvo=medico_alvo,
        medicos=(medicos_das_filiais(filiais) if pode_escolher_medico else []),
        locais_do_medico=locais_do_medico,
        clinica_alvo=clinica_alvo,
        dias_semana=list(enumerate(DIAS_SEMANA)),
        horarios_por_dia=horarios_por_dia,
    )


def _resolver_medico_alvo(filiais, medico_id):
    """Mesma regra usada em toda tela "do médico": um médico sem
    perm_equipe só vê/edita os próprios dados; secretárias e médicos com
    perm_equipe podem escolher qualquer médico dos locais em que a pessoa
    atua. Retorna (pode_escolher_medico, medico_alvo) — medico_alvo é None
    só quando não há nenhum médico cadastrado (caso em que o chamador deve
    redirecionar)."""
    pode_escolher_medico = current_user.perm_equipe or not eh_medico()
    if pode_escolher_medico:
        medicos = medicos_das_filiais(filiais)
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
    filiais = filiais_atuais()
    pode_escolher_medico, medico_alvo = _resolver_medico_alvo(filiais, medico_id)
    if not medico_alvo:
        flash("Nenhum médico cadastrado ainda.", "danger")
        return redirect(url_for("medico.equipe_lista"))

    # Só exames confirmados aparecem aqui — é a lista de trabalho do
    # médico para o que já está confirmado com o paciente, não uma agenda
    # geral (essa fica em "Agenda de exames", no Painel). Sem filtro de
    # data: um exame de hoje que já passou do horário mas ainda não foi
    # marcado como "realizado" continua precisando aparecer aqui.
    proximos = (
        Agendamento.query.filter(
            Agendamento.clinica_id.in_([f.id for f in filiais]),
            Agendamento.medico_id == medico_alvo.id,
            Agendamento.status == "confirmado",
        )
        .order_by(Agendamento.data_hora.asc())
        .all()
    )
    return render_template(
        "medico/medico_agenda_pessoal.html",
        medico_alvo=medico_alvo,
        medicos=(medicos_das_filiais(filiais) if pode_escolher_medico else []),
        proximos=proximos,
    )


@medico_bp.route("/minha-agenda-completa")
@login_required
@staff_required
def minha_agenda_completa():
    """Agenda CONSOLIDADA do médico logado: os agendamentos dele em TODOS
    os locais em que atende - inclusive filiais de outras empresas. As
    empresas não se enxergam entre si, mas a agenda do médico é uma só
    (ver conflito_de_agenda em app/agendamento_otimizador.py): esta tela
    é a visão dessa agenda única, um dos ganhos do médico multi-clínica
    por código mestre. Só a própria pessoa logada vê a consolidação - a
    secretária de cada clínica continua vendo apenas a agenda da clínica
    dela."""
    if not eh_medico():
        flash("Esta tela é a agenda pessoal consolidada de contas de médico.", "danger")
        return redirect(url_for("medico.dashboard"))
    agendamentos = (
        Agendamento.query.filter(
            Agendamento.medico_id == current_user.id,
            Agendamento.status.in_(("solicitado", "agendado", "confirmado")),
            Agendamento.data_hora >= datetime.utcnow() - timedelta(days=1),
        )
        .order_by(Agendamento.data_hora.asc())
        .all()
    )
    return render_template("medico/minha_agenda_completa.html", agendamentos=agendamentos)


# ---------- Bloqueio de agenda (compromisso próprio do médico) ----------

@medico_bp.route("/medico-bloqueios", methods=["GET", "POST"])
@medico_bp.route("/medico-bloqueios/<int:medico_id>", methods=["GET", "POST"])
@login_required
@staff_required
def medico_bloqueios(medico_id=None):
    filiais = filiais_atuais()
    filial_ids = [f.id for f in filiais]
    pode_escolher_medico, medico_alvo = _resolver_medico_alvo(filiais, medico_id)
    if not medico_alvo:
        flash("Nenhum médico cadastrado ainda.", "danger")
        return redirect(url_for("medico.equipe_lista"))

    # O bloqueio é de um local específico (a agenda é por filial) — com mais
    # de um local, a pessoa escolhe qual no próprio formulário.
    locais_do_medico = [f for f in filiais if any(m.id == medico_alvo.id for m in f.medicos_e_secretarias)] or list(filiais)

    if request.method == "POST":
        filial_bloqueio = _filial_do_form(locais_do_medico)
        if not filial_bloqueio:
            flash("Escolha o local do bloqueio de agenda.", "danger")
            return redirect(url_for("medico.medico_bloqueios", medico_id=medico_alvo.id))

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
                clinica_id=filial_bloqueio.id, medico_id=medico_alvo.id,
                data_inicio=data_inicio_dt, data_fim=data_fim_dt,
                motivo=motivo or None, dia_inteiro=dia_inteiro,
            )
            db.session.add(bloqueio)
            db.session.commit()
            flash("Bloqueio de agenda cadastrado.", "success")
        return redirect(url_for("medico.medico_bloqueios", medico_id=medico_alvo.id))

    bloqueios = (
        MedicoBloqueio.query.filter(
            MedicoBloqueio.clinica_id.in_(filial_ids),
            MedicoBloqueio.medico_id == medico_alvo.id,
        )
        .order_by(MedicoBloqueio.data_inicio.desc())
        .all()
    )
    return render_template(
        "medico/medico_bloqueios.html",
        medico_alvo=medico_alvo,
        medicos=(medicos_das_filiais(filiais) if pode_escolher_medico else []),
        locais_do_medico=locais_do_medico,
        bloqueios=bloqueios,
    )


@medico_bp.route("/medico-bloqueios/<int:bloqueio_id>/remover", methods=["POST"])
@login_required
@staff_required
def medico_bloqueio_remover(bloqueio_id):
    query = MedicoBloqueio.query.filter(
        MedicoBloqueio.id == bloqueio_id,
        MedicoBloqueio.clinica_id.in_(filiais_atuais_ids()),
    )
    if eh_medico() and not current_user.perm_equipe:
        query = query.filter(MedicoBloqueio.medico_id == current_user.id)
    bloqueio = query.first_or_404()
    medico_id = bloqueio.medico_id
    db.session.delete(bloqueio)
    db.session.commit()
    flash("Bloqueio removido.", "success")
    return redirect(url_for("medico.medico_bloqueios", medico_id=medico_id))


# ---------- Certificado digital pessoal do médico (assinatura de evolução) ----------

@medico_bp.route("/meu-certificado-digital", methods=["GET", "POST"])
@login_required
@staff_required
def certificado_digital():
    """Upload do certificado digital pessoal (e-CPF, ICP-Brasil) do
    médico, usado para assinar digitalmente as evoluções clínicas que ele
    registrar (ver app/assinatura_clinica.py). Só médicos têm essa tela —
    é uma assinatura pessoal, não algo que uma secretária faça em nome de
    outra pessoa."""
    if current_user.tipo != "medico":
        flash("Só médicos têm certificado digital pessoal para assinar evoluções.", "danger")
        return redirect(url_for("medico.dashboard"))

    if request.method == "POST":
        arquivo = request.files.get("certificado_arquivo")
        senha = request.form.get("certificado_senha", "")

        if not arquivo or not arquivo.filename:
            flash("Selecione o arquivo do certificado (.pfx) antes de enviar.", "danger")
            return redirect(url_for("medico.certificado_digital"))
        if not senha:
            flash("Informe a senha do certificado.", "danger")
            return redirect(url_for("medico.certificado_digital"))

        conteudo = arquivo.read()
        if len(conteudo) > 5 * 1024 * 1024:
            flash("Arquivo muito grande para ser um certificado válido.", "danger")
            return redirect(url_for("medico.certificado_digital"))

        try:
            _chave_privada, certificado, _cadeia = pkcs12.load_key_and_certificates(
                conteudo, senha.encode("utf-8")
            )
        except Exception:
            flash(
                "Não foi possível abrir o certificado — verifique se o arquivo é um .pfx/.p12 "
                "válido e se a senha está correta.",
                "danger",
            )
            return redirect(url_for("medico.certificado_digital"))

        if certificado is None:
            flash("O arquivo enviado não contém um certificado válido.", "danger")
            return redirect(url_for("medico.certificado_digital"))

        titular_extraido = None
        try:
            titular_extraido = certificado.subject.rfc4514_string()
        except Exception:
            titular_extraido = None

        if hasattr(certificado, "not_valid_after_utc"):
            validade = certificado.not_valid_after_utc.date()
        else:
            validade = certificado.not_valid_after.date()

        current_user.certificado_digital_pfx = criptografar_bytes_clinico(conteudo)
        current_user.certificado_digital_senha_cripto = criptografar_texto_clinico(senha)
        current_user.certificado_digital_titular = titular_extraido
        current_user.certificado_digital_validade = validade
        db.session.commit()

        flash("Certificado digital validado e salvo com sucesso. Suas próximas evoluções já serão assinadas.", "success")
        return redirect(url_for("medico.certificado_digital"))

    return render_template("medico/certificado_digital.html")


# ---------- Atendimento (continuidade/encerramento da consulta) ----------

@medico_bp.route("/agenda/<int:agendamento_id>/atendimento", methods=["GET", "POST"])
@login_required
@staff_required
def atendimento(agendamento_id):
    query = Agendamento.query.filter(
        Agendamento.id == agendamento_id,
        Agendamento.clinica_id.in_(filiais_atuais_ids()),
    )
    if eh_medico():
        query = query.filter(Agendamento.medico_id == current_user.id)
    agendamento = query.first_or_404()

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

    # Histórico clínico (evolução) completo do paciente, mais recente
    # primeiro — inclui entradas de qualquer atendimento, não só deste.
    evolucoes_paciente = (
        EvolucaoClinica.query.filter_by(paciente_id=agendamento.paciente_id)
        .order_by(EvolucaoClinica.criado_em.desc())
        .all()
    )

    registrar_acesso(agendamento.paciente_id, "visualizar_prontuario", detalhe=f"agendamento_id={agendamento.id}")

    return render_template(
        "medico/atendimento.html",
        agendamento=agendamento,
        mensagens_chat=mensagens_chat,
        atendimentos_anteriores=atendimentos_anteriores,
        evolucoes_paciente=evolucoes_paciente,
    )


@medico_bp.route("/agenda/<int:agendamento_id>/evolucao/nova", methods=["POST"])
@login_required
@staff_required
def atendimento_evolucao_nova(agendamento_id):
    """Registra uma nova entrada de evolução clínica. Ver
    app.models.EvolucaoClinica: é IMUTÁVEL por desenho — esta rota só cria,
    nunca edita nem apaga uma entrada existente. Se algo foi anotado
    errado, a correção é uma entrada nova, igual num prontuário de papel."""
    query = Agendamento.query.filter(
        Agendamento.id == agendamento_id,
        Agendamento.clinica_id.in_(filiais_atuais_ids()),
    )
    if eh_medico():
        query = query.filter(Agendamento.medico_id == current_user.id)
    agendamento = query.first_or_404()

    texto = request.form.get("texto", "").strip()
    if not texto:
        flash("Escreva alguma coisa na evolução antes de salvar.", "danger")
        return redirect(url_for("medico.atendimento", agendamento_id=agendamento.id))

    def _numero(campo, tipo=float):
        valor = request.form.get(campo, "").strip().replace(",", ".")
        if not valor:
            return None
        try:
            return tipo(valor)
        except ValueError:
            return None

    sinais_vitais = {
        "peso_kg": _numero("peso_kg"),
        "altura_cm": _numero("altura_cm", tipo=int),
        "pressao_arterial": request.form.get("pressao_arterial", "").strip() or None,
        "frequencia_cardiaca_bpm": _numero("frequencia_cardiaca_bpm", tipo=int),
        "temperatura_celsius": _numero("temperatura_celsius"),
    }
    criado_em = datetime.utcnow()

    evolucao = EvolucaoClinica(
        agendamento_id=agendamento.id,
        paciente_id=agendamento.paciente_id,
        autor_id=current_user.id,
        texto=texto,
        criado_em=criado_em,
        **sinais_vitais,
    )

    # Tenta assinar digitalmente com o certificado pessoal do autor (só
    # médico com certificado configurado — ver app/assinatura_clinica.py).
    # Se não houver certificado, a entrada é salva do mesmo jeito, só sem
    # assinatura (nível NGS2 em vez de NGS3).
    assinatura_ok = None
    try:
        assinatura_ok = assinar_evolucao_se_possivel(
            current_user, texto, agendamento.paciente_id, agendamento.id, current_user.id, criado_em, sinais_vitais,
        )
    except ErroAssinatura as erro:
        flash(str(erro), "warning")

    if assinatura_ok:
        evolucao.assinatura_base64 = assinatura_ok["assinatura_base64"]
        evolucao.assinatura_certificado_titular = assinatura_ok["assinatura_certificado_titular"]
        evolucao.assinatura_certificado_serial = assinatura_ok["assinatura_certificado_serial"]
        evolucao.assinatura_certificado_pem = assinatura_ok["assinatura_certificado_pem"]
        evolucao.assinatura_hash_sha256 = assinatura_ok["assinatura_hash_sha256"]
        evolucao.assinado_em = assinatura_ok["assinado_em"]

    db.session.add(evolucao)
    db.session.commit()

    registrar_acesso(
        agendamento.paciente_id, "criar_evolucao",
        detalhe=f"evolucao_id={evolucao.id} ({'assinada' if assinatura_ok else 'nao assinada'})",
    )

    if assinatura_ok:
        flash("Evolução clínica registrada e assinada digitalmente.", "success")
    else:
        flash(
            "Evolução clínica registrada (sem assinatura digital — configure seu certificado em "
            '"Meu certificado digital", no menu Médico, para assinar automaticamente as próximas).',
            "success",
        )
    return redirect(url_for("medico.atendimento", agendamento_id=agendamento.id))


# ---------- Resultado de exame (upload de PDF) ----------

def _pasta_resultados():
    pasta = os.path.join(current_app.instance_path, PASTA_RESULTADOS)
    os.makedirs(pasta, exist_ok=True)
    return pasta


@medico_bp.route("/agenda/<int:agendamento_id>/resultado", methods=["GET", "POST"])
@login_required
@staff_required
def resultado_upload(agendamento_id):
    query = Agendamento.query.filter(
        Agendamento.id == agendamento_id,
        Agendamento.clinica_id.in_(filiais_atuais_ids()),
    )
    if eh_medico():
        query = query.filter(Agendamento.medico_id == current_user.id)
    agendamento = query.first_or_404()

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


# ---------- Achados do procedimento gastro ----------

@medico_bp.route("/agenda/<int:agendamento_id>/procedimento-gastro", methods=["GET", "POST"])
@login_required
@staff_required
def procedimento_gastro_salvar(agendamento_id):
    """Registra os achados de um procedimento gastroenterológico (colonoscopia,
    endoscopia, etc.) — qualidade de preparo, sedação, achados, pólipos,
    complicações, tempo de duração."""
    query = Agendamento.query.filter(
        Agendamento.id == agendamento_id,
        Agendamento.clinica_id.in_(filiais_atuais_ids()),
    )
    if eh_medico():
        query = query.filter(Agendamento.medico_id == current_user.id)
    agendamento = query.first_or_404()

    procedimento = agendamento.procedimento_gastro

    if request.method == "POST":
        def _int_ou_none(campo):
            valor = request.form.get(campo, "").strip()
            if not valor:
                return None
            try:
                return int(valor)
            except ValueError:
                return None

        # Dados principais
        qualidade_preparo = request.form.get("qualidade_preparo", "").strip() or None
        sedacao_realizada = request.form.get("sedacao_realizada") == "true"
        sedacao_tipo = request.form.get("sedacao_tipo", "").strip() or None
        sedacao_dose = request.form.get("sedacao_dose", "").strip() or None
        achados_texto = request.form.get("achados_texto", "").strip() or None
        numero_polipos = _int_ou_none("numero_polipos") or 0
        polipos_removidos = _int_ou_none("polipos_removidos") or 0
        biopsias_coletadas = _int_ou_none("biopsias_coletadas") or 0
        resseccao_endoscopica = request.form.get("resseccao_endoscopica") == "true"
        hemorragia_controlada = request.form.get("hemorragia_controlada") == "true"
        complicacoes = request.form.get("complicacoes", "").strip() or None
        tempo_procedimento_minutos = _int_ou_none("tempo_procedimento_minutos")
        observacoes = request.form.get("observacoes", "").strip() or None

        if not procedimento:
            procedimento = ProcedimentoGastro(
                agendamento_id=agendamento.id,
                paciente_id=agendamento.paciente_id,
                medico_id=current_user.id,
            )
            db.session.add(procedimento)

        # Remove pólipos antigos se for uma atualização
        if procedimento.id:
            for polipo in procedimento.polipos:
                db.session.delete(polipo)

        procedimento.qualidade_preparo = qualidade_preparo
        procedimento.sedacao_realizada = sedacao_realizada
        procedimento.sedacao_tipo = sedacao_tipo if sedacao_realizada else None
        procedimento.sedacao_dose = sedacao_dose if sedacao_realizada else None
        procedimento.achados_texto = achados_texto
        procedimento.numero_polipos = numero_polipos
        procedimento.polipos_removidos = polipos_removidos
        procedimento.biopsias_coletadas = biopsias_coletadas
        procedimento.resseccao_endoscopica = resseccao_endoscopica
        procedimento.hemorragia_controlada = hemorragia_controlada
        procedimento.complicacoes = complicacoes
        procedimento.tempo_procedimento_minutos = tempo_procedimento_minutos
        procedimento.observacoes = observacoes

        # Processa os pólipos (arrays do form)
        localizacoes = request.form.getlist("polipo_localizacao[]")
        tamanhos = request.form.getlist("polipo_tamanho[]")
        paris = request.form.getlist("polipo_paris[]")
        acoes = request.form.getlist("polipo_acoes[]")
        histopatologias = request.form.getlist("polipo_histopatologia[]")
        obs_polipos = request.form.getlist("polipo_obs[]")

        for i, loc in enumerate(localizacoes):
            if loc:  # Só cria polipo se houver localização preenchida
                polipo = ProcedimentoPolipo(
                    localizacao=loc,
                    tamanho_mm=int(tamanhos[i]) if i < len(tamanhos) and tamanhos[i] else None,
                    classificacao_paris=paris[i] if i < len(paris) and paris[i] else None,
                    acoes=acoes[i] if i < len(acoes) and acoes[i] else None,
                    histopatologia=histopatologias[i] if i < len(histopatologias) and histopatologias[i] else None,
                    observacoes=obs_polipos[i] if i < len(obs_polipos) and obs_polipos[i] else None,
                )
                procedimento.polipos.append(polipo)

        db.session.commit()
        flash("Achados do procedimento registrados com sucesso.", "success")
        return redirect(url_for("medico.atendimento", agendamento_id=agendamento.id))

    # GET: renderiza o form com dados existentes (se houver)
    return render_template(
        "medico/procedimento_gastro.html",
        agendamento=agendamento,
        procedimento=procedimento,
    )


# ---------- Pagamento e descontos ----------

@medico_bp.route("/financeiro/receber-pagamento", methods=["GET"])
@login_required
@staff_required
def financeiro_receber_pagamento():
    """Lista os agendamentos que ainda não têm pagamento registrado, pra
    quem cuida do financeiro dar baixa sem precisar navegar pela agenda —
    ver medico.pagamento_registrar para o registro em si."""
    pendentes = (
        Agendamento.query
        .outerjoin(Pagamento, Pagamento.agendamento_id == Agendamento.id)
        .filter(
            Agendamento.clinica_id.in_(filiais_atuais_ids()),
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
    filiais = filiais_atuais()

    if request.method == "POST":
        filial = _filial_do_form(filiais)
        nome = request.form.get("nome", "").strip()
        percentual = _parse_valor_decimal(request.form.get("percentual", ""))
        if not filial:
            flash("Escolha a filial do desconto.", "danger")
        elif not nome or percentual is None:
            flash("Informe o nome e o percentual do desconto.", "danger")
        else:
            db.session.add(DescontoConfig(clinica_id=filial.id, nome=nome, percentual=percentual, ativo=True))
            db.session.commit()
            flash("Desconto cadastrado.", "success")
        return redirect(url_for("medico.descontos_lista"))

    descontos = (
        DescontoConfig.query.filter(DescontoConfig.clinica_id.in_([f.id for f in filiais]))
        .order_by(DescontoConfig.nome).all()
    )
    return render_template("medico/descontos_lista.html", descontos=descontos)


@medico_bp.route("/descontos/<int:desconto_id>/alternar", methods=["POST"])
@login_required
@staff_required
@permissao_required("perm_dados_clinica")
def descontos_alternar(desconto_id):
    desconto = DescontoConfig.query.filter(
        DescontoConfig.id == desconto_id, DescontoConfig.clinica_id.in_(filiais_atuais_ids())
    ).first_or_404()
    desconto.ativo = not desconto.ativo
    db.session.commit()
    return redirect(url_for("medico.descontos_lista"))


@medico_bp.route("/agenda/<int:agendamento_id>/pagamento", methods=["GET", "POST"])
@login_required
@staff_required
def pagamento_registrar(agendamento_id):
    query = Agendamento.query.filter(
        Agendamento.id == agendamento_id,
        Agendamento.clinica_id.in_(filiais_atuais_ids()),
    )
    if eh_medico():
        query = query.filter(Agendamento.medico_id == current_user.id)
    agendamento = query.first_or_404()
    # Descontos são configurados por filial — valem os da filial do
    # agendamento que está sendo pago.
    descontos = (
        DescontoConfig.query.filter_by(clinica_id=agendamento.clinica_id, ativo=True)
        .order_by(DescontoConfig.nome).all()
    )

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
    query = Agendamento.query.filter(
        Agendamento.id == agendamento_id,
        Agendamento.clinica_id.in_(filiais_atuais_ids()),
    )
    if eh_medico():
        query = query.filter(Agendamento.medico_id == current_user.id)
    agendamento = query.first_or_404()
    if not agendamento.pagamento:
        flash("Nenhum pagamento registrado para este agendamento ainda.", "danger")
        return redirect(url_for("medico.pagamento_registrar", agendamento_id=agendamento.id))
    return render_template(
        "medico/pagamento_comprovante.html", agendamento=agendamento, clinica=agendamento.clinica
    )


@medico_bp.route("/agenda/<int:agendamento_id>/pagamento/emitir-nfse", methods=["POST"])
@login_required
@staff_required
def pagamento_emitir_nfse(agendamento_id):
    """Emite a NFS-e do pagamento já registrado (ver app/nfse_nacional.py
    para o fluxo completo: monta o DPS, assina com o certificado da
    clínica e tenta enviar ao Ambiente de Dados Nacional). Em modo
    simulação, nada é assinado nem enviado — só marca a nota como
    simulada, sem valor fiscal, pra testar o fluxo de tela."""
    query = Agendamento.query.filter(
        Agendamento.id == agendamento_id,
        Agendamento.clinica_id.in_(filiais_atuais_ids()),
    )
    if eh_medico():
        query = query.filter(Agendamento.medico_id == current_user.id)
    agendamento = query.first_or_404()
    pagamento = agendamento.pagamento
    if not pagamento:
        flash("Registre o pagamento antes de emitir a nota fiscal.", "danger")
        return redirect(url_for("medico.pagamento_registrar", agendamento_id=agendamento.id))

    try:
        resultado = emitir_nfse(agendamento.clinica, agendamento.paciente, agendamento, pagamento)
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
    agendamento.clinica.fiscal_rps_proximo_numero = resultado["numero_dps"]
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


@medico_bp.route("/agenda/<int:agendamento_id>/pagamento/nfse/reenviar", methods=["POST"])
@login_required
@staff_required
def pagamento_nfse_reenviar(agendamento_id):
    """Tenta reenviar ao Ambiente de Dados Nacional uma NFS-e que ficou
    "assinada_pendente_envio" (o envio anterior falhou), sem gerar um
    novo número de DPS — usa o mesmo XML já assinado (ver
    app.nfse_nacional.reenviar_nfse_pendente)."""
    query = Agendamento.query.filter(
        Agendamento.id == agendamento_id,
        Agendamento.clinica_id.in_(filiais_atuais_ids()),
    )
    if eh_medico():
        query = query.filter(Agendamento.medico_id == current_user.id)
    agendamento = query.first_or_404()
    pagamento = agendamento.pagamento
    if not pagamento:
        flash("Registre o pagamento antes de reenviar a nota fiscal.", "danger")
        return redirect(url_for("medico.pagamento_registrar", agendamento_id=agendamento.id))

    try:
        resultado = reenviar_nfse_pendente(agendamento.clinica, pagamento)
    except ErroEmissaoNfse as erro:
        flash(str(erro), "danger")
        return redirect(url_for("medico.pagamento_comprovante", agendamento_id=agendamento.id))
    except Exception as erro:
        flash(f"Erro inesperado ao reenviar a NFS-e: {erro}", "danger")
        return redirect(url_for("medico.pagamento_comprovante", agendamento_id=agendamento.id))

    pagamento.nfse_status = resultado["status"]
    pagamento.nfse_numero = resultado.get("numero_nfse")
    pagamento.nfse_codigo_verificacao = resultado.get("codigo_verificacao")
    pagamento.nfse_xml_assinado = resultado.get("xml_assinado")
    pagamento.nfse_erro = resultado.get("erro")
    pagamento.nfse_emitida_em = datetime.utcnow()
    db.session.commit()

    if resultado["status"] == "enviada":
        flash("Reenvio concluído — NFS-e transmitida ao Ambiente de Dados Nacional com sucesso.", "success")
    else:
        flash(
            "Reenvio ainda não confirmado (" + (resultado.get("erro") or "motivo desconhecido") +
            "). Pode tentar novamente mais tarde, ou usar o PDF de contingência enquanto isso.",
            "warning",
        )
    return redirect(url_for("medico.pagamento_comprovante", agendamento_id=agendamento.id))


@medico_bp.route("/agenda/<int:agendamento_id>/pagamento/nfse/contingencia.pdf")
@login_required
@staff_required
def pagamento_nfse_contingencia_pdf(agendamento_id):
    """Gera na hora um PDF provisório (não é o DANFSe oficial — ver aviso
    em app.nfse_nacional.gerar_pdf_contingencia) para a clínica entregar
    ao paciente enquanto a NFS-e definitiva não é transmitida com
    sucesso ao Ambiente de Dados Nacional."""
    query = Agendamento.query.filter(
        Agendamento.id == agendamento_id,
        Agendamento.clinica_id.in_(filiais_atuais_ids()),
    )
    if eh_medico():
        query = query.filter(Agendamento.medico_id == current_user.id)
    agendamento = query.first_or_404()
    pagamento = agendamento.pagamento
    if not pagamento or pagamento.nfse_status != "assinada_pendente_envio":
        flash("Só é possível gerar o comprovante de contingência quando a NFS-e está assinada, mas pendente de envio.", "danger")
        return redirect(url_for("medico.pagamento_comprovante", agendamento_id=agendamento.id))

    pdf_buffer = gerar_pdf_contingencia(agendamento.clinica, agendamento.paciente, agendamento, pagamento)
    return send_file(
        pdf_buffer,
        mimetype="application/pdf",
        as_attachment=False,
        download_name=f"nfse-contingencia-agendamento-{agendamento.id}.pdf",
    )


# ---------- Perguntas pendentes (aprendizado da "IA") ----------

@medico_bp.route("/perguntas")
@login_required
@staff_required
def perguntas_pendentes():
    filial_ids = filiais_atuais_ids()
    pendentes_q = PerguntaPendente.query.filter(
        PerguntaPendente.clinica_id.in_(filial_ids), PerguntaPendente.status == "pendente"
    )
    # Respostas que a IA já rascunhou e estão esperando o médico revisar,
    # editar se precisar, e aprovar antes de irem para o paciente.
    aguardando_q = PerguntaPendente.query.filter(
        PerguntaPendente.clinica_id.in_(filial_ids), PerguntaPendente.status == "aguardando_aprovacao"
    )
    respondidas_q = PerguntaPendente.query.filter(
        PerguntaPendente.clinica_id.in_(filial_ids), PerguntaPendente.status == "respondida"
    )

    if eh_medico():
        # O médico só acompanha perguntas sobre exames de sua
        # responsabilidade (mais as gerais, sem exame, se também administrar
        # pacientes) - vale mesmo pra quem tem perm_pacientes (ex.: médico
        # fundador de uma clínica com outros médicos na equipe): ter essa
        # permissão não deve fazer um médico ver perguntas sobre exames de
        # OUTRO médico. Ver _restringir_perguntas_para_medico.
        pendentes_q = _restringir_perguntas_para_medico(pendentes_q)
        aguardando_q = _restringir_perguntas_para_medico(aguardando_q)
        respondidas_q = _restringir_perguntas_para_medico(respondidas_q)

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
    pergunta = PerguntaPendente.query.filter(
        PerguntaPendente.id == pergunta_id, PerguntaPendente.clinica_id.in_(filiais_atuais_ids())
    ).first_or_404()

    # Mesma regra de quem PODE VER (ver _restringir_perguntas_para_medico):
    # um médico só responde perguntas dos seus próprios exames, mais as
    # gerais (sem exame) se também administrar pacientes - mesmo tendo
    # perm_pacientes, não pode responder pergunta de exame de outro médico.
    if eh_medico():
        exame_proprio = pergunta.exame is not None and pergunta.exame.medico_pode_atender(current_user.id)
        geral_administravel = pergunta.exame is None and current_user.perm_pacientes
        if not exame_proprio and not geral_administravel:
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
        # O item de FAQ nasce na MESMA filial da pergunta respondida.
        clinica_id=pergunta.clinica_id,
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
    query = FaqItem.query.filter(FaqItem.clinica_id.in_(filiais_atuais_ids()))
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
    filiais = filiais_atuais()
    exames_query = Exame.query.filter(Exame.clinica_id.in_([f.id for f in filiais]))
    if eh_medico():
        exames_query = exames_query.filter(
            or_(Exame.medico_id == current_user.id, Exame.medicos_extra.any(id=current_user.id))
        )
    exames = exames_query.order_by(Exame.nome).all()

    if request.method == "POST":
        exame_id = request.form.get("exame_id", type=int)
        exame_escolhido = next((e for e in exames if e.id == exame_id), None)
        # Item vinculado a um exame nasce na filial DESSE exame; item geral
        # (sem exame) usa a filial escolhida no formulário.
        filial = exame_escolhido.clinica if exame_escolhido else _filial_do_form(filiais)

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

        if not filial:
            flash("Escolha a filial em que este item será cadastrado.", "danger")
            return render_template("medico/faq_form.html", exames=exames)

        item = FaqItem(
            clinica_id=filial.id,
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


# ---------- Dados Cadastrais (gerais, endereço, fiscais) ----------

@medico_bp.route("/clinica/configuracoes", methods=["GET", "POST"])
@medico_bp.route("/clinica/configuracoes/<int:filial_id>", methods=["GET", "POST"])
@login_required
@staff_required
@permissao_required("perm_dados_clinica")
def clinica_configuracoes(filial_id=None):
    empresa = empresa_atual()
    if filial_id:
        # Permite editar qualquer filial da mesma empresa — usado a partir da
        # tela "Meus locais de atendimento".
        clinica = Clinica.query.filter_by(id=filial_id, empresa_id=empresa.id).first_or_404()
    else:
        clinica = clinica_atual()

    if not clinica:
        # Empresa recém-criada, ainda sem nenhum local de atendimento
        # cadastrado (isso agora é feito depois do cadastro público, ao
        # entrar no app - não mais automaticamente no cadastro).
        flash("Cadastre seu primeiro local de atendimento antes de preencher os dados dele.", "info")
        return redirect(url_for("medico.filiais_lista"))

    if request.method == "POST":
        # Dados gerais
        nome = request.form.get("nome", "").strip()
        if nome:
            clinica.nome = nome
        clinica.razao_social = request.form.get("razao_social", "").strip()
        clinica.cnpj = request.form.get("cnpj", "").strip()
        telefone = request.form.get("telefone", "").strip()
        if telefone_incompleto(telefone):
            flash("Telefone incompleto — digite o DDD e o número completos.", "danger")
            return redirect(url_for("medico.clinica_configuracoes", filial_id=filial_id))
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
        # O código IBGE do município continua sendo preenchido aqui (a
        # partir da busca automática do CEP, feita nesta mesma tela) mesmo
        # com os demais campos fiscais tendo se mudado para a tela "Dados
        # Fiscais" — ele é derivado do endereço, não é digitado à mão.
        clinica.codigo_ibge_municipio = request.form.get("codigo_ibge_municipio", "").strip()

        db.session.commit()
        flash(f"Dados Cadastrais de '{clinica.nome}' atualizados com sucesso.", "success")
        # Salvou -> FECHA a tela, voltando pra lista de locais (ou pro
        # assistente, se veio de lá) - em vez de continuar no formulário.
        return _destino_pos_onboarding("medico.filiais_lista")

    return render_template(
        "medico/clinica_configuracoes.html",
        clinica=clinica,
    )


# ---------- Dados Fiscais (inscrição estadual/CNAE/regime + emissão de NFS-e) ----------

@medico_bp.route("/clinica/dados-fiscais", methods=["GET", "POST"])
@medico_bp.route("/clinica/dados-fiscais/<int:filial_id>", methods=["GET", "POST"])
@login_required
@staff_required
@permissao_required("perm_dados_clinica")
def clinica_dados_fiscais(filial_id=None):
    """Tela própria (fora de "Dados Cadastrais") para os dados usados numa
    eventual nota fiscal: inscrição estadual, regime tributário, CNAE e a
    configuração de emissão de NFS-e. O código IBGE do município continua
    sendo mostrado aqui (só leitura), mas é preenchido em "Dados Cadastrais"
    a partir do CEP do endereço — não é um dado fiscal digitado à mão."""
    empresa = empresa_atual()
    if filial_id:
        clinica = Clinica.query.filter_by(id=filial_id, empresa_id=empresa.id).first_or_404()
    else:
        clinica = clinica_atual()

    if not clinica:
        flash("Cadastre seu primeiro local de atendimento antes de preencher os dados fiscais dele.", "info")
        return redirect(url_for("medico.filiais_lista"))

    if request.method == "POST":
        clinica.inscricao_estadual = request.form.get("inscricao_estadual", "").strip()
        clinica.regime_tributario = request.form.get("regime_tributario", "").strip()
        clinica.cnae = request.form.get("cnae", "").strip()

        db.session.commit()
        flash(f"Dados Fiscais de '{clinica.nome}' atualizados com sucesso.", "success")
        # Mesmo comportamento dos Dados Cadastrais: salvou -> fecha a
        # tela, voltando pra lista de locais.
        return _destino_pos_onboarding("medico.filiais_lista")

    return render_template(
        "medico/clinica_dados_fiscais.html",
        clinica=clinica,
    )


@medico_bp.route("/clinica/codigo-cadastro-paciente/regenerar", methods=["POST"])
@medico_bp.route("/clinica/codigo-cadastro-paciente/regenerar/<int:filial_id>", methods=["POST"])
@login_required
@staff_required
@permissao_required("perm_dados_clinica")
def clinica_codigo_cadastro_regenerar(filial_id=None):
    """Gera um novo código de auto-cadastro DA EMPRESA, invalidando o link
    antigo — útil se o link foi compartilhado por engano com quem não
    deveria ter acesso. (A rota manteve o nome antigo por compatibilidade,
    mas o código é da empresa - o paciente é da empresa, e o link fica no
    Painel. Também zera os códigos legados por filial, senão os links
    antigos continuariam funcionando e "gerar novo link" não invalidaria
    nada de verdade.)"""
    empresa = empresa_atual()
    empresa.codigo_cadastro_paciente = _gerar_codigo_cadastro_paciente()
    for filial in Clinica.query.filter_by(empresa_id=empresa.id).all():
        filial.codigo_cadastro_paciente = None
    db.session.commit()
    flash("Novo link de cadastro gerado — o link antigo não funciona mais.", "success")
    return redirect(url_for("medico.dashboard"))


@medico_bp.route("/clinica/emissao-fiscal", methods=["POST"])
@medico_bp.route("/clinica/emissao-fiscal/<int:filial_id>", methods=["POST"])
@login_required
@staff_required
@permissao_required("perm_dados_clinica")
def clinica_emissao_fiscal(filial_id=None):
    """Salva a configuração de emissão de NFS-e (ambiente, provedor,
    inscrição municipal, código de serviço, alíquota de ISS, série/número
    do RPS) — separado do formulário de "Dados fiscais" (inscrição
    estadual/regime/CNAE) porque fica em outro <form> na mesma página (ver
    medico/clinica_dados_fiscais.html). O upload do certificado digital em
    si tem sua própria rota, `clinica_certificado_upload`, abaixo."""
    empresa = empresa_atual()
    if filial_id:
        clinica = Clinica.query.filter_by(id=filial_id, empresa_id=empresa.id).first_or_404()
    else:
        clinica = clinica_atual()

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
    return _destino_pos_onboarding("medico.clinica_dados_fiscais", filial_id=clinica.id)


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
    empresa = empresa_atual()
    if filial_id:
        clinica = Clinica.query.filter_by(id=filial_id, empresa_id=empresa.id).first_or_404()
    else:
        clinica = clinica_atual()

    arquivo = request.files.get("certificado_arquivo")
    senha = request.form.get("certificado_senha", "")

    if not arquivo or not arquivo.filename:
        flash("Selecione o arquivo do certificado (.pfx) antes de enviar.", "danger")
        return _destino_pos_onboarding("medico.clinica_dados_fiscais", filial_id=clinica.id)

    if not senha:
        flash("Informe a senha do certificado.", "danger")
        return _destino_pos_onboarding("medico.clinica_dados_fiscais", filial_id=clinica.id)

    conteudo = arquivo.read()
    # Um certificado .pfx/.p12 normal tem poucos KB — um arquivo muito
    # maior do que isso quase certamente não é um certificado válido, então
    # rejeitamos antes mesmo de tentar abrir (evita gastar memória com um
    # upload indevido).
    if len(conteudo) > 5 * 1024 * 1024:
        flash("Arquivo muito grande para ser um certificado válido.", "danger")
        return _destino_pos_onboarding("medico.clinica_dados_fiscais", filial_id=clinica.id)

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
        return _destino_pos_onboarding("medico.clinica_dados_fiscais", filial_id=clinica.id)

    if certificado is None:
        flash("O arquivo enviado não contém um certificado válido.", "danger")
        return _destino_pos_onboarding("medico.clinica_dados_fiscais", filial_id=clinica.id)

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
    return _destino_pos_onboarding("medico.clinica_dados_fiscais", filial_id=clinica.id)


# ---------- Filiais da empresa ----------

@medico_bp.route("/filiais")
@login_required
@staff_required
def filiais_lista():
    """Tela "Meus locais de atendimento" - além de listar/trocar de filial
    (perm_filiais), também é a porta de entrada para editar os dados de
    cada filial (Dados Cadastrais e Dados Fiscais, que não têm mais item
    próprio no menu lateral), então continua acessível a quem só tem
    perm_dados_clinica, mesmo sem perm_filiais."""
    if not (current_user.perm_filiais or current_user.perm_dados_clinica):
        flash(
            "Você não tem permissão para acessar essa área. Fale com "
            "quem administra sua clínica.",
            "danger",
        )
        return redirect(url_for("medico.dashboard"))

    empresa = empresa_atual()
    filiais = Clinica.query.filter_by(empresa_id=empresa.id).order_by(Clinica.nome).all()
    # Não existe mais "local atual": os dados de todos os locais em que a
    # pessoa atua aparecem juntos. Aqui só marcamos quais são os locais dela.
    meus_ids = set(filiais_atuais_ids())
    return render_template(
        "medico/filiais_lista.html", filiais=filiais, empresa=empresa, meus_filiais_ids=meus_ids
    )


@medico_bp.route("/filiais/<int:filial_id>/vincular-me", methods=["POST"])
@login_required
@staff_required
def filiais_vincular_me(filial_id):
    """Ação explícita de "eu também atuo neste local" - cadastrar um local
    (medico.filiais_nova) não vincula ninguém a ele, então este botão é o
    caminho para a PRÓPRIA pessoa logada se vincular deliberadamente. Para
    vincular outra pessoa, o caminho é a tela "Equipe" (medico.equipe_novo /
    medico.equipe_associar_filial)."""
    empresa = empresa_atual()
    filial = Clinica.query.filter_by(id=filial_id, empresa_id=empresa.id).first_or_404()

    vinculo = ClinicaMembro.query.filter_by(clinica_id=filial.id, usuario_id=current_user.id).first()
    if vinculo and vinculo.ativo:
        flash("Você já está vinculado(a) a este local.", "warning")
        return redirect(url_for("medico.filiais_lista"))

    if vinculo:
        vinculo.reativar()  # já atuou aqui antes: reativa o histórico
    else:
        db.session.add(ClinicaMembro(clinica_id=filial.id, usuario_id=current_user.id, ativo=True))
    db.session.commit()
    flash(f"Você agora está vinculado(a) ao local '{filial.nome}'.", "success")
    return redirect(url_for("medico.filiais_lista"))


@medico_bp.route("/filiais/<int:filial_id>/desvincular-me", methods=["POST"])
@login_required
@staff_required
def filiais_desvincular_me(filial_id):
    """Ação inversa de filiais_vincular_me: remove o vínculo da PRÓPRIA
    pessoa logada com o local - para desfazer um vínculo que não deveria
    existir (ex.: criado automaticamente por versões antigas do cadastro
    de local, antes de o vínculo virar uma ação explícita e separada).

    Guarda importante: quem NÃO é fundador(a) da empresa
    (Usuario.empresa_fundadora_id) resolve seu acesso ao sistema
    exclusivamente pelos vínculos de filial (ver empresas_do_usuario em
    app/clinica_utils.py) - remover o último vínculo dessa pessoa a
    deixaria trancada fora da própria conta, então nesse caso bloqueamos
    e orientamos a usar a tela "Equipe" (onde outra pessoa com permissão
    gerencia os vínculos dela)."""
    empresa = empresa_atual()
    filial = Clinica.query.filter_by(id=filial_id, empresa_id=empresa.id).first_or_404()

    vinculo = ClinicaMembro.query.filter_by(
        clinica_id=filial.id, usuario_id=current_user.id, ativo=True
    ).first()
    if not vinculo:
        flash("Você já não está vinculado(a) a este local.", "warning")
        return redirect(url_for("medico.filiais_lista"))

    total_vinculos = ClinicaMembro.query.filter_by(usuario_id=current_user.id, ativo=True).count()
    if total_vinculos <= 1 and not getattr(current_user, "empresa_fundadora_id", None):
        flash(
            "Este é seu único vínculo de local — removê-lo deixaria sua conta sem acesso a nenhuma "
            "clínica. Peça a quem administra a equipe para ajustar seus vínculos na tela \"Equipe\".",
            "danger",
        )
        return redirect(url_for("medico.filiais_lista"))

    futuros = _agendamentos_futuros_do_vinculo(filial.id, current_user.id)
    if futuros:
        flash(
            f"Você ainda tem {futuros} agendamento(s) futuro(s) neste local. "
            "Cancele ou transfira esses agendamentos antes de se desvincular.",
            "danger",
        )
        return redirect(url_for("medico.filiais_lista"))

    # ENCERRA o vínculo (não apaga): o histórico de atuação e os
    # agendamentos já realizados ali continuam intactos - ver
    # ClinicaMembro.encerrado_em em app/models.py.
    vinculo.encerrar()
    db.session.commit()
    flash(f"Pronto — você não está mais marcado(a) como atuando no local '{filial.nome}'.", "success")
    return redirect(url_for("medico.filiais_lista"))


@medico_bp.route("/filiais/nova", methods=["GET", "POST"])
@login_required
@staff_required
@permissao_required("perm_filiais")
def filiais_nova():
    empresa = empresa_atual()

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        if not nome:
            flash("Informe o nome do novo local de atendimento.", "danger")
            return render_template("medico/filiais_form.html")

        if Clinica.query.filter_by(empresa_id=empresa.id, nome=nome).first():
            flash("Já existe um local de atendimento com esse nome nesta empresa.", "danger")
            return render_template("medico/filiais_form.html")

        nova_filial = Clinica(empresa_id=empresa.id, nome=nome)
        db.session.add(nova_filial)
        db.session.commit()

        # Cadastrar a estrutura de um local (nome, endereço, dados fiscais)
        # NÃO vincula ninguém a ele - nem quem cadastrou. "Quem atua em qual
        # local" é sempre uma associação separada, deliberada e explícita
        # (mesmo princípio de "não existe médico principal" por trás de
        # Exame.medico_confirmado e do cadastro público sem filial): pelo
        # botão "Marcar que você também atua aqui" em "Meus Locais de
        # Atendimento" (medico.filiais_vincular_me) para a própria pessoa,
        # ou pela tela "Equipe" para qualquer pessoa. Nenhum vínculo nasce
        # como efeito colateral de outro cadastro.
        flash(
            f"Local '{nova_filial.nome}' cadastrado com sucesso. Isso ainda não vincula ninguém a ele: "
            "se VOCÊ for atuar neste local, use o botão \"Marcar que você também atua aqui\" na lista "
            "abaixo; para vincular outras pessoas, use a tela \"Equipe\".",
            "success",
        )
        return _destino_pos_onboarding("medico.filiais_lista")

    return render_template("medico/filiais_form.html")


# ---------- Equipe (médicos e secretárias da clínica atual) ----------

@medico_bp.route("/equipe-membros")
@login_required
@staff_required
@permissao_required("perm_equipe")
def equipe_lista():
    """Mostra uma linha por PESSOA (não por vínculo) - uma pessoa que atua
    em mais de uma filial da empresa continua sendo uma única conta
    (Usuario), só com mais de um vínculo (ClinicaMembro); antes a tela
    repetia a linha inteira por filial, o que parecia (incorretamente) um
    cadastro duplicado."""
    empresa = empresa_atual()
    filiais = Clinica.query.filter_by(empresa_id=empresa.id).order_by(Clinica.nome).all()
    filial_ids = [f.id for f in filiais]
    membros = (
        ClinicaMembro.query.filter(
            ClinicaMembro.clinica_id.in_(filial_ids), ClinicaMembro.ativo.is_(True)
        )
        .order_by(ClinicaMembro.clinica_id)
        .all()
    )

    pessoas = {}
    for m in membros:
        pessoa = pessoas.setdefault(m.usuario_id, {"usuario": m.usuario, "vinculos": []})
        pessoa["vinculos"].append(m)

    # Quem FUNDOU a empresa faz parte da equipe desde o cadastro público,
    # mesmo sem vínculo com local nenhum (cadastrar local não vincula
    # ninguém automaticamente) - sem isso, o fundador não aparecia em
    # lugar nenhum da tela de Equipe.
    for u in Usuario.query.filter_by(empresa_fundadora_id=empresa.id).all():
        if u.is_staff and u.ativo:
            pessoas.setdefault(u.id, {"usuario": u, "vinculos": []})

    pessoas = sorted(pessoas.values(), key=lambda p: p["usuario"].nome)

    # Para cada pessoa, quais filiais da empresa ela ainda NÃO integra —
    # usado pelo pequeno formulário "+ Associar a outra filial" da tela,
    # que vincula direto sem precisar passar pelo formulário completo de
    # "Adicionar médico/secretária" de novo.
    for pessoa in pessoas:
        ja_vinculadas_ids = {v.clinica_id for v in pessoa["vinculos"]}
        pessoa["filiais_disponiveis"] = [f for f in filiais if f.id not in ja_vinculadas_ids]

    # Convites de vínculo por código mestre ainda aguardando o médico
    # decidir (ver equipe_vincular_por_codigo) - mostrados na tela pra
    # equipe acompanhar (e poder cancelar).
    convites_enviados = (
        ConviteVinculo.query.filter(
            ConviteVinculo.clinica_id.in_(filial_ids), ConviteVinculo.status == "pendente"
        )
        .order_by(ConviteVinculo.criado_em.asc())
        .all()
    )

    return render_template(
        "medico/equipe_lista.html", pessoas=pessoas, filiais=filiais,
        convites_enviados=convites_enviados,
    )


@medico_bp.route("/equipe-membros/novo", methods=["GET", "POST"])
@login_required
@staff_required
@permissao_required("perm_equipe")
def equipe_novo():
    empresa = empresa_atual()
    filiais = Clinica.query.filter_by(empresa_id=empresa.id).order_by(Clinica.nome).all()

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        email = request.form.get("email", "").strip().lower()
        # A clínica NÃO cria médico: médico é uma entidade própria da
        # plataforma - ele cria a própria conta (cadastro público) e a
        # clínica o traz pra equipe SÓ pelo código mestre dele, com o
        # aceite dele (ver equipe_vincular_por_codigo/convite_decidir).
        # Este formulário cadastra apenas secretárias.
        papel = "secretaria"
        if request.form.get("papel") == "medico":
            flash(
                "Médico não é cadastrado pela clínica: ele cria a própria conta na plataforma e "
                "você o traz pra equipe pelo código dele, em \"Vincular médico por código\" - o "
                "vínculo vale depois que ele aceitar o convite.",
                "danger",
            )
            return redirect(url_for("medico.equipe_lista"))
        senha = request.form.get("senha", "").strip()
        # Uma pessoa pode atuar em mais de uma filial da empresa desde já,
        # marcando todas de uma vez aqui — antes só dava pra escolher uma,
        # e para as demais era preciso cadastrar a mesma pessoa de novo do
        # zero (ver medico.equipe_associar_filial para o atalho equivalente
        # feito depois, direto na tela "Equipe").
        filial_ids_selecionadas = request.form.getlist("filial_ids", type=int)
        filiais_selecionadas = [f for f in filiais if f.id in filial_ids_selecionadas]
        if not filiais_selecionadas:
            flash("Escolha em qual(is) filial(is) essa pessoa vai atuar.", "danger")
            return render_template("medico/equipe_form.html", filiais=filiais)

        if not email:
            flash("Preencha o e-mail da secretária.", "danger")
            return render_template("medico/equipe_form.html", filiais=filiais)

        usuario_existente = Usuario.query.filter_by(email=email).first()

        if usuario_existente and usuario_existente.tipo == "medico":
            # Vincular um médico existente também passa pelo código dele
            # (e pelo aceite) - não por e-mail digitado pela clínica.
            flash(
                "Esse e-mail é de uma conta de MÉDICO. Peça o código de médico a ele e use "
                "\"Vincular médico por código\" - o vínculo vale depois que ele aceitar o convite.",
                "danger",
            )
            return redirect(url_for("medico.equipe_lista"))

        if usuario_existente:
            # A conta já existe na plataforma (pode ser de outra clínica) —
            # só criamos os vínculos dela com as filiais marcadas, sem
            # duplicar a conta.
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

            vinculos_existentes = {
                m.clinica_id: m
                for m in ClinicaMembro.query.filter_by(usuario_id=usuario_existente.id).all()
            }
            filiais_novas = [
                f for f in filiais_selecionadas
                if f.id not in vinculos_existentes or not vinculos_existentes[f.id].ativo
            ]
            if not filiais_novas:
                flash("Esse usuário já faz parte de todas as filiais marcadas.", "warning")
                return _destino_pos_onboarding("medico.equipe_lista")

            for f in filiais_novas:
                vinculo_antigo = vinculos_existentes.get(f.id)
                if vinculo_antigo:
                    vinculo_antigo.reativar()  # já atuou aqui: reativa o histórico
                else:
                    db.session.add(ClinicaMembro(clinica_id=f.id, usuario_id=usuario_existente.id, ativo=True))
            db.session.commit()
            nomes_filiais = ", ".join(f.nome for f in filiais_novas)
            flash(f"{usuario_existente.nome} foi vinculado(a) à(s) filial(is) '{nomes_filiais}'.", "success")
            return _destino_pos_onboarding("medico.equipe_lista")

        # Conta nova
        if not nome:
            flash("Informe o nome da pessoa.", "danger")
            return render_template("medico/equipe_form.html", filiais=filiais)

        cpf = request.form.get("cpf", "").strip()
        if cpf and not validar_cpf(cpf):
            flash("CPF inválido — confira os números digitados.", "danger")
            return render_template("medico/equipe_form.html", filiais=filiais)
        if cep_incompleto(request.form.get("cep", "")):
            flash("CEP incompleto — digite os 8 números.", "danger")
            return render_template("medico/equipe_form.html", filiais=filiais)

        senha_final = senha or "123456"
        usuario = Usuario(nome=nome, email=email, tipo=papel, cpf=cpf or None)
        usuario.set_senha(senha_final)
        usuario.cep = request.form.get("cep", "").strip()
        usuario.rua = request.form.get("rua", "").strip()
        usuario.numero = request.form.get("numero", "").strip()
        usuario.complemento = request.form.get("complemento", "").strip()
        usuario.bairro = request.form.get("bairro", "").strip()
        usuario.cidade = request.form.get("cidade", "").strip()
        usuario.uf = request.form.get("uf", "").strip().upper() or None
        # As permissões administrativas (pacientes, equipe, filiais, dados
        # da clínica) vêm dos checkboxes do formulário — nem toda clínica
        # tem secretária, então quem cadastra escolhe explicitamente quais
        # telas administrativas essa pessoa vai poder acessar.
        usuario.perm_pacientes = request.form.get("perm_pacientes") == "on"
        usuario.perm_equipe = request.form.get("perm_equipe") == "on"
        usuario.perm_filiais = request.form.get("perm_filiais") == "on"
        usuario.perm_dados_clinica = request.form.get("perm_dados_clinica") == "on"
        if papel == "medico":
            # Todo médico nasce com seu código mestre (ver
            # Usuario.codigo_mestre) - identidade portátil dele na
            # plataforma, usada por outras clínicas pra convidá-lo.
            usuario.codigo_mestre = gerar_codigo_mestre_medico()
        db.session.add(usuario)
        db.session.flush()

        for f in filiais_selecionadas:
            db.session.add(ClinicaMembro(clinica_id=f.id, usuario_id=usuario.id, ativo=True))
        db.session.commit()

        nomes_filiais = ", ".join(f.nome for f in filiais_selecionadas)
        flash(
            f"{nome} cadastrado(a) como {papel} na(s) filial(is) '{nomes_filiais}'. "
            f"Senha de acesso inicial: {senha_final}",
            "success",
        )
        return _destino_pos_onboarding("medico.equipe_lista")

    return render_template("medico/equipe_form.html", filiais=filiais)


@medico_bp.route("/equipe-membros/<int:usuario_id>/associar-filial", methods=["POST"])
@login_required
@staff_required
@permissao_required("perm_equipe")
def equipe_associar_filial(usuario_id):
    """Vincula uma pessoa que já faz parte da equipe (em pelo menos uma
    filial da empresa) a mais uma filial da mesma empresa - atalho direto
    na tela "Equipe" para o mesmo caso de "e-mail já cadastrado" tratado em
    equipe_novo, sem precisar passar pelo formulário completo de novo."""
    empresa = empresa_atual()
    filiais = Clinica.query.filter_by(empresa_id=empresa.id).all()
    filial_ids = {f.id for f in filiais}

    # A pessoa precisa já fazer parte desta empresa (por vínculo de
    # filial, ou por ser quem fundou a empresa - ver _pessoa_da_empresa).
    # Senão, isso não é "associar a mais uma filial", é criar do zero
    # (fluxo de medico.equipe_novo).
    if not _pessoa_da_empresa(usuario_id, empresa, filial_ids):
        flash("Pessoa não encontrada na equipe desta empresa.", "danger")
        return redirect(url_for("medico.equipe_lista"))
    usuario_alvo = Usuario.query.get(usuario_id)

    filial_destino = next((f for f in filiais if f.id == request.form.get("filial_id", type=int)), None)
    if not filial_destino:
        flash("Escolha uma filial válida.", "danger")
        return redirect(url_for("medico.equipe_lista"))

    vinculo_existente = ClinicaMembro.query.filter_by(
        clinica_id=filial_destino.id, usuario_id=usuario_id
    ).first()
    if vinculo_existente and vinculo_existente.ativo:
        flash("Essa pessoa já faz parte dessa filial.", "warning")
        return redirect(url_for("medico.equipe_lista"))

    if vinculo_existente:
        vinculo_existente.reativar()  # já atuou aqui: reativa o histórico
    else:
        db.session.add(ClinicaMembro(clinica_id=filial_destino.id, usuario_id=usuario_id, ativo=True))
    db.session.commit()
    flash(f"{usuario_alvo.nome} foi vinculado(a) à filial '{filial_destino.nome}'.", "success")
    return redirect(url_for("medico.equipe_lista"))


# ---------- Vincular médico por código mestre (convite + aceite) ----------

@medico_bp.route("/equipe-membros/vincular-por-codigo", methods=["POST"])
@login_required
@staff_required
@permissao_required("perm_equipe")
def equipe_vincular_por_codigo():
    """A clínica digita o código mestre de um médico (ver
    Usuario.codigo_mestre) para convidá-lo a atender nas filiais marcadas.
    NÃO vincula na hora: cria um ConviteVinculo pendente por filial, que o
    médico aceita ou recusa no painel dele (ver convite_decidir) -
    consentimento nas duas pontas."""
    empresa = empresa_atual()
    filiais = Clinica.query.filter_by(empresa_id=empresa.id).order_by(Clinica.nome).all()

    codigo = request.form.get("codigo_mestre", "").strip().upper()
    filial_ids_selecionadas = request.form.getlist("filial_ids", type=int)
    filiais_selecionadas = [f for f in filiais if f.id in filial_ids_selecionadas]

    if not codigo:
        flash("Digite o código do médico (ex.: MED-7K2F9).", "danger")
        return redirect(url_for("medico.equipe_lista"))
    if not filiais_selecionadas:
        flash("Marque em qual(is) filial(is) o médico vai atender.", "danger")
        return redirect(url_for("medico.equipe_lista"))

    medico = Usuario.query.filter_by(codigo_mestre=codigo, tipo="medico").first()
    if not medico or not medico.ativo:
        flash("Nenhum médico encontrado com esse código. Confira com ele o código atual (ele pode ter regenerado).", "danger")
        return redirect(url_for("medico.equipe_lista"))

    convidadas, ja_vinculadas, ja_convidadas = [], [], []
    for f in filiais_selecionadas:
        vinculo = ClinicaMembro.query.filter_by(clinica_id=f.id, usuario_id=medico.id).first()
        if vinculo and vinculo.ativo:
            ja_vinculadas.append(f.nome)
            continue
        if ConviteVinculo.query.filter_by(clinica_id=f.id, medico_id=medico.id, status="pendente").first():
            ja_convidadas.append(f.nome)
            continue
        db.session.add(ConviteVinculo(
            clinica_id=f.id, medico_id=medico.id, criado_por_id=current_user.id,
        ))
        convidadas.append(f.nome)
    db.session.commit()

    if convidadas:
        flash(
            f"Convite enviado para {medico.nome} atender em: {', '.join(convidadas)}. "
            "O vínculo só vale depois que o médico aceitar no painel dele.",
            "success",
        )
    if ja_vinculadas:
        flash(f"{medico.nome} já atende em: {', '.join(ja_vinculadas)}.", "warning")
    if ja_convidadas:
        flash(f"Já existe convite pendente para: {', '.join(ja_convidadas)}.", "warning")
    return redirect(url_for("medico.equipe_lista"))


@medico_bp.route("/equipe-membros/convites/<int:convite_id>/cancelar", methods=["POST"])
@login_required
@staff_required
@permissao_required("perm_equipe")
def equipe_convite_cancelar(convite_id):
    """A clínica desiste de um convite que o médico ainda não decidiu. O
    convite não é apagado (fica como histórico), só marcado 'cancelado'."""
    convite = ConviteVinculo.query.filter(
        ConviteVinculo.id == convite_id,
        ConviteVinculo.clinica_id.in_([f.id for f in _filiais_da_empresa()]),
        ConviteVinculo.status == "pendente",
    ).first_or_404()
    convite.status = "cancelado"
    convite.decidido_em = datetime.utcnow()
    db.session.commit()
    flash(f"Convite para {convite.medico.nome} cancelado.", "success")
    return redirect(url_for("medico.equipe_lista"))


@medico_bp.route("/convites/<int:convite_id>/decidir", methods=["POST"])
@login_required
@staff_required
def convite_decidir(convite_id):
    """O MÉDICO aceita ou recusa um convite de vínculo endereçado a ele
    (mostrado no painel). Aceitar cria o ClinicaMembro (ou reativa um
    vínculo antigo encerrado) - é aqui, e só aqui, que o vínculo nasce."""
    convite = ConviteVinculo.query.filter_by(
        id=convite_id, medico_id=current_user.id, status="pendente"
    ).first_or_404()
    acao = request.form.get("acao")
    convite.decidido_em = datetime.utcnow()
    if acao == "recusar":
        convite.status = "recusado"
        db.session.commit()
        flash(f"Convite de '{convite.clinica.nome}' recusado.", "success")
        return redirect(url_for("medico.dashboard"))

    convite.status = "aceito"
    vinculo = ClinicaMembro.query.filter_by(
        clinica_id=convite.clinica_id, usuario_id=current_user.id
    ).first()
    if vinculo:
        vinculo.ativo = True  # revinculação: reativa o histórico, não duplica
    else:
        db.session.add(ClinicaMembro(
            clinica_id=convite.clinica_id, usuario_id=current_user.id, ativo=True,
        ))
    db.session.commit()
    flash(f"Você agora atende em '{convite.clinica.nome}'.", "success")
    return redirect(url_for("medico.dashboard"))


@medico_bp.route("/meu-codigo/regenerar", methods=["POST"])
@login_required
@staff_required
def medico_codigo_regenerar():
    """O médico troca o próprio código mestre (ex.: se vazou). Convites já
    criados não mudam - só os usos futuros do código antigo param de
    funcionar."""
    if not eh_medico():
        flash("Só contas de médico têm código mestre.", "danger")
        return redirect(url_for("medico.dashboard"))
    current_user.codigo_mestre = gerar_codigo_mestre_medico()
    db.session.commit()
    flash(f"Novo código gerado: {current_user.codigo_mestre}. O código antigo deixou de valer.", "success")
    return redirect(url_for("medico.dashboard"))


@medico_bp.route("/equipe-membros/<int:usuario_id>/editar", methods=["GET", "POST"])
@login_required
@staff_required
@permissao_required("perm_equipe")
def equipe_editar(usuario_id):
    """Tela de edição de uma pessoa da equipe: o nome e, principalmente,
    em quais filiais da empresa ela atua — marcando/desmarcando várias de
    uma vez, ao invés de precisar do atalho "+ Associar a outra filial"
    (uma filial por vez) ou reabrir "Adicionar médico/secretária" com o
    mesmo e-mail para vincular mais filiais. Só mexe nos vínculos desta
    empresa - se a pessoa também atuar em outra empresa (outro médico
    multiempresa), aquele vínculo não aparece nem é afetado aqui. O papel
    (médico/secretária) não é editável nesta tela: trocar o tipo de conta
    tem implicações demais (permissões padrão, agenda, etc.) para ser só
    mais um campo de formulário."""
    empresa = empresa_atual()
    filiais = Clinica.query.filter_by(empresa_id=empresa.id).order_by(Clinica.nome).all()
    filial_ids = {f.id for f in filiais}

    usuario = Usuario.query.filter_by(id=usuario_id).first_or_404()
    # Traz TODOS os vínculos desta empresa, inclusive os encerrados
    # (ativo=False) - re-marcar uma filial de onde a pessoa já saiu REATIVA
    # o vínculo antigo (histórico preservado) em vez de criar outro.
    vinculos_desta_empresa = ClinicaMembro.query.filter(
        ClinicaMembro.usuario_id == usuario_id, ClinicaMembro.clinica_id.in_(filial_ids)
    ).all()
    vinculos_ativos = [v for v in vinculos_desta_empresa if v.ativo]
    eh_fundador_desta_empresa = usuario.empresa_fundadora_id == empresa.id
    if not vinculos_ativos and not eh_fundador_desta_empresa:
        flash("Pessoa não encontrada na equipe desta empresa.", "danger")
        return redirect(url_for("medico.equipe_lista"))

    filial_ids_atuais = {v.clinica_id for v in vinculos_ativos}

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        filial_ids_selecionadas = set(request.form.getlist("filial_ids", type=int)) & filial_ids

        if not nome:
            flash("Informe o nome da pessoa.", "danger")
            return render_template(
                "medico/equipe_editar.html", usuario=usuario, filiais=filiais,
                filial_ids_atuais=filial_ids_atuais,
            )
        if not filial_ids_selecionadas and not eh_fundador_desta_empresa:
            # O fundador pode ficar sem vínculo nenhum (o acesso dele vem
            # de empresa_fundadora_id) - as demais pessoas precisam de
            # pelo menos um vínculo, senão perdem o acesso à empresa.
            flash("Marque pelo menos uma filial em que essa pessoa atua.", "danger")
            return render_template(
                "medico/equipe_editar.html", usuario=usuario, filiais=filiais,
                filial_ids_atuais=filial_ids_atuais,
            )

        # Desmarcar uma filial ENCERRA o vínculo (não apaga - ver
        # ClinicaMembro.encerrado_em); com agendamentos futuros do médico
        # naquela filial, o encerramento é bloqueado até a equipe
        # cancelar/transferir as consultas marcadas.
        for v in vinculos_ativos:
            if v.clinica_id not in filial_ids_selecionadas:
                futuros = _agendamentos_futuros_do_vinculo(v.clinica_id, usuario.id)
                if futuros:
                    flash(
                        f"{usuario.nome} ainda tem {futuros} agendamento(s) futuro(s) na filial "
                        f"'{v.clinica.nome}'. Cancele ou transfira esses agendamentos antes de "
                        "encerrar a atuação ali.",
                        "danger",
                    )
                    return render_template(
                        "medico/equipe_editar.html", usuario=usuario, filiais=filiais,
                        filial_ids_atuais=filial_ids_atuais,
                    )

        cpf = request.form.get("cpf", "").strip()
        if cpf and not validar_cpf(cpf):
            flash("CPF inválido — confira os números digitados.", "danger")
            return render_template(
                "medico/equipe_editar.html", usuario=usuario, filiais=filiais,
                filial_ids_atuais=filial_ids_atuais,
            )
        if cep_incompleto(request.form.get("cep", "")):
            flash("CEP incompleto — digite os 8 números.", "danger")
            return render_template(
                "medico/equipe_editar.html", usuario=usuario, filiais=filiais,
                filial_ids_atuais=filial_ids_atuais,
            )

        usuario.nome = nome
        usuario.cpf = cpf or None
        usuario.cep = request.form.get("cep", "").strip()
        usuario.rua = request.form.get("rua", "").strip()
        usuario.numero = request.form.get("numero", "").strip()
        usuario.complemento = request.form.get("complemento", "").strip()
        usuario.bairro = request.form.get("bairro", "").strip()
        usuario.cidade = request.form.get("cidade", "").strip()
        usuario.uf = request.form.get("uf", "").strip().upper() or None
        if usuario.tipo == "medico":
            usuario.crm_numero = request.form.get("crm_numero", "").strip() or None
            usuario.crm_uf = request.form.get("crm_uf", "").strip().upper() or None

        vinculos_por_filial = {v.clinica_id: v for v in vinculos_desta_empresa}
        for f in filiais:
            if f.id in filial_ids_selecionadas and f.id not in filial_ids_atuais:
                vinculo_antigo = vinculos_por_filial.get(f.id)
                if vinculo_antigo:
                    vinculo_antigo.reativar()
                else:
                    db.session.add(ClinicaMembro(clinica_id=f.id, usuario_id=usuario.id, ativo=True))
        for v in vinculos_ativos:
            if v.clinica_id not in filial_ids_selecionadas:
                v.encerrar()

        db.session.commit()
        flash(f"Dados de {usuario.nome} atualizados.", "success")
        return redirect(url_for("medico.equipe_lista"))

    return render_template(
        "medico/equipe_editar.html", usuario=usuario, filiais=filiais,
        filial_ids_atuais=filial_ids_atuais,
    )


@medico_bp.route("/equipe-membros/<int:usuario_id>/permissoes", methods=["GET", "POST"])
@login_required
@staff_required
@permissao_required("perm_equipe")
def equipe_permissoes(usuario_id):
    """Ajusta quais telas administrativas uma pessoa da equipe pode
    acessar. É uma permissão da conta (vale em todas as filiais em que a
    pessoa atua), não só desta filial."""
    empresa = empresa_atual()
    filial_ids = [f.id for f in Clinica.query.filter_by(empresa_id=empresa.id).all()]
    if not _pessoa_da_empresa(usuario_id, empresa, filial_ids):
        flash("Pessoa não encontrada na equipe desta empresa.", "danger")
        return redirect(url_for("medico.equipe_lista"))
    usuario_alvo = Usuario.query.get_or_404(usuario_id)

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
    empresa = empresa_atual()
    filial_ids = [f.id for f in Clinica.query.filter_by(empresa_id=empresa.id).all()]
    membro = ClinicaMembro.query.filter(
        ClinicaMembro.id == membro_id, ClinicaMembro.clinica_id.in_(filial_ids),
        ClinicaMembro.ativo.is_(True),
    ).first_or_404()

    if membro.usuario_id == current_user.id:
        flash("Você não pode remover a si mesmo da clínica.", "danger")
        return redirect(url_for("medico.equipe_lista"))

    futuros = _agendamentos_futuros_do_vinculo(membro.clinica_id, membro.usuario_id)
    if futuros:
        flash(
            f"{membro.usuario.nome} ainda tem {futuros} agendamento(s) futuro(s) nesta filial. "
            "Cancele ou transfira esses agendamentos antes de encerrar a atuação.",
            "danger",
        )
        return redirect(url_for("medico.equipe_lista"))

    # ENCERRA o vínculo em vez de apagar (ver ClinicaMembro.encerrado_em):
    # o histórico de atuação e os atendimentos já feitos continuam - e se a
    # pessoa voltar um dia, o mesmo vínculo é reativado.
    membro.encerrar()
    db.session.commit()
    flash(f"{membro.usuario.nome} não atua mais na filial '{membro.clinica.nome}'. O histórico foi preservado.", "success")
    return redirect(url_for("medico.equipe_lista"))
