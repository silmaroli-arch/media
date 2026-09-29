"""Aprendizado da base de conhecimento compartilhada a partir das respostas
dos médicos (fatia 4 da "terceira IA", pedido do Silvan, 2026-09-29).

Quando um MÉDICO responde ou aprova uma pergunta de paciente
(medico.perguntas_responder), este módulo:
1. GENERALIZA a pergunta e a resposta com a Claude - tira nome de paciente,
   telefone, dados da clínica, valores e prazos/horas/doses específicos (a
   base é global, vale para todas as clínicas - LGPD). O que não é uma
   dúvida geral de preparo é descartado.
2. Procura na base a MESMA pergunta (similaridade alta, `LIMIAR_ATUALIZACAO`):
   - não achou -> CRIA um item novo (origem "medico", não revisado);
   - achou e o médico EDITOU a resposta (ou respondeu à mão) e o árbitro diz
     que ela é "muito diferente" da que está na base, IGNORANDO diferenças só
     de prazo -> ATUALIZA o item (a versão anterior vai para o histórico e o
     dono pode desfazer) e o marca como não revisado;
   - achou e nada disso -> não faz nada. Aprovar o rascunho das IAs sem
     editar NUNCA sobrescreve um item existente.

Só roda com o "aprendizado" ligado pelo dono (PlataformaConfig.
base_aprendizado_ativo) e só para respostas de MÉDICO. Nunca derruba nem
atrasa a resposta ao paciente: roda em segundo plano (thread) e qualquer
falha é só registrada no log.
"""
import json
import re
import threading

from flask import current_app

from app.extensions import db

# Pergunta da base tão parecida que é "a mesma pergunta" - só nesse caso a
# resposta da base pode ser ATUALIZADA (evita sobrescrever "posso comer
# arroz" por causa de "posso comer batata"). Abaixo disso, vira item novo.
LIMIAR_ATUALIZACAO = 0.85

MAX_PERGUNTA = 300
MAX_RESPOSTA = 900

_PADROES_DADO_PESSOAL = [
    re.compile(r"\(?\b\d{2}\)?[\s-]?9?\d{4}[-\s]?\d{4}\b"),   # telefone
    re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"),           # CPF
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),                     # e-mail
    re.compile(r"https?://|www\.", re.IGNORECASE),              # link
    re.compile(r"R\$\s*\d"),                                    # valor em dinheiro
]

PROMPT_GENERALIZACAO = """Você prepara conteúdo para uma base de conhecimento COMPARTILHADA entre várias clínicas, sobre o preparo de exames médicos. Você receberá a pergunta de um paciente e a resposta de um médico. Devolva SOMENTE um objeto JSON, sem texto fora dele, neste formato:
{"aproveitavel": true ou false, "motivo": "texto curto", "pergunta": "...", "resposta": "..."}

Regras:
- aproveitavel=false quando a conversa NÃO é uma dúvida geral sobre o preparo ou o dia do exame - por exemplo agendamento, remarcação, resultado de exame, preço, convênio, endereço, horário de funcionamento, o caso clínico de um paciente específico, ou uma resposta que só faz sentido para aquela clínica ou aquele paciente.
- "pergunta": reescreva de forma genérica e curta, como qualquer paciente perguntaria, sem nomes, telefones, CPF, datas ou locais.
- "resposta": reescreva a resposta do médico de forma genérica, acolhedora e curta (no máximo 4 frases), mantendo a orientação dada. REMOVA nomes de pessoas, clínicas e lugares, telefones, e valores em dinheiro. SUBSTITUA prazos, horários, quantidades de horas ou dias e doses específicas por uma referência ao preparo (ex.: "no prazo indicado no seu preparo"). NUNCA invente uma orientação que o médico não deu. NUNCA recomende "confirmar/consultar/falar com o médico".
- Se a resposta do médico for tão curta (ex.: apenas "sim" ou "não") que perde o sentido fora da conversa, complete usando a pergunta, sem acrescentar informação nova. Se mesmo assim não fizer sentido, aproveitavel=false."""


def aprendizado_ativo():
    from app.models import PlataformaConfig
    return bool(PlataformaConfig.obter().base_aprendizado_ativo)


def contem_dado_pessoal(texto):
    """Rede de segurança determinística sobre o texto já generalizado: se
    ainda sobrou telefone, CPF, e-mail, link ou valor em dinheiro, o
    conteúdo NÃO entra na base (a Claude pode falhar, o regex não)."""
    return any(padrao.search(texto or "") for padrao in _PADROES_DADO_PESSOAL)


def _normalizar(texto):
    return " ".join((texto or "").lower().split())


def generalizar_com_ia(pergunta_paciente, resposta_medico, usuario_id=None):
    """Devolve {"pergunta", "resposta"} generalizados, ou None se o conteúdo
    não for aproveitável, a IA não estiver disponível ou algo falhar."""
    import app.ia_preparo as ia
    from app.custo_ia import registrar_chamada_ia

    cliente = ia._cliente_anthropic()
    if not cliente:
        return None
    try:
        resposta_ia = cliente.messages.create(
            model=ia.MODELO_PADRAO, max_tokens=600, timeout=30,
            system=PROMPT_GENERALIZACAO,
            messages=[{
                "role": "user",
                "content": f"Pergunta do paciente: {pergunta_paciente}\n\nResposta do médico: {resposta_medico}",
            }],
        )
        uso = getattr(resposta_ia, "usage", None)
        registrar_chamada_ia(
            "base_conhecimento_aprendizado", "Claude", getattr(resposta_ia, "model", ia.MODELO_PADRAO),
            getattr(uso, "input_tokens", None), getattr(uso, "output_tokens", None),
            sucesso=True, usuario_id=usuario_id,
        )
        texto = "".join(getattr(bloco, "text", "") for bloco in resposta_ia.content).strip()
        inicio, fim = texto.find("{"), texto.rfind("}")
        dados = json.loads(texto[inicio:fim + 1])
    except Exception:
        return None

    if not isinstance(dados, dict) or dados.get("aproveitavel") is not True:
        return None
    pergunta = str(dados.get("pergunta") or "").strip()
    resposta = ia._remover_recomendacao_de_consultar_medico(str(dados.get("resposta") or "").strip()).strip()
    if not pergunta or not resposta:
        return None
    if len(pergunta) > MAX_PERGUNTA or len(resposta) > MAX_RESPOSTA:
        return None
    if contem_dado_pessoal(pergunta) or contem_dado_pessoal(resposta):
        return None
    return {"pergunta": pergunta, "resposta": resposta}


def aprender_com_resposta(pergunta_pendente, resposta_medico, medico, editou):
    """Aplica as regras do módulo (ver docstring). NÃO faz commit - quem
    chama decide. Devolve {"acao": "criou"|"atualizou"|"ignorou", "motivo",
    "item_id"} (usado nos testes e no log)."""
    import app.ia_preparo as ia
    from app.base_conhecimento import buscar_na_base, atualizar_embedding_do_item
    from app.models import BaseConhecimentoItem, BaseConhecimentoHistorico

    def ignorar(motivo):
        return {"acao": "ignorou", "motivo": motivo, "item_id": None}

    if not aprendizado_ativo():
        return ignorar("aprendizado desligado")
    exame = pergunta_pendente.exame
    modelo_preparo = getattr(exame, "preparo_modelo", None)
    tipo_exame_id = getattr(modelo_preparo, "tipo_exame_id", None)
    if not tipo_exame_id:
        return ignorar("preparo sem tipo de exame")
    if not (resposta_medico or "").strip():
        return ignorar("resposta vazia")

    geral = generalizar_com_ia(pergunta_pendente.pergunta, resposta_medico, usuario_id=medico.id)
    if not geral:
        return ignorar("conteúdo não aproveitável ou IA indisponível")

    achados = buscar_na_base(geral["pergunta"], tipo_exame_id=tipo_exame_id, limite=1)
    mesma = achados[0] if achados and achados[0]["score"] >= LIMIAR_ATUALIZACAO else None

    if not mesma:
        item = BaseConhecimentoItem(
            tipo_exame_id=tipo_exame_id, pergunta=geral["pergunta"], resposta=geral["resposta"],
            origem="medico", status="ativo", revisado=False,
            autor_usuario_id=medico.id, autor_nome=medico.nome,
        )
        db.session.add(item)
        db.session.flush()
        atualizar_embedding_do_item(item)
        return {"acao": "criou", "motivo": "pergunta nova", "item_id": item.id}

    item = mesma["item"]
    if not editou:
        return ignorar("aprovado sem edição não sobrescreve item existente")
    diverge = ia._respostas_divergem(
        ia._cliente_anthropic(), item.resposta, geral["resposta"],
        paciente_id=pergunta_pendente.paciente_id, ignorar_prazos=True,
    )
    if not diverge:
        return ignorar("resposta do médico não é muito diferente da base")

    db.session.add(BaseConhecimentoHistorico(
        item_id=item.id, pergunta=item.pergunta, resposta=item.resposta,
        alterado_por_nome=medico.nome,
        motivo=f"Atualizado a partir da edição do médico (pergunta #{pergunta_pendente.id})",
    ))
    item.resposta = geral["resposta"]
    item.revisado = False
    return {"acao": "atualizou", "motivo": "edição do médico muito diferente da base", "item_id": item.id}


def _executar(pergunta_id, resposta_medico, medico_id, editou):
    from app.models import PerguntaPendente, Usuario

    try:
        pergunta = PerguntaPendente.query.get(pergunta_id)
        medico = Usuario.query.get(medico_id)
        if not pergunta or not medico:
            return None
        resultado = aprender_com_resposta(pergunta, resposta_medico, medico, editou)
        db.session.commit()
        current_app.logger.info("Aprendizado da base (pergunta %s): %s", pergunta_id, resultado)
        return resultado
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Falha no aprendizado da base (pergunta %s).", pergunta_id)
        return None


def agendar_aprendizado(pergunta_id, resposta_medico, medico_id, editou):
    """Dispara o aprendizado SEM atrasar a resposta ao paciente: em segundo
    plano (thread com contexto próprio da aplicação). Com
    app.config["BASE_APRENDIZADO_SINCRONO"] = True (usado nos testes) roda
    na hora, no mesmo fluxo. Nunca levanta exceção."""
    try:
        if not aprendizado_ativo():
            return None
        app = current_app._get_current_object()
        if app.config.get("BASE_APRENDIZADO_SINCRONO"):
            return _executar(pergunta_id, resposta_medico, medico_id, editou)

        def _rodar():
            with app.app_context():
                try:
                    _executar(pergunta_id, resposta_medico, medico_id, editou)
                finally:
                    db.session.remove()

        threading.Thread(target=_rodar, daemon=True).start()
    except Exception:
        current_app.logger.exception("Não foi possível agendar o aprendizado da base.")
    return None
