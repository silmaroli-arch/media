"""Mensagem de boas-vindas do MÉDICO recém-cadastrado (pedido do Silvan,
2026-09-30): aparece num popup no primeiro acesso e fica guardada como
notificação no sininho (a mesma mensagem e as instruções), para o médico
poder reler quando quiser."""
from datetime import datetime, timedelta

from app.extensions import db


def titulo_boas_vindas():
    return "Bem-vindo(a) ao MedIA!"


def texto_boas_vindas(nome):
    primeiro_nome = (nome or "").strip().split(" ")[0] if (nome or "").strip() else ""
    saudacao = f"Olá, {primeiro_nome}! " if primeiro_nome else ""
    return (
        f"{saudacao}Sua conta foi criada com sucesso. O MedIA ajuda seus pacientes a tirarem dúvidas "
        "sobre o preparo dos exames, e você acompanha e aprova as respostas por aqui. "
        "Para ver as instruções do sistema, é só clicar no sininho (ícone de sino no topo da tela): "
        "lá ficam esta mensagem e o passo a passo para começar."
    )


PASSOS_INICIAIS = (
    "Passo a passo para começar: "
    "1) Em Exames & preparo, cadastre um modelo de preparo (você pode importar um PDF) e escolha o Tipo de exame, "
    "que liga o seu preparo à base de conhecimento da plataforma. "
    "2) Em Meus dados, confira o telefone e escolha a sua especialidade. "
    "3) Em Agendar exame, crie um agendamento para o seu paciente de teste. "
    "4) Em Testar IA, converse como se fosse um paciente para ver as respostas. "
    "Você pode rever o checklist a qualquer momento em Primeiros passos, no menu lateral."
)


def criar_notificacoes_boas_vindas(usuario):
    """Grava as duas notificações do sininho (sem commit - quem chama decide).
    A de boas-vindas fica por cima (mais recente) na lista."""
    from app.models import Notificacao

    agora = datetime.utcnow()
    db.session.add(Notificacao(
        usuario_id=usuario.id, tipo="boas_vindas", titulo="Primeiros passos no MedIA",
        mensagem=PASSOS_INICIAIS, link_endpoint="medico.primeiros_passos",
        criado_em=agora - timedelta(seconds=1),
    ))
    db.session.add(Notificacao(
        usuario_id=usuario.id, tipo="boas_vindas", titulo=titulo_boas_vindas(),
        mensagem=texto_boas_vindas(usuario.nome), link_endpoint="medico.dashboard",
        criado_em=agora,
    ))
