"""Monitor das perguntas dos pacientes (pedido do Silvan, 2026-10-26).

O caminho de uma pergunta passa por várias funções (validador, lista da
ANVISA, busca na internet, 2 IAs, divergência, junção). Em vez de mudar o
retorno de cada uma, elas deixam "anotações" (`anotar`) no contexto da
requisição e quem trata a pergunta (WhatsApp e chat web) fecha o registro com
`registrar`. Nunca derruba o chat: qualquer erro aqui é só logado."""
import logging

from flask import g

from app.extensions import db

logger = logging.getLogger(__name__)


def anotar(**campos):
    """Guarda informações do caminho da pergunta em andamento."""
    try:
        if not hasattr(g, "_monitor_trace"):
            g._monitor_trace = {}
        g._monitor_trace.update(campos)
    except Exception:
        pass


def _retirar_anotacoes():
    try:
        return g.pop("_monitor_trace", None) or {}
    except Exception:
        return {}


def registrar(canal, paciente, exame, grupo_id, pergunta, desfecho, *, resultado_ia=None, pendente=None,
              resposta_final=None, pergunta_corrigida=None, validacao=None, faq_item=None):
    """Grava o MonitorPergunta da pergunta. Não faz commit (vai junto com a
    transação de quem chamou); devolve o registro ou None se falhou."""
    from app.models import MonitorPergunta

    notas = _retirar_anotacoes()
    try:
        resultado_ia = resultado_ia or {}
        por_provedor = resultado_ia.get("por_provedor") or {}
        registro = MonitorPergunta(
            canal=canal, paciente_id=getattr(paciente, "id", None), exame_id=getattr(exame, "id", None),
            grupo_id=grupo_id, pergunta=pergunta, pergunta_corrigida=pergunta_corrigida or None,
            validacao=validacao or notas.get("validacao"), desfecho=desfecho,
            faq_item_id=getattr(faq_item, "id", None),
            resposta_claude=por_provedor.get("Claude"), resposta_chatgpt=por_provedor.get("ChatGPT"),
            resposta_gemini=por_provedor.get("Gemini"), resposta_base=por_provedor.get("Base"),
            ias_com_erro=",".join(resultado_ia.get("falhas") or []) or None,
            modelos=notas.get("modelos"), modelo_validador=notas.get("modelo_validador"),
            consultou_anvisa=bool(notas.get("anvisa")), anvisa_texto=notas.get("anvisa") or None,
            consultou_internet=bool(resultado_ia.get("pesquisa_internet")),
            internet_texto=resultado_ia.get("pesquisa_internet"),
            divergiu=notas.get("divergiu"), sintese_usada=bool(notas.get("sintese_usada")),
            exige_revisao=bool(resultado_ia.get("exige_revisao_medicamento") or resultado_ia.get("exige_revisao_base")),
            rascunho=resultado_ia.get("final"), resposta_final=resposta_final,
        )
        try:
            from app.ia_preparo import formatar_situacao_ias

            registro.ias_situacao = formatar_situacao_ias(resultado_ia) if resultado_ia else None
        except Exception:
            pass
        with db.session.begin_nested():
            db.session.add(registro)
            if pendente is not None:
                db.session.flush()
                registro.pergunta_pendente_id = getattr(pendente, "id", None) or _id_apos_flush(pendente)
        return registro
    except Exception:
        logger.exception("Falha ao registrar a pergunta no monitor")
        return None


def _id_apos_flush(pendente):
    db.session.flush()
    return pendente.id
