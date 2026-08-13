import re
import secrets
from datetime import datetime, date, timedelta
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db
from app.cripto_clinico import criptografar_texto, descriptografar_texto


def normalizar_telefone(telefone):
    """Mantém só os dígitos do telefone (ex.: '(27) 99999-9999' -> '27999999999'),
    para que o número usado no login do paciente seja comparado sempre da mesma
    forma, independente de como foi digitado no cadastro ou no login."""
    if not telefone:
        return None
    digitos = re.sub(r"\D", "", telefone)
    return digitos or None


class Empresa(db.Model):
    """Uma empresa cadastrada na plataforma. Uma empresa pode ter uma ou
    mais filiais (Clinica) — ex.: uma rede de clínicas com várias unidades.
    O controle de pagamento/trial/bloqueio é feito no nível da EMPRESA:
    bloquear a empresa bloqueia o acesso de todas as suas filiais de uma vez.
    """
    __tablename__ = "empresas"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), nullable=False)
    cnpj = db.Column(db.String(20))
    email_contato = db.Column(db.String(150))
    telefone = db.Column(db.String(30))

    # status: 'trial', 'ativa', 'inadimplente', 'bloqueada'
    status = db.Column(db.String(20), nullable=False, default="trial")
    data_vencimento = db.Column(db.Date)
    observacoes_pagamento = db.Column(db.Text)

    # Cobrança: valor mensal por médico vinculado a qualquer filial desta
    # empresa (cada médico conta uma única vez, mesmo atuando em mais de
    # uma filial da mesma empresa). Negociado individualmente por empresa.
    # O controle de cobrança continua manual — isso só ajuda o dono a saber
    # quanto cobrar; não existe emissão automática de fatura.
    valor_por_medico = db.Column(db.Numeric(10, 2))

    # Código público do link de auto-cadastro de paciente (ver
    # auth.cadastro_paciente). O paciente é da EMPRESA, então o link
    # também é - um só por empresa, mostrado no Painel. (O campo homônimo
    # em Clinica virou legado: links antigos por filial continuam
    # funcionando, mas o cadastro resultante sempre entra na empresa.)
    codigo_cadastro_paciente = db.Column(db.String(20), unique=True, nullable=True)

    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    filiais = db.relationship("Clinica", back_populates="empresa", cascade="all, delete-orphan")

    @property
    def bloqueada(self):
        return self.status == "bloqueada"

    @property
    def medicos_distintos(self):
        """Usuários médicos distintos vinculados (vínculo ativo) a qualquer
        filial desta empresa — usado para calcular a cobrança mensal."""
        vistos = {}
        for filial in self.filiais:
            for m in filial.medicos_e_secretarias:
                if m.tipo == "medico":
                    vistos[m.id] = m
        return list(vistos.values())

    @property
    def valor_mensal_estimado(self):
        if self.valor_por_medico is None:
            return None
        return self.valor_por_medico * len(self.medicos_distintos)

    def verificar_vencimento_trial(self):
        """Se a empresa está em trial e a data de vencimento já passou,
        marca automaticamente como 'inadimplente' (não bloqueia por conta
        própria — quem decide bloquear de fato é o dono da plataforma, no
        painel dele). Não faz commit; quem chamar decide quando salvar.
        Retorna True se o status foi alterado."""
        if self.status == "trial" and self.data_vencimento and self.data_vencimento < date.today():
            self.status = "inadimplente"
            return True
        return False


class Clinica(db.Model):
    """Uma filial de uma empresa cadastrada na plataforma. Pacientes,
    exames, agenda e perguntas continuam isolados por filial — só o
    controle de pagamento/bloqueio é feito no nível da empresa."""
    __tablename__ = "clinicas"

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False)
    nome = db.Column(db.String(150), nullable=False)
    razao_social = db.Column(db.String(200))
    cnpj = db.Column(db.String(20))
    email_contato = db.Column(db.String(150))
    telefone = db.Column(db.String(30))
    logo_url = db.Column(db.String(300))

    # Endereço — usado tanto para exibição quanto como base para a nota fiscal.
    cep = db.Column(db.String(10))
    rua = db.Column(db.String(200))
    numero = db.Column(db.String(20))
    complemento = db.Column(db.String(100))
    bairro = db.Column(db.String(100))
    cidade = db.Column(db.String(100))
    uf = db.Column(db.String(2))

    # Dados fiscais — guardados para uma futura emissão de nota fiscal ao
    # cliente/paciente. Por ora só armazenamos os dados; a emissão real
    # (integração com a SEFAZ, certificado digital etc.) não está implementada.
    inscricao_estadual = db.Column(db.String(30))
    regime_tributario = db.Column(db.String(50))
    cnae = db.Column(db.String(20))
    codigo_ibge_municipio = db.Column(db.String(10))

    # Código público usado no link de auto-cadastro do paciente pelo
    # app (ver auth.cadastro_paciente) — cada filial tem o seu, gerado
    # sob demanda na tela "Dados Cadastrais" (ver
    # medico.clinica_configuracoes / _gerar_codigo_cadastro_paciente).
    codigo_cadastro_paciente = db.Column(db.String(20), unique=True, nullable=True)

    # Emissão fiscal (NFS-e Nacional — ADN/Serpro) — ver app/cripto_fiscal.py
    # para a criptografia da senha do certificado e do token do provedor.
    # Nenhum dos dois é guardado em texto puro. NFS-e (serviço, ISS
    # municipal) é o padrão correto para exames médicos — NFC-e é venda de
    # produto (ICMS estadual) e não se aplica aqui.
    fiscal_ambiente = db.Column(db.String(20), nullable=False, default="homologacao")
    fiscal_modo_simulacao = db.Column(db.Boolean, nullable=False, default=False)
    fiscal_simular_falha_conexao = db.Column(db.Boolean, nullable=False, default=False)

    fiscal_certificado_pfx = db.Column(db.LargeBinary)
    fiscal_certificado_senha_cripto = db.Column(db.LargeBinary)
    fiscal_certificado_cnpj = db.Column(db.String(20))
    fiscal_certificado_validade = db.Column(db.Date)

    fiscal_provedor_emissao = db.Column(db.String(50), nullable=False, default="nenhum")
    fiscal_provedor_token_cripto = db.Column(db.LargeBinary)

    # Dados específicos da NFS-e (nota fiscal de serviço eletrônica).
    fiscal_inscricao_municipal = db.Column(db.String(30))
    fiscal_codigo_servico = db.Column(db.String(20))
    fiscal_aliquota_iss = db.Column(db.Numeric(5, 2))
    fiscal_rps_serie = db.Column(db.String(10))
    fiscal_rps_proximo_numero = db.Column(db.Integer)

    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    empresa = db.relationship("Empresa", back_populates="filiais")
    membros = db.relationship("ClinicaMembro", back_populates="clinica", cascade="all, delete-orphan")
    pacientes = db.relationship("Paciente", back_populates="clinica", cascade="all, delete-orphan")
    exames = db.relationship("Exame", back_populates="clinica", cascade="all, delete-orphan")

    @property
    def bloqueada(self):
        """O bloqueio é sempre decidido no nível da empresa."""
        return self.empresa.bloqueada

    @property
    def medicos_e_secretarias(self):
        return [m.usuario for m in self.membros if m.ativo]


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


class MedicoHorario(db.Model):
    """Horário de atendimento de um médico numa filial específica, por dia
    da semana — usado pelo otimizador de agenda (ver
    app.agendamento_otimizador) para sugerir datas/horários de acordo com
    a duração do exame (Exame.duracao_minutos) e os agendamentos já
    existentes. dia_semana: 0=segunda, 1=terça, ..., 6=domingo (padrão
    Python/ISO)."""
    __tablename__ = "medico_horarios"
    __table_args__ = (
        db.UniqueConstraint("clinica_id", "medico_id", "dia_semana", name="uq_medico_clinica_dia"),
    )

    id = db.Column(db.Integer, primary_key=True)
    clinica_id = db.Column(db.Integer, db.ForeignKey("clinicas.id"), nullable=False)
    medico_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    dia_semana = db.Column(db.Integer, nullable=False)
    ativo = db.Column(db.Boolean, default=False)
    hora_inicio = db.Column(db.Time)
    hora_fim = db.Column(db.Time)

    clinica = db.relationship("Clinica")
    medico = db.relationship("Usuario")


class MedicoBloqueio(db.Model):
    """Bloqueio de agenda de um médico por conta de compromisso próprio
    (consulta, viagem, férias etc.) — cobre um intervalo de data/hora
    (data_inicio até data_fim). Um bloqueio de dia inteiro é representado
    com data_inicio às 00:00 e data_fim às 23:59:59 do(s) dia(s) afetado(s).
    Usado pelo otimizador de agenda (app.agendamento_otimizador) para não
    sugerir horários dentro do período bloqueado, e também para impedir
    que a secretária agende manualmente nesse período."""
    __tablename__ = "medico_bloqueios"

    id = db.Column(db.Integer, primary_key=True)
    clinica_id = db.Column(db.Integer, db.ForeignKey("clinicas.id"), nullable=False)
    medico_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    data_inicio = db.Column(db.DateTime, nullable=False)
    data_fim = db.Column(db.DateTime, nullable=False)
    motivo = db.Column(db.String(200))
    dia_inteiro = db.Column(db.Boolean, nullable=False, default=False)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    clinica = db.relationship("Clinica")
    medico = db.relationship("Usuario")


class ClinicaMembro(db.Model):
    """Vínculo entre um usuário da equipe (médico/secretária) e uma clínica.
    Um mesmo usuário pode estar vinculado a várias clínicas."""
    __tablename__ = "clinica_membros"
    __table_args__ = (db.UniqueConstraint("clinica_id", "usuario_id", name="uq_clinica_usuario"),)

    id = db.Column(db.Integer, primary_key=True)
    clinica_id = db.Column(db.Integer, db.ForeignKey("clinicas.id"), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    # Vínculo nunca é APAGADO - é ENCERRADO (ativo=False + encerrado_em):
    # o histórico de "quem atendeu onde, de quando a quando" é permanente,
    # e uma revinculação futura REATIVA este mesmo registro (ver
    # medico.convite_decidir/equipe_associar_filial), sem duplicar. Uma
    # pessoa com o vínculo encerrado perde o acesso àquela clínica
    # normalmente (todo o acesso passa por ativo=True, ver
    # Usuario.clinicas_ativas), mas os agendamentos/registros feitos por
    # ela continuam intactos.
    encerrado_em = db.Column(db.DateTime, nullable=True)

    clinica = db.relationship("Clinica", back_populates="membros")
    usuario = db.relationship("Usuario", back_populates="vinculos_clinica")

    def encerrar(self):
        self.ativo = False
        self.encerrado_em = datetime.utcnow()

    def reativar(self):
        self.ativo = True
        self.encerrado_em = None


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

    # Empresa que esta pessoa CRIOU no cadastro público (auth.cadastro,
    # modo "empresa"), usado só como âncora ANTES de ela ter qualquer
    # filial/ClinicaMembro - o cadastro público não cria mais a primeira
    # filial automaticamente (isso agora é feito depois, ao entrar no
    # app, em "Meus Locais de Atendimento"), então por um tempinho a
    # pessoa não tem nenhum vínculo de filial ainda. Sem esta âncora,
    # empresa_atual() (ver app/clinica_utils.py) não teria como saber a
    # qual empresa ela pertence, e ela cairia fora do sistema até
    # cadastrar o primeiro local. Uma vez que a primeira filial é
    # cadastrada (com o ClinicaMembro correspondente), o vínculo normal
    # passa a resolver tudo e este campo vira só um resquício histórico -
    # não precisa ser limpo.
    empresa_fundadora_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=True)
    empresa_fundadora = db.relationship("Empresa", foreign_keys=[empresa_fundadora_id])

    # Permissões administrativas (só fazem sentido para médico/secretária).
    # Como nem toda clínica tem uma secretária, essas permissões não são
    # amarradas ao papel ('tipo') — quem administra a equipe decide quais
    # telas administrativas cada pessoa pode acessar, seja ela médico ou
    # secretária.
    perm_pacientes = db.Column(db.Boolean, nullable=False, default=False)
    perm_equipe = db.Column(db.Boolean, nullable=False, default=False)
    perm_filiais = db.Column(db.Boolean, nullable=False, default=False)
    perm_dados_clinica = db.Column(db.Boolean, nullable=False, default=False)

    # Certificado digital pessoal (e-CPF, ICP-Brasil) do médico, usado para
    # assinar digitalmente as evoluções clínicas que ele registra (ver
    # app/assinatura_clinica.py) — rumo ao NGS3 do CFM 1.821/2007. Guardado
    # criptografado (ver app/cripto_clinico.py), igual ao certificado
    # e-CNPJ da clínica usado na nota fiscal, mas por pessoa em vez de por
    # clínica: cada médico assina com o próprio certificado, não o da
    # empresa. Só faz sentido para tipo == "medico".
    certificado_digital_pfx = db.Column(db.LargeBinary)
    certificado_digital_senha_cripto = db.Column(db.LargeBinary)
    certificado_digital_titular = db.Column(db.String(200))
    certificado_digital_validade = db.Column(db.Date)

    # CONTA ÚNICA do paciente: uma pessoa (um Usuario) pode ter VÁRIOS
    # cadastros de paciente - um por empresa que frequenta (ver
    # encontrar_conta_paciente). O que é global é a PESSOA e o login dela;
    # os dados clínicos continuam separados por empresa (cada clínica só
    # vê o cadastro dela - LGPD). A propriedade `paciente` (abaixo) mantém
    # a compatibilidade com o código que assume um cadastro só.
    pacientes = db.relationship(
        "Paciente", back_populates="usuario", order_by="Paciente.id"
    )
    vinculos_clinica = db.relationship("ClinicaMembro", back_populates="usuario", cascade="all, delete-orphan")
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

    @property
    def clinicas_ativas(self):
        """Clínicas às quais este usuário está vinculado (vínculo ativo) e
        que não estão bloqueadas pelo dono da plataforma."""
        return [
            v.clinica for v in self.vinculos_clinica
            if v.ativo and not v.clinica.bloqueada
        ]


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


class ConviteVinculo(db.Model):
    """Convite de uma CLÍNICA (filial) para um MÉDICO passar a atender lá,
    criado quando a equipe digita o código mestre do médico (ver
    Usuario.codigo_mestre) em "Vincular médico por código". O vínculo
    (ClinicaMembro) só nasce quando o MÉDICO aceita o convite no painel
    dele - consentimento nas duas pontas: saber o código de alguém não
    basta pra vinculá-lo (e pra clínica ter a agenda dele gerida pelas
    secretárias) sem que ele concorde."""
    __tablename__ = "convites_vinculo"

    id = db.Column(db.Integer, primary_key=True)
    clinica_id = db.Column(db.Integer, db.ForeignKey("clinicas.id"), nullable=False)
    medico_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    # Quem criou o convite (alguém da equipe com perm_equipe) - fica no
    # histórico pra auditoria ("quem convidou o Dr. Bruno?").
    criado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    # status: 'pendente' (aguardando o médico), 'aceito', 'recusado',
    # 'cancelado' (a clínica desistiu antes do médico decidir). Convites
    # nunca são apagados - são o histórico de como cada vínculo nasceu.
    status = db.Column(db.String(20), nullable=False, default="pendente")
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    decidido_em = db.Column(db.DateTime, nullable=True)

    clinica = db.relationship("Clinica", foreign_keys=[clinica_id])
    medico = db.relationship("Usuario", foreign_keys=[medico_id])
    criado_por = db.relationship("Usuario", foreign_keys=[criado_por_id])


class Paciente(db.Model):
    __tablename__ = "pacientes"
    __table_args__ = (db.UniqueConstraint("empresa_id", "cpf", name="uq_empresa_cpf"),)

    id = db.Column(db.Integer, primary_key=True)
    # O paciente pertence à EMPRESA, não a uma filial - "o cliente é só
    # cliente". A filial só entra em cena na hora de marcar a consulta:
    # cada Agendamento tem sua própria clinica_id (a filial escolhida
    # naquele agendamento), escolhida tanto pela equipe (medico.agenda_novo)
    # quanto pelo próprio paciente no pedido de agendamento
    # (paciente.solicitar_agendamento, via exame - que é por filial).
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=True)
    # LEGADO: antes o paciente era amarrado a uma filial no cadastro. O
    # campo fica só para dados históricos (a migração copia
    # clinicas.empresa_id para empresa_id) - código novo não deve ler nem
    # gravar clinica_id em pacientes.
    clinica_id = db.Column(db.Integer, db.ForeignKey("clinicas.id"), nullable=True)
    # NÃO é mais unique: com a CONTA ÚNICA do paciente, a mesma conta
    # (Usuario) tem um cadastro (Paciente) por empresa que a pessoa
    # frequenta - a unicidade que vale é (empresa_id, cpf), acima. Ver
    # encontrar_conta_paciente e a migração que remove a constraint em
    # bases antigas (migrar_banco.py).
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
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
    # clínica aceitar) ou 'rejeitado'. Só paciente com status 'aprovado'
    # pode solicitar agendamento (ver paciente.solicitar_agendamento).
    status_cadastro = db.Column(db.String(20), nullable=False, default="aprovado")

    empresa = db.relationship("Empresa", foreign_keys=[empresa_id])
    clinica = db.relationship("Clinica", back_populates="pacientes")

    @property
    def empresa_efetiva(self):
        """Empresa do paciente, cobrindo também registros antigos que só
        têm a filial legada (clinica_id) e ainda não passaram pela
        migração que preenche empresa_id."""
        if self.empresa is not None:
            return self.empresa
        return self.clinica.empresa if self.clinica else None
    usuario = db.relationship("Usuario", back_populates="pacientes")
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
    __table_args__ = (db.UniqueConstraint("clinica_id", "nome", name="uq_clinica_exame_nome"),)

    id = db.Column(db.Integer, primary_key=True)
    clinica_id = db.Column(db.Integer, db.ForeignKey("clinicas.id"), nullable=False)
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

    # Quanto tempo (em minutos) esse exame costuma levar — usado pelo
    # otimizador de agenda para calcular horários disponíveis a partir do
    # horário de atendimento do médico (ver MedicoHorario e
    # app.agendamento_otimizador).
    duracao_minutos = db.Column(db.Integer)

    # Preço do procedimento — usado no registro de pagamento (ver Pagamento).
    preco = db.Column(db.Numeric(10, 2))

    # Se marcado, ao agendar esse exame o sistema exige/permite indicar
    # quem vai acompanhar o paciente no dia (ver Agendamento.acompanhante_nome).
    precisa_acompanhante = db.Column(db.Boolean, nullable=False, default=False)

    clinica = db.relationship("Clinica", back_populates="exames")
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
    __table_args__ = (db.UniqueConstraint("clinica_id", "nome", name="uq_clinica_preparo_modelo_nome"),)

    id = db.Column(db.Integer, primary_key=True)
    clinica_id = db.Column(db.Integer, db.ForeignKey("clinicas.id"), nullable=False)
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

    clinica = db.relationship("Clinica")
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
    clinica_id = db.Column(db.Integer, db.ForeignKey("clinicas.id"), nullable=False)
    paciente_id = db.Column(db.Integer, db.ForeignKey("pacientes.id"), nullable=False)
    exame_id = db.Column(db.Integer, db.ForeignKey("exames.id"), nullable=False)
    # Médico responsável por este agendamento — é assim que um médico
    # "acompanha" só os seus próprios pacientes: pelo vínculo do
    # agendamento, e não por um cadastro fixo do paciente a um médico
    # (o mesmo paciente pode ter agendamentos com médicos diferentes).
    medico_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    data_hora = db.Column(db.DateTime, nullable=False)
    # status: solicitado (pedido pelo paciente, aguardando confirmação da
    # clínica), agendado, confirmado, realizado, cancelado, nao_compareceu
    # (paciente não apareceu no horário marcado - usado só em agendamentos
    # já passados, pra distinguir de um cancelamento de fato e alimentar a
    # taxa de no-show nos relatórios).
    status = db.Column(db.String(20), default="agendado")
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

    clinica = db.relationship("Clinica")
    paciente = db.relationship("Paciente", back_populates="agendamentos")
    exame = db.relationship("Exame", back_populates="agendamentos")
    medico = db.relationship("Usuario", back_populates="agendamentos_medico", foreign_keys=[medico_id])
    resultado = db.relationship(
        "ResultadoExame", back_populates="agendamento", uselist=False, cascade="all, delete-orphan"
    )
    pagamento = db.relationship(
        "Pagamento", back_populates="agendamento", uselist=False, cascade="all, delete-orphan"
    )

    @property
    def encerrada(self):
        return self.encerrado_em is not None


class EvolucaoClinica(db.Model):
    """Registro clínico de uma consulta/atendimento — o começo do que, no
    futuro, deve se tornar um prontuário eletrônico de verdade (ver
    conversa sobre CFM 1.821/2007 e os Níveis de Garantia de Segurança).

    Por enquanto isto é conteúdo NGS1/NGS2 (sem assinatura digital
    ICP-Brasil ainda — teria que ser por médico, com certificado próprio,
    diferente do certificado e-CNPJ da clínica usado na nota fiscal), mas
    já segue a regra mais importante de um registro clínico: é
    IMUTÁVEL POR DESENHO. Não existe rota de editar nem de excluir uma
    evolução já salva — uma consulta errada não se corrige apagando o
    registro, se corrige com uma entrada nova. Isso é intencional e não
    deve mudar sem repensar a arquitetura toda."""
    __tablename__ = "evolucoes_clinicas"

    id = db.Column(db.Integer, primary_key=True)
    agendamento_id = db.Column(db.Integer, db.ForeignKey("agendamentos.id"), nullable=False)
    # Guardado também aqui (não só via agendamento.paciente_id) para poder
    # trazer o histórico clínico completo do paciente com uma consulta
    # direta, sem precisar sempre passar por Agendamento.
    paciente_id = db.Column(db.Integer, db.ForeignKey("pacientes.id"), nullable=False)
    autor_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)

    # Conteúdo cifrado (ver app/cripto_clinico.py) — nunca fica em texto
    # puro no banco. Use a propriedade `texto` abaixo para ler/gravar; ela
    # cuida de cifrar/decifrar automaticamente.
    texto_cripto = db.Column(db.LargeBinary)

    # Sinais vitais — todos opcionais, preenchidos só quando medidos nessa
    # consulta.
    peso_kg = db.Column(db.Numeric(5, 2))
    altura_cm = db.Column(db.Integer)
    pressao_arterial = db.Column(db.String(20))  # ex.: "120/80"
    frequencia_cardiaca_bpm = db.Column(db.Integer)
    temperatura_celsius = db.Column(db.Numeric(4, 1))

    criado_em = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Assinatura digital ICP-Brasil do autor (ver app/assinatura_clinica.py)
    # — preenchida automaticamente ao salvar, SE o autor for médico e tiver
    # um certificado digital pessoal configurado (Usuario.certificado_digital_*).
    # Fica None quando não há certificado disponível: a entrada continua
    # válida (nível NGS2), só não tem validade de documento assinado (NGS3).
    assinatura_base64 = db.Column(db.Text)
    assinatura_certificado_titular = db.Column(db.String(200))
    assinatura_certificado_serial = db.Column(db.String(80))
    # Certificado público (PEM, não é sigiloso) usado nesta assinatura,
    # guardado junto com o registro — permite verificar a assinatura no
    # futuro mesmo que o médico troque de certificado depois.
    assinatura_certificado_pem = db.Column(db.Text)
    assinatura_hash_sha256 = db.Column(db.String(64))
    assinado_em = db.Column(db.DateTime)

    agendamento = db.relationship("Agendamento", backref=db.backref("evolucoes", order_by="EvolucaoClinica.criado_em"))
    paciente = db.relationship("Paciente", backref=db.backref("evolucoes_clinicas", order_by="EvolucaoClinica.criado_em.desc()"))
    autor = db.relationship("Usuario")

    @property
    def texto(self):
        return descriptografar_texto(self.texto_cripto)

    @texto.setter
    def texto(self, valor):
        self.texto_cripto = criptografar_texto(valor)

    @property
    def assinada(self):
        return self.assinatura_base64 is not None


class LogAcessoProntuario(db.Model):
    """Trilha de auditoria de acesso ao prontuário — quem visualizou,
    criou, assinou ou exportou o histórico clínico de qual paciente, e
    quando (requisito técnico do CFM 1.821/2007 para os Níveis de Garantia
    de Segurança NGS2/NGS3). Assim como EvolucaoClinica, é IMUTÁVEL por
    desenho: só existe rota para criar um registro, nunca editar ou apagar
    — ver app/auditoria_clinica.py."""
    __tablename__ = "logs_acesso_prontuario"

    id = db.Column(db.Integer, primary_key=True)
    paciente_id = db.Column(db.Integer, db.ForeignKey("pacientes.id"), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    # acao: "visualizar_prontuario", "criar_evolucao", "exportar_prontuario"
    acao = db.Column(db.String(40), nullable=False)
    detalhe = db.Column(db.Text)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    paciente = db.relationship("Paciente")
    usuario = db.relationship("Usuario")


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


class DescontoConfig(db.Model):
    """Percentual de desconto pré-cadastrado, que pode ser aplicado ao
    registrar o pagamento de uma consulta/procedimento (ex.: "Convênio X —
    10%", "Desconto à vista — 5%")."""
    __tablename__ = "descontos_config"

    id = db.Column(db.Integer, primary_key=True)
    clinica_id = db.Column(db.Integer, db.ForeignKey("clinicas.id"), nullable=False)
    # DONO do modelo: quem o criou. Se foi um MÉDICO, só ele edita/remove
    # (conteúdo clínico é do médico). NULL em modelos antigos - esses
    # seguem editáveis pela equipe (comportamento antigo).
    criado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    nome = db.Column(db.String(150), nullable=False)
    percentual = db.Column(db.Numeric(5, 2), nullable=False)
    ativo = db.Column(db.Boolean, nullable=False, default=True)

    clinica = db.relationship("Clinica")


class Pagamento(db.Model):
    """Pagamento registrado para um agendamento — o valor precisa bater
    com o preço cadastrado no exame (Exame.preco), podendo aplicar um dos
    descontos percentuais cadastrados (ver DescontoConfig). Serve de base
    para gerar um comprovante para impressão; a emissão de nota fiscal em
    si não está implementada ainda."""
    __tablename__ = "pagamentos"

    id = db.Column(db.Integer, primary_key=True)
    agendamento_id = db.Column(db.Integer, db.ForeignKey("agendamentos.id"), nullable=False, unique=True)
    valor_procedimento = db.Column(db.Numeric(10, 2), nullable=False)
    desconto_id = db.Column(db.Integer, db.ForeignKey("descontos_config.id"), nullable=True)
    # Guarda o nome/percentual do desconto no momento do pagamento — se o
    # cadastro do desconto mudar ou for removido depois, o comprovante
    # antigo continua correto.
    desconto_nome = db.Column(db.String(150))
    desconto_percentual = db.Column(db.Numeric(5, 2), default=0)
    valor_final = db.Column(db.Numeric(10, 2), nullable=False)
    forma_pagamento = db.Column(db.String(30))  # dinheiro, cartao, pix, outro
    registrado_por = db.Column(db.String(150))
    pago_em = db.Column(db.DateTime, default=datetime.utcnow)

    # Emissão de NFS-e para este pagamento — ver app/nfse_nacional.py.
    # status: nao_emitida, simulada (modo simulação, sem valor fiscal),
    # assinada_pendente_envio (DPS assinado mas o envio automático ao
    # Ambiente de Dados Nacional falhou/não foi confirmado), enviada.
    nfse_status = db.Column(db.String(30), default="nao_emitida")
    nfse_numero_dps = db.Column(db.Integer)
    nfse_numero = db.Column(db.String(30))
    nfse_codigo_verificacao = db.Column(db.String(60))
    nfse_xml_assinado = db.Column(db.Text)
    nfse_erro = db.Column(db.Text)
    nfse_emitida_em = db.Column(db.DateTime)

    agendamento = db.relationship("Agendamento", back_populates="pagamento")
    desconto = db.relationship("DescontoConfig")


class FaqItem(db.Model):
    __tablename__ = "faq_itens"

    id = db.Column(db.Integer, primary_key=True)
    clinica_id = db.Column(db.Integer, db.ForeignKey("clinicas.id"), nullable=False)
    exame_id = db.Column(db.Integer, db.ForeignKey("exames.id"), nullable=True)  # None = pergunta geral
    pergunta = db.Column(db.Text, nullable=False)
    resposta = db.Column(db.Text, nullable=False)
    criado_por = db.Column(db.String(150))
    vezes_utilizada = db.Column(db.Integer, default=0)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    clinica = db.relationship("Clinica")
    exame = db.relationship("Exame", back_populates="faqs")


class PerguntaPendente(db.Model):
    __tablename__ = "perguntas_pendentes"

    id = db.Column(db.Integer, primary_key=True)
    clinica_id = db.Column(db.Integer, db.ForeignKey("clinicas.id"), nullable=False)
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

    clinica = db.relationship("Clinica")
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
