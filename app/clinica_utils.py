"""
Utilitários para trabalhar com a "clínica atual" de um usuário da equipe
(médico/secretária) durante a sessão — já que, na plataforma, um mesmo
usuário pode estar vinculado a mais de uma clínica.

A clínica escolhida fica guardada na sessão Flask (session['clinica_id']).
"""
from flask import session
from flask_login import current_user

from app.extensions import db
from app.models import Clinica


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


def clinica_atual():
    """Retorna o objeto Clinica selecionado na sessão, validando que o
    usuário ainda tem vínculo ativo com ela e que ela não foi bloqueada.
    Se só existir uma clínica disponível, seleciona automaticamente.
    """
    clinicas = clinicas_do_usuario()
    if not clinicas:
        return None

    clinica_id = session.get("clinica_id")
    if clinica_id:
        for c in clinicas:
            if c.id == clinica_id:
                return c
        # a clínica salva na sessão não é mais válida para esse usuário
        session.pop("clinica_id", None)

    if len(clinicas) == 1:
        session["clinica_id"] = clinicas[0].id
        return clinicas[0]

    return None


def selecionar_clinica(clinica_id):
    """Define a clínica atual na sessão, validando que o usuário tem acesso a ela."""
    clinicas = clinicas_do_usuario()
    if any(c.id == clinica_id for c in clinicas):
        session["clinica_id"] = clinica_id
        return True
    return False
