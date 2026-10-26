"""Chat de ajuda sobre o USO do MedIA, para a equipe (médico/secretária) -
pedido do Silvan, 2026-10-23.

Um botão "Ajuda" flutuante em todas as telas da equipe abre uma janelinha
de chat. A IA que responde é escolhida pelo dono em Painel do dono >
Configurações (`PlataformaConfig.ia_ajuda_app`: Claude, ChatGPT ou
Gemini) e responde só com base no MANUAL abaixo - não é a IA do preparo:
não responde dúvida médica nem de preparo e não vê dado de paciente.
Quando não sabe, começa a resposta com MARCADOR_NAO_SEI e a tela oferece
"Enviar para o suporte", que vira uma mensagem do "Fale com a gente".

O MANUAL é texto no código - ao mudar uma tela, atualize o trecho dela."""
from datetime import datetime, timedelta

from flask import current_app

MARCADOR_NAO_SEI = "AJUDA_NAO_SEI"

# Mensagens anteriores da mesma pessoa que entram como contexto da conversa.
JANELA_CONVERSA_MINUTOS = 30
MAX_MENSAGENS_CONTEXTO = 6

# Nome da tela atual (endpoint do Flask) - vai junto com a pergunta, para a
# IA saber de onde a pessoa está perguntando.
TELAS = {
    "medico.dashboard": "Painel",
    "auth.meus_dados": "Meus dados",
    "medico.primeiros_passos": "Primeiros passos",
    "medico.preparo_modelos_lista": "Exames & preparo",
    "medico.preparo_modelos_novo": "Exames & preparo (novo modelo de preparo)",
    "medico.preparo_modelos_editar": "Exames & preparo (editar modelo de preparo)",
    "medico.preparos_medicos": "Médicos por preparo",
    "medico.pacientes_lista": "Pacientes",
    "medico.medico_agenda_pessoal": "Agenda de Exames",
    "medico.portal_atendimento": "Portal de atendimento rápido",
    "medico.perguntas_pendentes": "Perguntas pendentes",
    "medico.perguntas_respondidas": "Últimas respondidas",
    "medico.faq_lista": "Meu FAQ",
    "medico.base_compartilhada": "FAQ do MedIA",
    "medico.testar_ia": "Testar IA",
    "grupo.meus_grupos": "Grupos de trabalho",
    "medico.fale_com_a_gente": "Fale com a gente",
    "medico.minha_licenca": "Minha licença",
}

MANUAL = """MANUAL DO MedIA (área da equipe: médico e secretária)

O QUE É O MedIA
- Plataforma para clínicas tirarem as dúvidas dos pacientes sobre o PREPARO de exames, pelo WhatsApp (e pela área do paciente no site). Duas IAs respondem com base no preparo cadastrado pelo médico; o médico revisa e aprova antes de a resposta ir ao paciente (se a aprovação estiver ligada). Respostas aprovadas entram no "Meu FAQ" e passam a responder sozinhas quando a mesma pergunta se repetir.
- O app pode ser instalado no celular como atalho (aviso "Instale o MedIA como um app" no topo, ou em Primeiros passos). Também dá para ativar notificações no celular para saber quando chega pergunta nova (aviso "Ative as notificações").

MENU (lado esquerdo; no celular, pelo botão ☰)
- Painel: resumo do dia, próximos exames e a chave "Exigir minha aprovação antes de responder o paciente". Ligada: toda resposta das IAs espera o médico aprovar. Desligada: as respostas vão direto ao paciente - exceto as que usam conhecimento geral (fora do preparo) ou falam de medicamento não cadastrado, que sempre passam pelo médico.
- Meus dados: nome, CPF, e-mail, telefone, data de nascimento e especialidade. Pede a senha (ou Google) antes de mostrar. Lá também: "Trocar senha"/"Criar senha", ligar/desligar a conta Google e "Solicitar exclusão da conta" (informa o motivo; quem exclui é o administrador da plataforma).
- Primeiros passos: checklist para começar (instalar o atalho, cadastrar o primeiro modelo de preparo e exame, agendar um exame para o paciente de teste, que tem o nome do próprio médico).
- Exames & preparo: lista dos modelos de preparo (coluna "Médico Criador" = médico principal). "+ Novo" cadastra o modelo de preparo e o exame juntos: nome, "Sua especialidade" (obrigatória), tipo de exame, duração (minutos), e as abas Instruções gerais, Cortes de alimentação (jejum em horas antes), Medicamentos (suspender X dias antes, ou pode manter), Informações gerais, Alimentos (proibidos com prazo ou permitidos) e Exames proibidos antes. Também dá para importar o preparo de um PDF ou de uma planilha Excel. "Editar" altera; ao editar, os exames que usam o modelo são atualizados. Funciona também pelo celular. No bloco "Preparo original (PDF)" (logo abaixo do nome) anexa-se o PDF do preparo como o médico escreveu (o PDF importado já vem marcado): o paciente abre o link do WhatsApp na aba "Preparo do médico" e tem a aba "Linha do tempo" como segunda opção; "Baixar PDF" baixa o PDF da aba aberta. Ao anexar, a IA monta a "Versão em página para o celular": em "Revisar e aprovar" o médico confere o texto ao lado do PDF (palavras/números do PDF que não aparecem ficam em vermelho), toca no texto para corrigir e clica "Aprovar e publicar para o paciente" (ou "Salvar sem publicar", "Tirar do ar", "Gerar de novo com a IA"). Sem aprovação, o paciente vê só o PDF. A caixa "Mostrar exames de todas as especialidades" lista tipos de exame de outras especialidades.
- Médicos por preparo: quais médicos fazem cada exame/preparo. Botão "Médicos" abre uma janela para marcar os colegas; o associado passa a atender (agendar) o exame e também pode editar o preparo. O médico principal sempre fica. Precisa da permissão "Associar médicos aos exames".
- Pacientes: lista com busca. "Novo paciente" abre uma janela: digite o CPF primeiro - se for novo, preenche nome, telefone e data de nascimento; se já existir no MedIA, oferece "Importar para este grupo"; se já for paciente do grupo, avisa. "Editar" (janela) altera nome, CPF, telefone e observações - não registre dados de saúde nas observações. "Todos os agendamentos" mostra os exames passados e futuros do paciente.
- Agenda de Exames: calendário (Mês, Semana, Dia e Lista; no celular, Lista). O médico vê só os exames dele; a secretária vê os de todos os médicos do grupo e pode filtrar por médico. Cada médico tem uma cor. "Agendar exame" (ou clicar num dia vazio) abre a janela: paciente, exame, médico (só os que atendem o exame), data/hora e observações. O sistema recusa dois exames do mesmo médico no mesmo horário, considerando a duração do exame, e mostra a previsão de término. Clicar num exame abre os detalhes: Atendimento (anotações), Resultado (anexar PDF), Encerrar, "Remarcar para" (nova data e hora) e "Cancelar exame" (apaga o agendamento). Remarcar e cancelar somem em exame encerrado. O paciente NÃO recebe aviso automático de remarcação/cancelamento pelo WhatsApp - avise por fora.
- Portal de atendimento rápido: a fila de perguntas dos pacientes, numa tela enxuta (bom para deixar como atalho no celular). "Atualizar perguntas" recarrega. Clicar numa pergunta abre a janela com: a pergunta do paciente e a "Pergunta corrigida pela IA" (digitação corrigida), o histórico de conversas, a sugestão de cada IA (ou o motivo de não ter respondido: não soube, deu erro, não configurada), a sugestão do FAQ do MedIA (se ligado) e o "Rascunho final". Botão "Usar" copia uma sugestão para o rascunho. O rascunho já vem com as datas do paciente; ao reutilizar a resposta para outro paciente, o sistema ajusta as datas sozinho. "Salvar no Meu FAQ" (marcado por padrão): desmarque se a resposta for só para aquele paciente. "Aprovar e enviar ao paciente" envia (pelo WhatsApp, se a pergunta veio de lá). "Apagar pergunta" tira da fila sem responder. Aviso amarelo "conhecimento geral" = a resposta não está no preparo cadastrado; revise e ajuste ao que vale na clínica.
- Perguntas pendentes: a mesma fila do portal, na tela completa (as sugestões das IAs lado a lado).
- Últimas respondidas: histórico de respostas dadas; "Editar" corrige a resposta (e o item do Meu FAQ junto); "Excluir" apaga (e tira do Meu FAQ).
- Meu FAQ: perguntas e respostas aprovadas que respondem sozinhas quando um paciente faz a mesma pergunta. Filtro por exame e busca por texto. "Adicionar manualmente" (janela) cadastra uma pergunta/resposta. "Editar" e "Excluir". Para datas do paciente na resposta, use [data do exame], [horário do exame], [3 dias antes do exame] ou [12 horas antes do exame] - cada paciente recebe as datas dele.
- FAQ do MedIA: base de perguntas e respostas compartilhada entre as clínicas, usada como apoio às IAs (o preparo do médico sempre tem prioridade). O médico vê os exames da especialidade dele, filtra por exame, busca por texto e pode "Sugerir alteração" ou "Sugerir um item novo" - o administrador aprova. As respostas aprovadas pelos médicos alimentam essa base, sem dados pessoais.
- Testar IA: o médico faz uma pergunta como se fosse paciente, sobre um dos seus exames, e vê o que as IAs responderiam (a resposta cai em Perguntas pendentes).
- Grupos de trabalho ("Meus grupos"): grupos de trabalho (clínicas) de que a pessoa participa. "Selecionar" troca o grupo ativo (aparece no topo da tela). "Dados do grupo" (janela) altera o nome. "Ver membros / Convidar" (janela): buscar colega por CPF e convidar, criar conta para ele, ajustar permissões, remover. "Criar novo grupo". "Excluir" só funciona com o grupo vazio (sem pacientes, exames etc. de outras pessoas).
- Permissões de cada pessoa (em Ver membros > Permissões): Cadastrar pacientes, Gerenciar equipe, Editar dados do grupo, Associar médicos aos exames (Médicos por preparo). Valem para a conta em todos os grupos.
- Fale com a gente: mensagem para o administrador da plataforma (dúvida, sugestão, problema); a resposta aparece na mesma tela.
- Minha licença (médico): situação da licença, vencimento e pagamentos (Pix ou link de pagamento).
- Sino no topo: avisos e anúncios do administrador.

WHATSAPP (como o paciente usa)
- O paciente manda mensagem para o número da clínica, informa o CPF e a data de nascimento (com ou sem barras, ex.: 16111983). Com um exame em preparo, recebe o link do preparo completo e pode perguntar. Com mais de um exame, escolhe pela lista (com data e horário); "trocar" muda de exame. Sem exame em preparo, é avisado e pode mandar outro CPF.
- Ao agendar um exame, o paciente recebe uma mensagem no WhatsApp com o exame, a data e a orientação para mandar o CPF.
- As perguntas do paciente aparecem no Portal de atendimento / Perguntas pendentes. Pedidos de remarcação ainda não têm fluxo próprio.

DICAS
- Para testar sem paciente real: use o paciente de teste (o próprio médico) e o "Testar IA".
- O preparo cadastrado é a fonte principal das respostas: quanto mais completo, melhores as respostas das IAs.
"""

PROMPT_SISTEMA = """Você é a Malu, a assistente de ajuda do MedIA (se perguntarem seu nome, é Malu). Responde dúvidas da equipe da clínica (médicos e secretárias) sobre COMO USAR o sistema, com base SOMENTE no manual abaixo.

Regras:
- Português do Brasil, curto e prático: diga onde clicar, passo a passo quando ajudar (no máximo uns 6 passos ou 5 frases).
- Use o nome exato das telas e botões do manual, entre aspas.
- Se a pessoa estiver numa tela (informada em "Tela atual"), use isso para responder melhor.
- Não responda dúvidas médicas, de preparo de exame de um paciente, nem assuntos fora do uso do MedIA - diga que este chat é só sobre o uso do sistema.
- Você não vê dados de pacientes nem da clínica; não invente funções, telas ou botões que não estão no manual.
- Quando o manual não responder a dúvida (ou for um problema/erro do sistema), comece a resposta EXATAMENTE com o texto AJUDA_NAO_SEI seguido de uma quebra de linha e, em uma frase, diga que vai ser preciso falar com o suporte.

""" + MANUAL


def _historico(usuario_id):
    from app.models import AjudaMensagem

    limite = datetime.utcnow() - timedelta(minutes=JANELA_CONVERSA_MINUTOS)
    anteriores = (
        AjudaMensagem.query.filter(AjudaMensagem.usuario_id == usuario_id, AjudaMensagem.criado_em >= limite)
        .order_by(AjudaMensagem.criado_em.desc())
        .limit(MAX_MENSAGENS_CONTEXTO)
        .all()
    )
    return list(reversed(anteriores))


def responder(pergunta, usuario, tela=None):
    """Grava e devolve a `AjudaMensagem` respondida (resposta None quando a
    IA escolhida não está disponível ou falhou)."""
    from app.custo_ia import registrar_chamada_ia
    from app.extensions import db
    from app.ia_modelos import (
        criar_chat_openai, criar_mensagem_claude, modelo_claude, modelo_gemini, modelo_openai, texto_claude,
    )
    from app.ia_preparo import _PROVEDORES_CHAT
    from app.models import AjudaMensagem, PlataformaConfig

    provedor = PlataformaConfig.obter().ia_ajuda_app or "Claude"
    nome_tela = TELAS.get(tela or "", "")
    mensagens = []
    for anterior in _historico(usuario.id):
        mensagens.append({"role": "user", "content": anterior.pergunta})
        if anterior.resposta:
            mensagens.append({"role": "assistant", "content": anterior.resposta})
    mensagens.append({"role": "user", "content": (f"Tela atual: {nome_tela}\n\n" if nome_tela else "") + pergunta})

    texto = None
    fabrica_cliente, _ = _PROVEDORES_CHAT.get(provedor, (None, None))
    cliente = fabrica_cliente() if fabrica_cliente else None
    if cliente:
        try:
            if provedor == "Claude":
                resposta = criar_mensagem_claude(
                    cliente, modelo_claude(), 600, esforco="low", system=PROMPT_SISTEMA, messages=mensagens,
                )
                uso = getattr(resposta, "usage", None)
                registrar_chamada_ia(
                    "ajuda_app", "Claude", getattr(resposta, "model", modelo_claude()),
                    getattr(uso, "input_tokens", None), getattr(uso, "output_tokens", None),
                    sucesso=True, usuario_id=usuario.id,
                )
                texto = texto_claude(resposta)
            elif provedor == "ChatGPT":
                resposta = criar_chat_openai(
                    cliente, modelo_openai(), 600, esforco="low",
                    messages=[{"role": "system", "content": PROMPT_SISTEMA}] + mensagens,
                )
                uso = getattr(resposta, "usage", None)
                registrar_chamada_ia(
                    "ajuda_app", "ChatGPT", getattr(resposta, "model", modelo_openai()),
                    getattr(uso, "prompt_tokens", None), getattr(uso, "completion_tokens", None),
                    sucesso=True, usuario_id=usuario.id,
                )
                texto = (resposta.choices[0].message.content or "").strip()
            else:  # Gemini - a conversa vai como um texto só
                from google.genai import types as genai_types

                conversa = "\n\n".join(
                    ("Usuário: " if m["role"] == "user" else "Assistente: ") + m["content"] for m in mensagens
                )
                resposta = cliente.models.generate_content(
                    model=modelo_gemini(), contents=conversa,
                    config=genai_types.GenerateContentConfig(system_instruction=PROMPT_SISTEMA, max_output_tokens=600),
                )
                uso = getattr(resposta, "usage_metadata", None)
                registrar_chamada_ia(
                    "ajuda_app", "Gemini", getattr(resposta, "model_version", None) or modelo_gemini(),
                    getattr(uso, "prompt_token_count", None), getattr(uso, "candidates_token_count", None),
                    sucesso=True, usuario_id=usuario.id,
                )
                texto = (getattr(resposta, "text", None) or "").strip()
        except Exception:
            current_app.logger.exception("Falha ao consultar %s na ajuda do app", provedor)
            texto = None

    nao_soube = not texto or texto.lstrip().startswith(MARCADOR_NAO_SEI)
    if texto and texto.lstrip().startswith(MARCADOR_NAO_SEI):
        texto = texto.lstrip()[len(MARCADOR_NAO_SEI):].strip() or None
    mensagem = AjudaMensagem(
        usuario_id=usuario.id, pergunta=pergunta, resposta=texto, tela=nome_tela or None,
        nao_soube=nao_soube, provedor=provedor,
    )
    db.session.add(mensagem)
    db.session.commit()
    return mensagem
