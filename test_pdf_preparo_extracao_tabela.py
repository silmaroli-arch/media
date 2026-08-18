"""Testa as melhorias na extração heurística de `app/pdf_preparo.py` feitas
pra cobrir o estilo de PDF de preparo "em tabela" (comum em preparos de
colonoscopia), sem marcadores de lista tradicionais (➢▪▶→•*-) em boa parte
do texto: rótulos diretos "NÃO COMER:"/"PODE COMER:", listas numeradas/
letradas ("1.", "a)") pra receituário e orientações do dia do exame, prazo
de horário fixo ("até às HH:MM da véspera"), e a lista de medicamentos que
NÃO precisam ser suspensos quebrada item a item (em vez de uma frase só).

As linhas do PDF de teste são geradas com `textwrap.wrap` (não com um corte
bruto tipo `l[:200]`) pra simular de forma realista a quebra de linha de um
PDF de verdade - cortar no meio de uma frase destrói pontuação (fim de
frase) da qual boa parte dessas regras depende."""
import io
import textwrap

from reportlab.pdfgen import canvas

from app.pdf_preparo import extrair_sugestao_de_pdf


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


LINHAS_LOGICAS = [
    "PREPARO TESTE MELHORIAS",
    "",
    "RECEITUARIO",
    "a) Tomar primeiro sache de PICOSSULFATO as 16:00hs",
    "b) Tomar segundo sache de PICOSSULFATO as 20:00hs Modo de preparo do PICOSSULFATO: "
    "dissolver o conteudo do sache em copo de 150 ml de agua em temperatura ambiente, "
    "mexer por 2 a 3 minutos.",
    "",
    "SUSPENDER",
    "14 dias antes: OZEMPIC, MOUNJARO, TRULICITY OU SIMILARES.",
    "",
    "OBS: NAO E NECESSARIO SUSPENDER O AAS, SOMALGIN, ASPIRINA.",
    "",
    "NAO COMER: alimentos com sementes, biscoito integral, agua de coco, sal ou maisena, pipoca.",
    "PODE COMER: arroz, carne branca, tapioca.",
    "",
    "VESPERA DO EXAME",
    "Pode comer até às 20:00 da véspera do exame (depois disso, só líquidos).",
    "",
    "DIA DO EXAME",
    "1. Chegar com uma hora de antecedencia.",
    "2. Trazer acompanhante.",
    "*Em caso de nauseas, gases ou dores, avisar a equipe.",
    "*Em caso de duvidas, ligar para a clinica.",
]


def _gerar_pdf(linhas_logicas):
    buffer_pdf = io.BytesIO()
    c = canvas.Canvas(buffer_pdf)
    y = 800
    for logica in linhas_logicas:
        if not logica:
            y -= 14
            continue
        for linha_fisica in textwrap.wrap(logica, width=100) or [""]:
            c.drawString(50, y, linha_fisica)
            y -= 14
    c.showPage()
    c.save()
    buffer_pdf.seek(0)
    return buffer_pdf


sugestao = extrair_sugestao_de_pdf(_gerar_pdf(LINHAS_LOGICAS))

# ---------- Medicamentos a suspender / mantidos ----------

checar(
    "Medicamentos a suspender extraídos da linha de prazo em tabela",
    {m["nome"] for m in sugestao["medicamentos"]} == {"OZEMPIC", "MOUNJARO", "TRULICITY OU SIMILARES"},
)
checar(
    "'Não suspender' vira um item por medicamento, não uma frase só",
    sugestao["medicamentos_mantidos"] == [
        {"nome": "AAS", "observacao": None},
        {"nome": "SOMALGIN", "observacao": None},
        {"nome": "ASPIRINA", "observacao": None},
    ],
)

# ---------- Informações gerais: receituário numerado/letrado + dia do exame ----------

textos_info = [i if isinstance(i, str) else i.get("texto") for i in sugestao["informacoes_gerais"]]
checar(
    "Passo 'a)' do receituário foi capturado",
    "Tomar primeiro sache de PICOSSULFATO as 16:00hs" in textos_info,
)
checar(
    "Passo 'b)' do receituário (mais longo, com o modo de preparo) foi capturado inteiro, "
    "sem 'engolir' o título de seção seguinte",
    any(t.startswith("Tomar segundo sache") and "mexer por 2 a 3 minutos" in t for t in textos_info),
)
checar(
    "Orientações numeradas do dia do exame foram capturadas",
    "Chegar com uma hora de antecedencia." in textos_info and "Trazer acompanhante." in textos_info,
)
checar(
    "Linhas marcadas com '*' (sem numeração) também foram capturadas, não descartadas",
    "Em caso de nauseas, gases ou dores, avisar a equipe." in textos_info
    and "Em caso de duvidas, ligar para a clinica." in textos_info,
)
checar(
    "Nenhum título de seção (RECEITUARIO/SUSPENDER/DIA DO EXAME/etc.) vazou pra dentro de um item",
    not any("SUSPENDER" in t or "RECEITUARIO" in t or "DIA DO EXAME" in t for t in textos_info),
)

# ---------- Prazo de horário fixo na véspera ----------

prazo_vespera = next((i for i in sugestao["informacoes_gerais"] if isinstance(i, dict)), None)
checar(
    "Prazo 'até às 20:00 da véspera' virou uma info geral com dias_antes+hora_exata estruturados",
    prazo_vespera is not None
    and prazo_vespera["dias_antes"] == 1
    and prazo_vespera["hora_exata"] == "20:00"
    and prazo_vespera["horas_antes"] is None,
)

# ---------- Alimentos: rótulo direto NÃO COMER / PODE COMER ----------

proibidos = {a["nome"] for a in sugestao["alimentos"] if not a["permitido"]}
permitidos = {a["nome"] for a in sugestao["alimentos"] if a["permitido"]}
checar(
    "Todos os alimentos da lista 'NÃO COMER' foram extraídos, com prazo padrão de 12h",
    proibidos == {"alimentos com sementes", "biscoito integral", "agua de coco", "sal ou maisena", "pipoca"}
    and all(a["horas_antes"] == 12 for a in sugestao["alimentos"] if not a["permitido"]),
)
checar(
    "Todos os alimentos da lista 'PODE COMER' foram extraídos, sem prazo (sempre liberados)",
    permitidos == {"arroz", "carne branca", "tapioca"},
)
checar(
    "Nenhum nome de alimento ficou com quebra de linha crua no meio (ex.: 'alimentos com\\nsementes')",
    all("\n" not in a["nome"] for a in sugestao["alimentos"]),
)

print("\nTodos os testes de extração de PDF em estilo tabela passaram.")
