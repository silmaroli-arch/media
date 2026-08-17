import os
import re
import secrets
import uuid
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import (
    Blueprint, render_template, redirect, url_for, request, flash, session,
    send_file, current_app,
)
from werkzeug.utils import secure_filename
from flask_login import login_required, current_user, logout_user
from sqlalchemy import or_, and_, func

from app.extensions import db
from app.models import (
    Paciente, Usuario, Exame, Agendamento, FaqItem,
    PerguntaPendente, GrupoPaciente, Grupo, GrupoMembro, GrupoConvite,
    PreparoModelo, PreparoCorte, PreparoMedicamentoSuspenso, PreparoInfoGeral, PreparoAlimento,
    PreparoExameAnterior, PreparoMedicamentoMantido, Medicamento, normalizar_telefone,
    ChatMensagem, ResultadoExame,
    encontrar_conta_paciente, encontrar_conta_paciente_por_cpf, formatar_nome_proprio,
    cep_incompleto, telefone_incompleto,
)
from app.clinica_utils import (
    clinica_atual, clinicas_do_usuario, selecionar_clinica,
    empresa_atual, empresas_do_usuario, selecionar_empresa,
    filiais_atuais, filtro_escopo_atual,
    tem_algum_vinculo_de_grupo,
)
from app.pdf_preparo import extrair_sugestao_de_pdf, gerar_xlsx_da_sugestao
from app.xlsx_preparo import extrair_sugestoes_de_xlsx
from app.cripto_fiscal import criptografar_bytes, criptografar_texto
from cryptography.hazmat.primitives.serialization import pkcs12

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


def _pessoa_da_empresa(usuario_id, empresa):
    """O usuário faz parte deste Grupo (tenant)? Fatia 5: checagem direta
    via GrupoMembro ativo - substitui o antigo vínculo por filial
    (ClinicaMembro) ou por ser quem fundou a Empresa (empresa_fundadora_id),
    já que agora o dono do Grupo já nasce com um GrupoMembro (ver
    Grupo.novo() em routes_grupo.py) - não existe mais o caso "fundador sem
    vínculo nenhum ainda"."""
    return GrupoMembro.query.filter_by(grupo_id=empresa.id, usuario_id=usuario_id, ativo=True).first() is not None


def _filiais_da_empresa():
    """Fatia 5: TODAS as "filiais" da empresa atual - como não existe mais
    o conceito de várias filiais dentro do mesmo tenant, isso é sempre uma
    lista de 0 ou 1 elemento (o próprio Grupo atual). Mantida pelo nome só
    por compatibilidade com o restante do arquivo."""
    return filiais_atuais()


def _filiais_da_empresa_ids():
    return [f.id for f in _filiais_da_empresa()]


def _grupos_da_empresa_ids():
    """Fatia 5: o Grupo atual JÁ é o grupo - não precisa mais de
    .grupo_pareado() (só necessário enquanto Clinica era a unidade real)."""
    return _filiais_da_empresa_ids()


def _filtro_pacientes_da_empresa():
    """Filtro SQLAlchemy para "pacientes do escopo atual". Fatia 5: o
    paciente é uma identidade global (ver Paciente em app/models.py) e,
    havendo Grupo, a associação canônica é 100% por GrupoPaciente.

    Fatia 6: quando a conta é solo (sem Grupo nenhum ainda), não existe
    GrupoPaciente pra criar - o paciente fica associado diretamente ao
    dono pessoal (`Paciente.cadastrado_por_id`), mesmo padrão dos outros
    modelos (ver clinica_utils.filtro_escopo_atual())."""
    grupo_ids = _grupos_da_empresa_ids()
    if not grupo_ids:
        return Paciente.cadastrado_por_id == current_user.id
    paciente_ids_do_grupo = db.session.query(GrupoPaciente.paciente_id).filter(
        GrupoPaciente.grupo_id.in_(grupo_ids)
    )
    return Paciente.id.in_(paciente_ids_do_grupo)


def _associar_paciente_ao_escopo_atual(paciente, empresa):
    """Torna este paciente visível no escopo atual: cria o GrupoPaciente
    que faltar quando há um Grupo (tenant) atual - equivalente ao antigo
    "paciente é da empresa" (Paciente.empresa_id), agora feito via
    associação em vez de campo direto na tabela (ver
    _filtro_pacientes_da_empresa acima).

    Fatia 6: quando a conta é solo (`empresa` é None), não há Grupo pra
    associar - o paciente passa a ter este usuário como dono pessoal
    (`cadastrado_por_id`), sem precisar de GrupoPaciente nenhum."""
    if not empresa:
        if paciente.cadastrado_por_id == current_user.id:
            return 0
        paciente.cadastrado_por_id = current_user.id
        return 1
    if GrupoPaciente.query.filter_by(grupo_id=empresa.id, paciente_id=paciente.id).first():
        return 0
    db.session.add(GrupoPaciente(grupo_id=empresa.id, paciente_id=paciente.id))
    return 1


def _gerar_codigo_cadastro_paciente():
    """Gera um código curto e único para o link público de auto-cadastro
    de paciente (ver auth.cadastro_paciente). Fatia 5: o código agora vive
    no Grupo (Grupo.codigo_cadastro_paciente, mesma coluna que Empresa/
    Clinica já tinham)."""
    for _ in range(10):
        codigo = secrets.token_urlsafe(6).replace("_", "").replace("-", "")[:8]
        if not Grupo.query.filter_by(codigo_cadastro_paciente=codigo).first():
            return codigo
    # Praticamente impossível de cair aqui (espaço de códigos é enorme),
    # mas por segurança nunca deixa a função sem devolver um código.
    return secrets.token_hex(8)


def staff_required(f):
    """Garante que o usuário é da equipe (médico/secretária). Como quase
    todo mundo só tem vínculo em um Grupo (ou nenhum ainda - ver abaixo),
    ele é escolhido automaticamente; só quem atua em Grupos diferentes cai
    na tela de escolha.

    Fatia 6: não ter NENHUM Grupo deixou de ser um erro - é o estado normal
    de uma conta solo, que ainda nunca convidou ninguém (ver
    routes_auth.py:cadastro(), que parou de criar um Grupo automaticamente).
    Os dados dessa conta ficam escopados pelo dono pessoal em vez de por
    Grupo (ver clinica_utils.filtro_escopo_atual()) - a rota segue
    normalmente, só `empresa_atual()` retorna None. Isso é diferente de ter
    um Grupo BLOQUEADO pelo dono da plataforma - esse caso continua sendo
    barrado, como sempre foi (`tem_algum_vinculo_de_grupo()` distingue os
    dois: "nunca teve Grupo" de "tem Grupo, mas bloqueado")."""
    @wraps(f)
    def decorado(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_staff:
            flash("Acesso restrito à equipe médica/secretaria.", "danger")
            return redirect(url_for("auth.login"))

        if empresa_atual() is None:
            if clinicas_do_usuario():
                # Ambíguo: 2+ Grupos ativos e nenhum selecionado ainda.
                return redirect(url_for("medico.escolher_clinica"))
            if tem_algum_vinculo_de_grupo():
                # Tem Grupo(s), mas todos bloqueados - continua barrado.
                logout_user()
                flash(
                    "Sua conta não está vinculada a nenhuma clínica ativa. "
                    "Fale com o administrador da sua clínica ou com o suporte.",
                    "danger",
                )
                return redirect(url_for("auth.login"))
            # Nenhum Grupo NUNCA existiu para esta conta - modo solo,
            # segue normalmente com escopo pessoal.

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


def _medicos_do_escopo_atual(grupo):
    """Fatia 6: médicos disponíveis no escopo atual - os da clínica/Grupo,
    se houver um; senão (conta solo, sem Grupo) só o próprio usuário
    logado, se ele for médico - não existe "equipe" pra listar sem Grupo,
    então uma secretária sozinha (sem médico algum) não tem ninguém pra
    escolher aqui."""
    if grupo:
        return medicos_da_clinica(grupo)
    return [current_user] if eh_medico() else []


def _filtro_exame_por_filial(filial):
    """Fatia 6: filtro SQLAlchemy pra "exames desta filial/Grupo" usado em
    medico.agenda_novo - quando não há Grupo (conta solo, `filial` é
    None), o escopo passa a ser o dono pessoal (criado_por_id), mesmo
    padrão de clinica_utils.filtro_escopo_atual()."""
    if filial:
        return Exame.grupo_id == filial.id
    return and_(Exame.grupo_id.is_(None), Exame.criado_por_id == current_user.id)


def _filial_do_form(filiais, campo="clinica_id"):
    """Filial escolhida num formulário de cadastro, sempre validada contra
    as filiais acessíveis do usuário (fronteira de acesso). Quando a pessoa
    só atua numa filial, o campo nem aparece na tela e essa única filial é
    usada direto — mesmo comportamento de antes."""
    if len(filiais) == 1:
        return filiais[0]
    filial_id = request.form.get(campo, type=int)
    return next((f for f in filiais if f.id == filial_id), None)


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

    agendamentos_q = Agendamento.query.filter(filtro_escopo_atual(Agendamento.grupo_id, Agendamento.criado_por_id))
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
        filtro_escopo_atual(PerguntaPendente.grupo_id, PerguntaPendente.criado_por_id),
        PerguntaPendente.status == "pendente",
    )
    aguardando_q = PerguntaPendente.query.filter(
        filtro_escopo_atual(PerguntaPendente.grupo_id, PerguntaPendente.criado_por_id),
        PerguntaPendente.status == "aguardando_aprovacao",
    )
    if eh_medico() and not current_user.perm_pacientes:
        total_pacientes = (
            db.session.query(Agendamento.paciente_id)
            .filter(
                filtro_escopo_atual(Agendamento.grupo_id, Agendamento.criado_por_id),
                Agendamento.medico_id == current_user.id,
            )
            .distinct()
            .count()
        )
    else:
        total_pacientes = Paciente.query.filter(_filtro_pacientes_da_empresa()).count()

    # Perguntas pendentes: um médico só vê (e conta) as de exames dos quais é
    # responsável, mais as gerais quando também administra pacientes - vale
    # mesmo pra médico com perm_pacientes (ver _restringir_perguntas_para_medico).
    if eh_medico():
        pendentes_q = _restringir_perguntas_para_medico(pendentes_q)
        aguardando_q = _restringir_perguntas_para_medico(aguardando_q)

    # Link de auto-cadastro de paciente é da EMPRESA e fica aqui no
    # Painel - gera o código na primeira vez que o painel é aberto. Fatia
    # 6: conta solo (sem Grupo ainda) não tem onde pendurar esse código -
    # o link de auto-cadastro público só existe depois que a pessoa forma
    # um Grupo de verdade.
    empresa = empresa_atual()
    if empresa and not empresa.codigo_cadastro_paciente:
        empresa.codigo_cadastro_paciente = _gerar_codigo_cadastro_paciente()
        db.session.commit()

    # Fatia 5: convites de Grupo (GrupoConvite, por CPF - ver
    # routes_grupo.py:convidar/responder_convite) pendentes para este
    # usuário - substitui o antigo convite por código mestre
    # (ConviteVinculo), que era só para médico; GrupoConvite vale para
    # qualquer papel de equipe.
    convites_pendentes = (
        GrupoConvite.query.filter_by(usuario_convidado_id=current_user.id, status="pendente")
        .order_by(GrupoConvite.criado_em.asc())
        .all()
    )

    proximos = (
        agendamentos_q.filter(Agendamento.data_hora >= datetime.utcnow())
        .order_by(Agendamento.data_hora.asc())
        .limit(5)
        .all()
    )
    pendentes = pendentes_q.count() + aguardando_q.count()
    # A agenda completa (lista) foi incorporada ao painel — não existe mais
    # uma tela separada de "Agenda" no menu.
    agendamentos = agendamentos_q.order_by(Agendamento.data_hora.asc()).all()
    return render_template(
        "medico/dashboard.html",
        clinica=clinica_atual(),
        empresa=empresa,
        filiais=filiais,
        total_pacientes=total_pacientes,
        proximos=proximos,
        pendentes=pendentes,
        convites_pendentes=convites_pendentes,
        agendamentos=agendamentos,
    )


# ---------- Pacientes ----------

@medico_bp.route("/pacientes")
@login_required
@staff_required
def pacientes_lista():
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
    # Fatia 6: cadastrar paciente não depende mais de ter um Grupo - uma
    # conta solo cadastra pacientes normalmente, com escopo pessoal (ver
    # _associar_paciente_ao_escopo_atual/_filtro_pacientes_da_empresa).
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
            _filtro_pacientes_da_empresa(), Paciente.id == encontrado.id
        ).first():
            flash(f"{encontrado.nome} já é paciente desta empresa.", "warning")
            return redirect(url_for("medico.pacientes_lista"))
        if not encontrado:
            flash("CPF não encontrado na plataforma — preencha o cadastro completo abaixo.", "info")

    if request.method == "POST":
        # O paciente é cadastrado na EMPRESA - não se escolhe filial aqui
        # ("o cliente é só cliente"). A filial só é escolhida na hora de
        # marcar cada consulta (medico.agenda_novo).
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

        # Fatia 5: o cadastro (Paciente) é único e GLOBAL por CPF - não dá
        # mais para criar um cadastro novo se o CPF já existe em QUALQUER
        # empresa (violaria a unicidade do banco). Se já existe cadastro
        # com este CPF, a secretária precisa usar "Buscar por CPF"/
        # medico.pacientes_importar em vez de preencher o form de novo.
        existente_global = _buscar_paciente_por_cpf_plataforma(cpf)
        if existente_global:
            if Paciente.query.filter(_filtro_pacientes_da_empresa(), Paciente.id == existente_global.id).first():
                flash("Já existe um paciente com esse CPF nesta empresa.", "danger")
            else:
                flash(
                    f"{existente_global.nome} já tem cadastro na plataforma (outra clínica) — use "
                    "\"Buscar por CPF\" no topo desta página para importá-lo.",
                    "warning",
                )
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

        # Fatia 5: cadastro GLOBAL (sem empresa_id) - a visibilidade para
        # esta empresa (e suas filiais) é dada pela associação
        # GrupoPaciente, criada logo abaixo, não mais por um campo direto
        # na tabela Paciente.
        paciente = Paciente(
            empresa_id=None,
            usuario_id=usuario.id,
            nome=nome,
            cpf=cpf,
            data_nascimento=data_nascimento,
            email=email or None,
            telefone=telefone,
        )
        _preencher_endereco_emergencia(paciente, request.form)
        db.session.add(paciente)
        db.session.flush()
        _associar_paciente_ao_escopo_atual(paciente, empresa)
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
    """Associa a ESTA empresa um paciente que já existe na plataforma
    (cadastro global ou de outra clínica), achado pelo CPF em
    pacientes_novo. Fatia 5: o cadastro (Paciente) é único e global - não
    cria uma cópia, só uma associação (GrupoPaciente) nova, então os dados
    de contato/endereço e o histórico em outras clínicas continuam sendo
    exatamente o mesmo cadastro (cada clínica ainda só vê os próprios
    agendamentos/perguntas, isso não muda)."""
    empresa = empresa_atual()
    origem = _buscar_paciente_por_cpf_plataforma(request.form.get("cpf", ""))
    if not origem:
        flash("CPF não encontrado na plataforma.", "danger")
        return redirect(url_for("medico.pacientes_novo"))

    ja_daqui = Paciente.query.filter(_filtro_pacientes_da_empresa(), Paciente.id == origem.id).first()
    if ja_daqui:
        flash(f"{origem.nome} já é paciente desta empresa.", "warning")
        return redirect(url_for("medico.pacientes_lista"))

    _associar_paciente_ao_escopo_atual(origem, empresa)
    db.session.commit()
    flash(
        f"{origem.nome} foi importado(a) da plataforma para esta empresa - o cadastro (contato/"
        "endereço) é o mesmo de sempre; o histórico dele(a) em outras clínicas continua lá (cada "
        "clínica vê só o que é dela).",
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


# ---------- Exames e preparo ----------

@medico_bp.route("/exames")
@login_required
@staff_required
def exames_lista():
    # Exames são dados de CONFIGURAÇÃO da empresa - a lista mostra os de
    # todas as filiais, mesmo pra quem não está vinculado a local nenhum.
    # Fatia 6: conta solo (sem Grupo) vê o próprio catálogo pessoal.
    query = Exame.query.filter(filtro_escopo_atual(Exame.grupo_id, Exame.criado_por_id))
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
    # Fatia 6: também não depende de haver Grupo nenhum - conta solo tem
    # seu próprio catálogo pessoal (ver filtro_escopo_atual).
    filiais = _filiais_da_empresa()
    medicos = medicos_das_filiais(filiais)
    modelos = PreparoModelo.query.filter(
        filtro_escopo_atual(PreparoModelo.grupo_id, PreparoModelo.criado_por_id)
    ).order_by(PreparoModelo.nome).all()
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
        # Fatia 6: sem Grupo (conta solo), o exame nasce sem grupo_id -
        # escopado por criado_por_id (ver Exame abaixo).
        filial = filiais[0] if filiais else None

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

        if Exame.query.filter(
            filtro_escopo_atual(Exame.grupo_id, Exame.criado_por_id), Exame.nome == nome
        ).first():
            flash("Já existe um exame com esse nome.", "danger")
            return render_template("medico/exames_form.html", exame=None, medicos=medicos, modelos=modelos)

        # Preço fica de fora do cadastro genérico - é definido depois, por
        # local de atendimento, em "Exames por filial" (mesmo esquema que já
        # vale pra médico e pra associar o exame a mais de uma filial).
        exame = Exame(
            grupo_id=filial.id if filial else None,
            medico_id=medico_id, nome=nome, descricao=descricao,
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
        return redirect(url_for("medico.exames_lista"))

    return render_template("medico/exames_form.html", exame=None, medicos=medicos, modelos=modelos)


@medico_bp.route("/exames/<int:exame_id>/editar", methods=["GET", "POST"])
@login_required
@staff_required
def exames_editar(exame_id):
    query = Exame.query.filter(
        Exame.id == exame_id, filtro_escopo_atual(Exame.grupo_id, Exame.criado_por_id)
    )
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
    # Médico responsável é do GRUPO do exame, mas modelo de preparo é
    # genérico - vale qualquer modelo acessível ao usuário na empresa.
    medicos = _medicos_do_escopo_atual(exame.grupo)
    modelos = PreparoModelo.query.filter(
        filtro_escopo_atual(PreparoModelo.grupo_id, PreparoModelo.criado_por_id)
    ).order_by(PreparoModelo.nome).all()
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
    associação = um exame do catálogo do Grupo, com um médico responsável
    e um preço. A tela é uma lista simples (Exame, Médico, Preço) com um
    botão "Adicionar" que abre um formulário só com esses 3 campos:
    preencheu, salvou, pronto. Cada linha tem um "Editar" que reaproveita
    o mesmo formulário pra trocar médico/preço daquela associação.

    Fatia 5 (passo 4): esta tela era "Exame × Filial" porque uma empresa
    podia ter várias filiais e o mesmo exame precisava ser associado
    filial a filial. Isso não existe mais - o Grupo atual já é a única
    unidade (1 Grupo = 1 antiga filial), então "associar" deixou de
    precisar escolher ONDE; só falta médico e preço.

    Não existe "médico principal": todo mundo (médico ou secretária)
    segue o mesmo fluxo, escolhendo o médico responsável numa lista.
    Enquanto o médico de uma associação for só o valor técnico/provisório
    do cadastro genérico do exame (ver exames_novo), a linha mostra um
    aviso de "não confirmado" - escolher e salvar o médico aqui é o que
    confirma."""
    empresa = empresa_atual()
    exames_do_grupo = Exame.query.filter(filtro_escopo_atual(Exame.grupo_id, Exame.criado_por_id)).all()

    # A lista mostra só ASSOCIAÇÕES de verdade (associado=True) - o
    # cadastro genérico de exame cria só um item de catálogo
    # (associado=False), que não aparece aqui até alguém associar.
    associacoes = sorted(
        (e for e in exames_do_grupo if e.associado),
        key=lambda e: e.nome.lower(),
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
            e for e in exames_do_grupo
            if e.dono_medico is None or e.dono_medico.id == current_user.id
        ]
    else:
        exames_visiveis = exames_do_grupo
    nomes = sorted({e.nome for e in exames_visiveis})

    # O dropdown "Médico" segue a mesma lógica do "Exame" acima: quando
    # quem está logado é médico, só aparece ele mesmo — um médico só pode
    # ser o responsável pelos SEUS próprios exames (ver dono_medico), então
    # listar os colegas do grupo ali só confunde. Secretária/dono
    # continuam vendo todos, já que são quem de fato define qual médico
    # atende cada exame.
    medicos_disponiveis = _medicos_do_escopo_atual(empresa)
    if current_user.tipo == "medico":
        medicos_disponiveis = [m for m in medicos_disponiveis if m.id == current_user.id]

    # "Editar" de uma linha reaproveita o mesmo formulário, pré-preenchido
    # com a associação escolhida (exame fica fixo; só médico e preço são
    # editáveis).
    editar_id = request.args.get("editar", type=int)
    editar_exame = next((e for e in associacoes if e.id == editar_id), None)

    return render_template(
        "medico/exames_por_filial.html",
        associacoes=associacoes,
        nomes=nomes,
        medicos_disponiveis=medicos_disponiveis,
        editar_exame=editar_exame,
    )


def _dono_medico_do_exame(nome, empresa):
    """O médico DONO do exame com esse nome no escopo atual - quem criou o
    cadastro (ver Exame.criado_por_id). Um médico não pode ser associado
    a um exame do qual não é o dono. Retorna None quando o exame não tem
    dono médico registrado (cadastro antigo ou criado pela secretária) -
    nesse caso vale o comportamento antigo (qualquer médico do grupo)."""
    for e in Exame.query.filter(
        filtro_escopo_atual(Exame.grupo_id, Exame.criado_por_id), Exame.nome == nome
    ).all():
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

    if not nome:
        flash("Escolha um exame válido.", "danger")
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

    existente = Exame.query.filter(
        filtro_escopo_atual(Exame.grupo_id, Exame.criado_por_id), Exame.nome == nome
    ).first()
    if not existente:
        flash("Exame não encontrado.", "danger")
        return redirect(url_for("medico.exames_por_filial"))

    if existente.associado:
        # A associação já existe - mas um exame pode ter MAIS de um médico
        # (o responsável + outros que também o atendem). Se o médico
        # escolhido for novo, ele é ADICIONADO à associação existente
        # como médico extra, em vez de rejeitar.
        medicos_disponiveis = _medicos_do_escopo_atual(empresa)
        medico_novo = next((m for m in medicos_disponiveis if m.id == medico_escolhido_id), None)
        if not medico_novo:
            flash("Escolha um médico válido.", "danger")
            return redirect(url_for("medico.exames_por_filial"))

        if medico_novo.id == existente.medico_id or medico_novo in existente.medicos_extra:
            flash(f"\"{nome}\" já está associado com {medico_novo.nome}.", "warning")
            return redirect(url_for("medico.exames_por_filial"))

        existente.medicos_extra = list(existente.medicos_extra) + [medico_novo]
        db.session.commit()
        flash(
            f"{medico_novo.nome} foi adicionado(a) como médico que também atende \"{nome}\" "
            f"(responsável: {existente.medico.nome}). O preço continua o já definido para essa "
            "associação — ajuste pelo Editar, se precisar.",
            "success",
        )
        return redirect(url_for("medico.exames_por_filial"))

    medicos_disponiveis = _medicos_do_escopo_atual(empresa)
    if not medico_escolhido_id or not any(m.id == medico_escolhido_id for m in medicos_disponiveis):
        flash("Escolha um médico válido.", "danger")
        return redirect(url_for("medico.exames_por_filial"))

    # O preço é informado aqui, na hora da associação — não vem mais
    # copiado silenciosamente de outro registro nem editável no cadastro
    # do exame (ver exames_editar).
    preco = _parse_valor_decimal(request.form.get("preco", ""))
    if preco is None:
        flash("Informe o preço deste exame.", "danger")
        return redirect(url_for("medico.exames_por_filial"))

    # O exame já existia como item de CATÁLOGO (cadastro genérico, sem
    # associação, ver exames_novo) - a associação promove esse mesmo
    # registro em vez de criar outro.
    existente.medico_id = medico_escolhido_id
    existente.preco = preco
    existente.medico_confirmado = True
    existente.associado = True
    db.session.commit()
    flash(f"\"{nome}\" associado com {existente.medico.nome} como responsável.", "success")
    return redirect(url_for("medico.exames_por_filial"))


@medico_bp.route("/exames/por-filial/<int:exame_id>/atualizar", methods=["POST"])
@login_required
@staff_required
def exames_por_filial_atualizar(exame_id):
    """Atualiza uma associação já existente - usada pelo "Editar" da tela
    de associações (medico.exames_por_filial). Exame, médico e preço são
    editáveis. Também aceita ajustar os médicos extras quando o chamador
    manda atualizar_extras=1.

    Fatia 5 (passo 4): não existe mais "trocar de filial" (só há uma, o
    Grupo atual) - só o exame associado pode mudar, e continua travado
    quando já há agendamento marcado (mesmo motivo de antes)."""
    empresa = empresa_atual()
    exame = Exame.query.filter(
        Exame.id == exame_id, filtro_escopo_atual(Exame.grupo_id, Exame.criado_por_id)
    ).first_or_404()

    nome_novo = request.form.get("nome", "").strip() if "nome" in request.form else None
    mudou_exame = bool(nome_novo) and nome_novo != exame.nome
    if mudou_exame:
        if exame.agendamentos:
            flash(
                "Esta associação já tem agendamentos - não dá pra trocar o exame dela. "
                "Troque só médico/preço, ou exclua a associação (após tratar os agendamentos) e crie outra.",
                "danger",
            )
            return redirect(url_for("medico.exames_por_filial", editar=exame.id))

        ja_existe = Exame.query.filter(
            filtro_escopo_atual(Exame.grupo_id, Exame.criado_por_id), Exame.nome == nome_novo,
            Exame.id != exame.id, Exame.associado.is_(True),
        ).first()
        if ja_existe:
            flash(f"\"{nome_novo}\" já está associado.", "warning")
            return redirect(url_for("medico.exames_por_filial", editar=exame.id))

        # Os dados do exame (descrição/duração/acompanhante) vêm do
        # cadastro dele no Grupo - a associação passa a ser DESSE exame,
        # não é só uma troca de rótulo.
        origem = Exame.query.filter(
            filtro_escopo_atual(Exame.grupo_id, Exame.criado_por_id), Exame.nome == nome_novo, Exame.id != exame.id,
        ).first()
        if not origem:
            flash("Exame não encontrado.", "danger")
            return redirect(url_for("medico.exames_por_filial", editar=exame.id))
        nome_origem = origem.nome
        descricao_origem = origem.descricao
        duracao_origem = origem.duracao_minutos
        acompanhante_origem = origem.precisa_acompanhante
        # Fatia 5: `origem` e `exame` agora sempre vivem no MESMO Grupo (não
        # existe mais outra filial pra abrigar cada um) - copiar o nome de
        # `origem` para `exame` sem removê-lo violaria a constraint única
        # (grupo_id, nome). `origem` só chega aqui como um item de
        # catálogo ainda não associado (ja_existe já garantiu que nenhuma
        # ASSOCIAÇÃO tem esse nome, e grupo_id+nome é único) - suas
        # referências opcionais (perguntas/chat) só perdem o vínculo, igual
        # à exclusão de associação (exames_por_filial_excluir).
        PerguntaPendente.query.filter_by(exame_id=origem.id).update({"exame_id": None})
        ChatMensagem.query.filter_by(exame_id=origem.id).update({"exame_id": None})
        db.session.delete(origem)
        db.session.flush()
        exame.nome = nome_origem
        exame.descricao = descricao_origem
        exame.duracao_minutos = duracao_origem
        exame.precisa_acompanhante = acompanhante_origem
        # O modelo de preparo era do exame antigo - com a associação
        # apontando pra outro exame, não vale mais; fica pra revisar.
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
    medicos_do_grupo = _medicos_do_escopo_atual(empresa)
    if medico_id and any(m.id == medico_id for m in medicos_do_grupo):
        exame.medico_id = medico_id
        exame.medico_confirmado = True

    if request.form.get("atualizar_extras") == "1":
        # Quem manda esse campo explicitamente pode ajustar os médicos
        # extras junto - as chamadas normais da tela de associação não
        # mandam, pra não mexer nos extras sem querer.
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
        exame.medicos_extra = [m for m in medicos_do_grupo if m.id in medicos_extra_ids]

    db.session.commit()
    flash(f"\"{exame.nome}\" atualizado.", "success")
    return redirect(url_for("medico.exames_por_filial"))


@medico_bp.route("/exames/por-filial/<int:exame_id>/excluir", methods=["POST"])
@login_required
@staff_required
def exames_por_filial_excluir(exame_id):
    """Exclui uma associação - botão "Excluir" do Editar na tela de
    associações. Associação com agendamento marcado não pode ser excluída
    (o agendamento aponta pra ela); trate os agendamentos primeiro.
    Perguntas e mensagens de chat antigas que citavam este exame são
    mantidas, só perdem o vínculo com ele."""
    exame = Exame.query.filter(
        Exame.id == exame_id, filtro_escopo_atual(Exame.grupo_id, Exame.criado_por_id)
    ).first_or_404()

    if exame.agendamentos:
        flash(
            f"\"{exame.nome}\" tem agendamento(s) - cancele/realize esses agendamentos antes de "
            "excluir a associação.",
            "danger",
        )
        return redirect(url_for("medico.exames_por_filial", editar=exame.id))

    # Referências opcionais (histórico) só perdem o vínculo - nada é apagado.
    PerguntaPendente.query.filter_by(exame_id=exame.id).update({"exame_id": None})
    ChatMensagem.query.filter_by(exame_id=exame.id).update({"exame_id": None})

    nome = exame.nome
    db.session.delete(exame)
    db.session.commit()
    flash(f"Associação de \"{nome}\" excluída.", "success")
    return redirect(url_for("medico.exames_por_filial"))


# ---------- Modelos de preparo (reaproveitáveis entre exames) ----------

@medico_bp.route("/preparo-modelos")
@login_required
@staff_required
def preparo_modelos_lista():
    modelos = (
        PreparoModelo.query.filter(filtro_escopo_atual(PreparoModelo.grupo_id, PreparoModelo.criado_por_id))
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
        # Fatia 6: sem Grupo (conta solo), o modelo nasce sem grupo_id -
        # escopado por criado_por_id (ver PreparoModelo abaixo).
        filial = filiais[0] if filiais else None

        nome = request.form.get("nome", "").strip()
        instrucoes = request.form.get("instrucoes", "").strip()
        observacoes_medicamentos = request.form.get("observacoes_medicamentos", "").strip()

        if not nome or not instrucoes:
            flash("Nome do modelo e instruções são obrigatórios.", "danger")
            return render_template("medico/preparo_modelo_form.html", modelo=None, sugestao=None, medicamentos_catalogo=Medicamento.query.order_by(Medicamento.nome).all())

        if PreparoModelo.query.filter(
            filtro_escopo_atual(PreparoModelo.grupo_id, PreparoModelo.criado_por_id), PreparoModelo.nome == nome
        ).first():
            flash("Já existe um modelo de preparo com esse nome.", "danger")
            return render_template("medico/preparo_modelo_form.html", modelo=None, sugestao=None, medicamentos_catalogo=Medicamento.query.order_by(Medicamento.nome).all())

        modelo = PreparoModelo(
            grupo_id=filial.id if filial else None,
            nome=nome, instrucoes=instrucoes,
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
        return redirect(url_for("medico.preparo_modelos_lista"))

    return render_template("medico/preparo_modelo_form.html", modelo=None, sugestao=sugestao, medicamentos_catalogo=Medicamento.query.order_by(Medicamento.nome).all())


@medico_bp.route("/preparo-modelos/<int:modelo_id>/editar", methods=["GET", "POST"])
@login_required
@staff_required
def preparo_modelos_editar(modelo_id):
    modelo = PreparoModelo.query.filter(
        PreparoModelo.id == modelo_id, filtro_escopo_atual(PreparoModelo.grupo_id, PreparoModelo.criado_por_id)
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
        PreparoModelo.id == modelo_id, filtro_escopo_atual(PreparoModelo.grupo_id, PreparoModelo.criado_por_id)
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

@medico_bp.route("/agenda")
@login_required
@staff_required
def agenda():
    # A tela de agenda foi incorporada ao painel (não existe mais um item
    # de menu separado) — este redirecionamento mantém funcionando os
    # links/botões antigos que ainda apontam para cá.
    return redirect(url_for("medico.dashboard", _anchor="agenda-completa"))


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

        # Fatia 6: sem Grupo (conta solo), `filiais_disponiveis` é vazia -
        # não há filial pra escolher, e não é mais um erro (era antes).
        filial = next((f for f in filiais_disponiveis if f.id == filial_id), None)
        if filiais_disponiveis and not filial:
            flash("Escolha uma filial válida.", "danger")
            return redirect(url_for("medico.agenda_novo"))

        if not paciente_id or not exame_id:
            flash("Escolha um paciente e um exame válidos.", "danger")
            return redirect(url_for("medico.agenda_novo", filial_id=filial.id if filial else None))

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
        exame_query = Exame.query.filter(_filtro_exame_por_filial(filial), Exame.id == exame_id, Exame.associado.is_(True))
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
            return redirect(url_for("medico.agenda_novo", filial_id=filial.id if filial else None, medico_id=medico_id_form))

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
            return redirect(url_for("medico.agenda_novo", filial_id=filial.id if filial else None, medico_id=medico_id_form))

        if exame.precisa_acompanhante and not acompanhante_nome:
            flash(
                f"O exame '{exame.nome}' exige acompanhante — informe o nome de quem vai acompanhar "
                "o paciente no dia (pode ser alterado depois, se necessário).",
                "danger",
            )
            return redirect(url_for("medico.agenda_novo", filial_id=filial.id if filial else None, medico_id=medico_id_form))

        agendamento = Agendamento(
            grupo_id=filial.id if filial else None,
            # Fatia 6: sem Grupo (conta solo), o agendamento fica escopado
            # pelo dono pessoal (ver clinica_utils.filtro_escopo_atual()).
            criado_por_id=current_user.id if not filial else None,
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
        medicos_disponiveis = _medicos_do_escopo_atual(filial_selecionada)
        medico_id_param = request.args.get("medico_id", type=int)
        medico_selecionado_id = medico_id_param if any(m.id == medico_id_param for m in medicos_disponiveis) else None
        if medico_selecionado_id is None and len(medicos_disponiveis) == 1:
            medico_selecionado_id = medicos_disponiveis[0].id

    # Pacientes são da EMPRESA - a lista não depende da filial escolhida
    # (a filial vale só para o exame e para onde o atendimento acontece).
    pacientes = Paciente.query.filter(_filtro_pacientes_da_empresa()).order_by(Paciente.nome).all()

    exames_query = Exame.query.filter(_filtro_exame_por_filial(filial_selecionada), Exame.associado.is_(True))
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

    # Lista de trabalho do médico — todos os exames agendados nas filiais
    # atuais, sem filtro de status (não existe mais workflow de
    # confirmação). Sem filtro de data: um exame de hoje que já passou do
    # horário mas ainda não foi encerrado continua precisando aparecer aqui.
    proximos = (
        Agendamento.query.filter(
            filtro_escopo_atual(Agendamento.grupo_id, Agendamento.criado_por_id),
            Agendamento.medico_id == medico_alvo.id,
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
    empresas não se enxergam entre si, mas a agenda do médico é uma só:
    esta tela é a visão dessa agenda única, um dos ganhos do médico
    multi-clínica por código mestre. Só a própria pessoa logada vê a
    consolidação - a secretária de cada clínica continua vendo apenas a
    agenda da clínica dela."""
    if not eh_medico():
        flash("Esta tela é a agenda pessoal consolidada de contas de médico.", "danger")
        return redirect(url_for("medico.dashboard"))
    agendamentos = (
        Agendamento.query.filter(
            Agendamento.medico_id == current_user.id,
            Agendamento.data_hora >= datetime.utcnow() - timedelta(days=1),
        )
        .order_by(Agendamento.data_hora.asc())
        .all()
    )
    return render_template("medico/minha_agenda_completa.html", agendamentos=agendamentos)


# ---------- Atendimento (continuidade/encerramento da consulta) ----------

@medico_bp.route("/agenda/<int:agendamento_id>/atendimento", methods=["GET", "POST"])
@login_required
@staff_required
def atendimento(agendamento_id):
    query = Agendamento.query.filter(
        Agendamento.id == agendamento_id,
        filtro_escopo_atual(Agendamento.grupo_id, Agendamento.criado_por_id),
    )
    if eh_medico():
        query = query.filter(Agendamento.medico_id == current_user.id)
    agendamento = query.first_or_404()

    if request.method == "POST":
        agendamento.notas_atendimento = request.form.get("notas_atendimento", "").strip() or None
        if request.form.get("encerrar") == "on":
            agendamento.encerrado_em = datetime.utcnow()
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
    query = Agendamento.query.filter(
        Agendamento.id == agendamento_id,
        filtro_escopo_atual(Agendamento.grupo_id, Agendamento.criado_por_id),
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


# ---------- Perguntas pendentes (aprendizado da "IA") ----------

@medico_bp.route("/perguntas")
@login_required
@staff_required
def perguntas_pendentes():
    escopo = filtro_escopo_atual(PerguntaPendente.grupo_id, PerguntaPendente.criado_por_id)
    pendentes_q = PerguntaPendente.query.filter(escopo, PerguntaPendente.status == "pendente")
    # Respostas que a IA já rascunhou e estão esperando o médico revisar,
    # editar se precisar, e aprovar antes de irem para o paciente.
    aguardando_q = PerguntaPendente.query.filter(escopo, PerguntaPendente.status == "aguardando_aprovacao")
    respondidas_q = PerguntaPendente.query.filter(escopo, PerguntaPendente.status == "respondida")

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
        PerguntaPendente.id == pergunta_id,
        filtro_escopo_atual(PerguntaPendente.grupo_id, PerguntaPendente.criado_por_id),
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
        # O item de FAQ nasce no MESMO escopo (filial/grupo, ou dono
        # pessoal se a pergunta era de uma conta solo) da pergunta respondida.
        clinica_id=pergunta.clinica_id,
        grupo_id=pergunta.grupo_id,
        criado_por_id=pergunta.criado_por_id,
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
    query = FaqItem.query.filter(filtro_escopo_atual(FaqItem.grupo_id, FaqItem.criado_por_id))
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
    exames_query = Exame.query.filter(filtro_escopo_atual(Exame.grupo_id, Exame.criado_por_id))
    if eh_medico():
        exames_query = exames_query.filter(
            or_(Exame.medico_id == current_user.id, Exame.medicos_extra.any(id=current_user.id))
        )
    exames = exames_query.order_by(Exame.nome).all()

    if request.method == "POST":
        exame_id = request.form.get("exame_id", type=int)
        exame_escolhido = next((e for e in exames if e.id == exame_id), None)
        # Item vinculado a um exame nasce no Grupo DESSE exame; item geral
        # (sem exame) usa o Grupo escolhido no formulário. Fatia 6: sem
        # Grupo (conta solo), não há filial pra escolher - `filial` fica
        # None e o item nasce escopado pelo dono pessoal (criado_por_id).
        filial = exame_escolhido.grupo if exame_escolhido else _filial_do_form(filiais)

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

        if filiais and not filial:
            flash("Escolha a filial em que este item será cadastrado.", "danger")
            return render_template("medico/faq_form.html", exames=exames)

        item = FaqItem(
            grupo_id=filial.id if filial else None,
            criado_por_id=current_user.id if not filial else None,
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
    # Fatia 5 (passo 4): não existe mais "várias filiais da mesma empresa"
    # para escolher via `filial_id` — o Grupo atual já É a única unidade.
    # O parâmetro de URL é ignorado (mantido só para não quebrar links/
    # favoritos antigos que ainda apontam para "/clinica/configuracoes/<id>").
    clinica = empresa_atual()

    if not clinica:
        # Fatia 6: conta solo, sem Grupo nenhum ainda - não há "dados da
        # clínica" pra configurar sem um Grupo (isso não é mais um erro,
        # só um estado válido; a pessoa cria um Grupo quando decidir
        # convidar alguém, ver routes_grupo.py:novo()).
        flash("Cadastre seu primeiro grupo antes de preencher os dados dele.", "info")
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
        return redirect(url_for("medico.filiais_lista"))

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
    a partir do CEP do endereço — não é um dado fiscal digitado à mão.

    Fatia 5 (passo 4): `filial_id` é ignorado (ver clinica_configuracoes
    acima) — o Grupo atual já é a única unidade a configurar."""
    clinica = empresa_atual()

    if not clinica:
        flash("Cadastre seu primeiro grupo antes de preencher os dados fiscais dele.", "info")
        return redirect(url_for("medico.filiais_lista"))

    if request.method == "POST":
        clinica.inscricao_estadual = request.form.get("inscricao_estadual", "").strip()
        clinica.regime_tributario = request.form.get("regime_tributario", "").strip()
        clinica.cnae = request.form.get("cnae", "").strip()

        db.session.commit()
        flash(f"Dados Fiscais de '{clinica.nome}' atualizados com sucesso.", "success")
        # Mesmo comportamento dos Dados Cadastrais: salvou -> fecha a
        # tela, voltando pra lista de locais.
        return redirect(url_for("medico.filiais_lista"))

    return render_template(
        "medico/clinica_dados_fiscais.html",
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
    do RPS) — separado do formulário de "Dados fiscais" (inscrição
    estadual/regime/CNAE) porque fica em outro <form> na mesma página (ver
    medico/clinica_dados_fiscais.html). O upload do certificado digital em
    si tem sua própria rota, `clinica_certificado_upload`, abaixo.

    Fatia 5 (passo 4): `filial_id` é ignorado — o Grupo atual já é a única
    unidade a configurar."""
    clinica = empresa_atual()

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
    return redirect(url_for("medico.clinica_dados_fiscais", filial_id=clinica.id))


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
    puro nem em disco.

    Fatia 5 (passo 4): `filial_id` é ignorado — o Grupo atual já é a única
    unidade a configurar."""
    clinica = empresa_atual()

    arquivo = request.files.get("certificado_arquivo")
    senha = request.form.get("certificado_senha", "")

    if not arquivo or not arquivo.filename:
        flash("Selecione o arquivo do certificado (.pfx) antes de enviar.", "danger")
        return redirect(url_for("medico.clinica_dados_fiscais", filial_id=clinica.id))

    if not senha:
        flash("Informe a senha do certificado.", "danger")
        return redirect(url_for("medico.clinica_dados_fiscais", filial_id=clinica.id))

    conteudo = arquivo.read()
    # Um certificado .pfx/.p12 normal tem poucos KB — um arquivo muito
    # maior do que isso quase certamente não é um certificado válido, então
    # rejeitamos antes mesmo de tentar abrir (evita gastar memória com um
    # upload indevido).
    if len(conteudo) > 5 * 1024 * 1024:
        flash("Arquivo muito grande para ser um certificado válido.", "danger")
        return redirect(url_for("medico.clinica_dados_fiscais", filial_id=clinica.id))

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
        return redirect(url_for("medico.clinica_dados_fiscais", filial_id=clinica.id))

    if certificado is None:
        flash("O arquivo enviado não contém um certificado válido.", "danger")
        return redirect(url_for("medico.clinica_dados_fiscais", filial_id=clinica.id))

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
    return redirect(url_for("medico.clinica_dados_fiscais", filial_id=clinica.id))


# ---------- "Meus Locais de Atendimento" -> "Meus Grupos" ----------
#
# Fatia 5: não existe mais "várias filiais dentro de uma empresa" - cada
# Grupo já é sua própria unidade completa. Criar um novo local de
# atendimento, entrar/sair de um, e ver a lista dos seus, é exatamente o
# que routes_grupo.py (novo/meus_grupos/selecionar/sair) já faz - em vez
# de duplicar essa lógica aqui, os endpoints antigos (usados em vários
# links/redirects deste arquivo e templates) viram redirecionamentos pra
# lá, preservando as URLs/nomes de rota já em uso.

@medico_bp.route("/filiais")
@login_required
def filiais_lista():
    return redirect(url_for("grupo.meus_grupos"))


@medico_bp.route("/filiais/nova", methods=["GET", "POST"])
@login_required
def filiais_nova():
    return redirect(url_for("grupo.novo"))


# ---------- Equipe (médicos e secretárias da clínica atual) ----------

@medico_bp.route("/equipe-membros")
@login_required
@staff_required
@permissao_required("perm_equipe")
def equipe_lista():
    """Fatia 5 (passo 4): a tela de equipe (listar membros, convidar por
    CPF, criar conta nova, remover, promover a administrador) passou a
    ser routes_grupo.py:convidar() - ela já cobre tudo isso pelo modelo
    Grupo/GrupoMembro/GrupoConvite (ver "Achado principal" no plano da
    Fatia 5). Esta rota só existe pra não quebrar os vários links/menus
    antigos que ainda apontam pra cá."""
    empresa = empresa_atual()
    if not empresa:
        flash("Cadastre seu primeiro grupo antes de gerenciar a equipe.", "info")
        return redirect(url_for("medico.filiais_lista"))
    return redirect(url_for("grupo.convidar", grupo_id=empresa.id))


@medico_bp.route("/equipe-membros/novo", methods=["GET", "POST"])
@login_required
@staff_required
@permissao_required("perm_equipe")
def equipe_novo():
    return redirect(url_for("medico.equipe_lista"))


@medico_bp.route("/equipe-membros/<int:usuario_id>/permissoes", methods=["GET", "POST"])
@login_required
@staff_required
@permissao_required("perm_equipe")
def equipe_permissoes(usuario_id):
    """Ajusta quais telas administrativas uma pessoa da equipe pode
    acessar. É uma permissão da conta - vale em qualquer Grupo em que a
    pessoa atue, não só neste.

    Fatia 5 (passo 4): a checagem de "essa pessoa faz parte da equipe
    deste tenant" passou a usar GrupoMembro (era ClinicaMembro) - ver
    _pessoa_da_empresa."""
    empresa = empresa_atual()
    if not empresa or not _pessoa_da_empresa(usuario_id, empresa):
        flash("Pessoa não encontrada na equipe deste grupo.", "danger")
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
