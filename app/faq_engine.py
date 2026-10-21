"""
Motor simples de busca de perguntas e respostas (a "IA" do sistema).

Não usa modelos de linguagem externos: compara a pergunta do paciente com
as perguntas já cadastradas na base de FAQ (por exame). Pedido do Silvan
(2026-09-11): a FAQ só responde automaticamente (sem passar pela
aprovação do médico) quando a pergunta é IDÊNTICA (depois de normalizar
acentuação/pontuação/maiúsculas) a uma pergunta já cadastrada - nunca por
semelhança aproximada, nem por sobreposição de palavras-chave, mesmo para
FAQs cadastradas manualmente pela equipe (antes disso só as FAQs geradas
pela própria IA exigiam igualdade exata; as manuais aceitavam
correspondência aproximada via `similaridade()`, removida nesta rodada -
ver git history se precisar recuperá-la). Motivo: correspondência
aproximada gera falsos positivos - ex.: um alimento cadastrado com vários
sinônimos juntos (ex.: "Mandioca / Aipim") sendo reconhecido por engano
numa pergunta sobre "batata"; ou (bug pré-existente, também sanado por
tabela com esta mudança) uma FAQ aprendida da IA sobre um sabor (ex.:
"gatorade de uva") sendo reaproveitada indevidamente pra pergunta sobre
outro sabor (ex.: "limão") - o guard antigo que tentava evitar isso
(`item.criado_por == "Assistente (IA)"`, mais abaixo neste arquivo antes
desta mudança) nunca disparava de verdade, porque
app.routes_medico.perguntas_responder sempre grava o nome de quem
aprovou (o médico), nunca esse valor - ver test_smoke.py e HANDOFF_CHAT.md.

Quando uma pergunta nova é respondida pela secretária/médico (ou pela IA,
depois de aprovada), ela é adicionada à base de FAQ — é assim que o
sistema "aprende", mas só vale pra repetição EXATA dali em diante.
"""
import re
import unicodedata
from difflib import SequenceMatcher

from app.models import FaqItem

STOPWORDS = {
    "a", "o", "e", "é", "de", "da", "do", "das", "dos", "em", "um", "uma",
    "para", "por", "com", "sem", "que", "se", "eu", "posso", "pode", "podem",
    "no", "na", "nos", "nas", "meu", "minha", "os", "as", "ao", "aos", "às",
    "sobre", "antes", "depois", "vou", "estou", "tem", "ter", "seria",
}


def normalizar(texto: str) -> str:
    texto = texto.lower().strip()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^a-z0-9\s]", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def _radical(palavra: str) -> str:
    """Normalização leve de plural/singular para melhorar o casamento
    (ex.: 'batatas' -> 'batata'). Não é um stemmer completo, só o suficiente
    para o caso de uso de perguntas curtas em português."""
    if len(palavra) > 4 and palavra.endswith("oes"):
        return palavra[:-3] + "ao"
    if len(palavra) > 4 and palavra.endswith(("as", "os", "es")):
        return palavra[:-1]
    if len(palavra) > 3 and palavra.endswith("s"):
        return palavra[:-1]
    return palavra


def palavras_chave(texto: str) -> set:
    return {
        _radical(p)
        for p in normalizar(texto).split()
        if p not in STOPWORDS and len(p) > 1
    }


# Confiança mínima para considerar que duas palavras são "a mesma", mesmo
# com pequenos erros de digitação (ex.: 'amendoin' vs 'amendoim'). Abaixo
# disso, a diferença já muda demais a palavra pra confiar na correspondência.
LIMIAR_CONFIANCA_PALAVRA = 0.8


def _palavras_correspondem(palavra_a: str, palavra_b: str) -> bool:
    """Compara duas palavras já normalizadas, tolerando pequenos erros de
    digitação — assim 'amendoin' (com "n" no lugar do "m") ainda é
    reconhecido como 'amendoim'. Palavras muito curtas (até 3 letras) só
    casam se forem idênticas, pra não gerar falso positivo (ex.: 'sim' não
    deveria casar com qualquer outra palavra de 3 letras)."""
    if palavra_a == palavra_b:
        return True
    if len(palavra_a) <= 3 or len(palavra_b) <= 3:
        return False
    if SequenceMatcher(None, palavra_a, palavra_b).ratio() < LIMIAR_CONFIANCA_PALAVRA:
        return False
    # Palavra curta: só 1 letra de diferença. Sem isso, "carro" casava com
    # "claro" (ratio 0.8) e "Posso ir de carro?" recebia a resposta pronta
    # de "Líquidos claros" (relatado pelo Silvan, 2026-10-21).
    limite = 1 if min(len(palavra_a), len(palavra_b)) <= 6 else 2
    return _distancia_edicao(palavra_a, palavra_b) <= limite


def _distancia_edicao(a: str, b: str) -> int:
    """Quantas letras é preciso trocar, inserir ou apagar para ir de `a`
    a `b` (distância de Levenshtein)."""
    anterior = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        atual = [i]
        for j, cb in enumerate(b, 1):
            atual.append(min(anterior[j] + 1, atual[j - 1] + 1, anterior[j - 1] + (ca != cb)))
        anterior = atual
    return anterior[-1]


def _quantidade_correspondencias(palavras_a: set, palavras_b: set) -> int:
    """Quantas palavras de `palavras_a` têm alguma correspondente (exata ou
    por tolerância a erro de digitação) em `palavras_b`."""
    return sum(1 for pa in palavras_a if any(_palavras_correspondem(pa, pb) for pb in palavras_b))


def buscar_resposta(pergunta_usuario: str, grupo_id, exame_id=None, criado_por_id=None):
    """
    Procura na base de FAQ uma pergunta IDÊNTICA (depois de normalizar
    acentuação/pontuação/maiúsculas) à do paciente, restrita ao Grupo
    (Fatia 4) do paciente (para não misturar conhecimento entre clínicas
    diferentes) - também considera FAQs gerais do exame (sem exame_id
    específico).

    Fatia 6: quando não há Grupo (`grupo_id` None - conta solo), a busca é
    restrita ao dono pessoal (`criado_por_id`) em vez de por Grupo - mesmo
    padrão de clinica_utils.filtro_escopo_atual().

    Pedido do Silvan (2026-09-11): só EXATA, nunca por semelhança
    aproximada - ver docstring do módulo para o motivo (dois bugs reais
    que a correspondência aproximada causava).

    Retorna (faq_item, 1.0) quando acha, ou (None, 0.0) quando não acha -
    o score é mantido só por compatibilidade com quem já desempacota a
    tupla (sempre 1.0/0.0 agora, nunca um valor intermediário).
    """
    if grupo_id is not None:
        escopo = FaqItem.grupo_id == grupo_id
    else:
        escopo = FaqItem.criado_por_id == criado_por_id
    candidatos = FaqItem.query.filter(
        escopo,
        (FaqItem.exame_id == exame_id) | (FaqItem.exame_id.is_(None)),
    ).all()

    pergunta_normalizada = normalizar(pergunta_usuario)
    for item in candidatos:
        if normalizar(item.pergunta) == pergunta_normalizada:
            return item, 1.0
    return None, 0.0


# As respostas prontas de alimento/medicamento (buscar_resposta_alimento/
# buscar_resposta_medicamento, por comparação de palavras com o preparo)
# saíram em 2026-10-21, a pedido do Silvan: erravam ("carro" virava
# "Líquidos claros", "nimesulida" virava Clopidogrel) e pulavam as 2 IAs,
# que já recebem o preparo completo. Ver git history se precisar.


def texto_preparo_whatsapp(agendamento):
    """Monta o mesmo conteúdo de app/templates/paciente/preparo.html, só
    que como texto plano formatado para WhatsApp (negrito com
    *asteriscos*, sem HTML) — usado pela opção "Ver informações do
    preparo" da área de WhatsApp (Fatia 7, ver app/whatsapp_conversa.py).

    Não duplica nenhuma regra de cálculo de prazo: lê os mesmos campos e
    chama os mesmos métodos `.limite()`/`.limite_formatado()` que a tela
    web já usa, sempre a partir de `agendamento.data_hora` — só troca o
    formato de saída (texto em vez de HTML)."""
    exame = agendamento.exame
    preparo = exame.preparo
    data_hora = agendamento.data_hora

    linhas = [f"*{exame.nome}* — {data_hora.strftime('%d/%m/%Y às %H:%M')}"]

    if not preparo:
        linhas.append(
            "\nEste agendamento não tem instruções de preparo cadastradas "
            "— não é necessário nenhum preparo prévio. Em caso de dúvida, "
            "entre em contato com a clínica."
        )
        return "\n".join(linhas)

    if preparo.cortes:
        linhas.append("")
        for corte in preparo.cortes:
            linhas.append(
                f"⏱ *{corte.descricao}*: até "
                f"{corte.limite(data_hora).strftime('%d/%m/%Y às %H:%M')} "
                f"({corte.horas_antes}h antes do exame)"
            )

    if preparo.medicamentos_suspensos:
        linhas.append("\n*Medicamentos a suspender:*")
        for ms in preparo.medicamentos_suspensos:
            obs = f" — {ms.observacao}" if ms.observacao else ""
            linhas.append(
                f"- {ms.medicamento.nome}: suspender a partir de "
                f"{ms.limite(data_hora).strftime('%d/%m/%Y')} "
                f"({ms.dias_antes} dias antes){obs}"
            )
        if preparo.observacoes_medicamentos:
            linhas.append(preparo.observacoes_medicamentos)
        linhas.append(
            "Em caso de dúvida sobre suspender algum medicamento "
            "(principalmente anticoagulantes), fale com o médico que "
            "prescreveu antes de suspender por conta própria."
        )

    if preparo.medicamentos_mantidos:
        linhas.append("\n*Medicamentos que podem ser mantidos:*")
        for mm in preparo.medicamentos_mantidos:
            obs = f" — {mm.observacao}" if mm.observacao else ""
            linhas.append(f"- {mm.nome}{obs}")

    if preparo.alimentos:
        proibidos = [a for a in preparo.alimentos if not a.permitido]
        permitidos = [a for a in preparo.alimentos if a.permitido]
        if proibidos:
            linhas.append("\n*Alimentos proibidos:*")
            for a in proibidos:
                if a.horas_antes is not None:
                    linhas.append(f"- {a.nome} — evitar a partir de {a.limite_formatado(data_hora)} ({a.horas_antes}h antes)")
                elif a.dias_antes is not None:
                    linhas.append(f"- {a.nome} — evitar a partir de {a.limite_formatado(data_hora)} ({a.dias_antes} dias antes)")
                else:
                    linhas.append(f"- {a.nome}")
        if permitidos:
            linhas.append("\n*Sugestão para consumo:*")
            for a in permitidos:
                linhas.append(f"- {a.nome}")

    if preparo.exames_anteriores_proibidos:
        linhas.append("\n*Não pode ter feito recentemente:*")
        for e in preparo.exames_anteriores_proibidos:
            if e.dias_antes is not None:
                linhas.append(
                    f"- {e.nome} — não deve ter sido feito desde "
                    f"{e.limite(data_hora).strftime('%d/%m/%Y')} ({e.dias_antes} dias antes)"
                )
            else:
                linhas.append(f"- {e.nome}")
        linhas.append("Se você fez algum desses procedimentos recentemente, avise a secretaria antes do exame.")

    if preparo.informacoes_gerais:
        linhas.append("\n*Outras orientações:*")
        for info in preparo.informacoes_gerais:
            limite_info = info.limite(data_hora)
            sufixo = f" — {limite_info.strftime('%d/%m/%Y às %H:%M')}" if limite_info else ""
            linhas.append(f"- {info.texto}{sufixo}")

    if preparo.instrucoes:
        linhas.append("\n" + preparo.instrucoes.strip())

    return "\n".join(linhas).strip()
