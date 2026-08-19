"""Extração de sugestão de preparo a partir de um PDF usando IA (Google
Gemini) - substitui a extração heurística por regex (`app.pdf_preparo`)
como caminho PRINCIPAL de importação de PDF: a IA lê o conteúdo do PDF e
devolve o mesmo formato estruturado usado pela importação de Excel (ver
`app.xlsx_preparo`), permitindo reaproveitar a mesma tela de revisão antes
de salvar - nada é gravado no banco até a pessoa conferir e clicar em
"Salvar", exatamente como já acontecia com a extração por regex.

Motor de IA: Google Gemini (`google-genai`), não Claude/Anthropic - trocado
nesta rodada porque a extração de PDF é uma tarefa de leitura/estruturação
de documento (sem o raciocínio de reconhecimento de marca farmacêutica que
justifica usar o Sonnet no chat de dúvidas do paciente, ver
`app.ia_preparo`) e o Gemini tem um custo por chamada bem menor para esse
tipo de tarefa. Configurado de forma totalmente independente do
`ia_preparo.py` (variável de ambiente própria, `GEMINI_API_KEY`) - trocar
aqui não afeta o chat do paciente.

Custo de tokens: por padrão manda-se só o TEXTO extraído do PDF (de graça,
via pypdf - ver app.pdf_preparo.extrair_texto), bem mais barato que mandar
o PDF inteiro como arquivo nativo - tanto a Claude quanto o Gemini tratam
cada página de um PDF nativo de forma parecida com uma imagem por baixo
dos panos, o que custa muito mais token que ler o mesmo conteúdo em texto
puro. O PDF inteiro (nativo, lido diretamente pela IA) só é usado como
QUEDA quando o texto extraído vier vazio/quase vazio - sinal de PDF
escaneado/imagem, sem texto selecionável, que `extrair_texto` não
consegue ler (ver LIMIAR_TEXTO_MINIMO abaixo).

Só é usada quando GEMINI_API_KEY está configurada (ver _cliente_gemini) -
sem ela, ou se a chamada falhar por qualquer motivo (rede, PDF ilegível,
resposta que não veio em JSON válido), `extrair_sugestao_de_pdf_com_ia`
retorna None e quem chama deve cair de volta pra extração heurística de
`app.pdf_preparo.extrair_sugestao_de_pdf`, que nunca lança uma exceção
não tratada."""
import io
import json
import os

from app.pdf_preparo import extrair_texto

# Pode ser trocado por variável de ambiente sem precisar mexer no código —
# útil pra ajustar custo/qualidade sem um novo deploy. O Flash é o modelo
# mais barato/rápido da família Gemini, adequado pra essa tarefa de
# extração estruturada (não exige o raciocínio mais caro de reconhecimento
# de marca comercial que o chat de dúvidas do paciente precisa, esse
# continua na Claude - ver app.ia_preparo).
MODELO_PADRAO = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# Abaixo desse número de caracteres de texto extraído, o PDF é tratado como
# "sem texto selecionável o suficiente" (provavelmente escaneado/imagem) -
# nesse caso manda-se o PDF inteiro pra IA ler nativamente, mesmo custando
# mais token, porque não há alternativa mais barata pra ler o conteúdo.
LIMIAR_TEXTO_MINIMO = 200

PROMPT_SISTEMA = """Você é um assistente que lê documentos de preparo para exames médicos (ex.: colonoscopia, endoscopia, exames de imagem) e extrai as regras num formato estruturado (JSON), para uma secretária ou médico revisar e cadastrar no sistema.

Devolva SOMENTE um objeto JSON válido, sem nenhum texto antes ou depois (nem cercado por ```), com exatamente estas chaves:

- "nome_sugerido": string curta com o nome do exame/preparo (ex.: "Colonoscopia com Picoprep"), ou null se não conseguir identificar.
- "instrucoes": string com o texto completo das instruções gerais, dieta por fase, receituário, modo de preparo e observações finais - tudo que não se encaixa nos campos estruturados abaixo. Preserve quebras de linha com "\\n".
- "cortes": lista de objetos {"descricao": string, "horas_antes": inteiro} - cortes de alimentação/líquido (ex.: jejum total, líquidos claros, sólidos), com o prazo em horas antes do exame.
- "medicamentos": lista de objetos {"nome": string, "dias_antes": inteiro, "categoria": string ou null} - medicamentos que devem ser SUSPENSOS antes do exame, com o prazo em dias antes.
- "medicamentos_mantidos": lista de objetos {"nome": string, "observacao": string ou null} - medicamentos que NÃO precisam ser suspensos.
- "observacoes_medicamentos": string com uma observação livre sobre medicamentos (ex.: a frase completa dizendo que não é necessário suspender certos itens), ou null.
- "informacoes_gerais": lista de objetos {"texto": string, "horas_antes": inteiro ou null, "dias_antes": inteiro ou null, "hora_exata": string "HH:MM" ou null} - avisos/prazos que não são cortes de alimentação nem medicamentos (ex.: "tomar o primeiro sachê às 16:00 do dia anterior").
- "alimentos": lista de objetos {"nome": string, "permitido": true ou false, "horas_antes": inteiro ou null, "dias_antes": inteiro ou null} - alimentos/bebidas especificamente permitidos ou proibidos.
- "exames_anteriores": lista de objetos {"nome": string, "dias_antes": inteiro ou null} - exames/procedimentos que o paciente não deve ter feito num período antes deste exame.

Regras importantes:
- NUNCA invente informação que não está no documento - isso é um preparo médico real, um dado inventado pode colocar a saúde de um paciente em risco.
- Extraia SOMENTE o que está explícito no texto - não deduza prazos ou regras que não estão escritos.
- Se um campo não se aplica, use uma lista vazia [] ou null - nunca omita a chave.
- "instrucoes" deve conter o texto completo relevante, mesmo que partes dele também apareçam de forma estruturada em outros campos - é a base da revisão manual."""


def _cliente_gemini():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        from google import genai
        return genai.Client(api_key=api_key)
    except Exception:
        # Cobre tanto a falta da biblioteca quanto qualquer erro ao
        # construir o cliente - nunca deve derrubar a importação de PDF
        # com um erro 500, só faz o sistema seguir sem essa IA (cai pra
        # extração heurística de app.pdf_preparo).
        return None


def _extrair_json(texto):
    texto = texto.strip()
    if texto.startswith("```"):
        texto = texto.strip("`")
        if texto.lower().startswith("json"):
            texto = texto[4:]
        texto = texto.strip()
    return json.loads(texto)


def _lista(dados, chave):
    valor = dados.get(chave)
    return valor if isinstance(valor, list) else []


def _normalizar_sugestao(dados):
    """Preenche valores padrão e descarta itens malformados vindos da IA -
    nunca confia cegamente no shape da resposta, mesmo pedindo JSON
    estrito no prompt (ver mesmo cuidado em app.xlsx_preparo/pdf_preparo,
    que também nunca assumem um dado ausente/errado como impossível).

    Medicamentos a suspender SEM um prazo em dias explícito (ex.: "suspender
    alguns dias antes, conforme orientação do médico que prescreveu" - sem
    número fixo) não têm como virar uma linha estruturada: o campo
    PreparoMedicamentoSuspenso.dias_antes é obrigatório no banco (não dá pra
    calcular "a partir de quando" sem um número). Em vez de descartar esse
    medicamento silenciosamente da tela de revisão (o que já aconteceu e
    escondeu informação real de um preparo), ele é preservado como texto em
    "observacoes_medicamentos" - continua visível pra pessoa revisar e
    cadastrar manualmente se quiser, mesmo sem virar item da lista."""
    medicamentos_com_prazo = []
    nomes_sem_prazo = []
    for m in _lista(dados, "medicamentos"):
        if not isinstance(m, dict) or not m.get("nome"):
            continue
        if m.get("dias_antes") is not None:
            medicamentos_com_prazo.append(
                {"nome": m.get("nome"), "dias_antes": m.get("dias_antes"), "categoria": m.get("categoria") or None}
            )
        else:
            nomes_sem_prazo.append(m.get("nome"))

    observacoes_medicamentos = (dados.get("observacoes_medicamentos") or "").strip() or None
    if nomes_sem_prazo:
        aviso_sem_prazo = (
            "Medicamentos citados no documento sem prazo fixo em dias (revisar manualmente): "
            + ", ".join(nomes_sem_prazo)
        )
        observacoes_medicamentos = (
            f"{observacoes_medicamentos}\n\n{aviso_sem_prazo}" if observacoes_medicamentos else aviso_sem_prazo
        )

    return {
        "nome_sugerido": dados.get("nome_sugerido") or None,
        "instrucoes": (dados.get("instrucoes") or "").strip(),
        "cortes": [
            {"descricao": c.get("descricao"), "horas_antes": c.get("horas_antes")}
            for c in _lista(dados, "cortes")
            if isinstance(c, dict) and c.get("descricao") and c.get("horas_antes") is not None
        ],
        "medicamentos": medicamentos_com_prazo,
        "medicamentos_mantidos": [
            {"nome": m.get("nome"), "observacao": m.get("observacao") or None}
            for m in _lista(dados, "medicamentos_mantidos")
            if isinstance(m, dict) and m.get("nome")
        ],
        "observacoes_medicamentos": observacoes_medicamentos,
        "informacoes_gerais": [
            {
                "texto": i.get("texto"), "horas_antes": i.get("horas_antes"),
                "dias_antes": i.get("dias_antes"), "hora_exata": i.get("hora_exata"),
            }
            for i in _lista(dados, "informacoes_gerais")
            if isinstance(i, dict) and i.get("texto")
        ],
        "alimentos": [
            {
                "nome": a.get("nome"), "permitido": bool(a.get("permitido")),
                "horas_antes": a.get("horas_antes"), "dias_antes": a.get("dias_antes"),
            }
            for a in _lista(dados, "alimentos")
            if isinstance(a, dict) and a.get("nome")
        ],
        "exames_anteriores": [
            {"nome": e.get("nome"), "dias_antes": e.get("dias_antes")}
            for e in _lista(dados, "exames_anteriores")
            if isinstance(e, dict) and e.get("nome")
        ],
    }


def _conteudo_mensagem(genai_types, pdf_bytes, texto_extraido):
    """Monta a lista de partes da mensagem pra IA: texto extraído (barato)
    quando houver o suficiente, ou o PDF inteiro como arquivo nativo (caro,
    tratado quase como imagem por página) só quando o texto vier vazio/
    quase vazio - ver LIMIAR_TEXTO_MINIMO e o motivo disso no docstring do
    módulo."""
    if len(texto_extraido.strip()) >= LIMIAR_TEXTO_MINIMO:
        return [
            "Texto extraído do PDF de preparo (abaixo). Extraia os dados "
            "no formato JSON descrito nas instruções do sistema.\n\n"
            f"---\n{texto_extraido}\n---"
        ]
    return [
        genai_types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
        (
            "O texto extraído deste PDF veio vazio ou quase vazio (provavelmente é um "
            "PDF escaneado/imagem) - leia o documento diretamente e extraia os dados de "
            "preparo no formato JSON descrito nas instruções do sistema."
        ),
    ]


def extrair_sugestao_de_pdf_com_ia(pdf_bytes):
    """Retorna a sugestão estruturada (mesmo formato de
    `app.pdf_preparo.extrair_sugestao_de_pdf`) usando o Gemini para ler o
    conteúdo do PDF, ou None se a IA não estiver configurada ou a chamada
    falhar por qualquer motivo (rede, PDF ilegível, resposta que não veio
    em JSON válido) - quem chama deve cair de volta pra extração
    heurística nesse caso, nunca deixar essa falha virar um erro 500 na
    tela. Ver o docstring do módulo sobre a estratégia de custo (texto
    primeiro, PDF nativo só como queda para PDFs escaneados)."""
    cliente = _cliente_gemini()
    if cliente is None:
        return None

    try:
        texto_extraido = extrair_texto(io.BytesIO(pdf_bytes))
    except Exception:
        # PDF corrompido/protegido/ilegível para o pypdf - ainda vale
        # tentar a leitura nativa pela IA antes de desistir.
        texto_extraido = ""

    try:
        from google.genai import types as genai_types

        resposta = cliente.models.generate_content(
            model=MODELO_PADRAO,
            contents=_conteudo_mensagem(genai_types, pdf_bytes, texto_extraido),
            config=genai_types.GenerateContentConfig(
                system_instruction=PROMPT_SISTEMA,
                max_output_tokens=4096,
            ),
        )
        dados = _extrair_json(resposta.text)
    except Exception:
        return None

    return _normalizar_sugestao(dados)
