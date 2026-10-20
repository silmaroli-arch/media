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
# Versão do TEXTO usado no vetor dos itens (ver texto_do_item_para_vetor).
# Vai junto no nome do modelo gravado em embedding_modelo: ao mudar o texto,
# todos os vetores antigos passam a contar como pendentes e um clique em
# "Calcular vetores" refaz tudo. v2 (2026-10-20): exame + pergunta + resposta.
VERSAO_TEXTO_VETOR = "v2"

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
            return list(resposta.data[0].embedding), modelo_do_provedor(provedor)
        if provedor == PROVEDOR_GEMINI:
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                return None
            from google import genai
            cliente = genai.Client(api_key=api_key)
            resposta = cliente.models.embed_content(model=MODELO_EMBEDDING_GEMINI, contents=texto)
            return list(resposta.embeddings[0].values), modelo_do_provedor(provedor)
    except Exception:
        return None
    return None


# Cálculo de vetores em lote (pedido do Silvan, 2026-10-20): antes era uma
# chamada de API por item, com teto de 40 itens por clique para a página não
# estourar o tempo - os 866 itens da carga inicial exigiam ~22 cliques. As
# APIs de embeddings aceitam vários textos numa chamada só.
TAMANHO_LOTE_EMBEDDING = 100
# Para de processar perto disso (segundos) e avisa o que sobrou, bem antes do
# timeout do gunicorn (120s, ver render.yaml) - mesma lição do 502 de
# "Gerar cobranças do ano".
TEMPO_MAXIMO_CALCULO_VETORES = 60


def modelo_do_provedor(provedor):
    """Nome do modelo gravado em BaseConhecimentoItem.embedding_modelo para
    o provedor (ou None se o provedor não usa vetores)."""
    if provedor == PROVEDOR_OPENAI:
        return f"openai:{MODELO_EMBEDDING_OPENAI}:{VERSAO_TEXTO_VETOR}"
    if provedor == PROVEDOR_GEMINI:
        return f"gemini:{MODELO_EMBEDDING_GEMINI}:{VERSAO_TEXTO_VETOR}"
    return None


def texto_do_item_para_vetor(item):
    """Texto usado no vetor de um ITEM da base: "Exame: pergunta" + a
    resposta. Pedido do Silvan (2026-10-20): antes era só a pergunta, e
    "o remédio pra limpar o intestino não fez efeito" não achava "O laxante
    não fez efeito, o que eu faço?" - a resposta ("intestino mal limpo")
    traz as palavras que o paciente usa. O nome do exame deixa o item no
    mesmo formato da pergunta do paciente (ver _pergunta_com_exame)."""
    from app.models import TipoExame

    # pelo id (e não item.tipo_exame): item recém-criado ou com o tipo
    # trocado ainda pode estar com a relação desatualizada
    tipo = db.session.get(TipoExame, item.tipo_exame_id) if item.tipo_exame_id else None
    nome_exame = tipo.nome if tipo and tipo.nome else ""
    pergunta = (item.pergunta or "").strip()
    if nome_exame:
        pergunta = f"{nome_exame}: {pergunta}"
    return f"{pergunta}\n{(item.resposta or '').strip()}".strip()


def gerar_embeddings_em_lote(textos, provedor):
    """Uma chamada de API para vários textos. Devolve (lista_de_vetores,
    nome_do_modelo), com um vetor por texto na MESMA ordem, ou None se o
    provedor não for de embeddings, faltar a chave ou a chamada falhar.
    Nunca levanta exceção."""
    textos = [(t or "").strip() or "-" for t in textos]
    if not textos:
        return None
    try:
        if provedor == PROVEDOR_OPENAI:
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                return None
            import openai
            cliente = openai.OpenAI(api_key=api_key)
            resposta = cliente.embeddings.create(model=MODELO_EMBEDDING_OPENAI, input=textos)
            dados = sorted(resposta.data, key=lambda d: d.index)
            vetores = [list(d.embedding) for d in dados]
        elif provedor == PROVEDOR_GEMINI:
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                return None
            from google import genai
            cliente = genai.Client(api_key=api_key)
            resposta = cliente.models.embed_content(model=MODELO_EMBEDDING_GEMINI, contents=textos)
            vetores = [list(e.values) for e in resposta.embeddings]
        else:
            return None
    except Exception:
        return None
    if len(vetores) != len(textos):
        return None
    return vetores, modelo_do_provedor(provedor)


def itens_sem_vetor_query(provedor=None):
    """Itens ativos sem vetor ou com vetor de OUTRO modelo (ex.: depois de
    trocar de OpenAI para Gemini) - os que a busca por sentido ignora."""
    provedor = provedor or provedor_configurado()
    modelo = modelo_do_provedor(provedor)
    consulta = BaseConhecimentoItem.query.filter(BaseConhecimentoItem.status == "ativo")
    if modelo:
        consulta = consulta.filter(db.or_(
            BaseConhecimentoItem.embedding.is_(None),
            BaseConhecimentoItem.embedding_modelo.is_(None),
            BaseConhecimentoItem.embedding_modelo != modelo,
        ))
    else:
        consulta = consulta.filter(BaseConhecimentoItem.embedding.is_(None))
    return consulta


def calcular_vetores_pendentes(provedor=None, tempo_maximo=TEMPO_MAXIMO_CALCULO_VETORES):
    """Calcula o vetor de TODOS os itens pendentes (ver itens_sem_vetor_query)
    em lotes de TAMANHO_LOTE_EMBEDDING. Se um lote inteiro falhar, tenta
    aqueles itens um a um (ver atualizar_embedding_do_item). Faz commit a
    cada lote (o que já foi calculado não se perde se algo der errado
    depois) e para ao passar de `tempo_maximo` segundos. Devolve
    (calculados, falhas, restantes)."""
    import time

    provedor = provedor or provedor_configurado()
    inicio = time.monotonic()
    pendentes = itens_sem_vetor_query(provedor).order_by(BaseConhecimentoItem.id).all()
    ok = falhas = 0
    processados = 0
    for i in range(0, len(pendentes), TAMANHO_LOTE_EMBEDDING):
        if i > 0 and time.monotonic() - inicio > tempo_maximo:
            break  # o 1º lote sempre roda - cada clique sempre avança
        lote = pendentes[i:i + TAMANHO_LOTE_EMBEDDING]
        resultado = gerar_embeddings_em_lote([texto_do_item_para_vetor(item) for item in lote], provedor)
        if resultado:
            vetores, modelo = resultado
            for item, vetor in zip(lote, vetores):
                item.embedding = json.dumps(vetor)
                item.embedding_modelo = modelo
            ok += len(lote)
        else:
            falhas_seguidas = 0
            for item in lote:
                if atualizar_embedding_do_item(item, provedor):
                    ok += 1
                    falhas_seguidas = 0
                else:
                    falhas += 1
                    falhas_seguidas += 1
                    if falhas_seguidas >= 3 and ok == 0:
                        # provavelmente sem chave/credencial - não insistir
                        db.session.commit()
                        return ok, falhas, len(pendentes) - ok
        processados += len(lote)
        db.session.commit()
    restantes = len(pendentes) - processados
    return ok, falhas, restantes


def atualizar_embedding_do_item(item, provedor=None):
    """(Re)calcula o embedding do item (ver texto_do_item_para_vetor) com o provedor atual e
    grava no próprio item (quem chama decide o commit). Devolve True se
    gravou. Com provedor "palavra_chave" (ou falha), limpa o embedding
    antigo para não sobrar um vetor de outro modelo/texto desatualizado."""
    provedor = provedor or provedor_configurado()
    resultado = gerar_embedding(texto_do_item_para_vetor(item), provedor)
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


# Com o tipo de exame já definido, "exame" na pergunta do paciente quer
# dizer ESTE exame - a palavra nunca casa com o nome dele no item ("posso
# dirigir depois do exame" x "Posso dirigir depois da colonoscopia?") e só
# baixava a nota abaixo do limiar. Decisão do Silvan, 2026-10-14.
_PALAVRAS_GENERICAS_EXAME = {"exame"}


def _buscar_por_palavra_chave(pergunta, itens, limite, ignorar_genericas=False):
    kw_pergunta = palavras_chave(pergunta)
    if ignorar_genericas:
        kw_pergunta = kw_pergunta - _PALAVRAS_GENERICAS_EXAME
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


def _pergunta_com_exame(pergunta, tipo_exame_id):
    """Texto usado no vetor da PERGUNTA do paciente na busca por sentido:
    com o tipo de exame definido, prefixa o nome dele ("Colonoscopia: dá pra
    dirigir depois?"). Pedido do Silvan (2026-10-20): perguntas curtas não
    citam o exame - para o paciente ele é óbvio -, e sem esse contexto
    "dá pra dirigir depois?" ficava abaixo do limiar para "Posso dirigir
    depois da colonoscopia?". Os vetores dos itens também levam o nome do
    exame (ver texto_do_item_para_vetor)."""
    if not tipo_exame_id:
        return pergunta
    from app.models import TipoExame

    tipo = db.session.get(TipoExame, tipo_exame_id)
    if not tipo or not tipo.nome:
        return pergunta
    return f"{tipo.nome}: {pergunta.strip()}"


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
        vetor_pergunta = gerar_embedding(_pergunta_com_exame(pergunta, tipo_exame_id), provedor)
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

    return _buscar_por_palavra_chave(pergunta, itens, limite, ignorar_genericas=bool(tipo_exame_id))
