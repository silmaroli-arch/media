import re
from datetime import datetime, date, timedelta

from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from flask_login import login_user, logout_user, login_required, current_user

from app.extensions import db
from app.models import Usuario, Paciente, PlataformaConfig, normalizar_telefone, encontrar_conta_paciente, validar_cpf, formatar_nome_proprio, cep_incompleto, telefone_incompleto
# `proximo_seguro` mora em clinica_utils.py (compartilhado com
# routes_medico.py:escolher_clinica, que precisa do mesmo tratamento pra
# não perder o destino original de quem tem vínculo em mais de um Grupo -
# ver staff_required/escolher_clinica).
from app.clinica_utils import proximo_seguro as _proximo_seguro

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(_proximo_seguro(request.args.get("next")) or url_for("index"))

    if request.method == "POST":
        # BBP MedIA (tela 5.1.2): login do sistema principal por CPF +
        # senha. O e-mail continua funcionando no mesmo campo por
        # compatibilidade com contas já existentes (transição) — se o
        # texto digitado tiver cara de CPF (só dígitos, 11 caracteres),
        # busca por CPF; senão, cai no comportamento antigo (e-mail).
        identificador = (request.form.get("identificador") or request.form.get("email") or "").strip()
        senha = request.form.get("senha", "")
        cpf_digitos = re.sub(r"\D", "", identificador)

        usuario = None
        if len(cpf_digitos) == 11:
            for candidato in Usuario.query.filter(Usuario.cpf.isnot(None), Usuario.tipo != "paciente").all():
                if re.sub(r"\D", "", candidato.cpf or "") == cpf_digitos:
                    usuario = candidato
                    break
        if not usuario:
            usuario = Usuario.query.filter_by(email=identificador.lower()).first()

        if usuario and usuario.ativo and usuario.checar_senha(senha):
            # Remove qualquer seleção de clínica/grupo de uma sessão
            # anterior — importante em computadores compartilhados (ex.:
            # recepção da clínica), onde uma pessoa pode fazer logout e
            # outra logar em seguida no mesmo navegador.
            session.pop("clinica_id", None)
            session.pop("grupo_ativo_id", None)
            login_user(usuario)
            return redirect(_proximo_seguro(request.values.get("next")) or url_for("index"))

        flash("CPF/e-mail ou senha inválidos.", "danger")

    return render_template("auth/login.html")


@auth_bp.route("/login-paciente", methods=["GET", "POST"])
def login_paciente():
    """Login do paciente: sem senha, feito com CPF + data de nascimento.
    O CPF é a identidade da pessoa e não muda (o telefone muda - por isso
    deixou de ser a credencial e virou só um dado de contato). Com a conta
    única, os cadastros da pessoa em todas as clínicas apontam pra mesma
    conta, então o login por CPF entra direto; a tela de escolha só sobra
    pro caso raro de CONTAS distintas baterem (dados legados ainda não
    unificados)."""
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    # Etapa 2: o paciente já viu a lista de clínicas (etapa 1 encontrou
    # mais de um CADASTRO válido - com a conta única, podem ser vários
    # cadastros da mesma conta, um por empresa, e/ou contas legadas ainda
    # não unificadas) e está escolhendo qual acessar. Não confia no valor
    # bruto vindo do form - só aceita um id que a própria etapa 1 já
    # validou e guardou na sessão do servidor.
    if request.method == "POST" and "paciente_id_escolhido" in request.form:
        candidatos_ids = session.get("login_paciente_candidatos") or []
        try:
            escolhido_id = int(request.form.get("paciente_id_escolhido", ""))
        except ValueError:
            escolhido_id = None

        session.pop("login_paciente_candidatos", None)
        if escolhido_id in candidatos_ids:
            paciente = Paciente.query.get(escolhido_id)
            if paciente and paciente.usuario and paciente.usuario.ativo:
                session.pop("clinica_id", None)
                login_user(paciente.usuario)
                # Qual cadastro (empresa) da conta esta sessão vai usar -
                # ver paciente_atual() em app/routes_paciente.py.
                session["paciente_id"] = paciente.id
                return redirect(url_for("index"))

        flash("Seleção inválida — faça login novamente.", "danger")
        return redirect(url_for("auth.login_paciente"))

    if request.method == "POST":
        # Login por CPF + data de nascimento: o CPF é a identidade que
        # não muda (telefone muda, e por isso deixou de ser a credencial).
        cpf_alvo = _cpf_digitos(request.form.get("cpf", ""))
        data_nascimento_str = request.form.get("data_nascimento", "").strip()

        data_nascimento = None
        if data_nascimento_str:
            # Aceita o formato brasileiro (DD/MM/AAAA, usado pelo campo com
            # máscara) e, por compatibilidade, o formato ISO (AAAA-MM-DD,
            # usado antes quando o campo era um <input type="date"> nativo).
            for formato in ("%d/%m/%Y", "%Y-%m-%d"):
                try:
                    data_nascimento = datetime.strptime(data_nascimento_str, formato).date()
                    break
                except ValueError:
                    continue

        # Candidatos são CADASTROS (Paciente) cujo CPF e data de
        # nascimento batem: com a conta única, o mesmo Usuario pode ter um
        # cadastro por empresa - e contas legadas (pré-unificação)
        # continuam funcionando igual. O CPF é comparado só nos dígitos
        # (é guardado como digitado, com ou sem pontuação).
        candidatos = []
        if cpf_alvo and len(cpf_alvo) == 11 and data_nascimento:
            for p in Paciente.query.filter(Paciente.cpf.isnot(None)).all():
                if (
                    _cpf_digitos(p.cpf) == cpf_alvo
                    and p.data_nascimento == data_nascimento
                    and p.usuario
                    and p.usuario.ativo
                    and p.usuario.tipo == "paciente"
                ):
                    candidatos.append(p)

        # Área do paciente UNIFICADA: se todos os cadastros encontrados
        # são da MESMA conta (a mesma pessoa em várias clínicas), entra
        # direto - o painel já mostra tudo junto, e a pessoa troca a
        # clínica ativa lá dentro quando precisar (paciente.trocar_clinica).
        # A tela de escolha só sobra pro caso raro de CONTAS diferentes
        # baterem (ex.: contas legadas ainda não unificadas pela migração).
        contas_distintas = {p.usuario_id for p in candidatos}
        if candidatos and len(contas_distintas) == 1:
            session.pop("clinica_id", None)
            login_user(candidatos[0].usuario)
            preferido = next((p for p in candidatos if p.status_cadastro == "aprovado"), candidatos[0])
            session["paciente_id"] = preferido.id
            return redirect(url_for("index"))

        if len(candidatos) > 1:
            session["login_paciente_candidatos"] = [p.id for p in candidatos]
            return render_template("auth/login_paciente_escolher_clinica.html", candidatos=candidatos)

        # CPF não existe na plataforma? Em vez de só dizer "incorreto",
        # oferece criar a conta (leva pro cadastro global). Se o CPF
        # existe mas a data não bate, aí sim é credencial errada.
        cpf_existe = cpf_alvo and any(
            _cpf_digitos(p.cpf) == cpf_alvo
            for p in Paciente.query.filter(Paciente.cpf.isnot(None)).all()
        )
        if not cpf_existe and cpf_alvo and len(cpf_alvo) == 11:
            return render_template("auth/login_paciente.html", cpf_nao_encontrado=True)

        flash("CPF ou data de nascimento incorretos.", "danger")

    return render_template("auth/login_paciente.html")


@auth_bp.route("/logout")
@login_required
def logout():
    session.pop("clinica_id", None)
    session.pop("paciente_id", None)
    logout_user()
    return redirect(url_for("auth.login"))


@auth_bp.route("/trocar-senha", methods=["GET", "POST"])
@login_required
def trocar_senha():
    """Disponível para dono, secretária e médico trocarem a própria senha.
    Pacientes não têm senha — o acesso deles é por telefone + data de
    nascimento (ver auth.login_paciente)."""
    if not current_user.tem_senha:
        flash("Seu acesso não usa senha — você entra informando telefone e data de nascimento.", "info")
        return redirect(url_for("index"))

    if request.method == "POST":
        senha_atual = request.form.get("senha_atual", "")
        senha_nova = request.form.get("senha_nova", "")
        senha_confirmacao = request.form.get("senha_confirmacao", "")

        if not current_user.checar_senha(senha_atual):
            flash("Senha atual incorreta.", "danger")
            return render_template("auth/trocar_senha.html")

        if len(senha_nova) < 6:
            flash("A nova senha deve ter pelo menos 6 caracteres.", "danger")
            return render_template("auth/trocar_senha.html")

        if senha_nova != senha_confirmacao:
            flash("A confirmação não corresponde à nova senha.", "danger")
            return render_template("auth/trocar_senha.html")

        current_user.set_senha(senha_nova)
        db.session.commit()
        flash("Senha alterada com sucesso.", "success")
        return redirect(url_for("index"))

    return render_template("auth/trocar_senha.html")


# Quanto tempo a confirmação de senha vale para acessar/editar "Meus
# dados" (auth.meus_dados) - depois desse tempo sem usar a tela, pede a
# senha de novo (mesmo padrão de "reautenticação por tempo" usado em
# vários apps para telas de dados sensíveis). Guardado na sessão, não no
# banco - não sobrevive a logout/troca de navegador, de propósito.
MEUS_DADOS_CONFIRMACAO_VALIDA_MINUTOS = 15


@auth_bp.route("/meus-dados", methods=["GET", "POST"])
@login_required
def meus_dados():
    """Dados pessoais da PRÓPRIA conta logada (nome, CPF, e-mail, telefone,
    endereço) - pedido do Silvan para o dono da plataforma poder manter seu
    próprio cadastro atualizado (hoje só dava pra editar dados de médico/
    secretária pela tela de exclusão/licença; o dono não tinha cadastro
    nenhum pra editar). Disponível para qualquer tipo de conta com senha
    (dono/médico/secretária) - não só o dono - já que a mesma necessidade
    vale para qualquer um deles.

    Por serem dados sensíveis (CPF é inclusive credencial de login), o
    acesso exige confirmar a senha atual antes de ver/editar qualquer
    coisa, mesmo já estando logado - a confirmação vale por
    MEUS_DADOS_CONFIRMACAO_VALIDA_MINUTOS minutos (guardado na sessão),
    então a pessoa não precisa digitar a senha de novo a cada campo que
    for ajustar na mesma visita."""
    if not current_user.tem_senha:
        flash("Esta tela é só para contas com senha (dono, médico ou secretária).", "info")
        return redirect(url_for("index"))

    confirmado_em = session.get("meus_dados_confirmado_em")
    confirmado = (
        confirmado_em is not None
        and datetime.utcnow() - datetime.fromisoformat(confirmado_em) < timedelta(minutes=MEUS_DADOS_CONFIRMACAO_VALIDA_MINUTOS)
    )

    if request.method == "POST" and request.form.get("acao") == "confirmar_senha":
        senha_atual = request.form.get("senha_atual", "")
        if not current_user.checar_senha(senha_atual):
            flash("Senha incorreta.", "danger")
            return render_template("auth/meus_dados.html", confirmado=False)
        session["meus_dados_confirmado_em"] = datetime.utcnow().isoformat()
        return redirect(url_for("auth.meus_dados"))

    if not confirmado:
        return render_template("auth/meus_dados.html", confirmado=False)

    if request.method == "POST" and request.form.get("acao") == "salvar":
        nome = request.form.get("nome", "").strip()
        email = request.form.get("email", "").strip().lower()
        cpf = request.form.get("cpf", "").strip()

        if not nome or not email or not cpf:
            flash("Nome, CPF e e-mail são obrigatórios.", "danger")
            return render_template("auth/meus_dados.html", confirmado=True)

        if not validar_cpf(cpf):
            flash("CPF inválido — confira os números digitados.", "danger")
            return render_template("auth/meus_dados.html", confirmado=True)

        if telefone_incompleto(request.form.get("telefone", "")):
            flash("Telefone incompleto — digite o DDD e o número completos.", "danger")
            return render_template("auth/meus_dados.html", confirmado=True)

        if cep_incompleto(request.form.get("cep", "")):
            flash("CEP incompleto — digite os 8 números.", "danger")
            return render_template("auth/meus_dados.html", confirmado=True)

        # E-mail/CPF são credenciais de login (ver auth.login) - não podem
        # colidir com a conta de outra pessoa (comparação de CPF ignora
        # pontuação, mesma lógica usada pra localizar a conta no login).
        if Usuario.query.filter(Usuario.id != current_user.id, Usuario.email == email).first():
            flash("Já existe uma conta com esse e-mail.", "danger")
            return render_template("auth/meus_dados.html", confirmado=True)

        cpf_alvo = re.sub(r"\D", "", cpf)
        for outro in Usuario.query.filter(Usuario.id != current_user.id, Usuario.cpf.isnot(None)).all():
            if re.sub(r"\D", "", outro.cpf or "") == cpf_alvo:
                flash("Já existe uma conta com esse CPF.", "danger")
                return render_template("auth/meus_dados.html", confirmado=True)

        current_user.nome = nome
        current_user.email = email
        current_user.cpf = cpf
        current_user.telefone = normalizar_telefone(request.form.get("telefone", ""))
        current_user.cep = request.form.get("cep", "").strip()
        current_user.rua = request.form.get("rua", "").strip()
        current_user.numero = request.form.get("numero", "").strip()
        current_user.complemento = request.form.get("complemento", "").strip()
        current_user.bairro = request.form.get("bairro", "").strip()
        current_user.cidade = request.form.get("cidade", "").strip()
        current_user.uf = request.form.get("uf", "").strip().upper() or None
        db.session.commit()
        flash("Dados atualizados com sucesso.", "success")
        return redirect(url_for("auth.meus_dados"))

    return render_template("auth/meus_dados.html", confirmado=True)


@auth_bp.route("/cadastro", methods=["GET", "POST"])
def cadastro():
    """Cadastro público — uma ÚNICA tela pra todo mundo (médico(a) ou
    secretário(a)), sem "modo" nenhum pra escolher e sem nenhum campo de
    clínica/local de atendimento: só os dados PESSOAIS de quem está se
    cadastrando (nome, CPF — que agora é o login, ver auth.login —,
    e-mail, senha, endereço pessoal).

    Fatia 6: o cadastro NÃO cria mais um Grupo. A conta nasce solo,
    plenamente usável desde já (pode cadastrar paciente, exame,
    agendamento) sem nunca precisar de um Grupo — os dados que essa
    pessoa cria ficam com escopo pessoal (`criado_por_id`/
    `cadastrado_por_id`, ver app/clinica_utils.py:filtro_escopo_atual())
    em vez de por Grupo, e não há trial/vencimento/bloqueio nenhum
    enquanto ela estiver sozinha. Um Grupo de verdade só nasce se essa
    pessoa decidir convidar alguém pra trabalhar junto (tela "Equipe",
    que por baixo é routes_grupo.py:convidar()/novo()) — nesse momento o
    histórico pessoal dela é migrado pro Grupo recém-criado (ver
    migrar_dados_pessoais_para_grupo()).

    Antes existia aqui também um campo opcional de CNPJ que, quando já
    pertencia a uma clínica cadastrada, vinculava a pessoa direto a ela
    (evitando empresas duplicadas para colegas da mesma clínica). Esse
    mecanismo foi removido junto com o resto do formulário de clínica —
    pessoas da mesma clínica que se cadastrarem separadamente agora
    ficam, cada uma, na sua própria conta solo, e se juntam depois
    manualmente (convite pela tela "Equipe", por CPF).

    A pessoa escolhe se é médico(a) ou secretário(a) (isso só muda a
    exigência de CRM). Recebe todas as permissões administrativas
    automaticamente (conceder_todas_permissoes) — são por Usuario, não
    por Grupo, então valem tanto pro uso solo quanto se um Grupo vier a
    existir depois."""
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")
        cpf = request.form.get("cpf", "").strip()

        papel = request.form.get("papel", "secretaria")

        if not nome or not email or not senha or not cpf:
            flash("Preencha todos os campos obrigatórios (nome, e-mail, senha e CPF).", "danger")
            return render_template("auth/cadastro.html")

        if not validar_cpf(cpf):
            flash("CPF inválido — confira os números digitados.", "danger")
            return render_template("auth/cadastro.html")

        if telefone_incompleto(request.form.get("telefone", "")):
            flash("Telefone incompleto — digite o DDD e o número completos.", "danger")
            return render_template("auth/cadastro.html")

        if cep_incompleto(request.form.get("cep", "")):
            flash("CEP incompleto — digite os 8 números.", "danger")
            return render_template("auth/cadastro.html")

        if papel not in ("medico", "secretaria"):
            flash("Escolha se você é médico(a) ou secretário(a).", "danger")
            return render_template("auth/cadastro.html")

        if papel == "medico":
            crm_numero = request.form.get("crm_numero", "").strip()
            crm_uf = request.form.get("crm_uf", "").strip().upper()
            if not crm_numero or not crm_uf:
                flash("Informe o número e o estado (UF) do seu CRM.", "danger")
                return render_template("auth/cadastro.html")
        else:
            crm_numero = crm_uf = None

        if len(senha) < 6:
            flash("A senha deve ter pelo menos 6 caracteres.", "danger")
            return render_template("auth/cadastro.html")

        if Usuario.query.filter_by(email=email).first():
            flash("Já existe uma conta com esse e-mail. Faça login ou use outro e-mail.", "danger")
            return render_template("auth/cadastro.html")

        usuario = Usuario(nome=nome, email=email, tipo=papel, cpf=cpf)
        usuario.set_senha(senha)
        usuario.telefone = normalizar_telefone(request.form.get("telefone", ""))
        usuario.cep = request.form.get("cep", "").strip()
        usuario.rua = request.form.get("rua", "").strip()
        usuario.numero = request.form.get("numero", "").strip()
        usuario.complemento = request.form.get("complemento", "").strip()
        usuario.bairro = request.form.get("bairro", "").strip()
        usuario.cidade = request.form.get("cidade", "").strip()
        usuario.uf = request.form.get("uf", "").strip().upper() or None
        usuario.crm_numero = crm_numero
        usuario.crm_uf = crm_uf

        # Fatia 8 (licença individual): a cobrança é por médico e vale a
        # partir do cadastro, independente de Grupo (decisão do Silvan) -
        # todo médico novo já nasce com um prazo de trial usando o mesmo
        # parâmetro configurável que os Grupos usam (PlataformaConfig.
        # trial_dias), pra não introduzir uma segunda constante de negócio.
        if papel == "medico":
            usuario.licenca_vencimento = date.today() + timedelta(days=PlataformaConfig.obter().trial_dias)

        db.session.add(usuario)
        # Fatia 6: o cadastro NÃO cria mais um Grupo. A conta nasce solo,
        # plenamente usável (pacientes/exames/agendamentos ficam com
        # escopo pessoal via criado_por_id/cadastrado_por_id - ver
        # app/clinica_utils.py:filtro_escopo_atual()), sem trial/cobrança
        # nenhuma até que essa pessoa decida convidar alguém pra
        # trabalhar junto (tela "Equipe", que por baixo é
        # routes_grupo.py:convidar()/novo()) - só nesse momento um Grupo
        # de verdade nasce e o histórico pessoal é migrado pra ele (ver
        # migrar_dados_pessoais_para_grupo()).
        #
        # Quem funda a conta recebe todas as permissões administrativas
        # desde já — elas são por Usuario, não por Grupo, e valem tanto
        # pro uso solo quanto para quando um Grupo vier a existir.
        usuario.conceder_todas_permissoes()
        db.session.commit()
        login_user(usuario)

        flash(
            f"Conta criada com sucesso, {usuario.nome}! Bem-vindo(a) ao MedIA.",
            "success",
        )
        # O passo a passo de atalho/modelo de preparo/exame deixou de ser
        # forçado logo após o cadastro - agora é um item de menu
        # ("Primeiros passos", ver medico.primeiros_passos em
        # routes_medico.py) que a pessoa acessa quando quiser.
        return redirect(url_for("medico.dashboard"))

    return render_template("auth/cadastro.html")


def _parse_data_nascimento(valor_str):
    """Mesma conversão usada em medico.pacientes_novo — aceita o formato
    brasileiro (DD/MM/AAAA, usado pelo campo com máscara) e, por
    compatibilidade, o formato ISO (AAAA-MM-DD)."""
    valor_str = (valor_str or "").strip()
    if not valor_str:
        return None
    for formato in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(valor_str, formato).date()
        except ValueError:
            continue
    return None




def _cpf_digitos(cpf):
    """Só os dígitos do CPF - os cadastros guardam o CPF como digitado
    (com ou sem pontuação), então toda comparação é feita nos dígitos."""
    return re.sub(r"\D", "", cpf or "")


@auth_bp.route("/cadastro-paciente", methods=["GET", "POST"])
def cadastro_paciente_global():
    """Cadastro do paciente INDEPENDENTE de clínica: o paciente se
    cadastra uma vez na plataforma (cadastro global, Paciente sem
    empresa) e as clínicas o IMPORTAM pelo CPF quando ele chega lá (ver
    medico.pacientes_importar). Substitui o antigo link de auto-cadastro
    por clínica - o paciente não se cadastra mais "numa clínica"."""
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        # Nome próprio entra formatado (primeira letra de cada nome em
        # maiúscula) - no celular quase todo mundo digita minúsculo.
        nome = formatar_nome_proprio(request.form.get("nome", ""))
        cpf = request.form.get("cpf", "").strip()
        email = request.form.get("email", "").strip().lower()
        telefone_digitado = request.form.get("telefone", "").strip()
        data_nascimento_str = request.form.get("data_nascimento", "").strip()
        telefone = normalizar_telefone(telefone_digitado)

        if not nome or not _cpf_digitos(cpf) or not telefone or not data_nascimento_str:
            flash("Nome, CPF, telefone e data de nascimento são obrigatórios.", "danger")
            return render_template("auth/cadastro_paciente.html")

        # Telefone incompleto (ex.: "(27" digitado e enviado sem terminar)
        # não travava o envio - a máscara só formata o que foi digitado,
        # não garante que a pessoa terminou de digitar.
        if telefone_incompleto(telefone_digitado):
            flash("Telefone incompleto — digite o DDD e o número completos.", "danger")
            return render_template("auth/cadastro_paciente.html")
        if telefone_incompleto(request.form.get("contato_emergencia_telefone", "")):
            flash("Telefone do contato de emergência incompleto — digite o DDD e o número completos.", "danger")
            return render_template("auth/cadastro_paciente.html")

        # O CPF é o login e a chave que a clínica usa pra importar o
        # paciente - precisa ser um CPF que EXISTE (dígitos verificadores
        # conferem), não qualquer número digitado.
        if not validar_cpf(cpf):
            flash("CPF inválido — confira os números digitados.", "danger")
            return render_template("auth/cadastro_paciente.html")

        data_nascimento = _parse_data_nascimento(data_nascimento_str)
        if not data_nascimento:
            flash("Data de nascimento inválida — use o formato DD/MM/AAAA.", "danger")
            return render_template("auth/cadastro_paciente.html")

        # CEP incompleto (ex.: "29055") não bloqueava o envio e ficava
        # salvo pela metade, com rua/bairro/cidade/UF vazios (a busca do
        # ViaCEP só preenche esses campos com os 8 números completos).
        if cep_incompleto(request.form.get("cep", "")):
            flash("CEP incompleto — digite os 8 números.", "danger")
            return render_template("auth/cadastro_paciente.html")

        # A pessoa já tem conta (telefone + nascimento)? Então já está
        # cadastrada na plataforma - é só entrar.
        if encontrar_conta_paciente(telefone, data_nascimento):
            flash("Você já tem cadastro na plataforma — entre pelo login (CPF + data de nascimento).", "warning")
            return redirect(url_for("auth.login_paciente"))

        # CPF já cadastrado por alguém (em qualquer clínica ou global)?
        cpf_alvo = _cpf_digitos(cpf)
        for p_existente in Paciente.query.filter(Paciente.cpf.isnot(None)).all():
            if _cpf_digitos(p_existente.cpf) == cpf_alvo:
                flash(
                    "Esse CPF já está cadastrado na plataforma. Se é você, entre pelo login "
                    "(telefone + data de nascimento) — ou fale com a sua clínica.",
                    "danger",
                )
                return render_template("auth/cadastro_paciente.html")

        usuario = Usuario(nome=nome, email=email or None, telefone=telefone, tipo="paciente")
        db.session.add(usuario)
        db.session.flush()

        # Cadastro GLOBAL: sem empresa nenhuma (empresa_id vazio). Não há
        # "aprovação" aqui - quem aceita o paciente é cada clínica, no ato
        # de importá-lo pelo CPF (medico.pacientes_importar).
        paciente = Paciente(
            empresa_id=None,
            usuario_id=usuario.id,
            nome=nome,
            cpf=cpf,
            data_nascimento=data_nascimento,
            email=email or None,
            telefone=telefone,
            status_cadastro="aprovado",
        )
        paciente.cep = request.form.get("cep", "").strip()
        paciente.rua = request.form.get("rua", "").strip()
        paciente.numero = request.form.get("numero", "").strip()
        paciente.complemento = request.form.get("complemento", "").strip()
        paciente.bairro = request.form.get("bairro", "").strip()
        paciente.cidade = request.form.get("cidade", "").strip()
        paciente.uf = request.form.get("uf", "").strip().upper() or None
        paciente.contato_emergencia_nome = formatar_nome_proprio(request.form.get("contato_emergencia_nome", ""))
        paciente.contato_emergencia_telefone = request.form.get("contato_emergencia_telefone", "").strip()
        db.session.add(paciente)
        db.session.commit()

        login_user(usuario)
        session["paciente_id"] = paciente.id
        flash(
            "Cadastro criado! Informe seu CPF na recepção da clínica para que ela puxe seus "
            "dados — a partir daí você acompanha e solicita seus exames por aqui.",
            "success",
        )
        return redirect(url_for("paciente.dashboard"))

    return render_template("auth/cadastro_paciente.html")


@auth_bp.route("/paciente/cadastro/<codigo>", methods=["GET", "POST"])
def cadastro_paciente(codigo):
    """LEGADO: o auto-cadastro por link de clínica foi desativado - o
    paciente agora se cadastra na plataforma, independente de clínica
    (ver cadastro_paciente_global), e a clínica o importa pelo CPF (ver
    medico.pacientes_importar). Links antigos divulgados pelas clínicas
    caem aqui e são redirecionados pro cadastro global."""
    return redirect(url_for("auth.cadastro_paciente_global"))

