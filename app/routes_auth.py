import re
from datetime import date, datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, request, flash, session, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy import or_

from app.extensions import db
from app.models import Usuario, Empresa, Clinica, ClinicaMembro, Paciente, PlataformaConfig, normalizar_telefone, gerar_codigo_mestre_medico, encontrar_conta_paciente, encontrar_conta_paciente_por_cpf, validar_cpf, formatar_nome_proprio, cep_incompleto, telefone_incompleto, validar_cnpj, encontrar_clinica_por_cnpj

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")
        usuario = Usuario.query.filter_by(email=email).first()

        if usuario and usuario.ativo and usuario.checar_senha(senha):
            # Remove qualquer seleção de clínica de uma sessão anterior —
            # importante em computadores compartilhados (ex.: recepção da
            # clínica), onde uma pessoa pode fazer logout e outra logar
            # em seguida no mesmo navegador.
            session.pop("clinica_id", None)
            login_user(usuario)
            return redirect(url_for("index"))

        flash("E-mail ou senha inválidos.", "danger")

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


@auth_bp.route("/cadastro", methods=["GET", "POST"])
def cadastro():
    """Cadastro público. Dois modos, escolhidos pelo campo "modo":

    - "empresa" (padrão/original): quem se cadastra informa o CNPJ da
      clínica (agora obrigatório) — se já existir uma Clinica cadastrada
      com esse CNPJ na plataforma (ver encontrar_clinica_por_cnpj em
      app/models.py), a pessoa é vinculada direto a ela (ClinicaMembro),
      SEM criar uma empresa/filial nova e SEM precisar de convite ou
      aceite de ninguém — resolve o caso de dois médicos da mesma clínica
      se cadastrarem sem um saber do outro e acabarem com duas empresas
      duplicadas. Só quando o CNPJ é inédito é que uma empresa e uma
      filial novas são criadas, com quem se cadastrou como fundador(a).
      Nem toda clínica tem secretária, então a pessoa escolhe se é médico
      ou secretário(a) — mas só quem FUNDA a empresa recebe todas as
      permissões administrativas automaticamente; quem entra numa clínica
      já existente pelo CNPJ recebe as permissões padrão do papel (ver
      Usuario.definir_permissoes_padrao) e pode ter mais concedidas depois
      por quem já administra a equipe.

    - "independente": pensado pra quem se cadastra por conta própria, sem
      uma empresa/clínica de verdade por trás — médico(a) ou secretário(a)
      (a pessoa escolhe o papel aqui também, igual no modo "empresa"; até
      pouco tempo esse modo era só pra médico, mas a secretária se
      cadastra do mesmo jeito, como "um usuário qualquer"). Não pede nome
      de empresa nem de filial — esses são gerados automaticamente a
      partir do nome de quem se cadastrou, ficando "invisíveis" (nunca vê
      as palavras "empresa"/"filial" no cadastro). Diferente do modo
      "empresa" acima, aqui a primeira filial JÁ vem criada (com o
      ClinicaMembro correspondente) — é a promessa explícita dessa opção,
      pra pessoa já cair pronta pra usar. Por baixo, a estrutura é
      exatamente a mesma (Empresa -> Clinica -> ClinicaMembro): se essa
      pessoa passar a atender/trabalhar em mais de um local, cadastra os
      próximos em "Meus locais de atendimento" (medico.filiais_nova), que
      viram novas filiais dentro dessa mesma empresa oculta.

    Em ambos os modos, quem cria a conta recebe todas as permissões
    administrativas (conceder_todas_permissoes) e pode ajustá-las depois
    para cada pessoa que adicionar à equipe."""
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        modo = request.form.get("modo", "empresa")
        independente = modo == "independente"

        nome = request.form.get("nome", "").strip()
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")
        cpf = request.form.get("cpf", "").strip()

        papel = request.form.get("papel", "secretaria")
        if independente:
            # Nomes gerados a partir do nome de quem se cadastrou — a
            # pessoa não preenche nem vê "empresa"/"filial" nessa tela.
            # Ficam só como identificação interna dos registros no banco.
            nome_empresa = nome
        else:
            nome_empresa = request.form.get("nome_empresa", "").strip()

        # Dados da filial (local de atendimento) - agora coletados
        # completos já no cadastro (igual à tela "Dados Cadastrais"),
        # substituindo a etapa que antes deixava isso pra depois. Em
        # ambos os modos a pessoa já sai com o primeiro local pronto.
        # O campo já é obrigatório na tela (HTML + JS) nos dois modos, mas
        # se ainda assim chegar vazio (ex.: JS desabilitado, ou alguém
        # chega direto em /cadastro sem selecionar um modo antes de
        # preencher), o fallback usa: no modo independente, o nome da
        # própria pessoa (sincronizado pelo JS, ver cadastro.html) em vez
        # de um texto fixo tipo "Consultório"; no modo empresa, o nome da
        # empresa cadastrada (nome_empresa) em vez de deixar em branco ou
        # usar o nome de quem se cadastrou.
        nome_filial = request.form.get("nome_filial", "").strip() or (nome if independente else nome_empresa)
        telefone_filial_digitado = request.form.get("telefone_filial", "").strip()
        cnpj_filial = request.form.get("cnpj_filial", "").strip()

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

        if not independente and not nome_empresa:
            flash("Preencha todos os campos.", "danger")
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

        # CNPJ - obrigatório e validado só no modo "empresa": é o
        # identificador que permite encontrar uma clínica já cadastrada
        # (ver encontrar_clinica_por_cnpj) e vincular direto a ela, em vez
        # de duas pessoas da mesma clínica criarem cada uma a sua própria
        # empresa duplicada. No modo "independente" a empresa é oculta e
        # pessoal — o CNPJ continua opcional, sem essa checagem.
        clinica_existente = None
        if not independente:
            if not cnpj_filial:
                flash("Informe o CNPJ da sua clínica.", "danger")
                return render_template("auth/cadastro.html")
            if not validar_cnpj(cnpj_filial):
                flash("CNPJ inválido — confira os números digitados.", "danger")
                return render_template("auth/cadastro.html")
            clinica_existente = encontrar_clinica_por_cnpj(cnpj_filial)

        if clinica_existente is None:
            if not nome_filial:
                flash("Informe o nome do seu local de atendimento.", "danger")
                return render_template("auth/cadastro.html")

            if not telefone_filial_digitado or telefone_incompleto(telefone_filial_digitado):
                flash("Telefone do local de atendimento incompleto — digite o DDD e o número completos.", "danger")
                return render_template("auth/cadastro.html")

            if cep_incompleto(request.form.get("cep_filial", "")):
                flash("CEP do local de atendimento incompleto — digite os 8 números.", "danger")
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
        if papel == "medico":
            # Todo médico nasce com seu código mestre (ver
            # Usuario.codigo_mestre em app/models.py).
            usuario.codigo_mestre = gerar_codigo_mestre_medico()

        if clinica_existente is not None:
            # O CNPJ já pertence a uma clínica cadastrada por outra pessoa
            # - a conta é criada e já entra vinculada a ela (sem convite,
            # sem aceite: quem se cadastra está confirmando que atua ali).
            # Não é fundador(a) dessa empresa, então recebe as permissões
            # PADRÃO do papel, não todas - quem já administra a equipe
            # concede mais depois, se for o caso.
            usuario.definir_permissoes_padrao()
            db.session.add(usuario)
            db.session.flush()
            vinculo = ClinicaMembro(clinica_id=clinica_existente.id, usuario_id=usuario.id, ativo=True)
            db.session.add(vinculo)
            db.session.commit()
            login_user(usuario)
            session["clinica_id"] = clinica_existente.id
            flash(
                f"Encontramos '{clinica_existente.nome}' já cadastrada na plataforma com esse CNPJ — "
                f"sua conta foi criada e você já está vinculado(a) a ela!",
                "success",
            )
            return redirect(url_for("medico.onboarding"))

        trial_dias = PlataformaConfig.obter().trial_dias
        empresa = Empresa(
            nome=nome_empresa,
            email_contato=email,
            status="trial",
            data_vencimento=date.today() + timedelta(days=trial_dias),
        )
        db.session.add(empresa)
        db.session.flush()
        usuario.empresa_fundadora_id = empresa.id
        # Quem funda a empresa é a administradora inicial — recebe todas as
        # permissões administrativas independentemente de ser médico(a) ou
        # secretário(a), já que a clínica pode não ter uma secretária. Isso
        # inclui perm_filiais, essencial pro médico independente poder
        # cadastrar novos locais de atendimento sozinho depois.
        usuario.conceder_todas_permissoes()

        # O primeiro local de atendimento já vem completo (nome, telefone,
        # CNPJ e endereço) em vez de ficar pra depois - mesma ideia da tela
        # "Dados Cadastrais" (medico.clinica_configuracoes).
        filial = Clinica(
            empresa_id=empresa.id,
            nome=nome_filial,
            email_contato=email,
            telefone=telefone_filial_digitado or None,
            cnpj=cnpj_filial or None,
            cep=request.form.get("cep_filial", "").strip(),
            rua=request.form.get("rua_filial", "").strip(),
            numero=request.form.get("numero_filial", "").strip(),
            complemento=request.form.get("complemento_filial", "").strip(),
            bairro=request.form.get("bairro_filial", "").strip(),
            cidade=request.form.get("cidade_filial", "").strip(),
            uf=request.form.get("uf_filial", "").strip().upper() or None,
        )
        db.session.add(filial)
        db.session.flush()
        db.session.add(usuario)
        db.session.flush()
        vinculo = ClinicaMembro(clinica_id=filial.id, usuario_id=usuario.id, ativo=True)
        db.session.add(vinculo)
        db.session.commit()
        login_user(usuario)
        session["clinica_id"] = filial.id

        if independente:
            flash(
                f"Conta criada com sucesso, {usuario.nome}! "
                "Vamos te ajudar a deixar tudo pronto para uso.",
                "success",
            )
        else:
            flash(
                f"Empresa '{empresa.nome}' criada com sucesso, junto com o seu primeiro local "
                "de atendimento! Vamos te ajudar a deixar tudo pronto para uso.",
                "success",
            )
        return redirect(url_for("medico.onboarding"))

    return render_template("auth/cadastro.html")


@auth_bp.route("/cadastro/verificar-cnpj")
def cadastro_verificar_cnpj():
    """Endpoint público (sem login - é chamado da própria tela de
    cadastro, antes de existir conta) usado pela busca automática de CNPJ:
    ao a pessoa terminar de digitar o CNPJ da clínica, o front consulta
    aqui se já existe uma Clinica com ele. Devolve nome, telefone e
    endereço da clínica encontrada, pra já preencher esses campos na tela
    (evita redigitar dados que já existem) - são dados cadastrais da
    empresa, não dados pessoais de ninguém, então não há problema em
    mostrá-los antes de a pessoa se identificar."""
    cnpj = request.args.get("cnpj", "")
    if not validar_cnpj(cnpj):
        return jsonify({"encontrada": False})
    clinica = encontrar_clinica_por_cnpj(cnpj)
    if not clinica:
        return jsonify({"encontrada": False})
    return jsonify({
        "encontrada": True,
        "nome": clinica.nome,
        "telefone": clinica.telefone or "",
        "cep": clinica.cep or "",
        "rua": clinica.rua or "",
        "numero": clinica.numero or "",
        "complemento": clinica.complemento or "",
        "bairro": clinica.bairro or "",
        "cidade": clinica.cidade or "",
        "uf": clinica.uf or "",
    })


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



# ---------- Ferramenta temporária: limpar base de dados (uso interno) ----------
#
# Botão de uso pessoal do Silvan para limpar dados de teste rapidamente,
# direto pela tela de login, sem precisar entrar no banco na mão. Fica
# visível em QUALQUER ambiente onde este código estiver publicado (não é
# um recurso pensado para clientes) - a única proteção é exigir que a
# pessoa digite a frase de confirmação abaixo antes de apagar qualquer
# coisa, já que é um endpoint acessível sem estar logado.
#
# ATENÇÃO: remover esta rota, o link em auth/login.html e este comentário
# assim que a limpeza de dados de teste não for mais necessária - não é
# para ficar em produção a longo prazo.
FRASE_CONFIRMACAO_LIMPAR_BASE = "APAGAR TUDO"

# Tabelas que NÃO são "dados de teste" e por isso não são apagadas:
# histórico de deploy (metadado de infraestrutura) e a config global da
# plataforma (configuração única, não dado de clínica/paciente).
TABELAS_PRESERVADAS_LIMPAR_BASE = {"historico_deploy", "plataforma_config"}


@auth_bp.route("/dev/limpar-base", methods=["GET", "POST"])
def dev_limpar_base():
    erro = None
    if request.method == "POST":
        confirmacao = request.form.get("confirmacao", "").strip()
        if confirmacao != FRASE_CONFIRMACAO_LIMPAR_BASE:
            erro = f'Frase incorreta. Digite exatamente "{FRASE_CONFIRMACAO_LIMPAR_BASE}" para confirmar.'
        else:
            # Apaga na ordem inversa de dependência (tabelas "filhas" antes
            # das "pai") para não esbarrar em restrições de chave
            # estrangeira, sem precisar listar cada model manualmente -
            # assim continua funcionando mesmo se novos models forem
            # adicionados no futuro.
            for tabela in reversed(db.metadata.sorted_tables):
                if tabela.name in TABELAS_PRESERVADAS_LIMPAR_BASE:
                    continue
                if tabela.name == "usuarios":
                    # Preserva a(s) conta(s) do DONO da plataforma - sem
                    # isso, a limpeza apagava a credencial do dono junto e
                    # ninguém conseguia mais entrar no painel dele (o dono
                    # não é recriado pelo cadastro público nem depende de
                    # empresa/filial, então preservar a linha é seguro).
                    db.session.execute(tabela.delete().where(tabela.c.tipo != "dono"))
                    continue
                db.session.execute(tabela.delete())

            # Garantia extra: se por qualquer motivo a base ficou SEM a
            # conta do dono (ex.: uma limpeza feita por versões antigas,
            # que apagavam o dono junto), recria a conta padrão - senão
            # ninguém consegue mais entrar no painel da plataforma.
            if not Usuario.query.filter_by(tipo="dono").first():
                dono = Usuario(nome="Dono da Plataforma", email="dono@plataforma.com", tipo="dono")
                dono.set_senha("123456")
                db.session.add(dono)

            db.session.commit()
            flash(
                "Base de dados limpa com sucesso (preservados: conta do dono da plataforma, "
                "configuração da plataforma e histórico de deploy). Use \"Criar minha clínica\" "
                "para começar de novo, ou rode o seed.py.",
                "success",
            )
            return redirect(url_for("auth.login"))

    return render_template("auth/dev_limpar_base.html", erro=erro, frase=FRASE_CONFIRMACAO_LIMPAR_BASE)
