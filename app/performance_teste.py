"""Ferramenta de teste de performance (pedido do Silvan, 2026-09-29):
gera um volume grande de médicos e pacientes "sintéticos" direto no banco
(bulk insert, sem passar pelas rotas normais de cadastro) para observar
como a aplicação se comporta com uma base de dados bem maior do que a
atual - listagens, busca, dashboard etc.

Pensada para rodar SÓ no ambiente de teste (media-dev) - por isso o
acesso exige senha do dono (mesmo padrão de app.limpar_dados) e os
registros gerados são claramente marcados (e-mail/nome com o prefixo
"perfteste"), para nunca serem confundidos com dados reais e para
poderem ser apagados de uma vez só depois do teste (ver
apagar_dados_teste).

Cada médico/paciente gerado é independente (médico solo, sem Grupo;
paciente com cadastrado_por_id apontando pra um desses médicos, em
rodízio) - não usa Grupo/GrupoPaciente de propósito, pra manter a
geração simples e rápida (bulk_insert_mappings, sem cascatas de ORM).
"""
from datetime import date, datetime, timedelta

from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import Usuario, Paciente, PlataformaConfig

PREFIXO_EMAIL_MEDICO = "medico.perfteste"
PREFIXO_NOME_PACIENTE = "Paciente PerfTeste "
PREFIXO_CODIGO_MESTRE = "PFT-"

# Faixas de CPF/telefone bem distintas das reais (só dígitos, sem
# checksum de verdade - não passam por validar_cpf porque este módulo
# nunca usa as rotas normais de cadastro), pra não ter nenhuma chance
# de colidir com um CPF de paciente/médico real já cadastrado.
_CPF_BASE_MEDICO = 99_000_000_000
_CPF_BASE_PACIENTE = 88_000_000_000
_TELEFONE_BASE_MEDICO = 27_900_000_000
_TELEFONE_BASE_PACIENTE = 27_800_000_000

_SENHA_PADRAO_TESTE = "PerfTeste@2026"

_TAMANHO_LOTE = 2000  # commits em pedaços, pra não segurar uma transação gigante


def _senha_hash_padrao():
    # Gerar o hash uma única vez (é lento de propósito, por segurança) e
    # reaproveitar em todos os médicos - senha real não importa aqui,
    # nenhum desses médicos precisa logar de fato.
    if not hasattr(_senha_hash_padrao, "_cache"):
        _senha_hash_padrao._cache = generate_password_hash(_SENHA_PADRAO_TESTE)
    return _senha_hash_padrao._cache


def _formatar_cpf(numero):
    s = str(numero).zfill(11)
    return f"{s[0:3]}.{s[3:6]}.{s[6:9]}-{s[9:11]}"


def contar_dados_teste():
    """(qtd_medicos, qtd_pacientes) já gerados por este módulo, presentes
    agora no banco."""
    qtd_medicos = Usuario.query.filter(Usuario.email.like(f"{PREFIXO_EMAIL_MEDICO}%")).count()
    qtd_pacientes = Paciente.query.filter(Paciente.nome.like(f"{PREFIXO_NOME_PACIENTE}%")).count()
    return qtd_medicos, qtd_pacientes


def _proximo_indice_medico():
    """Índice a partir do qual gerar os próximos médicos, continuando a
    numeração dos que já existem (permite gerar em vários lotes sem
    colidir e-mail/CPF/código mestre com os já criados)."""
    existente = (
        Usuario.query.filter(Usuario.email.like(f"{PREFIXO_EMAIL_MEDICO}%"))
        .count()
    )
    return existente + 1


def _proximo_indice_paciente():
    existente = Paciente.query.filter(Paciente.nome.like(f"{PREFIXO_NOME_PACIENTE}%")).count()
    return existente + 1


def gerar_medicos_teste(quantidade):
    """Cria `quantidade` médicos sintéticos (bulk insert), cada um solo
    (sem Grupo), com todas as permissões administrativas e licença em
    trial - mesmo estado inicial de um cadastro normal (ver
    auth.cadastro), só que sem passar pela rota nem pelo formulário.
    Devolve quantos foram efetivamente criados."""
    config = PlataformaConfig.obter()
    inicio = _proximo_indice_medico()
    senha_hash = _senha_hash_padrao()
    agora = datetime.utcnow()
    vencimento_trial = date.today() + timedelta(days=config.trial_dias)

    lote = []
    for i in range(inicio, inicio + quantidade):
        lote.append({
            "nome": f"Médico PerfTeste {i:05d}",
            "email": f"{PREFIXO_EMAIL_MEDICO}{i:05d}@teste.dev",
            "senha_hash": senha_hash,
            "telefone": str(_TELEFONE_BASE_MEDICO + i),
            "tipo": "medico",
            "ativo": True,
            "criado_em": agora,
            "cpf": _formatar_cpf(_CPF_BASE_MEDICO + i),
            "uf": "ES",
            "cidade": "Vitória",
            "data_nascimento": date(1980, 1, 1) + timedelta(days=i % 15000),
            "crm_numero": str(100000 + i),
            "crm_uf": "ES",
            "codigo_mestre": f"{PREFIXO_CODIGO_MESTRE}{i:06d}",
            "perm_pacientes": True,
            "perm_equipe": True,
            "perm_filiais": True,
            "perm_dados_clinica": True,
            "aprovacao_perguntas_paciente": True,
            "licenca_status": "trial",
            "licenca_vencimento": vencimento_trial,
            "valor_licenca_mensal": config.valor_licenca_padrao,
            "ciclo_licenca": "mensal",
        })
        if len(lote) >= _TAMANHO_LOTE:
            db.session.bulk_insert_mappings(Usuario, lote)
            db.session.commit()
            lote = []
    if lote:
        db.session.bulk_insert_mappings(Usuario, lote)
        db.session.commit()
    return quantidade


def gerar_pacientes_teste(quantidade):
    """Cria `quantidade` pacientes sintéticos (bulk insert), distribuídos
    em rodízio entre os médicos PerfTeste já existentes (cadastrado_por_id
    = escopo pessoal, mesmo padrão de uma conta solo - ver
    Paciente.cadastrado_por_id). Precisa de pelo menos um médico PerfTeste
    já criado (ver gerar_medicos_teste) - devolve 0 se não houver
    nenhum."""
    medico_ids = [
        row[0] for row in
        db.session.query(Usuario.id)
        .filter(Usuario.email.like(f"{PREFIXO_EMAIL_MEDICO}%"))
        .order_by(Usuario.id)
        .all()
    ]
    if not medico_ids:
        return 0

    inicio = _proximo_indice_paciente()
    agora = datetime.utcnow()
    qtd_medicos = len(medico_ids)

    lote = []
    for i in range(inicio, inicio + quantidade):
        lote.append({
            "cadastrado_por_id": medico_ids[i % qtd_medicos],
            "nome": f"{PREFIXO_NOME_PACIENTE}{i:06d}",
            "cpf": _formatar_cpf(_CPF_BASE_PACIENTE + i),
            "data_nascimento": date(1950, 1, 1) + timedelta(days=i % 25000),
            "telefone": str(_TELEFONE_BASE_PACIENTE + i),
            "status_cadastro": "aprovado",
            "eh_teste": False,
            "criado_em": agora,
        })
        if len(lote) >= _TAMANHO_LOTE:
            db.session.bulk_insert_mappings(Paciente, lote)
            db.session.commit()
            lote = []
    if lote:
        db.session.bulk_insert_mappings(Paciente, lote)
        db.session.commit()
    return quantidade


def apagar_dados_teste():
    """Remove TODOS os médicos e pacientes gerados por este módulo
    (identificados pelo prefixo de e-mail/nome - ver PREFIXO_EMAIL_MEDICO/
    PREFIXO_NOME_PACIENTE), sem tocar em nenhum outro dado. Os pacientes
    são apagados primeiro (a tabela pacientes referencia usuarios via
    cadastrado_por_id) - nenhum deles tem agendamento/pergunta/mensagem de
    chat de verdade (foram criados só com bulk insert direto, sem passar
    por nenhum fluxo que criasse esse histórico), então um DELETE em
    massa aqui é seguro. Devolve (qtd_medicos_apagados,
    qtd_pacientes_apagados)."""
    qtd_pacientes = Paciente.query.filter(Paciente.nome.like(f"{PREFIXO_NOME_PACIENTE}%")).delete(synchronize_session=False)
    qtd_medicos = Usuario.query.filter(Usuario.email.like(f"{PREFIXO_EMAIL_MEDICO}%")).delete(synchronize_session=False)
    return qtd_medicos, qtd_pacientes
