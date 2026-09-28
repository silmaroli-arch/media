"""Tela pública (SEM login) do preparo de um exame - aberta por um link
mandado no WhatsApp (pedido do Silvan, 2026-09-24): antes disso, a única
tela que mostra o preparo formatado (app/templates/paciente/preparo.html,
via app.routes_paciente.preparo_exame) exige login no site - inviável
para quem só conversa pelo WhatsApp, nunca fez login nenhum (só se
identificou por CPF+data de nascimento dentro da própria conversa, ver
app.whatsapp_conversa). Este módulo é a fonte ÚNICA da montagem desses
dados (`montar_documento_preparo`) e da geração do link
(`montar_link_preparo`) e do PDF (`gerar_pdf_preparo`) - reaproveitada
tanto pela tela HTML quanto pelo PDF baixável dela, para as duas NUNCA
divergirem uma da outra (ver app.routes_paciente.preparo_publico/
preparo_publico_pdf, que só chamam essas funções e renderizam).

Diferente da tela em abas usada no CADASTRO do preparo
(medico/preparo_modelo_form.html, com uma aba por categoria: instruções
gerais, cortes, medicamentos, informações gerais, alimentos, exames
anteriores), esta tela junta TUDO que tem um prazo calculável numa única
LINHA DO TEMPO cronológica (pedido explícito do Silvan: "tipo: dia 22
suspender monjauro etc, colocar os itens na linha do tempo") - mais fácil
de entender de um olhar só do que 6 abas separadas. O que não tem prazo
(medicamento mantido, alimento permitido, alimento/exame anterior
proibido sem prazo específico, informação geral solta) aparece em seções
à parte, depois da linha do tempo."""
import os
from datetime import datetime
from xml.sax.saxutils import escape as _escapar_xml


def _url_publica_base():
    """Mesmo padrão de app.push_notificacoes._link_perguntas (ver
    docstring lá, "Não usa url_for(_external=True) de propósito"): sem
    SERVER_NAME/ProxyFix configurado neste projeto, o esquema gerado por
    url_for(_external=True) poderia sair como "http://" mesmo em
    produção, atrás do proxy do Render. Env var opcional APP_URL_PUBLICA,
    com o domínio de dev do Render como padrão."""
    base = os.environ.get("APP_URL_PUBLICA", "https://media-dev.onrender.com")
    return base.rstrip("/")


def montar_link_preparo(agendamento):
    """Link público (sem login) para a tela de preparo deste agendamento -
    gera/reaproveita o token (ver Agendamento.obter_token_preparo_publico
    em app.models) e COMITA IMEDIATAMENTE se o token acabou de ser criado
    agora, para garantir que ele sobrevive mesmo que o resto da
    transação desta requisição não seja commitado por algum outro motivo
    depois (ver chamador em app.whatsapp_conversa._texto_pedir_pergunta).
    Reaproveitar um token já existente não precisa de commit extra
    nenhum - não muda nada no banco."""
    from app.extensions import db

    token_era_novo = not agendamento.token_preparo_publico
    token = agendamento.obter_token_preparo_publico()
    if token_era_novo:
        db.session.commit()
    return f"{_url_publica_base()}/paciente/preparo/{token}"


def _como_datetime(valor):
    """Normaliza date/datetime/None para datetime (meia-noite quando só
    havia uma date) - só para poder ordenar TUDO junto na mesma linha do
    tempo, já que os vários `limite()` do preparo (ver app/models.py)
    devolvem tipos diferentes entre si (corte e medicamento sempre têm
    hora certa ou não, alimento/informação geral dependem de qual campo
    de prazo foi preenchido)."""
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor
    return datetime.combine(valor, datetime.min.time())


def montar_documento_preparo(agendamento):
    """Devolve um dicionário com tudo que a tela/PDF público de preparo
    precisa, já processado: `agendamento`/`exame`/`preparo` (para o
    cabeçalho: nome do exame, data/hora, médico responsável),
    `linha_do_tempo` (lista de itens com prazo calculável, JÁ ORDENADA
    cronologicamente, cada um com "quando" (datetime), "titulo",
    "detalhe" e "categoria"), `sem_data` (dicionário de listas dos itens
    SEM prazo calculável, por categoria) e `instrucoes`/
    `observacoes_medicamentos` (texto livre do preparo). Quem chama
    (tela HTML ou PDF) só itera sobre isso - nenhuma lógica de cálculo
    de data mora na tela/PDF, só aqui."""
    exame = agendamento.exame
    preparo = exame.preparo if exame else None
    data_hora_exame = agendamento.data_hora

    linha_do_tempo = []
    sem_data = {
        "medicamentos_mantidos": [],
        "alimentos_permitidos": [],
        "alimentos_proibidos_sem_prazo": [],
        "exames_anteriores_sem_prazo": [],
        "informacoes_sem_prazo": [],
    }

    if preparo:
        for corte in preparo.cortes:
            linha_do_tempo.append({
                "quando": _como_datetime(corte.limite(data_hora_exame)),
                "titulo": corte.descricao,
                "detalhe": f"{corte.horas_antes} horas antes do exame",
                "categoria": "corte",
            })

        for ms in preparo.medicamentos_suspensos:
            linha_do_tempo.append({
                "quando": _como_datetime(ms.limite(data_hora_exame)),
                "titulo": f"Suspender {ms.medicamento.nome}",
                "detalhe": ms.observacao or f"{ms.dias_antes} dias antes do exame",
                "categoria": "medicamento",
            })

        for mm in preparo.medicamentos_mantidos:
            sem_data["medicamentos_mantidos"].append(mm)

        for a in preparo.alimentos:
            if a.permitido:
                sem_data["alimentos_permitidos"].append(a)
                continue
            quando = _como_datetime(a.limite(data_hora_exame))
            if quando:
                linha_do_tempo.append({
                    "quando": quando,
                    "titulo": f"Evitar {a.nome}",
                    "detalhe": a.limite_formatado(data_hora_exame),
                    "categoria": "alimento",
                })
            else:
                sem_data["alimentos_proibidos_sem_prazo"].append(a)

        for e in preparo.exames_anteriores_proibidos:
            quando = _como_datetime(e.limite(data_hora_exame))
            if quando:
                linha_do_tempo.append({
                    "quando": quando,
                    "titulo": f"Não pode ter feito recentemente: {e.nome}",
                    "detalhe": f"desde {quando.strftime('%d/%m/%Y')} ({e.dias_antes} dias antes do exame)",
                    "categoria": "exame_anterior",
                })
            else:
                sem_data["exames_anteriores_sem_prazo"].append(e)

        for info in preparo.informacoes_gerais:
            quando = _como_datetime(info.limite(data_hora_exame))
            if quando:
                linha_do_tempo.append({
                    "quando": quando,
                    "titulo": info.texto,
                    "detalhe": None,
                    "categoria": "informacao",
                })
            else:
                sem_data["informacoes_sem_prazo"].append(info)

        # O próprio exame encerra a linha do tempo - é a "chegada" que
        # todo o resto conta prazo pra trás a partir dela, então ajuda a
        # ver o quadro completo de um olhar só.
        linha_do_tempo.append({
            "quando": data_hora_exame,
            "titulo": f"Exame: {exame.nome}",
            "detalhe": f"com {agendamento.medico.nome}" if agendamento.medico else None,
            "categoria": "exame",
        })

    linha_do_tempo.sort(key=lambda item: item["quando"])

    return {
        "agendamento": agendamento,
        "exame": exame,
        "preparo": preparo,
        "linha_do_tempo": linha_do_tempo,
        "sem_data": sem_data,
        "instrucoes": preparo.instrucoes if preparo else None,
        "observacoes_medicamentos": preparo.observacoes_medicamentos if preparo else None,
    }


def _texto_seguro(texto):
    """Escapa &, < e > antes de entrar num `Paragraph` do reportlab, que
    interpreta um mini-XML dentro do texto (ex.: <br/>, <b>) - sem isso,
    um preparo cujo texto livre tenha um "&" ou "<" sozinho (ex.: "chá &
    água", "manhã < 2h antes") quebraria a geração do PDF inteiro com um
    erro de parsing, em vez de só exibir o caractere normalmente."""
    if not texto:
        return ""
    return _escapar_xml(str(texto))


def gerar_pdf_preparo(documento):
    """Devolve uma resposta Flask com o PDF do preparo (mesmo conteúdo de
    `montar_documento_preparo`, formatado como documento) - reportlab
    (Platypus), já dependência do projeto (ver requirements.txt e
    app.relatorios_utils.exportar_pdf, mesmo padrão de estilo/estrutura
    reaproveitado aqui: SimpleDocTemplate + getSampleStyleSheet + Table
    com cabeçalho azul)."""
    import io

    from flask import Response
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    agendamento = documento["agendamento"]
    exame = documento["exame"]
    preparo = documento["preparo"]

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=1.5 * cm, bottomMargin=1.5 * cm, leftMargin=1.5 * cm, rightMargin=1.5 * cm,
    )
    estilos = getSampleStyleSheet()
    estilo_item = ParagraphStyle("ItemPreparo", parent=estilos["Normal"], spaceAfter=4)

    cabecalho_info = f"Exame em: {agendamento.data_hora.strftime('%d/%m/%Y às %H:%M')}"
    if agendamento.medico:
        cabecalho_info += f" · Médico: {_texto_seguro(agendamento.medico.nome)}"

    elementos = [
        Paragraph(_texto_seguro(exame.nome if exame else "Preparo de exame"), estilos["Title"]),
        Paragraph(cabecalho_info, estilos["Normal"]),
        Paragraph(
            f"Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')} - documento informativo, não substitui "
            "orientação médica em caso de dúvida.",
            estilos["Italic"],
        ),
        Spacer(1, 0.5 * cm),
    ]

    if documento["linha_do_tempo"]:
        elementos.append(Paragraph("Linha do tempo do preparo", estilos["Heading2"]))
        dados_tabela = [["Data", "O que fazer"]]
        for item in documento["linha_do_tempo"]:
            texto = _texto_seguro(item["titulo"])
            if item["detalhe"]:
                texto += f" — {_texto_seguro(item['detalhe'])}"
            dados_tabela.append([
                item["quando"].strftime("%d/%m/%Y %H:%M"),
                Paragraph(texto, estilo_item),
            ])
        tabela = Table(dados_tabela, colWidths=[3.6 * cm, None], repeatRows=1)
        tabela.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0d6efd")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        elementos.append(tabela)
        elementos.append(Spacer(1, 0.5 * cm))

    sem_data = documento["sem_data"]

    def _secao_lista(titulo, itens, texto_item):
        if not itens:
            return
        elementos.append(Paragraph(titulo, estilos["Heading3"]))
        for item in itens:
            elementos.append(Paragraph(f"• {_texto_seguro(texto_item(item))}", estilo_item))
        elementos.append(Spacer(1, 0.3 * cm))

    _secao_lista(
        "Medicamentos que podem ser mantidos", sem_data["medicamentos_mantidos"],
        lambda mm: mm.nome + (f" — {mm.observacao}" if mm.observacao else ""),
    )
    _secao_lista("Sugestão de consumo", sem_data["alimentos_permitidos"], lambda a: a.nome)
    _secao_lista(
        "Alimentos proibidos (sem prazo específico)", sem_data["alimentos_proibidos_sem_prazo"],
        lambda a: a.nome,
    )
    _secao_lista(
        "Não pode ter feito recentemente", sem_data["exames_anteriores_sem_prazo"], lambda e: e.nome,
    )
    _secao_lista(
        "Outras orientações", sem_data["informacoes_sem_prazo"], lambda info: info.texto,
    )

    if documento.get("observacoes_medicamentos"):
        elementos.append(Paragraph(_texto_seguro(documento["observacoes_medicamentos"]), estilo_item))
        elementos.append(Spacer(1, 0.3 * cm))

    if documento.get("instrucoes"):
        elementos.append(Paragraph("Instruções gerais", estilos["Heading3"]))
        for paragrafo in documento["instrucoes"].splitlines():
            if paragrafo.strip():
                elementos.append(Paragraph(_texto_seguro(paragrafo), estilo_item))

    if not preparo:
        elementos.append(Paragraph(
            "Este agendamento não tem instruções de preparo — não é necessário nenhum preparo prévio.",
            estilos["Italic"],
        ))

    doc.build(elementos)
    buffer.seek(0)

    nome_exame_arquivo = "".join(c if c.isalnum() else "_" for c in (exame.nome if exame else "preparo"))
    nome_arquivo = f"preparo_{nome_exame_arquivo}.pdf"
    return Response(
        buffer.read(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nome_arquivo}"'},
    )
