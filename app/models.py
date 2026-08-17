import re
import secrets
from datetime import datetime, date, timedelta
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db


def normalizar_telefone(telefone):
    """Mantém só os dígitos do telefone (ex.: '(27) 99999-9999' -> '27999999999'),
    para que o número usado no login do paciente seja comparado sempre da mesma
    forma, independente de como foi digitado no cadastro ou no login."""
    if not telefone:
        return None
    digitos = re.sub(r"\D", "", telefone)
    return digitos or None


class PlataformaConfig(db.Model):
    """Configurações globais da plataforma, controladas pelo dono.
    Sempre existe (no máximo) uma única linha nesta tabela — use
    `PlataformaConfig.obter()` para ler/criar essa linha com segurança."""
    __tablename__ = "plataforma_config"

    id = db.Column(db.Integer, primary_key=True)
    # Quantos dias de trial uma clínica nova recebe ao se cadastrar.
    trial_dias = db.Column(db.Integer, nullable=False, default=14)

    @classmethod
    def obter(cls):
        config = cls.query.first()
        if not config:
            config = cls(trial_dias=14)
            db.session.add(config)
            db.session.commit()
        return config


class Usuario(db.Model, UserMixin):
    __tablename__ = "usuarios"
    # E-mail é único globalmente só para quem não é paciente (dono/
    # secretária/médico usam e-mail como credencial de login, inclusive
    # entre clínicas diferentes via ClinicaMembro). Paciente não usa e-mail
    # pra entrar (ver `telefone` abaixo) e pode legitimamente ser a mesma
    # pessoa cadastrada em clínicas diferentes com o mesmo e-mail — por
    # isso a unicidade de e-mail de paciente NÃO é garantida aqui, só a de
    # quem não é paciente (índice parcial, único que funciona igual em
    # Postgres e SQLite).
    __table_args__ = (
        db.Index(
            "uq_usuarios_email_nao_paciente", "email", unique=True,
            postgresql_where=db.text("tipo <> 'paciente'"),
            sqlite_where=db.text("tipo <> 'paciente'"),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), nullable=False)
    # E-mail/senha só são obrigatórios para dono/secretária/médico — o
    # paciente entra pelo telefone (ver `telefone` abaixo) + data de
    # nascimento, sem precisar de senha (ver rota auth.login_paciente).
    # Unicidade real: ver __table_args__ acima (índice parcial).
    email = db.Column(db.String(150), nullable=True)
    senha_hash = db.Column(db.String(255), nullable=True)
    # Identificador de login do paciente (só ele usa este campo), guardado
    # sempre só com dígitos (ver `normalizar_telefone`). NÃO é globalmente
    # único: a mesma pessoa pode ser paciente em clínicas diferentes com o
    # mesmo telefone — cada clínica tem sua própria conta (Usuario) pra
    # aquele telefone, e a unicidade de fato é garantida por clínica na
    # aplicação (ver app/routes_medico.py:pacientes_novo e
    # app/routes_auth.py:cadastro_paciente). No login
    # (auth.login_paciente), se telefone + data de nascimento baterem em
    # mais de uma clínica, o paciente escolhe qual clínica quer acessar.
    telefone = db.Column(db.String(30), nullable=True, index=True)
    # tipo: 'dono', 'secretaria', 'medico' ou 'paciente'
    tipo = db.Column(db.String(20), nullable=False)
    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    # Código mestre do MÉDICO (ex.: "MED-7K2F9") - identidade portátil do
    # médico na plataforma: a clínica que quiser contar com ele digita
    # este código em "Vincular médico por código" (tela Equipe), o que cria
    # um CONVITE (ver ConviteVinculo) que o médico aceita ou recusa no
    # próprio painel. O código NÃO é senha: sozinho ele não dá acesso a
    # nada - todo vínculo passa pelo aceite do médico. Só faz sentido para
    # tipo == 'medico'; gerado na criação da conta (e, para contas antigas,
    # na primeira visita ao painel ou pela migração). O médico pode
    # regenerar o código quando quiser (ex.: se vazou) - convites já
    # criados não são afetados, só os futuros usos do código antigo.
    codigo_mestre = db.Column(db.String(20), unique=True, nullable=True)

    # Permissões administrativas (só fazem sentido para médico/secretária).
    # Como nem toda clínica tem uma secretária, essas permissões não são
    # amarradas ao papel ('tipo') — quem administra a equipe decide quais
    # telas administrativas cada pessoa pode acessar, seja ela médico ou
    # secretária.
    perm_pacientes = db.Column(db.Boolean, nullable=False, default=False)
    perm_equipe = db.Column(db.Boolean, nullable=False, default=False)
    perm_filiais = db.Column(db.Boolean, nullable=False, default=False)
    perm_dados_clinica = db.Column(db.Boolean, nullable=False, default=False)

    # CPF e endereço PESSOAL de quem trabalha na plataforma (dono/médico/
    # secretária) - coletados no cadastro (auth.cadastro) e também no
    # cadastro/edição de membros da equipe (medico.equipe_novo/
    # equipe_editar). Endereço da PESSOA, não da clínica (ver Clinica.cep
    # etc. para o endereço do local de atendimento).
    cpf = db.Column(db.String(20))
    cep = db.Column(db.String(10))
    rua = db.Column(db.String(200))
    numero = db.Column(db.String(20))
    complemento = db.Column(db.String(100))
    bairro = db.Column(db.String(100))
    cidade = db.Column(db.String(100))
    uf = db.Column(db.String(2))

    # CRM (registro no Conselho Regional de Medicina) - só faz sentido
    # para tipo == "medico". Dois campos porque o CRM é emitido por
    # estado (ex.: "12345" + "ES") - o número sozinho não identifica o
    # médico sem o estado de emissão.
    crm_numero = db.Column(db.String(20))
    crm_uf = db.Column(db.String(2))

    # CONTA ÚNICA do paciente: uma pessoa (um Usuario) pode ter VÁRIOS
    # cadastros de paciente - um por empresa que frequenta (ver
    # encontrar_conta_paciente). O que é global é a PESSOA e o login dela;
    # os dados clínicos continuam separados por empresa (cada clínica só
    # vê o cadastro dela - LGPD). A propriedade `paciente` (abaixo) mantém
    # a compatibilidade com o código que assume um cadastro só.
    pacientes = db.relationship(
        "Paciente", back_populates="usuario", order_by="Paciente.id",
        foreign_keys="Paciente.usuario_id",
    )
    # Exames e agendamentos pelos quais este usuário é o médico responsável
    # (só se aplica quando tipo == 'medico').
    exames_medico = db.relationship("Exame", back_populates="medico", foreign_keys="Exame.medico_id")
    agendamentos_medico = db.relationship("Agendamento", back_populates="medico", foreign_keys="Agendamento.medico_id")

    def set_senha(self, senha):
        self.senha_hash = generate_password_hash(senha)

    def checar_senha(self, senha):
        if not self.senha_hash:
            return False
        return check_password_hash(self.senha_hash, senha)

    @property
    def paciente(self):
        """O cadastro de paciente EM USO nesta sessão. Com a conta única,
        a mesma conta pode ter um cadastro por empresa: o login guarda na
        sessão qual cadastro a pessoa escolheu (session["paciente_id"], ver
        auth.login_paciente) e esta propriedade o devolve - toda a área do
        paciente (rotas e templates) usa current_user.paciente, então a
        escolha vale em tudo automaticamente. Fora de uma requisição (ou
        sem escolha na sessão), cai no primeiro cadastro da conta."""
        try:
            from flask import has_request_context, session
            if has_request_context():
                paciente_id = session.get("paciente_id")
                if paciente_id:
                    for p in self.pacientes:
                        if p.id == paciente_id:
                            return p
        except Exception:
            pass
        return self.pacientes[0] if self.pacientes else None

    @property
    def tem_senha(self):
        """Pacientes cadastrados a partir da mudança para login por telefone
        não têm senha nenhuma — usado para esconder a opção "Trocar senha"
        para eles."""
        return bool(self.senha_hash)

    def definir_permissoes_padrao(self):
        """Preenche as permissões administrativas padrão de acordo com o
        papel — usado só como sugestão inicial ao criar a conta; quem
        administra a equipe pode ajustar cada permissão individualmente
        depois (por exemplo, um médico sem secretária pode precisar de
        todas elas)."""
        administrativo = self.tipo == "secretaria"
        self.perm_pacientes = administrativo
        self.perm_equipe = administrativo
        self.perm_filiais = administrativo
        self.perm_dados_clinica = administrativo

    def conceder_todas_permissoes(self):
        self.perm_pacientes = True
        self.perm_equipe = True
        self.perm_filiais = True
        self.perm_dados_clinica = True

    @property
    def is_staff(self):
        return self.tipo in ("secretaria", "medico")

    @property
    def is_dono(self):
        return self.tipo == "dono"


def formatar_nome_proprio(nome):
    """Nome de gente com a primeira letra de cada nome em maiúscula:
    "silvan martins de oliveira" -> "Silvan Martins de Oliveira".
    Conectivos (de/da/do/das/dos/e) ficam em minúsculas, e nomes compostos
    com hífen ou apóstrofo são tratados ("anna-luiza d'ávila" ->
    "Anna-Luiza D'Ávila"). Usado nos cadastros de paciente - quem digita
    no celular quase sempre deixa tudo minúsculo."""
    nome = " ".join((nome or "").split())
    if not nome:
        return nome
    conectivos = {"de", "da", "do", "das", "dos", "e", "di", "del", "van", "von"}

    def capitalizar(palavra):
        for separador in ("-", "'"):
            if separador in palavra:
                return separador.join(capitalizar(p) for p in palavra.split(separador))
        return palavra[:1].upper() + palavra[1:] if palavra else palavra

    partes = []
    for i, palavra in enumerate(nome.split(" ")):
        minuscula = palavra.lower()
        if i > 0 and minuscula in conectivos:
            partes.append(minuscula)
        else:
            partes.append(capitalizar(minuscula))
    return " ".join(partes)


def validar_cpf(cpf):
    """True se o CPF é um número VÁLIDO de verdade (dígitos verificadores
    conferem e não é uma sequência repetida tipo 111.111.111-11). O CPF é
    o login do paciente e a chave de importação entre clínicas - um CPF
    inexistente no cadastro quebraria tudo isso."""
    digitos = re.sub(r"\D", "", cpf or "")
    if len(digitos) != 11 or digitos == digitos[0] * 11:
        return False
    for tamanho in (9, 10):
        soma = sum(int(digitos[i]) * (tamanho + 1 - i) for i in range(tamanho))
        resto = (soma * 10) % 11
        if resto == 10:
            resto = 0
        if resto != int(digitos[tamanho]):
            return False
    return True


def telefone_incompleto(telefone):
    """True se o telefone foi digitado mas ficou incompleto (nem os 10 ou
    11 dígitos de um telefone brasileiro - DDD + número) - a máscara
    "(99) 99999-9999" deixava sair, por exemplo, só "(27" sem travar o
    envio do formulário."""
    digitos = re.sub(r"\D", "", telefone or "")
    return bool(digitos) and len(digitos) not in (10, 11)


def validar_cnpj(cnpj):
    """True se o CNPJ é um número VÁLIDO de verdade (dígitos verificadores
    conferem e não é uma sequência repetida tipo 11.111.111/1111-11). É o
    identificador único da clínica - usado no cadastro público pra
    encontrar uma clínica que já exista na plataforma (ver
    encontrar_clinica_por_cnpj), então precisa ser um CNPJ que existe de
    verdade, não qualquer número digitado."""
    digitos = re.sub(r"\D", "", cnpj or "")
    if len(digitos) != 14 or digitos == digitos[0] * 14:
        return False
    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]

    def _digito_verificador(nums, pesos):
        soma = sum(int(n) * p for n, p in zip(nums, pesos))
        resto = soma % 11
        return "0" if resto < 2 else str(11 - resto)

    dv1 = _digito_verificador(digitos[:12], pesos1)
    if dv1 != digitos[12]:
        return False
    dv2 = _digito_verificador(digitos[:12] + dv1, pesos2)
    return dv2 == digitos[13]


def cep_incompleto(cep):
    """True se o CEP foi digitado mas ficou incompleto (nem todos os 8
    dígitos) - sem isso, o cadastro deixava salvar um CEP pela metade
    (ex.: "29055") com rua/bairro/cidade/UF vazios, porque a busca do
    ViaCEP só dispara com os 8 números completos e nada travava o envio
    do formulário com o restante em branco."""
    digitos = re.sub(r"\D", "", cep or "")
    return bool(digitos) and len(digitos) != 8


def encontrar_conta_paciente_por_cpf(cpf):
    """Conta única por CPF: acha a conta (Usuario) do paciente dono deste
    CPF - agora que o CPF é o login (é a identidade que não muda), ele é
    o jeito mais forte de reconhecer a pessoa nos cadastros. O CPF é
    comparado só nos dígitos (é guardado como digitado)."""
    digitos = re.sub(r"\D", "", cpf or "")
    if len(digitos) != 11:
        return None
    for p in Paciente.query.filter(Paciente.cpf.isnot(None)).all():
        if re.sub(r"\D", "", p.cpf or "") == digitos:
            usuario = p.usuario
            if usuario and usuario.ativo and usuario.tipo == "paciente":
                return usuario
    return None


def encontrar_conta_paciente(telefone, data_nascimento):
    """CONTA ÚNICA do paciente: acha a conta (Usuario) existente desta
    pessoa - identificada por telefone + data de nascimento, o mesmo par
    usado no login do paciente. Telefone sozinho NÃO identifica (uma
    família inteira pode dividir o mesmo telefone, cada um com sua data
    de nascimento). Usada pelos cadastros de paciente (equipe e
    auto-cadastro pelo link) para NÃO criar uma segunda conta quando a
    pessoa já usa o app por outra empresa: cria-se só o cadastro
    (Paciente) da nova empresa, apontando para a conta existente - assim
    o paciente loga uma vez e vê tudo que é dele, enquanto cada clínica
    continua vendo apenas o cadastro dela."""
    if not telefone or not data_nascimento:
        return None
    for usuario in Usuario.query.filter_by(telefone=telefone, tipo="paciente", ativo=True).all():
        if any(p.data_nascimento == data_nascimento for p in usuario.pacientes):
            return usuario
    return None


def gerar_codigo_mestre_medico():
    """Gera um código mestre único para um médico (ver
    Usuario.codigo_mestre). Formato "MED-XXXXX" com um alfabeto sem
    caracteres ambíguos (sem 0/O, 1/I/L) - é um código pra ser DITADO por
    telefone ou digitado da recepção, então precisa ser curto e à prova de
    confusão visual."""
    alfabeto = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
    for _ in range(20):
        codigo = "MED-" + "".join(secrets.choice(alfabeto) for _ in range(5))
        if not Usuario.query.filter_by(codigo_mestre=codigo).first():
            return codigo
    # Espaço de ~28 milhões de códigos - na prática nunca chega aqui; o
    # fallback só garante que a função sempre devolve algo único.
    return "MED-" + secrets.token_hex(5).upper()


class Grupo(db.Model):
    """Trabalho compartilhado (BBP MedIA, seção 4.2 / 5.1.4): um grupo de
    usuários — médicos e/ou administrativos — que trabalham juntos. Quem
    cria o grupo é o seu DONO (GrupoMembro.papel == "dono"); o dono pode
    conceder o papel de ADMINISTRADOR a outros membros (podem convidar e
    remover membros), mas só ele concede esse papel. Um mesmo usuário pode
    pertencer a mais de um grupo.

    Implementado como uma camada nova, adicional ao modelo de
    Empresa/Clínica já existente — esta é a primeira fatia (prova de
    conceito) da reformulação descrita no BBP; a migração completa do
    restante do sistema para este conceito é um trabalho futuro maior."""
    __tablename__ = "grupos"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), nullable=False)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    # Observação histórica: até a Fatia 4 da migração para Grupo, este
    # modelo tinha uma "clínica interna" (clinica_interna_id/
    # clinica_interna()) criada por baixo dos panos como âncora técnica,
    # porque Exame/PreparoModelo/Agendamento/PerguntaPendente/FaqItem só
    # aceitavam clinica_id (NOT NULL). Agora esses 5 modelos têm um
    # grupo_id próprio (ver mais abaixo e app/routes_grupo.py) e não
    # precisam mais dessa ponte - removida nesta fatia. Bancos já
    # existentes ficam com a coluna `grupos.clinica_interna_id` órfã (sem
    # Flask-Migrate neste projeto; ver nota de limpeza manual no relatório
    # da fatia).

    # ---------- Fatia 5: cobrança, endereço e fiscal (por Grupo) ----------
    # Antes da Fatia 5 isso vivia em Empresa (cobrança) e Clinica (endereço/
    # fiscal). Como cada Grupo já corresponde a uma filial de hoje (1 Grupo
    # por Clinica, ver Fatia 4), a cobrança deixa de ser "por empresa com
    # várias filiais" e passa a ser POR GRUPO — decisão de negócio tomada
    # nesta fatia (cada Grupo é sua própria unidade de cobrança agora).
    razao_social = db.Column(db.String(200))
    cnpj = db.Column(db.String(20))
    email_contato = db.Column(db.String(150))
    telefone = db.Column(db.String(30))
    logo_url = db.Column(db.String(300))

    cep = db.Column(db.String(10))
    rua = db.Column(db.String(200))
    numero = db.Column(db.String(20))
    complemento = db.Column(db.String(100))
    bairro = db.Column(db.String(100))
    cidade = db.Column(db.String(100))
    uf = db.Column(db.String(2))

    # status: 'trial', 'ativa', 'inadimplente', 'bloqueada' — mesmo
    # vocabulário de Empresa.status.
    status = db.Column(db.String(20), nullable=False, default="trial")
    data_vencimento = db.Column(db.Date)
    observacoes_pagamento = db.Column(db.Text)
    # Cobrança: valor mensal por médico com vínculo ativo neste Grupo (ver
    # medicos_distintos abaixo) — cada Grupo negocia o seu, controle manual
    # (sem emissão automática de fatura), igual valia para Empresa.
    valor_por_medico = db.Column(db.Numeric(10, 2))
    codigo_cadastro_paciente = db.Column(db.String(20), unique=True, nullable=True)

    inscricao_estadual = db.Column(db.String(30))
    regime_tributario = db.Column(db.String(50))
    cnae = db.Column(db.String(20))
    codigo_ibge_municipio = db.Column(db.String(10))

    fiscal_ambiente = db.Column(db.String(20), nullable=False, default="homologacao")
    fiscal_modo_simulacao = db.Column(db.Boolean, nullable=False, default=False)
    fiscal_simular_falha_conexao = db.Column(db.Boolean, nullable=False, default=False)

    fiscal_certificado_pfx = db.Column(db.LargeBinary)
    fiscal_certificado_senha_cripto = db.Column(db.LargeBinary)
    fiscal_certificado_cnpj = db.Column(db.String(20))
    fiscal_certificado_validade = db.Column(db.Date)

    fiscal_provedor_emissao = db.Column(db.String(50), nullable=False, default="nenhum")
    fiscal_provedor_token_cripto = db.Column(db.LargeBinary)

    fiscal_inscricao_municipal = db.Column(db.String(30))
    fiscal_codigo_servico = db.Column(db.String(20))
    fiscal_aliquota_iss = db.Column(db.Numeric(5, 2))
    fiscal_rps_serie = db.Column(db.String(10))
    fiscal_rps_proximo_numero = db.Column(db.Integer)

    membros = db.relationship("GrupoMembro", back_populates="grupo", order_by="GrupoMembro.id")
    convites = db.relationship("GrupoConvite", back_populates="grupo", order_by="GrupoConvite.id")

    @property
    def dono(self):
        for m in self.membros:
            if m.papel == "dono" and m.ativo:
                return m.usuario
        return None

    @property
    def bloqueada(self):
        return self.status == "bloqueada"

    @property
    def medicos_distintos(self):
        """Usuários médicos com vínculo ativo neste Grupo — usado para
        calcular a cobrança mensal (ver valor_mensal_estimado)."""
        return [m.usuario for m in self.membros if m.ativo and m.usuario.tipo == "medico"]

    @property
    def medicos_e_secretarias(self):
        """Fatia 5 (passo 4): equivalente a Clinica.medicos_e_secretarias -
        todos os membros ativos (médico ou secretária), usado por
        medico.medicos_da_clinica()/medicos_das_filiais() agora que essas
        funções recebem Grupo em vez de Clinica."""
        return [m.usuario for m in self.membros if m.ativo]

    @property
    def valor_mensal_estimado(self):
        if self.valor_por_medico is None:
            return None
        return self.valor_por_medico * len(self.medicos_distintos)

    def verificar_vencimento_trial(self):
        """Mesma regra de Empresa.verificar_vencimento_trial() — não faz
        commit, quem chamar decide quando salvar. Retorna True se mudou."""
        if self.status == "trial" and self.data_vencimento and self.data_vencimento < date.today():
            self.status = "inadimplente"
            return True
        return False

    def membro_ativo(self, usuario_id):
        for m in self.membros:
            if m.usuario_id == usuario_id and m.ativo:
                return m
        return None

    def paciente_pode_ser_removido(self, paciente_id):
        """BBP seção 7: um paciente sem nenhuma consulta agendada neste
        grupo pode ser removido normalmente; a partir da primeira consulta
        agendada por um médico deste grupo, a associação é definitiva.
        Considera médicos que JÁ foram membros do grupo (não só os ativos
        hoje), já que o vínculo do paciente nasceu de um atendimento real
        que aconteceu enquanto o médico era membro."""
        medico_ids = [m.usuario_id for m in self.membros]
        if not medico_ids:
            return True
        existe_agendamento = Agendamento.query.filter(
            Agendamento.paciente_id == paciente_id,
            Agendamento.medico_id.in_(medico_ids),
        ).first()
        return existe_agendamento is None


class GrupoMembro(db.Model):
    """Vínculo de um usuário a um grupo de trabalho, com um papel: "dono"
    (quem criou o grupo — único, imutável), "administrador" (pode
    convidar/remover membros; o dono concede este papel) ou "membro"
    (participação comum)."""
    __tablename__ = "grupo_membros"
    __table_args__ = (
        db.UniqueConstraint("grupo_id", "usuario_id", name="uq_grupo_membro"),
    )

    id = db.Column(db.Integer, primary_key=True)
    grupo_id = db.Column(db.Integer, db.ForeignKey("grupos.id"), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    papel = db.Column(db.String(20), nullable=False, default="membro")  # dono | administrador | membro
    ativo = db.Column(db.Boolean, nullable=False, default=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    grupo = db.relationship("Grupo", back_populates="membros")
    usuario = db.relationship("Usuario", foreign_keys=[usuario_id])


class GrupoConvite(db.Model):
    """Convite para um usuário (já cadastrado no sistema, tela 5.1.1)
    entrar em um grupo de trabalho — enviado por CPF (tela 5.1.5). Só vira
    membro (GrupoMembro) quando o convidado aprova (tela 5.1.6); não existe
    cadastro de equipe nem criação de conta a partir deste convite."""
    __tablename__ = "grupo_convites"

    id = db.Column(db.Integer, primary_key=True)
    grupo_id = db.Column(db.Integer, db.ForeignKey("grupos.id"), nullable=False)
    usuario_convidado_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    convidado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="pendente")  # pendente | aceito | recusado | cancelado
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    decidido_em = db.Column(db.DateTime, nullable=True)

    grupo = db.relationship("Grupo", back_populates="convites")
    usuario_convidado = db.relationship("Usuario", foreign_keys=[usuario_convidado_id])
    convidado_por = db.relationship("Usuario", foreign_keys=[convidado_por_id])


class GrupoPaciente(db.Model):
    """Associação de um paciente (cadastro único no sistema, por CPF — BBP
    seção 4.3 / tela 5.1.8) a um grupo de trabalho. Um mesmo paciente pode
    estar associado a mais de um grupo; ao cadastrar, fica disponível para
    ser importado (encontrado por CPF) por outros usuários do(s) grupo(s)
    escolhido(s).

    Regra de negócio (BBP seção 7, validada com o cliente): um paciente
    sem nenhuma consulta agendada NESTE grupo pode ser removido dele
    normalmente; a partir da primeira consulta agendada por um médico
    deste grupo, a associação passa a ser definitiva (ver
    Grupo.paciente_pode_ser_removido)."""
    __tablename__ = "grupo_pacientes"
    __table_args__ = (
        db.UniqueConstraint("grupo_id", "paciente_id", name="uq_grupo_paciente"),
    )

    id = db.Column(db.Integer, primary_key=True)
    grupo_id = db.Column(db.Integer, db.ForeignKey("grupos.id"), nullable=False)
    paciente_id = db.Column(db.Integer, db.ForeignKey("pacientes.id"), nullable=False)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    grupo = db.relationship("Grupo", foreign_keys=[grupo_id])
    paciente = db.relationship("Paciente", foreign_keys=[paciente_id])


class Paciente(db.Model):
    __tablename__ = "pacientes"
    # Fatia 5: o paciente é uma identidade ÚNICA E GLOBAL na plataforma -
    # um cadastro (Paciente) por CPF, ponto. A associação com cada
    # clínica/grupo de trabalho é feita por GrupoPaciente (ver a classe
    # acima), não mais por empresa_id/clinica_id nesta tabela.
    __table_args__ = (db.UniqueConstraint("cpf", name="uq_pacientes_cpf"),)

    id = db.Column(db.Integer, primary_key=True)
    # LEGADO (Fatia 5): antes o paciente era amarrado a uma filial e depois
    # a uma empresa no cadastro. As classes Empresa/Clinica foram removidas
    # nesta fatia - os dois campos ficam só como dado histórico/de exibição
    # em registros antigos, sem mais FK (as tabelas empresas/clinicas não
    # existem mais) - código novo não deve ler nem gravar
    # empresa_id/clinica_id em pacientes; a associação real com um grupo de
    # trabalho é sempre via GrupoPaciente (ver medico._filtro_pacientes_da_empresa,
    # Paciente.grupos abaixo, e migrar_paciente_para_grupo.py).
    empresa_id = db.Column(db.Integer, nullable=True)
    clinica_id = db.Column(db.Integer, nullable=True)
    # NÃO é mais unique por si só: com a CONTA ÚNICA do paciente, a mesma
    # conta (Usuario) pode ter agendamentos em várias clínicas/grupos - a
    # unicidade que vale agora é só o CPF (acima), globalmente. Ver
    # encontrar_conta_paciente e a migração que troca a constraint em
    # bases antigas (migrar_banco.py).
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    # Fatia 6: dono pessoal do CADASTRO (o médico/secretária que cadastrou
    # este paciente), usado só quando quem cadastrou ainda não tem Grupo
    # (conta solo) - não confundir com `usuario_id` acima, que é a conta de
    # LOGIN do próprio PACIENTE. Enquanto há Grupo, a associação real
    # continua sendo por GrupoPaciente (ver Paciente.grupos abaixo); este
    # campo só entra em jogo no fallback de escopo pessoal - ver
    # clinica_utils.filtro_escopo_atual().
    cadastrado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    nome = db.Column(db.String(150), nullable=False)
    cpf = db.Column(db.String(20), nullable=False)
    data_nascimento = db.Column(db.Date, nullable=True)
    telefone = db.Column(db.String(30))
    email = db.Column(db.String(150))
    observacoes = db.Column(db.Text)

    # Endereço (preenchido a partir da busca por CEP no cadastro).
    cep = db.Column(db.String(10))
    rua = db.Column(db.String(200))
    numero = db.Column(db.String(20))
    complemento = db.Column(db.String(100))
    bairro = db.Column(db.String(100))
    cidade = db.Column(db.String(100))
    uf = db.Column(db.String(2))

    # Contato de emergência.
    contato_emergencia_nome = db.Column(db.String(150))
    contato_emergencia_telefone = db.Column(db.String(30))

    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    # 'aprovado' (padrão — cadastro feito pela equipe já é confiável),
    # 'pendente' (paciente se cadastrou sozinho pelo app e aguarda a
    # clínica aceitar) ou 'rejeitado'.
    status_cadastro = db.Column(db.String(20), nullable=False, default="aprovado")

    usuario = db.relationship("Usuario", back_populates="pacientes", foreign_keys=[usuario_id])

    @property
    def grupos(self):
        """Grupos de trabalho aos quais este cadastro está associado (ver
        GrupoPaciente) - fonte de verdade da associação clínica/grupo desde
        a Fatia 5 (Empresa/Clinica não existem mais)."""
        return [gp.grupo for gp in GrupoPaciente.query.filter_by(paciente_id=self.id).all()]
    agendamentos = db.relationship("Agendamento", back_populates="paciente", cascade="all, delete-orphan")
    perguntas_pendentes = db.relationship("PerguntaPendente", back_populates="paciente", cascade="all, delete-orphan")
    mensagens_chat = db.relationship("ChatMensagem", back_populates="paciente", cascade="all, delete-orphan")


# Associação extra entre exame e médicos que também podem atendê-lo, além
# do médico principal (Exame.medico_id). Um exame pode ter vários médicos
# associados — ao agendar, escolhe-se qual deles vai atender.
exame_medicos_associados = db.Table(
    "exame_medicos_associados",
    db.Column("exame_id", db.Integer, db.ForeignKey("exames.id"), primary_key=True),
    db.Column("medico_id", db.Integer, db.ForeignKey("usuarios.id"), primary_key=True),
)


class Exame(db.Model):
    __tablename__ = "exames"
    __table_args__ = (
        db.UniqueConstraint("clinica_id", "nome", name="uq_clinica_exame_nome"),
        db.UniqueConstraint("grupo_id", "nome", name="uq_grupo_exame_nome"),
    )

    id = db.Column(db.Integer, primary_key=True)
    # Fatia 4 da migração para Grupo: clinica_id passou a ser um campo
    # legado/de exibição (nullable) — grupo_id é a chave real de
    # escopo/isolamento a partir daqui. Toda escrita da aplicação continua
    # preenchendo os dois (ver Clinica.grupo_pareado()), então clinica_id
    # nunca fica desatualizado para os registros legados.
    clinica_id = db.Column(db.Integer, nullable=True)
    grupo_id = db.Column(db.Integer, db.ForeignKey("grupos.id"), nullable=True)
    # DONO do exame: quem criou o cadastro. Se foi um MÉDICO, o exame é
    # dele - só ele pode editar o cadastro, e só ele pode ser associado a
    # este exame nas filiais (ver pode_ser_editado_por / a validação de
    # dono em medico.exames_por_filial_associar). NULL em cadastros
    # antigos (antes da coluna existir) - esses seguem o comportamento
    # antigo, editáveis pela equipe.
    criado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    # Médico principal do exame. Além dele, outros médicos podem ser
    # associados ao mesmo exame (ver `medicos_extra` abaixo e a
    # propriedade `medicos`) — nesse caso, ao agendar, a secretária
    # escolhe qual dos médicos associados atende.
    medico_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    # Continua sendo obrigatório escolher ALGUM médico no banco (não dá
    # pra deixar em branco), mas o cadastro do exame (medico.exames_novo)
    # é genérico e não pergunta quem faz o exame - só preenche medico_id
    # com um valor técnico/provisório pra passar pela constraint. Esta
    # flag marca se essa escolha já foi CONFIRMADA de propósito por
    # alguém, na tela "Exames por filial" - não existe "médico principal"
    # que seja assumido automaticamente só por ter cadastrado a empresa
    # ou o exame; até essa confirmação, a tela de associação mostra um
    # aviso em vez de tratar o valor técnico como se já estivesse certo.
    # O default é True (não False) porque a maioria dos caminhos que criam
    # um Exame sem passar este campo explicitamente (seed de dados de
    # teste, exames já existentes antes desta mudança) representam
    # cadastros de quando o médico ainda era escolhido de verdade no
    # próprio formulário - só o cadastro genérico atual (exames_novo)
    # passa medico_confirmado=False explicitamente, de propósito.
    medico_confirmado = db.Column(db.Boolean, nullable=False, default=True)

    # CADASTRAR um exame não cria associação nenhuma - o cadastro genérico
    # (medico.exames_novo) cria o exame como item de CATÁLOGO
    # (associado=False): ele não aparece na tela de associações, não entra
    # na agenda nem no pedido de agendamento do paciente. A associação de
    # verdade (exame + filial + médico + preço) só nasce na tela "Associar
    # exames" (medico.exames_por_filial_associar), que vira este flag pra
    # True. O default é True porque os registros já existentes (e os
    # criados pela própria tela de associação) SÃO associações reais.
    associado = db.Column(db.Boolean, nullable=False, default=True)
    nome = db.Column(db.String(150), nullable=False)
    descricao = db.Column(db.Text)

    # Modelo de preparo reaproveitável (ver PreparoModelo) — várias
    # variações do mesmo exame (ex.: diferentes substratos de um teste
    # respiratório, que precisam ser agendados em dias distintos mas usam
    # exatamente o mesmo preparo) podem apontar para o mesmo modelo, sem
    # precisar duplicar o cadastro do preparo.
    preparo_modelo_id = db.Column(db.Integer, db.ForeignKey("preparo_modelos.id"))

    # Quanto tempo (em minutos) esse exame costuma levar — informativo.
    duracao_minutos = db.Column(db.Integer)

    # Se marcado, ao agendar esse exame o sistema exige/permite indicar
    # quem vai acompanhar o paciente no dia (ver Agendamento.acompanhante_nome).
    precisa_acompanhante = db.Column(db.Boolean, nullable=False, default=False)

    grupo = db.relationship("Grupo", foreign_keys=[grupo_id])
    medico = db.relationship("Usuario", back_populates="exames_medico", foreign_keys=[medico_id])
    medicos_extra = db.relationship(
        "Usuario", secondary=exame_medicos_associados,
        backref=db.backref("exames_associados_extra", lazy="dynamic"),
    )
    preparo_modelo = db.relationship("PreparoModelo", back_populates="exames")
    agendamentos = db.relationship("Agendamento", back_populates="exame")
    faqs = db.relationship("FaqItem", back_populates="exame", cascade="all, delete-orphan")

    @property
    def preparo(self):
        """Atalho de compatibilidade: o preparo efetivo do exame é o do
        modelo de preparo vinculado a ele."""
        return self.preparo_modelo

    @property
    def medicos(self):
        """Todos os médicos que podem atender este exame: o médico
        principal mais os médicos associados adicionalmente (sem
        duplicar, e preservando o principal primeiro na lista)."""
        vistos = {self.medico_id}
        lista = [self.medico] if self.medico else []
        for m in self.medicos_extra:
            if m.id not in vistos:
                vistos.add(m.id)
                lista.append(m)
        return lista

    def medico_pode_atender(self, medico_id):
        return medico_id == self.medico_id or any(m.id == medico_id for m in self.medicos_extra)

    @property
    def criado_por(self):
        return Usuario.query.get(self.criado_por_id) if self.criado_por_id else None

    @property
    def dono_medico(self):
        """O médico DONO do exame (quem o criou), ou None se o exame não
        tem dono médico registrado (cadastro antigo, ou criado por uma
        secretária) - nesse caso vale o comportamento antigo."""
        criador = self.criado_por
        return criador if criador and criador.tipo == "medico" else None

    def pode_ser_editado_por(self, usuario):
        """Exame com dono médico só é editado POR ELE. Sem dono médico
        (legado/criado pela secretária), a equipe segue editando."""
        dono = self.dono_medico
        return dono is None or usuario.id == dono.id


class Medicamento(db.Model):
    """Catálogo de medicamentos/classes que costumam precisar ser
    suspensos antes de um exame (ex.: 'Ozempic/Mounjaro/Trulicity ou
    similares'). É compartilhado por toda a plataforma — qualquer clínica
    pode usar ou adicionar um medicamento novo ao catálogo, evitando
    recadastrar o mesmo prazo de suspensão em cada empresa."""
    __tablename__ = "medicamentos"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(200), nullable=False, unique=True)
    # Prazo padrão sugerido (em dias antes do exame) ao adicionar este
    # medicamento a um modelo de preparo — cada preparo pode usar um prazo
    # diferente do padrão, se o protocolo da clínica exigir.
    dias_padrao_suspensao = db.Column(db.Integer, nullable=False, default=0)
    # Classe/categoria do medicamento (ex.: "medicamento antiplaquetário",
    # "medicamento anticoagulante") — opcional, só para agrupar/exibir e
    # ajudar o reconhecimento no chat quando o paciente pergunta pela classe
    # em vez do nome comercial (ex.: "posso tomar meu anticoagulante?").
    categoria = db.Column(db.String(150))
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)


class PreparoModelo(db.Model):
    """Um modelo de preparo reaproveitável, pertencente a uma filial. Pode
    ser usado por vários exames ao mesmo tempo (ex.: os 3 substratos do
    teste de hidrogênio expirado, que usam o mesmo preparo mas precisam de
    agendamentos em dias diferentes) — evita recadastrar o mesmo texto em
    cada exame."""
    __tablename__ = "preparo_modelos"
    __table_args__ = (
        db.UniqueConstraint("clinica_id", "nome", name="uq_clinica_preparo_modelo_nome"),
        db.UniqueConstraint("grupo_id", "nome", name="uq_grupo_preparo_modelo_nome"),
    )

    id = db.Column(db.Integer, primary_key=True)
    # Fatia 4: clinica_id vira legado/exibição (nullable); grupo_id é a
    # chave real de escopo — ver mesmo comentário em Exame.clinica_id.
    clinica_id = db.Column(db.Integer, nullable=True)
    grupo_id = db.Column(db.Integer, db.ForeignKey("grupos.id"), nullable=True)
    # DONO do modelo: quem o criou. Se foi um MÉDICO, só ele edita/remove
    # (conteúdo clínico é do médico). NULL em modelos antigos - esses
    # seguem editáveis pela equipe (comportamento antigo).
    criado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    nome = db.Column(db.String(150), nullable=False)
    # Instruções gerais em texto livre (dieta por fase, receituário, modo
    # de preparo, observações finais etc.) — continua sendo o "guarda-tudo"
    # para o que não tem campo estruturado próprio.
    instrucoes = db.Column(db.Text, nullable=False, default="")
    # Observações sobre medicamentos que NÃO precisam ser suspensos (ex.:
    # "não é necessário suspender o AAS, Somalgin, Aspirina").
    observacoes_medicamentos = db.Column(db.Text)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    grupo = db.relationship("Grupo", foreign_keys=[grupo_id])
    exames = db.relationship("Exame", back_populates="preparo_modelo")
    cortes = db.relationship(
        "PreparoCorte", back_populates="preparo_modelo", cascade="all, delete-orphan",
        order_by="PreparoCorte.horas_antes.desc()",
    )
    medicamentos_suspensos = db.relationship(
        "PreparoMedicamentoSuspenso", back_populates="preparo_modelo", cascade="all, delete-orphan",
        order_by="PreparoMedicamentoSuspenso.dias_antes.desc()",
    )
    informacoes_gerais = db.relationship(
        "PreparoInfoGeral", back_populates="preparo_modelo", cascade="all, delete-orphan",
        order_by="PreparoInfoGeral.id",
    )
    alimentos = db.relationship(
        "PreparoAlimento", back_populates="preparo_modelo", cascade="all, delete-orphan",
        order_by="PreparoAlimento.nome",
    )
    exames_anteriores_proibidos = db.relationship(
        "PreparoExameAnterior", back_populates="preparo_modelo", cascade="all, delete-orphan",
        order_by="PreparoExameAnterior.nome",
    )
    medicamentos_mantidos = db.relationship(
        "PreparoMedicamentoMantido", back_populates="preparo_modelo", cascade="all, delete-orphan",
        order_by="PreparoMedicamentoMantido.nome",
    )


class PreparoCorte(db.Model):
    """Um corte de alimentação/líquido do preparo, expresso em horas antes
    do horário marcado do exame (e não um horário fixo do relógio) — assim
    o alerta mostrado ao paciente se ajusta automaticamente ao horário do
    agendamento (ex.: 'não coma nada sólido a partir de 12 horas antes do
    seu exame')."""
    __tablename__ = "preparo_cortes"

    id = db.Column(db.Integer, primary_key=True)
    preparo_modelo_id = db.Column(db.Integer, db.ForeignKey("preparo_modelos.id"), nullable=False)
    descricao = db.Column(db.String(150), nullable=False)
    horas_antes = db.Column(db.Integer, nullable=False)

    preparo_modelo = db.relationship("PreparoModelo", back_populates="cortes")

    def limite(self, data_hora_exame):
        """Data/hora até quando é permitido, calculada a partir do
        horário marcado do exame — é isso que faz o alerta se ajustar
        automaticamente ao horário de cada agendamento."""
        return data_hora_exame - timedelta(hours=self.horas_antes)


class PreparoInfoGeral(db.Model):
    """Um item de informação genérica do preparo — regras avulsas que não
    são nem um corte de alimentação/líquido nem um medicamento a suspender
    (ex.: 'não utilizar enxaguante bucal com álcool no dia do exame', 'não
    fumar ou mascar chiclete antes do exame'). Mostradas ao paciente como
    uma lista, à parte do texto livre de instruções gerais.

    Opcionalmente pode ter um prazo calculado a partir do horário do
    exame — igual aos cortes, mas usado para avisos que não são
    exatamente um "corte de alimentação" (ex.: 'tomar o Manitol 4 horas
    antes do exame') ou que têm um horário fixo do relógio num dia
    relativo ao exame, em vez de um número de horas antes (ex.: 'pode
    comer até as 20:00 do dia anterior ao exame' -> dias_antes=1,
    hora_exata=20:00). Só um dos dois estilos de prazo deve ser
    preenchido por item: ou `horas_antes` sozinho, ou `dias_antes` +
    `hora_exata` juntos."""
    __tablename__ = "preparo_infos_gerais"

    id = db.Column(db.Integer, primary_key=True)
    preparo_modelo_id = db.Column(db.Integer, db.ForeignKey("preparo_modelos.id"), nullable=False)
    texto = db.Column(db.String(500), nullable=False)
    horas_antes = db.Column(db.Integer)
    dias_antes = db.Column(db.Integer)
    hora_exata = db.Column(db.Time)

    preparo_modelo = db.relationship("PreparoModelo", back_populates="informacoes_gerais")

    def limite(self, data_hora_exame):
        """Data/hora calculada do prazo deste aviso, ou None quando o item
        não tem um prazo calculável (a maioria dos avisos gerais é só um
        texto solto, sem data associada)."""
        if self.horas_antes is not None:
            return data_hora_exame - timedelta(hours=self.horas_antes)
        if self.dias_antes is not None and self.hora_exata is not None:
            dia = (data_hora_exame - timedelta(days=self.dias_antes)).date()
            return datetime.combine(dia, self.hora_exata)
        return None


class PreparoAlimento(db.Model):
    """Um alimento específico do preparo, marcado como permitido (sugestão
    de consumo) ou proibido. Usado tanto para exibir ao paciente quanto
    para o chat responder automaticamente perguntas do tipo 'posso comer
    X?' sem precisar de uma FAQ cadastrada manualmente para cada
    alimento."""
    __tablename__ = "preparo_alimentos"

    id = db.Column(db.Integer, primary_key=True)
    preparo_modelo_id = db.Column(db.Integer, db.ForeignKey("preparo_modelos.id"), nullable=False)
    nome = db.Column(db.String(150), nullable=False)
    permitido = db.Column(db.Boolean, nullable=False, default=False)
    # Só fazem sentido quando permitido=False — prazo a partir de quando o
    # alimento passa a ser proibido, em horas OU em dias antes do exame
    # (nunca os dois ao mesmo tempo). Pode ficar tudo em branco quando o
    # alimento é proibido durante todo o preparo, sem um prazo específico.
    horas_antes = db.Column(db.Integer)
    dias_antes = db.Column(db.Integer)

    preparo_modelo = db.relationship("PreparoModelo", back_populates="alimentos")

    def limite(self, data_hora_exame):
        if self.horas_antes is not None:
            return data_hora_exame - timedelta(hours=self.horas_antes)
        if self.dias_antes is not None:
            return (data_hora_exame - timedelta(days=self.dias_antes)).date()
        return None

    def limite_formatado(self, data_hora_exame):
        """Mesma data/hora de `limite()`, já formatada para exibição —
        evita espalhar a lógica de "horas usa hora cheia, dias usa só a
        data" em todo template/rota que precisa mostrar esse prazo."""
        limite = self.limite(data_hora_exame)
        if limite is None:
            return None
        if self.horas_antes is not None:
            return limite.strftime("%d/%m/%Y às %H:%M")
        return limite.strftime("%d/%m/%Y")


class PreparoExameAnterior(db.Model):
    """Um exame/procedimento que o paciente NÃO deve ter feito num período
    antes deste exame (ex.: 'não deve ter feito colonoscopia ou lavagens
    intestinais nas 4 semanas anteriores'). Diferente do medicamento a
    suspender (que é uma ação a tomar dali para frente), aqui a regra é
    sobre algo que já pode ter acontecido no passado e invalidaria o
    preparo/resultado do exame."""
    __tablename__ = "preparo_exames_anteriores"

    id = db.Column(db.Integer, primary_key=True)
    preparo_modelo_id = db.Column(db.Integer, db.ForeignKey("preparo_modelos.id"), nullable=False)
    nome = db.Column(db.String(150), nullable=False)
    # Em dias — pode ficar em branco quando não há um prazo definido (a
    # restrição vale para "nunca ter feito", sem uma janela de tempo certa).
    dias_antes = db.Column(db.Integer)

    preparo_modelo = db.relationship("PreparoModelo", back_populates="exames_anteriores_proibidos")

    def limite(self, data_hora_exame):
        """A partir de que data o procedimento passaria a invalidar o
        preparo — calculado a partir do horário marcado do exame, igual aos
        outros prazos do preparo."""
        if self.dias_antes is None:
            return None
        return (data_hora_exame - timedelta(days=self.dias_antes)).date()


class PreparoMedicamentoSuspenso(db.Model):
    """Vincula um medicamento do catálogo a um modelo de preparo, com o
    prazo (em dias antes do exame) em que ele precisa ser suspenso — o
    prazo pode ser diferente do padrão sugerido pelo catálogo, caso o
    protocolo da clínica exija."""
    __tablename__ = "preparo_medicamentos_suspensos"

    id = db.Column(db.Integer, primary_key=True)
    preparo_modelo_id = db.Column(db.Integer, db.ForeignKey("preparo_modelos.id"), nullable=False)
    medicamento_id = db.Column(db.Integer, db.ForeignKey("medicamentos.id"), nullable=False)
    dias_antes = db.Column(db.Integer, nullable=False)
    observacao = db.Column(db.String(300))

    preparo_modelo = db.relationship("PreparoModelo", back_populates="medicamentos_suspensos")
    medicamento = db.relationship("Medicamento")

    def limite(self, data_hora_exame):
        """Data até quando o medicamento deve ser suspenso, calculada a
        partir da data do exame marcado."""
        return (data_hora_exame - timedelta(days=self.dias_antes)).date()


class PreparoMedicamentoMantido(db.Model):
    """Um medicamento que o paciente NÃO precisa suspender antes do exame
    (ex.: 'não é necessário suspender o AAS, Somalgin, Aspirina') —
    complementa `observacoes_medicamentos` (texto livre) com itens
    estruturados, que o chat também consegue reconhecer quando o paciente
    pergunta se pode continuar tomando um medicamento específico."""
    __tablename__ = "preparo_medicamentos_mantidos"

    id = db.Column(db.Integer, primary_key=True)
    preparo_modelo_id = db.Column(db.Integer, db.ForeignKey("preparo_modelos.id"), nullable=False)
    nome = db.Column(db.String(200), nullable=False)
    observacao = db.Column(db.String(300))

    preparo_modelo = db.relationship("PreparoModelo", back_populates="medicamentos_mantidos")


class Agendamento(db.Model):
    __tablename__ = "agendamentos"

    id = db.Column(db.Integer, primary_key=True)
    # Fatia 4: clinica_id vira legado/exibição (nullable); grupo_id é a
    # chave real de escopo — ver mesmo comentário em Exame.clinica_id.
    clinica_id = db.Column(db.Integer, nullable=True)
    grupo_id = db.Column(db.Integer, db.ForeignKey("grupos.id"), nullable=True)
    # Fatia 6: dono pessoal do agendamento, usado só quando quem criou ainda
    # não tem Grupo (conta solo) — mesmo padrão de Exame.criado_por_id/
    # PreparoModelo.criado_por_id. Ver clinica_utils.filtro_escopo_atual().
    criado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    paciente_id = db.Column(db.Integer, db.ForeignKey("pacientes.id"), nullable=False)
    exame_id = db.Column(db.Integer, db.ForeignKey("exames.id"), nullable=False)
    # Médico responsável por este agendamento — é assim que um médico
    # "acompanha" só os seus próprios pacientes: pelo vínculo do
    # agendamento, e não por um cadastro fixo do paciente a um médico
    # (o mesmo paciente pode ter agendamentos com médicos diferentes).
    medico_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    data_hora = db.Column(db.DateTime, nullable=False)
    observacoes = db.Column(db.Text)

    # Quem vai acompanhar o paciente no dia do exame — só usado quando
    # Exame.precisa_acompanhante é True. Pode ser preenchido/alterado tanto
    # no momento do agendamento quanto no próprio dia do exame.
    acompanhante_nome = db.Column(db.String(150))
    acompanhante_telefone = db.Column(db.String(30))

    # Continuidade/encerramento do atendimento pelo médico: observações da
    # consulta (reaproveitáveis no histórico do paciente em atendimentos
    # futuros) e o momento em que o médico encerrou o atendimento.
    notas_atendimento = db.Column(db.Text)
    encerrado_em = db.Column(db.DateTime)

    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    grupo = db.relationship("Grupo", foreign_keys=[grupo_id])
    paciente = db.relationship("Paciente", back_populates="agendamentos")
    exame = db.relationship("Exame", back_populates="agendamentos")
    medico = db.relationship("Usuario", back_populates="agendamentos_medico", foreign_keys=[medico_id])
    resultado = db.relationship(
        "ResultadoExame", back_populates="agendamento", uselist=False, cascade="all, delete-orphan"
    )

    @property
    def encerrada(self):
        return self.encerrado_em is not None


class ChatMensagem(db.Model):
    """Registro de cada pergunta feita pelo paciente no chat do app (e a
    resposta obtida, seja pela FAQ curada, pela IA, pela correspondência
    por palavra-chave, ou o encaminhamento para a secretaria) — permite ao
    médico ver, ao iniciar o atendimento, todas as dúvidas que o paciente
    já tirou pelo aplicativo sobre aquele exame."""
    __tablename__ = "chat_mensagens"

    id = db.Column(db.Integer, primary_key=True)
    paciente_id = db.Column(db.Integer, db.ForeignKey("pacientes.id"), nullable=False)
    exame_id = db.Column(db.Integer, db.ForeignKey("exames.id"), nullable=True)
    # Vínculo com o agendamento/consulta específico sobre o qual a
    # pergunta foi feita (o paciente escolhe isso no seletor da tela de
    # dúvidas) — permite ao médico ver exatamente quais perguntas
    # pertencem a qual consulta no histórico de atendimentos, em vez de só
    # aproximar por data. Pode ser None em perguntas "gerais" (sem exame
    # selecionado) ou em registros antigos, de antes deste campo existir.
    agendamento_id = db.Column(db.Integer, db.ForeignKey("agendamentos.id"), nullable=True)
    pergunta = db.Column(db.Text, nullable=False)
    resposta = db.Column(db.Text)
    # origem: faq, ia, ia_aguardando (resposta da IA esperando aprovação do médico), alimento, medicamento, pendente (encaminhada)
    origem = db.Column(db.String(20))
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    paciente = db.relationship("Paciente", back_populates="mensagens_chat")
    exame = db.relationship("Exame")
    agendamento = db.relationship("Agendamento")


class ResultadoExame(db.Model):
    """Um resultado de exame (PDF) anexado pela equipe a um agendamento —
    o paciente pode baixar esse mesmo arquivo pelo aplicativo."""
    __tablename__ = "resultados_exame"

    id = db.Column(db.Integer, primary_key=True)
    agendamento_id = db.Column(db.Integer, db.ForeignKey("agendamentos.id"), nullable=False, unique=True)
    nome_arquivo = db.Column(db.String(255), nullable=False)
    caminho_arquivo = db.Column(db.String(500), nullable=False)
    enviado_por = db.Column(db.String(150))
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    agendamento = db.relationship("Agendamento", back_populates="resultado")


class FaqItem(db.Model):
    __tablename__ = "faq_itens"

    id = db.Column(db.Integer, primary_key=True)
    # Fatia 4: clinica_id vira legado/exibição (nullable); grupo_id é a
    # chave real de escopo — ver mesmo comentário em Exame.clinica_id.
    clinica_id = db.Column(db.Integer, nullable=True)
    grupo_id = db.Column(db.Integer, db.ForeignKey("grupos.id"), nullable=True)
    exame_id = db.Column(db.Integer, db.ForeignKey("exames.id"), nullable=True)  # None = pergunta geral
    pergunta = db.Column(db.Text, nullable=False)
    resposta = db.Column(db.Text, nullable=False)
    criado_por = db.Column(db.String(150))
    # Fatia 6: dono pessoal do item, usado só quando quem criou ainda não
    # tem Grupo (conta solo). Coluna separada de `criado_por` acima (que é
    # só uma string solta pra exibição) — ver
    # clinica_utils.filtro_escopo_atual().
    criado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    vezes_utilizada = db.Column(db.Integer, default=0)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    grupo = db.relationship("Grupo", foreign_keys=[grupo_id])
    exame = db.relationship("Exame", back_populates="faqs")


class PerguntaPendente(db.Model):
    __tablename__ = "perguntas_pendentes"

    id = db.Column(db.Integer, primary_key=True)
    # Fatia 4: clinica_id vira legado/exibição (nullable); grupo_id é a
    # chave real de escopo — ver mesmo comentário em Exame.clinica_id.
    clinica_id = db.Column(db.Integer, nullable=True)
    grupo_id = db.Column(db.Integer, db.ForeignKey("grupos.id"), nullable=True)
    # Fatia 6: dono pessoal da pergunta, usado só quando quem responde ainda
    # não tem Grupo (conta solo). Ver clinica_utils.filtro_escopo_atual().
    criado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    paciente_id = db.Column(db.Integer, db.ForeignKey("pacientes.id"), nullable=False)
    exame_id = db.Column(db.Integer, db.ForeignKey("exames.id"), nullable=True)
    pergunta = db.Column(db.Text, nullable=False)
    # status: pendente (sem nenhuma resposta ainda, aguardando a
    # secretaria/médico digitar uma do zero), aguardando_aprovacao (a IA já
    # rascunhou uma resposta em `resposta_sugerida_ia`, mas o médico ainda
    # precisa revisar/editar e aprovar antes dela ir para o paciente),
    # respondida (finalizada e já visível ao paciente).
    status = db.Column(db.String(20), default="pendente")
    # Rascunho de resposta gerado pela IA, mostrado ao médico como sugestão
    # editável na tela de aprovação — nunca é exibido diretamente ao
    # paciente; só o conteúdo de `resposta` (preenchido na aprovação) é.
    resposta_sugerida_ia = db.Column(db.Text)
    # Respostas "cruas" de cada IA consultada, guardadas separadas do
    # rascunho final acima (que já pode ser a junção das duas, quando
    # ambas respondem e divergem — ver app.ia_preparo.responder_com_ia) —
    # servem só para o médico comparar lado a lado na tela de aprovação;
    # ficam em branco quando aquela IA não estava configurada ou não
    # respondeu a esta pergunta específica.
    resposta_bruta_claude = db.Column(db.Text)
    resposta_bruta_chatgpt = db.Column(db.Text)
    resposta = db.Column(db.Text)
    respondida_por = db.Column(db.String(150))
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    respondida_em = db.Column(db.DateTime)

    grupo = db.relationship("Grupo", foreign_keys=[grupo_id])
    paciente = db.relationship("Paciente", back_populates="perguntas_pendentes")
    exame = db.relationship("Exame")


class HistoricoDeploy(db.Model):
    """Um registro por deploy realizado neste ambiente (media-dev, media-qa
    ou media-prod - cada um tem seu proprio banco, entao cada um acumula
    seu proprio historico). E preenchido automaticamente pelo proprio app
    na inicializacao (ver app._registrar_deploy_atual em app/__init__.py)
    lendo o deploy_info.json gerado pelo pipeline do GitHub Actions (ver
    .github/workflows/deploy.yml) - nao precisa de nenhuma acao manual.

    Serve so para mostrar "o que mudou" na tela de login (ver
    versao_info/historico_deploy no context processor) - nao tem nenhum
    dado sensivel de paciente/clinica."""
    __tablename__ = "historico_deploy"

    id = db.Column(db.Integer, primary_key=True)
    commit = db.Column(db.String(64), unique=True, nullable=False)
    commit_curto = db.Column(db.String(16))
    branch = db.Column(db.String(50))
    mensagem = db.Column(db.Text)
    deploy_em = db.Column(db.DateTime)
    registrado_em = db.Column(db.DateTime, default=datetime.utcnow)


def _preparo_pode_ser_editado_por(self, usuario):
    """Modelo com dono médico só é editado POR ELE (ver criado_por_id).
    Sem dono médico (legado/criado pela secretária), a equipe edita."""
    criador = Usuario.query.get(self.criado_por_id) if self.criado_por_id else None
    if criador and criador.tipo == "medico":
        return usuario.id == criador.id
    return True


def _preparo_dono_medico(self):
    criador = Usuario.query.get(self.criado_por_id) if self.criado_por_id else None
    return criador if criador and criador.tipo == "medico" else None


PreparoModelo.pode_ser_editado_por = _preparo_pode_ser_editado_por
PreparoModelo.dono_medico = property(_preparo_dono_medico)
