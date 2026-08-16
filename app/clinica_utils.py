"""Utilitários para trabalhar com o contexto (tenant) do usuário da equipe
(médico/secretária) durante a sessão.

O que delimita o que a pessoa vê é a EMPRESA (o cliente/tenant da
plataforma), não mais uma "clínica atual". Um usuário vinculado a duas
filiais (Clinica) da mesma empresa vê os dados das DUAS juntos, com a
filial indicada em cada registro — quem determina onde o médico está é o
agendamento/consulta que ele vai atender, não uma troca manual de local.

A empresa ativa fica guardada na sessão Flask (session['empresa_id']) e só
precisa ser escolhida à mão no caso raro de a pessoa ter vínculo em mais de
uma EMPRESA (tenants diferentes) — ver medico.escolher_clinica.

session['clinica_id'] continua existindo, mas SÓ como "filial padrão"
(pré-seleção de campo em formulário). Ela não filtra mais nada.
"""
from flask import session
from flask_login import current_user

from app.extensions import db


def verificar_vencimento_empresa(empresa):
    """Atualiza (e salva) o status da empresa para 'inadimplente' se o
    trial já venceu. Não bloqueia por conta própria — isso continua sendo
    uma decisão manual do dono da plataforma."""
    if empresa.verificar_vencimento_trial():
        db.session.commit()


def verificar_vencimento(clinica):
    """Mesma verificação acima, mas a partir de uma filial (Clinica) —
    o controle de pagamento/trial vive na empresa dona da filial."""
    verificar_vencimento_empresa(clinica.empresa)


def clinicas_do_usuario():
    """Lista de clínicas ativas (vínculo ativo e não bloqueadas) do usuário logado."""
    if not current_user.is_authenticated or not current_user.is_staff:
        return []
    clinicas = current_user.clinicas_ativas
    for c in clinicas:
        verificar_vencimento(c)
    return clinicas


def empresas_do_usuario():
    """Empresas distintas (tenants) em que o usuário tem vínculo ativo.
    Na esmagadora maioria dos casos é uma só.

    Também inclui a empresa que a pessoa criou no cadastro público (ver
    Usuario.empresa_fundadora_id), mesmo que ela ainda não tenha nenhuma
    filial/ClinicaMembro - o cadastro público não cria mais a primeira
    filial automaticamente (isso é feito depois, ao entrar no app, em
    "Meus Locais de Atendimento"), então por um tempo a pessoa não tem
    nenhum vínculo de filial ainda, e sem este fallback ela ficaria sem
    empresa nenhuma (empresa_atual() retornaria None) logo após criar a
    conta."""
    empresas = {}
    for c in clinicas_do_usuario():
        empresas.setdefault(c.empresa_id, c.empresa)
    if (
        current_user.is_authenticated
        and getattr(current_user, "empresa_fundadora_id", None)
        and current_user.empresa_fundadora_id not in empresas
    ):
        empresas[current_user.empresa_fundadora_id] = current_user.empresa_fundadora
    return sorted(empresas.values(), key=lambda e: (e.nome or "").lower())


def empresa_atual():
    """Empresa (tenant) ativa da sessão — é ELA que delimita tudo o que o
    usuário vê. Se o usuário só tem vínculo em uma empresa, é selecionada
    automaticamente (nenhuma tela de escolha aparece). Se tiver vínculo em
    mais de uma empresa, precisa escolher explicitamente (ver
    medico.escolher_clinica)."""
    empresas = empresas_do_usuario()
    if not empresas:
        return None

    empresa_id = session.get("empresa_id")
    if empresa_id:
        for e in empresas:
            if e.id == empresa_id:
                return e
        # a empresa salva na sessão não é mais válida para esse usuário
        session.pop("empresa_id", None)
        session.pop("clinica_id", None)

    if len(empresas) == 1:
        session["empresa_id"] = empresas[0].id
        return empresas[0]

    return None


def selecionar_empresa(empresa_id):
    """Define a empresa atual na sessão, validando que o usuário tem
    vínculo com ela."""
    if any(e.id == empresa_id for e in empresas_do_usuario()):
        if session.get("empresa_id") != empresa_id:
            # a filial padrão pré-selecionada era de outra empresa
            session.pop("clinica_id", None)
        session["empresa_id"] = empresa_id
        return True
    return False


def filiais_atuais():
    """Filiais (Clinica) da empresa atual com as quais o usuário tem
    vínculo ativo. Pode ser uma ou várias — os dados de todas elas são
    mostrados juntos, com a filial indicada em cada registro."""
    empresa = empresa_atual()
    if not empresa:
        return []
    filiais = [c for c in clinicas_do_usuario() if c.empresa_id == empresa.id]
    return sorted(filiais, key=lambda c: (c.nome or "").lower())


def filiais_atuais_ids():
    """Só os ids de filiais_atuais() — o filtro usado em toda consulta
    (`Model.clinica_id.in_(...)`) e também a fronteira de acesso."""
    return [f.id for f in filiais_atuais()]


def grupos_atuais_ids():
    """Ids dos Grupos pareados (Fatia 4) das filiais_atuais() — a chave real
    de escopo para Exame/PreparoModelo/Agendamento/PerguntaPendente/FaqItem
    a partir desta fatia. Usa .grupo_pareado() (não o id direto) para parear
    na hora qualquer filial que a migração/backfill ainda não tenha
    coberto."""
    return [f.grupo_pareado().id for f in filiais_atuais()]


def clinica_atual():
    """Uma filial "padrão" do usuário dentro da empresa atual.

    ATENÇÃO: só serve para casos em que uma única filial é genuinamente
    necessária (ex.: pré-selecionar um valor num <select>, ou os dados
    cadastrais/fiscais de um local). NÃO usar para filtrar/escopar o que
    o usuário pode ver — para isso use filiais_atuais_ids()."""
    filiais = filiais_atuais()
    if not filiais:
        return None

    clinica_id = session.get("clinica_id")
    if clinica_id:
        for c in filiais:
            if c.id == clinica_id:
                return c
        session.pop("clinica_id", None)

    return filiais[0]


def selecionar_clinica(clinica_id):
    """Define a filial PADRÃO na sessão (pré-seleção de formulário),
    validando que o usuário tem vínculo ativo com ela. Não afeta mais o
    que é visível — isso é definido pela empresa atual."""
    clinicas = clinicas_do_usuario()
    escolhida = next((c for c in clinicas if c.id == clinica_id), None)
    if not escolhida:
        return False
    session["empresa_id"] = escolhida.empresa_id
    session["clinica_id"] = escolhida.id
    return True
