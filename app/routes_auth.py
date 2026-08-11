from datetime import date, datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy import or_

from app.extensions import db
from app.models import Usuario, Empresa, Clinica, ClinicaMembro, Paciente, PlataformaConfig, normalizar_telefone

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
    """Login do paciente: sem senha, feito só com telefone + data de
    nascimento (a secretária/médico cadastra o telefone e a data de
    nascimento na hora de criar o paciente — ver medico.pacientes_novo).

    Telefone não é mais único globalmente (ver app/models.py, classe
    Usuario): a mesma pessoa pode ser paciente em clínicas diferentes com
    o mesmo telefone, cada uma com sua própria conta (Usuario). Por isso
    esse login tem duas etapas quando telefone+data de nascimento batem em
    mais de uma clínica ao mesmo tempo - a segunda etapa deixa o paciente
    escolher qual clínica quer acessar (ver
    auth/login_paciente_escolher_clinica.html)."""
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    # Etapa 2: o paciente já viu a lista de clínicas (etapa 1 encontrou
    # mais de uma conta válida) e está escolhendo qual delas acessar. Não
    # confia no valor bruto vindo do form - só aceita um id que a própria
    # etapa 1 já validou e guardou na sessão do servidor.
    if request.method == "POST" and "usuario_id_escolhido" in request.form:
        candidatos_ids = session.get("login_paciente_candidatos") or []
        try:
            escolhido_id = int(request.form.get("usuario_id_escolhido", ""))
        except ValueError:
            escolhido_id = None

        session.pop("login_paciente_candidatos", None)
        if escolhido_id in candidatos_ids:
            usuario = Usuario.query.get(escolhido_id)
            if usuario and usuario.ativo:
                session.pop("clinica_id", None)
                login_user(usuario)
                return redirect(url_for("index"))

        flash("Seleção inválida — faça login novamente.", "danger")
        return redirect(url_for("auth.login_paciente"))

    if request.method == "POST":
        telefone = normalizar_telefone(request.form.get("telefone", ""))
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

        candidatos = []
        if telefone and data_nascimento:
            for usuario in Usuario.query.filter_by(telefone=telefone, tipo="paciente").all():
                if (
                    usuario.ativo
                    and usuario.paciente
                    and usuario.paciente.data_nascimento == data_nascimento
                ):
                    candidatos.append(usuario)

        if len(candidatos) == 1:
            session.pop("clinica_id", None)
            login_user(candidatos[0])
            return redirect(url_for("index"))

        if len(candidatos) > 1:
            session["login_paciente_candidatos"] = [u.id for u in candidatos]
            return render_template("auth/login_paciente_escolher_clinica.html", candidatos=candidatos)

        flash("Telefone ou data de nascimento incorretos.", "danger")

    return render_template("auth/login_paciente.html")


@auth_bp.route("/logout")
@login_required
def logout():
    session.pop("clinica_id", None)
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

    - "empresa" (padrão/original): quem se cadastra cria uma empresa nova
      na plataforma, informando só o nome DELA — nenhuma filial é criada
      aqui. O cadastro do(s) local(is) de atendimento de verdade (nome,
      endereço, CNPJ, telefone) é feito depois, ao entrar no app, na
      etapa "Meus Locais de Atendimento" do assistente de configuração
      inicial (ver medico.onboarding e medico.filiais_nova) — até lá, o
      vínculo da pessoa com a empresa é feito por
      Usuario.empresa_fundadora_id (ver app/models.py e
      empresas_do_usuario() em app/clinica_utils.py), não por
      ClinicaMembro. Nem toda clínica tem secretária, então a pessoa
      escolhe se é médico ou secretário(a) — mas por ser quem está
      criando a empresa, recebe todas as permissões administrativas.

    - "independente": pensado para o médico que atende por conta própria,
      sem uma empresa/clínica de verdade por trás. Não pede nome de
      empresa nem de filial — esses são gerados automaticamente a partir
      do nome do médico, ficando "invisíveis" pra ele (ele nunca vê as
      palavras "empresa"/"filial" no cadastro). Diferente do modo
      "empresa" acima, aqui a primeira filial JÁ vem criada (com o
      ClinicaMembro correspondente) — é a promessa explícita dessa
      opção, pra pessoa já cair pronta pra usar. Por baixo, a estrutura é
      exatamente a mesma (Empresa -> Clinica -> ClinicaMembro): se esse
      médico passar a atender em mais de um local, ele cadastra os
      próximos em "Meus locais de atendimento" (medico.filiais_nova), que
      viram novas filiais dentro dessa mesma empresa oculta. papel é
      sempre "medico" nesse modo.

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

        if independente:
            papel = "medico"
            # Nomes gerados a partir do nome do médico — ele não preenche
            # nem vê "empresa"/"filial" nessa tela. Ficam só como
            # identificação interna dos registros no banco.
            nome_empresa = nome
            nome_filial = "Consultório"
        else:
            nome_empresa = request.form.get("nome_empresa", "").strip()
            papel = request.form.get("papel", "secretaria")

        if not nome or not email or not senha:
            flash("Preencha todos os campos.", "danger")
            return render_template("auth/cadastro.html")

        if not independente and not nome_empresa:
            flash("Preencha todos os campos.", "danger")
            return render_template("auth/cadastro.html")

        if papel not in ("medico", "secretaria"):
            flash("Escolha se você é médico(a) ou secretário(a).", "danger")
            return render_template("auth/cadastro.html")

        if len(senha) < 6:
            flash("A senha deve ter pelo menos 6 caracteres.", "danger")
            return render_template("auth/cadastro.html")

        if Usuario.query.filter_by(email=email).first():
            flash("Já existe uma conta com esse e-mail. Faça login ou use outro e-mail.", "danger")
            return render_template("auth/cadastro.html")

        trial_dias = PlataformaConfig.obter().trial_dias
        empresa = Empresa(
            nome=nome_empresa,
            email_contato=email,
            status="trial",
            data_vencimento=date.today() + timedelta(days=trial_dias),
        )
        db.session.add(empresa)
        db.session.flush()

        usuario = Usuario(nome=nome, email=email, tipo=papel)
        usuario.set_senha(senha)
        # Quem cria a empresa é a administradora inicial — recebe todas as
        # permissões administrativas independentemente de ser médico(a) ou
        # secretário(a), já que a clínica pode não ter uma secretária. Isso
        # inclui perm_filiais, essencial pro médico independente poder
        # cadastrar novos locais de atendimento sozinho depois.
        usuario.conceder_todas_permissoes()

        if independente:
            # Modo "independente" continua vindo com o primeiro local já
            # pronto pra usar (promessa explícita dessa opção - a pessoa
            # nunca vê "filial" nessa tela) - diferente do modo "empresa"
            # logo abaixo.
            filial = Clinica(empresa_id=empresa.id, nome=nome_filial, email_contato=email)
            db.session.add(filial)
            db.session.flush()
            db.session.add(usuario)
            db.session.flush()
            vinculo = ClinicaMembro(clinica_id=filial.id, usuario_id=usuario.id, ativo=True)
            db.session.add(vinculo)
            db.session.commit()
            login_user(usuario)
            session["clinica_id"] = filial.id
            flash(
                f"Conta criada com sucesso, {usuario.nome}! "
                "Vamos te ajudar a deixar tudo pronto para uso.",
                "success",
            )
        else:
            # Modo "empresa": o cadastro público cria só a EMPRESA - nenhuma
            # filial/ClinicaMembro ainda. A pessoa cadastra o(s) local(is)
            # de atendimento de verdade depois, ao entrar no app, em "Meus
            # Locais de Atendimento" (etapa do assistente de configuração
            # inicial) - até lá, o vínculo com a empresa é feito por
            # empresa_fundadora_id (ver Usuario em app/models.py e
            # empresas_do_usuario() em app/clinica_utils.py), não por
            # ClinicaMembro.
            usuario.empresa_fundadora_id = empresa.id
            db.session.add(usuario)
            db.session.commit()
            login_user(usuario)
            flash(
                f"Empresa '{empresa.nome}' criada com sucesso! "
                "Vamos te ajudar a deixar tudo pronto para uso — o primeiro passo é cadastrar "
                "seu local de atendimento.",
                "success",
            )
        return redirect(url_for("medico.onboarding"))

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


@auth_bp.route("/paciente/cadastro/<codigo>", methods=["GET", "POST"])
def cadastro_paciente(codigo):
    """Auto-cadastro do paciente pelo app, usando o link/código público de
    uma clínica específica (gerado em "Dados Cadastrais" — ver
    medico.clinica_configuracoes). O cadastro entra com
    status_cadastro="pendente": o paciente já consegue entrar no sistema
    (mesmo login por telefone + data de nascimento de sempre), mas só
    consegue solicitar agendamento depois que a equipe aceitar o cadastro
    (ver medico.pacientes_solicitacoes)."""
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    clinica = Clinica.query.filter_by(codigo_cadastro_paciente=codigo).first()
    if not clinica:
        flash("Link de cadastro inválido ou expirado. Confira o link com a clínica.", "danger")
        return render_template("auth/cadastro_paciente_invalido.html")

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        cpf = request.form.get("cpf", "").strip()
        email = request.form.get("email", "").strip().lower()
        telefone_digitado = request.form.get("telefone", "").strip()
        data_nascimento_str = request.form.get("data_nascimento", "").strip()
        telefone = normalizar_telefone(telefone_digitado)

        if not nome or not cpf or not telefone or not data_nascimento_str:
            flash("Nome, CPF, telefone e data de nascimento são obrigatórios.", "danger")
            return render_template("auth/cadastro_paciente.html", clinica=clinica)

        data_nascimento = _parse_data_nascimento(data_nascimento_str)
        if not data_nascimento:
            flash("Data de nascimento inválida — use o formato DD/MM/AAAA.", "danger")
            return render_template("auth/cadastro_paciente.html", clinica=clinica)

        # Telefone não é único por pessoa - é normal uma família inteira
        # (pais e filhos, por exemplo) compartilhar o mesmo telefone de
        # contato, cada um com seu próprio cadastro de paciente. Por isso
        # o login de paciente (auth.login_paciente) já identifica a conta
        # certa por telefone + data de nascimento, não só telefone. Aqui
        # só bloqueamos se as duas coisas baterem ao mesmo tempo - aí sim
        # é a mesma pessoa tentando se cadastrar de novo nesta clínica; a
        # unicidade que de fato importa (e é garantida pelo banco) é por
        # CPF, verificada logo abaixo.
        # O paciente é cadastrado na EMPRESA dona da filial do link - o
        # link/código continua sendo por filial (é como a clínica divulga),
        # mas o cadastro resultante vale para a empresa inteira, e a
        # filial de cada atendimento é escolhida na hora de agendar.
        filiais_da_empresa = db.session.query(Clinica.id).filter(Clinica.empresa_id == clinica.empresa_id)
        filtro_empresa = or_(
            Paciente.empresa_id == clinica.empresa_id,
            Paciente.clinica_id.in_(filiais_da_empresa),
        )
        if (
            Paciente.query.join(Usuario, Paciente.usuario_id == Usuario.id)
            .filter(
                filtro_empresa,
                Usuario.telefone == telefone,
                Paciente.data_nascimento == data_nascimento,
            )
            .first()
        ):
            flash(
                "Já existe um cadastro com esse telefone e data de nascimento nesta clínica. "
                "Se já é paciente aqui, use a tela de login normal.",
                "danger",
            )
            return render_template("auth/cadastro_paciente.html", clinica=clinica)

        if email and (
            Paciente.query.join(Usuario, Paciente.usuario_id == Usuario.id)
            .filter(filtro_empresa, Usuario.email == email)
            .first()
        ):
            flash("Já existe um cadastro com esse e-mail nesta clínica.", "danger")
            return render_template("auth/cadastro_paciente.html", clinica=clinica)

        if Paciente.query.filter(filtro_empresa, Paciente.cpf == cpf).first():
            flash("Já existe um paciente com esse CPF cadastrado nesta clínica.", "danger")
            return render_template("auth/cadastro_paciente.html", clinica=clinica)

        usuario = Usuario(nome=nome, email=email or None, telefone=telefone, tipo="paciente")
        db.session.add(usuario)
        db.session.flush()

        paciente = Paciente(
            empresa_id=clinica.empresa_id,
            usuario_id=usuario.id,
            nome=nome,
            cpf=cpf,
            data_nascimento=data_nascimento,
            email=email or None,
            telefone=telefone,
            status_cadastro="pendente",
        )
        # Endereço (preenchido via busca de CEP no formulário) e contato de
        # emergência - mesmos campos que a equipe preenche em
        # medico.pacientes_novo, mas aqui opcionais, já que quem está se
        # autocadastrando pode preferir completar depois.
        paciente.cep = request.form.get("cep", "").strip()
        paciente.rua = request.form.get("rua", "").strip()
        paciente.numero = request.form.get("numero", "").strip()
        paciente.complemento = request.form.get("complemento", "").strip()
        paciente.bairro = request.form.get("bairro", "").strip()
        paciente.cidade = request.form.get("cidade", "").strip()
        paciente.uf = request.form.get("uf", "").strip().upper() or None
        paciente.contato_emergencia_nome = request.form.get("contato_emergencia_nome", "").strip()
        paciente.contato_emergencia_telefone = request.form.get("contato_emergencia_telefone", "").strip()
        db.session.add(paciente)
        db.session.commit()

        login_user(usuario)
        flash(
            "Cadastro enviado! Assim que a clínica aceitar seu cadastro, você já vai poder "
            "solicitar agendamento de exames por aqui.",
            "success",
        )
        return redirect(url_for("paciente.dashboard"))

    return render_template("auth/cadastro_paciente.html", clinica=clinica)



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
                db.session.execute(tabela.delete())
            db.session.commit()
            flash(
                "Base de dados limpa com sucesso (exceto configuração da plataforma e histórico de "
                "deploy). Use \"Criar minha clínica\" para começar de novo, ou rode o seed.py.",
                "success",
            )
            return redirect(url_for("auth.login"))

    return render_template("auth/dev_limpar_base.html", erro=erro, frase=FRASE_CONFIRMACAO_LIMPAR_BASE)
