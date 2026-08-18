"""Extração de sugestão de preparo a partir de um PDF usando IA (Claude) -
substitui a extração heurística por regex (`app.pdf_preparo`) como caminho
PRINCIPAL de importação de PDF: a Claude lê o PDF nativamente (inclusive
PDFs escaneados/imagem, que a extração de texto puro de `app.pdf_preparo`
não consegue interpretar) e devolve o mesmo formato estruturado usado pela
importação de Excel (ver `app.xlsx_preparo`), permitindo reaproveitar a
mesma tela de revisão antes de salvar - nada é gravado no banco até a
pessoa conferir e clicar em "Salvar", exatamente como já acontecia com a
extração por regex.

Só é usada quando ANTHROPIC_API_KEY está configurada (ver
app.ia_preparo._cliente_anthropic) - sem ela, ou se a chamada falhar por
qualquer motivo (rede, PDF ilegível, resposta que não veio em JSON
válido), `extrair_sugestao_de_pdf_com_ia` retorna None e quem chama deve
cair de volta pra extração heurística de
`app.pdf_preparo.extrair_sugestao_de_pdf`, que nunca lança uma exceção
não tratada."""
import base64
import json

from app.ia_preparo import MODELO_PADRAO, _cliente_anthropic

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
    que também nunca assumem um dado ausente/errado como impossível)."""
    return {
        "nome_sugerido": dados.get("nome_sugerido") or None,
        "instrucoes": (dados.get("instrucoes") or "").strip(),
        "cortes": [
            {"descricao": c.get("descricao"), "horas_antes": c.get("horas_antes")}
            for c in _lista(dados, "cortes")
            if isinstance(c, dict) and c.get("descricao") and c.get("horas_antes") is not None
        ],
        "medicamentos": [
            {"nome": m.get("nome"), "dias_antes": m.get("dias_antes"), "categoria": m.get("categoria") or None}
            for m in _lista(dados, "medicamentos")
            if isinstance(m, dict) and m.get("nome") and m.get("dias_antes") is not None
        ],
        "medicamentos_mantidos": [
            {"nome": m.get("nome"), "observacao": m.get("observacao") or None}
            for m in _lista(dados, "medicamentos_mantidos")
            if isinstance(m, dict) and m.get("nome")
        ],
        "observacoes_medicamentos": dados.get("observacoes_medicamentos") or None,
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


def extrair_sugestao_de_pdf_com_ia(pdf_bytes):
    """Retorna a sugestão estruturada (mesmo formato de
    `app.pdf_preparo.extrair_sugestao_de_pdf`) usando a Claude para ler o
    PDF, ou None se a IA não estiver configurada ou a chamada falhar por
    qualquer motivo (rede, PDF ilegível, resposta que não veio em JSON
    válido) - quem chama deve cair de volta pra extração heurística nesse
    caso, nunca deixar essa falha virar um erro 500 na tela."""
    cliente = _cliente_anthropic()
    if cliente is None:
        return None

    try:
        resposta = cliente.messages.create(
            model=MODELO_PADRAO,
            max_tokens=4096,
            system=PROMPT_SISTEMA,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": base64.b64encode(pdf_bytes).decode("ascii"),
                        },
                    },
                    {
                        "type": "text",
                        "text": "Extraia os dados de preparo deste PDF no formato JSON descrito nas instruções do sistema.",
                    },
                ],
            }],
        )
        dados = _extrair_json(resposta.content[0].text)
    except Exception:
        return None

    return _normalizar_sugestao(dados)
