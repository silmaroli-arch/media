"""Base de conhecimento compartilhada - a "terceira IA" (pedido do Silvan,
2026-09-29). Não é um modelo treinado: é uma base de perguntas e respostas
de preparo (ver app.models.BaseConhecimentoItem), organizada por tipo de
exame, consultada por busca de similaridade.

Este módulo cuida de: (1) gerar o "vetor de busca" (embedding) de uma
pergunta com o provedor que o dono escolheu, (2) achar os itens mais
parecidos com a pergunta do paciente - por palavra-chave (reaproveitando o
motor de FAQ, sem custo) ou por embeddings (busca por sentido) e (3) dizer
se a base está ligada.

REGRAS DO SILVAN que este módulo NÃO decide sozinho (ficam para a fatia 3,
integração na resposta): o preparo cadastrado pelo médico sempre vence, a
resposta da base só vai para o médico revisar quando for "muito diferente"
das IAs (julgado pelo árbitro Claude) e diferenças só de prazo/horas não
contam como divergência.

Falha de rede/API/chave ausente NUNCA levanta exceção para quem chama:
`gerar_embedding` devolve None e `buscar_na_base` cai para palavra-chave.
"""
import json
import math
import os

from app.extensions import db
from app.models import BaseConhecimentoItem, PlataformaConfig
from app.faq_engine import palavras_chave, _quantidade_correspondencias

PROVEDOR_PALAVRA_CHAVE = "palavra_chave"
PROVEDOR_OPENAI = "openai"
PROVEDOR_GEMINI = "gemini"
PROVEDORES_BUSCA = {
    PROVEDOR_PALAVRA_CHAVE: "Palavra-chave (sem custo)",
    PROVEDOR_OPENAI: "OpenAI (embeddings)",
    PROVEDOR_GEMINI: "Gemini (embeddings)",
}

MODELO_EMBEDDING_OPENAI = os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
MODELO_EMBEDDING_GEMINI = os.environ.get("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")

# Pontos de corte iniciais - PRECISAM ser calibrados com perguntas reais
# (ver "Testar busca" em /dono/base-conhecimento). Palavra-chave: fração das
# palavras da pergunta do paciente que aparecem na pergunta do item.
# Embeddings: similaridade do cosseno.
LIMIAR_PALAVRA_CHAVE = 0.6
LIMIAR_EMBEDDING = 0.70


def base_ativa():
    return bool(PlataformaConfig.obter().base_conhecimento_ativa)


def provedor_configurado():
    valor = PlataformaConfig.obter().base_busca_provedor
    return valor if valor in PROVEDORES_BUSCA else PROVEDOR_PALAVRA_CHAVE


def gerar_embedding(texto, provedor):
    """Devolve (vetor, nome_do_modelo) ou None se o provedor não for de
    embeddings, a chave de API não estiver configurada ou a chamada falhar
    por qualquer motivo. Nunca levanta exceção."""
    texto = (texto or "").strip()
    if not texto:
        return None
    try:
        if provedor == PROVEDOR_OPENAI:
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                return None
            import openai
            cliente = openai.OpenAI(api_key=api_key)
            resposta = cliente.embeddings.create(model=MODELO_EMBEDDING_OPENAI, input=texto)
            return list(resposta.data[0].embedding), f"openai:{MODELO_EMBEDDING_OPENAI}"
        if provedor == PROVEDOR_GEMINI:
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                return None
            from google import genai
            cliente = genai.Client(api_key=api_key)
            resposta = cliente.models.embed_content(model=MODELO_EMBEDDING_GEMINI, contents=texto)
            return list(resposta.embeddings[0].values), f"gemini:{MODELO_EMBEDDING_GEMINI}"
    except Exception:
        return None
    return None


def atualizar_embedding_do_item(item, provedor=None):
    """(Re)calcula o embedding da PERGUNTA do item com o provedor atual e
    grava no próprio item (quem chama decide o commit). Devolve True se
    gravou. Com provedor "palavra_chave" (ou falha), limpa o embedding
    antigo para não sobrar um vetor de outro modelo/texto desatualizado."""
    provedor = provedor or provedor_configurado()
    resultado = gerar_embedding(item.pergunta, provedor)
    if not resultado:
        item.embedding = None
        item.embedding_modelo = None
        return False
    vetor, modelo = resultado
    item.embedding = json.dumps(vetor)
    item.embedding_modelo = modelo
    return True


def _cosseno(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    produto = sum(x * y for x, y in zip(a, b))
    norma_a = math.sqrt(sum(x * x for x in a))
    norma_b = math.sqrt(sum(y * y for y in b))
    if not norma_a or not norma_b:
        return 0.0
    return produto / (norma_a * norma_b)


def _buscar_por_palavra_chave(pergunta, itens, limite):
    kw_pergunta = palavras_chave(pergunta)
    if not kw_pergunta:
        return []
    resultados = []
    for item in itens:
        kw_item = palavras_chave(item.pergunta)
        if not kw_item:
            continue
        pontuacao = _quantidade_correspondencias(kw_pergunta, kw_item) / len(kw_pergunta)
        if pontuacao >= LIMIAR_PALAVRA_CHAVE:
            resultados.append({"item": item, "score": pontuacao, "metodo": PROVEDOR_PALAVRA_CHAVE})
    resultados.sort(key=lambda r: r["score"], reverse=True)
    return resultados[:limite]


def buscar_na_base(pergunta, tipo_exame_id=None, limite=3, provedor=None):
    """Acha os itens ATIVOS da base mais parecidos com `pergunta`, do
    tipo de exame dado (ou de qualquer tipo, se None). Devolve uma lista de
    {"item", "score", "metodo"} do mais para o menos parecido (pode ser
    vazia). Com provedor de embeddings: só considera itens cujo vetor veio
    do MESMO modelo da pergunta - se a chamada da pergunta falhar, cai para
    palavra-chave (a busca nunca fica muda por causa de uma falha de API)."""
    if not (pergunta or "").strip():
        return []
    provedor = provedor or provedor_configurado()

    consulta = BaseConhecimentoItem.query.filter_by(status="ativo")
    if tipo_exame_id:
        consulta = consulta.filter_by(tipo_exame_id=tipo_exame_id)
    itens = consulta.all()
    if not itens:
        return []

    if provedor in (PROVEDOR_OPENAI, PROVEDOR_GEMINI):
        vetor_pergunta = gerar_embedding(pergunta, provedor)
        if vetor_pergunta:
            vetor, modelo = vetor_pergunta
            resultados = []
            for item in itens:
                if not item.embedding or item.embedding_modelo != modelo:
                    continue
                try:
                    similaridade = _cosseno(vetor, json.loads(item.embedding))
                except ValueError:
                    continue
                if similaridade >= LIMIAR_EMBEDDING:
                    resultados.append({"item": item, "score": similaridade, "metodo": provedor})
            resultados.sort(key=lambda r: r["score"], reverse=True)
            return resultados[:limite]

    return _buscar_por_palavra_chave(pergunta, itens, limite)
