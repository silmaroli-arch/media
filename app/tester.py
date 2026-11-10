"""Programa de testadores (pedido do Silvan, 2026-11-10): enquanto ligado
pelo dono (/dono, "Programa de testadores"), o cadastro público (/cadastro)
aceita só um número limitado de pessoas (padrão: 10) e o primeiro mês de
quem se cadastra é gratuito (padrão: 30 dias). Passando o período, a licença
segue o fluxo normal (trial -> ativa -> cobrança)."""
from app.models import PlataformaConfig, Usuario


def estado():
    """Situação atual do programa: ligado, vagas, quantas já usadas, dias grátis."""
    config = PlataformaConfig.obter()
    vagas = config.tester_vagas if config.tester_vagas is not None else 10
    usadas = Usuario.query.filter_by(tester=True).count()
    ativo = bool(config.tester_ativo)
    return {
        "ativo": ativo,
        "vagas": vagas,
        "usadas": usadas,
        "restantes": max(vagas - usadas, 0),
        "dias": config.tester_dias or 30,
        "esgotado": ativo and usadas >= vagas,
    }
