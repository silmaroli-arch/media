"""Utilitários para trabalhar com o contexto (tenant) do usuário da equipe
(médico/secretária) durante a sessão.

Fatia 5 (passo 4): o tenant passou a ser o GRUPO, não mais a Empresa - cada
Grupo já é sua própria unidade completa (cobrança, fiscal, endereço - ver
Fatia 5 passo 1), então não existe mais o conceito de "várias filiais
dentro de uma mesma empresa". Uma pessoa com vínculo em mais de um Grupo
simplesmente pertence a vários Grupos independentes; se for só um, ele é
escolhido automaticamente (nenhuma tela de escolha aparece) - se for mais
de um, precisa escolher explicitamente (ver medico.escolher_clinica).

As funções com nome "empresa"/"clinica"/"filial" são mantidas por
compatibilidade com o código existente em routes_medico.py/routes_
relatorios.py/routes_paciente.py/routes_dono.py (evita reescrever cada
chamada de uma vez só) - mas todas operam sobre Grupo agora, não mais
sobre Empresa/Clinica. `filiais_atuais()` sempre devolve uma lista de 0 ou
1 elemento (o próprio Grupo atual) - não existe mais "a empresa tem várias
filiais". `Grupo` foi desenhado (Fatia 5 passo 1) para espelhar os mesmos
nomes de campo que Empresa/Clinica tinham (nome, razao_social, cnpj,
endereço, fiscal_*, status, bloqueada, valor_mensal_estimado,
codigo_cadastro_paciente) - é por isso que essa troca é segura sem
reescrever os templates que leem esses campos.

A sessão guarda o Grupo ativo em session['empresa_id'] (nome antigo,
mantido só pra não invalidar sessões já abertas). session['clinica_id']
não é mais usado (não existe mais "filial padrão" distinta do Grupo).
"""
from urllib.parse import urlparse

from flask import session
from flask_login import current_user

from app.extensions import db
from app.models import GrupoMembro


def proximo_seguro(destino_bruto):
    """Valida o parâmetro "next" (usado pelo @login_required do
    Flask-Login e propagado por auth/login.html e
    medico/escolher_clinica.html) antes de redirecionar - só aceita um
    caminho relativo do próprio site (nunca um domínio externo, o que
    abriria um open redirect). Usado para o médico conseguir criar um
    atalho direto pra uma tela específica (ex.: /equipe/portal, ver
    medico.portal_atendimento) que, ao logar - mesmo passando pela tela de
    escolha de empresa (ver medico.escolher_clinica/staff_required, para
    quem tem vínculo em mais de um Grupo) -, cai direto nela em vez de
    sempre ir para o painel principal (index())."""
    if not destino_bruto:
        return None
    se_e_absoluto_ou_protocolo_relativo = urlparse(destino_bruto).netloc or urlparse(destino_bruto).scheme
    if se_e_absoluto_ou_protocolo_relativo:
        return None
    if not destino_bruto.startswith("/") or destino_bruto.startswith("//"):
        return None
    return destino_bruto


def verificar_vencimento_empresa(grupo):
    """Atualiza (e salva) o status do Grupo para 'inadimplente' se o
    trial já venceu (ver Grupo.verificar_vencimento_trial()). Não bloqueia
    por conta própria — isso continua sendo uma decisão manual do dono da
    plataforma."""
    if grupo.verificar_vencimento_trial():
        db.session.commit()


# Alias explícito com o nome novo - mesma função, dois nomes durante a
# transição (routes_paciente.py já usa o nome novo desde a Fatia 5 passo 3).
verificar_vencimento_grupo = verificar_vencimento_empresa


def verificar_vencimento(grupo):
    """Antes recebia uma Clinica e delegava pra empresa dela; agora o
    Grupo já É o tenant, então é a mesma verificação direto."""
    verificar_vencimento_empresa(grupo)


def clinicas_do_usuario():
    """Grupos em que o usuário logado tem vínculo ATIVO e que não estão
    bloqueados pelo dono da plataforma (Grupo.bloqueada). Na esmagadora
    maioria dos casos é um só."""
    if not current_user.is_authenticated or not current_user.is_staff:
        return []
    grupos = [
        gm.grupo for gm in GrupoMembro.query.filter_by(usuario_id=current_user.id, ativo=True).all()
        if not gm.grupo.bloqueada
    ]
    for g in grupos:
        verificar_vencimento_empresa(g)
    return sorted(grupos, key=lambda g: (g.nome or "").lower())


# Nome novo, mesma função - ver docstring do módulo.
grupos_do_usuario = clinicas_do_usuario


def tem_algum_vinculo_de_grupo():
    """Fatia 6: True se o usuário logado tem QUALQUER vínculo de Grupo
    (`GrupoMembro` ativo), mesmo que o Grupo esteja bloqueado - diferente
    de `clinicas_do_usuario()`, que já filtra os bloqueados fora.

    Usada só por `staff_required` pra distinguir dois casos que
    `clinicas_do_usuario()` sozinha não separa (ambos dão lista vazia):
    (a) a conta é solo de verdade, nunca teve Grupo - deve ser deixada
    passar, com escopo pessoal (ver filtro_escopo_atual); (b) a conta tem
    Grupo, mas ele foi bloqueado pelo dono da plataforma - deve continuar
    sendo barrada, como sempre foi."""
    if not current_user.is_authenticated or not current_user.is_staff:
        return False
    return GrupoMembro.query.filter_by(usuario_id=current_user.id, ativo=True).first() is not None


def empresas_do_usuario():
    """Tenants (Grupos) distintos em que o usuário tem vínculo ativo.
    Antes da Fatia 5 isso agregava várias Clinica sob uma mesma Empresa;
    como cada Grupo já é uma unidade completa, é a mesma lista de
    clinicas_do_usuario() acima."""
    return clinicas_do_usuario()


def empresa_atual():
    """Grupo (tenant) ativo da sessão — é ELE que delimita tudo o que o
    usuário vê. Se o usuário só tem vínculo em um Grupo, é selecionado
    automaticamente (nenhuma tela de escolha aparece). Se tiver vínculo em
    mais de um, precisa escolher explicitamente (ver
    medico.escolher_clinica)."""
    empresas = empresas_do_usuario()
    if not empresas:
        return None

    empresa_id = session.get("empresa_id")
    if empresa_id:
        for e in empresas:
            if e.id == empresa_id:
                return e
        # o grupo salvo na sessão não é mais válido para esse usuário
        session.pop("empresa_id", None)
        session.pop("clinica_id", None)

    if len(empresas) == 1:
        session["empresa_id"] = empresas[0].id
        return empresas[0]

    return None


# Nome novo, mesma função - ver docstring do módulo.
grupo_atual = empresa_atual


def selecionar_empresa(empresa_id):
    """Define o Grupo atual na sessão, validando que o usuário tem
    vínculo com ele."""
    if any(e.id == empresa_id for e in empresas_do_usuario()):
        if session.get("empresa_id") != empresa_id:
            session.pop("clinica_id", None)
        session["empresa_id"] = empresa_id
        return True
    return False


selecionar_grupo = selecionar_empresa


def filiais_atuais():
    """Fatia 5: não existe mais "várias filiais dentro da empresa atual" -
    o Grupo atual JÁ é a única unidade. Devolve uma lista de 0 ou 1
    elemento só para não obrigar a reescrever todo o código que ainda
    itera filiais_atuais() em routes_medico.py - ver clinica_atual() logo
    abaixo, que é o jeito direto de pegar esse único elemento."""
    empresa = empresa_atual()
    return [empresa] if empresa else []


def filiais_atuais_ids():
    """Só os ids de filiais_atuais() — o filtro usado em toda consulta
    (`Model.grupo_id.in_(...)`) e também a fronteira de acesso."""
    return [f.id for f in filiais_atuais()]


def grupos_atuais_ids():
    """Fatia 5: o Grupo atual JÁ é o grupo - não precisa mais de
    .grupo_pareado() (essa ponte só existia enquanto Clinica era a
    unidade real e Grupo, a sombra dela)."""
    return filiais_atuais_ids()


def clinica_atual():
    """Fatia 5: não existe mais uma filial "padrão" distinta do Grupo -
    esta função devolve o próprio Grupo atual. Mantida pelo nome por
    compatibilidade com o código existente (dados cadastrais/fiscais,
    formulários que pré-selecionam uma "filial")."""
    filiais = filiais_atuais()
    return filiais[0] if filiais else None


def selecionar_clinica(clinica_id):
    """Fatia 5: não existe mais filial distinta do Grupo - selecionar uma
    "clínica" agora é o mesmo que selecionar o Grupo em si. Mantida pelo
    nome só por compatibilidade."""
    grupos = clinicas_do_usuario()
    escolhido = next((g for g in grupos if g.id == clinica_id), None)
    if not escolhido:
        return False
    session["empresa_id"] = escolhido.id
    session.pop("clinica_id", None)
    return True


def papel_no_grupo_atual():
    """Papel (dono/administrador/membro) do usuário logado no Grupo
    atual, ou None se não houver Grupo atual/vínculo."""
    grupo = grupo_atual()
    if not grupo:
        return None
    membro = GrupoMembro.query.filter_by(grupo_id=grupo.id, usuario_id=current_user.id, ativo=True).first()
    return membro.papel if membro else None


def filtro_escopo_atual(coluna_grupo, coluna_dono):
    """Fatia 6: filtro SQLAlchemy pro escopo de dados do usuário logado -
    substitui o antigo `coluna_grupo.in_(grupos_atuais_ids())` usado em toda
    consulta de Exame/PreparoModelo/Agendamento/PerguntaPendente/FaqItem.

    Desde a Fatia 6, uma conta pode existir e ser plenamente usável SEM
    nunca ter um Grupo (ver routes_auth.py:cadastro() - o Grupo só nasce se
    a pessoa decidir convidar alguém, via grupo.novo()). Enquanto isso, os
    dados que ela cria (pacientes/exames/agendamentos/etc.) ficam com
    `grupo_id` NULL e são escopados pelo dono pessoal
    (`criado_por_id`/`cadastrado_por_id`, conforme o modelo) em vez de por
    Grupo. Se/quando a pessoa forma um Grupo de verdade,
    migrar_dados_pessoais_para_grupo() abaixo migra esses dados de uma vez
    só - a partir daí o filtro volta a ser 100% por `grupo_id`, igual
    sempre foi.

    Uso: `Exame.query.filter(filtro_escopo_atual(Exame.grupo_id, Exame.criado_por_id))`."""
    grupo_ids = grupos_atuais_ids()
    if grupo_ids:
        return coluna_grupo.in_(grupo_ids)
    return coluna_dono == current_user.id


def migrar_dados_pessoais_para_grupo(usuario, grupo):
    """Fatia 6: chamada em todo momento em que uma conta solo PASSA A TER um
    Grupo - seja criando um novo (ver routes_grupo.py:novo()) ou aceitando o
    convite de um Grupo já existente (ver routes_grupo.py:responder_convite())
    - migra todo o histórico pessoal dela (pacientes/exames/preparo-modelos/
    agendamentos/perguntas/faq com dono pessoal == usuario e `grupo_id`
    ainda NULL) pro Grupo em questão. Sem isso, o histórico da pessoa
    "sumiria" da vista assim que ela passasse a ter um Grupo, porque as
    consultas passam a ser 100% por `grupo_id` de novo quando há um Grupo
    (ver filtro_escopo_atual acima).

    Idempotente: só pega registros com `grupo_id IS NULL` ainda, então
    rodar de novo (ou chamar por engano) não duplica nem sobrescreve nada
    já migrado."""
    from app.models import Exame, PreparoModelo, Agendamento, PerguntaPendente, FaqItem, Paciente, GrupoPaciente

    Exame.query.filter_by(criado_por_id=usuario.id, grupo_id=None).update({"grupo_id": grupo.id})
    PreparoModelo.query.filter_by(criado_por_id=usuario.id, grupo_id=None).update({"grupo_id": grupo.id})
    Agendamento.query.filter_by(criado_por_id=usuario.id, grupo_id=None).update({"grupo_id": grupo.id})
    PerguntaPendente.query.filter_by(criado_por_id=usuario.id, grupo_id=None).update({"grupo_id": grupo.id})
    FaqItem.query.filter_by(criado_por_id=usuario.id, grupo_id=None).update({"grupo_id": grupo.id})

    pacientes_pessoais = Paciente.query.filter_by(cadastrado_por_id=usuario.id).all()
    for paciente in pacientes_pessoais:
        ja_associado = GrupoPaciente.query.filter_by(grupo_id=grupo.id, paciente_id=paciente.id).first()
        if not ja_associado:
            db.session.add(GrupoPaciente(grupo_id=grupo.id, paciente_id=paciente.id))
