"""Busca na internet quando uma IA não reconhece um nome (pedido do Silvan,
2026-10-25).

Fluxo, só com a busca ligada pelo dono (PlataformaConfig.busca_internet_ativa):
1. Uma das IAs respondeu "Não reconheci ..." (MARCADOR_NAO_RECONHECIDO).
2. O nome é pesquisado UMA vez, pelo Claude com a ferramenta de busca na web
   da Anthropic, só nos sites de DOMINIOS_PERMITIDOS e com no máximo
   MAXIMO_BUSCAS buscas. A resposta é uma linha "Nome = o que é", ou
   NAO_ENCONTRADO quando as páginas não confirmam - nunca um chute.
3. O resultado fica em NomePesquisado: o mesmo nome nunca é pesquisado de
   novo, e o dono confirma ou rejeita em "Nomes pesquisados".
4. Com resultado, as 2 IAs respondem de novo com a linha no contexto; a
   resposta sempre passa pelo médico, que vê o que foi pesquisado e as fontes.

Custo: US$ 10 por 1.000 buscas (Anthropic) mais os tokens das páginas lidas;
limite diário em PlataformaConfig.busca_internet_limite_dia."""
import logging
import re
from datetime import datetime

from app.extensions import db

logger = logging.getLogger(__name__)

TIPO_USO = "busca_internet"
MARCADOR_NAO_ENCONTRADO = "NAO_ENCONTRADO"
MAXIMO_BUSCAS = 2
CUSTO_POR_BUSCA_USD = 0.01  # US$ 10 por 1.000 buscas (preço oficial, 2026-10-25)

# Sites onde a busca pode procurar (os subdomínios entram junto, ex.:
# consultas.anvisa.gov.br). Fontes oficiais e de referência - nada de fórum
# ou blog, pela regra de a IA nunca chutar.
DOMINIOS_PERMITIDOS = [
    "gov.br",               # ANVISA, Ministério da Saúde, bulário eletrônico
    "consultaremedios.com.br",
    "medlineplus.gov",
    "drugs.com",
    "msdmanuals.com",
]

PROMPT_BUSCA = f"""Você identifica o que é um nome citado por um paciente brasileiro numa pergunta sobre preparo de exame (medicamento, suplemento, alimento, bebida ou outro produto), usando SOMENTE os resultados da busca na web.

Pesquise o nome e responda em UMA linha, sem mais nada, no formato:
NOME = o que é
- Medicamento: princípio ativo e classe (ex.: "Poviztra = semaglutida, agonista do GLP-1 (classe do Ozempic)").
- Outro produto: o que é e o principal componente.

Só afirme o que as páginas encontradas confirmam com clareza. Se não confirmarem, se houver dúvida, ou se o nome puder ser mais de um produto, responda exatamente {MARCADOR_NAO_ENCONTRADO}. Não dê orientação médica nem opine sobre o preparo."""

_RE_NOME_ENTRE_ASPAS = re.compile(r"reconhec\w*\b[^\"“”'\n]{0,40}[\"“”']([^\"“”'\n]{2,60})[\"“”']", re.IGNORECASE)


def busca_ativa():
    from app.models import PlataformaConfig

    return bool(PlataformaConfig.obter().busca_internet_ativa)


def nome_nao_reconhecido(textos, pergunta=""):
    """O nome que a IA disse não reconhecer (entre aspas na resposta, como
    pede o prompt) ou, sem aspas, a primeira palavra estranha da pergunta
    (fora do dicionário, ver app.medicamentos_anvisa)."""
    for texto in textos:
        achado = _RE_NOME_ENTRE_ASPAS.search(texto or "")
        if achado:
            return achado.group(1).strip()
    try:
        from app.medicamentos_anvisa import _candidatos

        palavras = [c for c in _candidatos(pergunta) if " " not in c]
        return palavras[0].title() if palavras else None
    except Exception:
        return None


def _buscas_hoje():
    from app.models import ChamadaIA

    inicio = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return ChamadaIA.query.filter(ChamadaIA.tipo_uso == TIPO_USO, ChamadaIA.criado_em >= inicio).count()


def _fontes(resposta):
    urls = []
    for bloco in resposta.content:
        if getattr(bloco, "type", None) == "web_search_tool_result" and isinstance(getattr(bloco, "content", None), list):
            urls.extend(getattr(item, "url", None) for item in bloco.content)
        for citacao in getattr(bloco, "citations", None) or []:
            urls.append(getattr(citacao, "url", None))
    return list(dict.fromkeys(u for u in urls if u))[:5]


def _perguntar_com_busca(cliente, nome):
    """(linha_ou_None, fontes, uso_total, buscas). Continua quando a API pausa
    a vez (pause_turn), no máximo 3 rodadas."""
    from app.ia_modelos import criar_mensagem_claude, modelo_do_ponto, texto_claude

    mensagens = [{"role": "user", "content": f"Nome citado pelo paciente: {nome}"}]
    ferramenta = {"type": "web_search_20260209", "name": "web_search", "max_uses": MAXIMO_BUSCAS,
                  "allowed_domains": DOMINIOS_PERMITIDOS, "user_location": {"type": "approximate", "country": "BR"}}
    entrada = saida = buscas = 0
    fontes = []
    modelo = modelo_do_ponto("busca")
    for _ in range(3):
        resposta = criar_mensagem_claude(cliente, modelo, 400, esforco="low", system=PROMPT_BUSCA,
                                         messages=mensagens, tools=[ferramenta])
        modelo = getattr(resposta, "model", None) or modelo
        uso = getattr(resposta, "usage", None)
        entrada += getattr(uso, "input_tokens", 0) or 0
        saida += getattr(uso, "output_tokens", 0) or 0
        buscas += getattr(getattr(uso, "server_tool_use", None), "web_search_requests", 0) or 0
        fontes += _fontes(resposta)
        if getattr(resposta, "stop_reason", None) != "pause_turn":
            break
        mensagens.append({"role": "assistant", "content": resposta.content})
    linha = texto_claude(resposta).strip().splitlines()
    linha = linha[-1].strip() if linha else ""
    if not linha or MARCADOR_NAO_ENCONTRADO in linha or "=" not in linha:
        linha = None
    return linha, list(dict.fromkeys(fontes))[:5], (modelo, entrada, saida), buscas


def pesquisar(nome, usuario_id=None, paciente_id=None):
    """O NomePesquisado deste nome: o que já estava guardado ou uma busca
    nova. None quando a busca está desligada, passou do limite do dia, não
    há Claude configurado ou a chamada falhou."""
    from app.custo_ia import registrar_chamada_ia
    from app.ia_preparo import _cliente_anthropic
    from app.medicamentos_anvisa import normalizar_nome
    from app.models import NomePesquisado, PlataformaConfig

    nome = (nome or "").strip()[:255]
    chave = normalizar_nome(nome)
    if not chave:
        return None
    guardado = NomePesquisado.query.filter_by(nome_normalizado=chave).first()
    if guardado:
        return guardado
    config = PlataformaConfig.obter()
    if not config.busca_internet_ativa or _buscas_hoje() >= (config.busca_internet_limite_dia or 0):
        return None
    cliente = _cliente_anthropic()
    if not cliente:
        return None
    try:
        linha, fontes, (modelo, entrada, saida), buscas = _perguntar_com_busca(cliente, nome)
    except Exception:
        logger.exception("Falha na busca na internet por %r", nome)
        return None
    registrar_chamada_ia(TIPO_USO, "Claude", modelo, entrada, saida, sucesso=True, usuario_id=usuario_id,
                         paciente_id=paciente_id, custo_extra_usd=buscas * CUSTO_POR_BUSCA_USD)
    registro = NomePesquisado(nome=nome, nome_normalizado=chave, resultado=linha, fontes="\n".join(fontes))
    db.session.add(registro)
    db.session.commit()
    return registro


def linha_contexto(registro):
    """Texto para o contexto das IAs, ou "" se não serve (sem resultado ou
    rejeitado pelo dono)."""
    if not registro or not registro.resultado or registro.status == "rejeitado":
        return ""
    situacao = "conferida pela equipe" if registro.status == "confirmado" else "ainda não conferida pelo médico"
    return (f"Pesquisa na internet sobre um nome da pergunta (sites oficiais e de referência; {situacao}): "
            f"{registro.resultado}")


def texto_para_medico(registro):
    """Resumo para a tela de aprovação: o que foi achado e as fontes."""
    if not registro:
        return None
    if not registro.resultado:
        return f'"{registro.nome}" - as fontes não confirmaram o que é'
    fontes = "; ".join(registro.lista_fontes) or "-"
    return f"{registro.resultado} (fontes: {fontes})"

