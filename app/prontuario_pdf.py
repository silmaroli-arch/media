"""Exportação do prontuário (histórico de evolução clínica) em PDF — um
dos requisitos técnicos do manual SBIS-CFM para os Níveis de Garantia de
Segurança (NGS2/NGS3): o sistema precisa conseguir exportar o prontuário
num formato aberto. Reaproveita o mesmo padrão (reportlab) já usado em
app/nfse_nacional.py para o PDF de contingência da NFS-e."""
import io
from datetime import datetime

from app.assinatura_clinica import verificar_assinatura


def gerar_pdf_prontuario(paciente, evolucoes):
    """Gera o PDF com todo o histórico de evolução clínica do paciente
    (mais recente primeiro), indicando para cada entrada se ela está
    assinada digitalmente (e se a assinatura ainda confere com o
    conteúdo) ou não."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    largura, altura = A4
    margem = 50
    y = altura - margem

    def nova_pagina():
        nonlocal y
        c.showPage()
        y = altura - margem

    def garantir_espaco(linhas_necessarias=1, altura_linha=13):
        nonlocal y
        if y - (linhas_necessarias * altura_linha) < margem:
            nova_pagina()

    c.setFont("Helvetica-Bold", 14)
    c.drawString(margem, y, f"Prontuário — {paciente.nome}")
    y -= 20
    c.setFont("Helvetica", 9)
    c.setFillGray(0.3)
    c.drawString(margem, y, f"CPF: {paciente.cpf or '-'}  ·  Exportado em {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    c.setFillGray(0)
    y -= 12
    c.setFont("Helvetica-Oblique", 8)
    c.setFillGray(0.4)
    for linha in [
        "Documento gerado automaticamente a partir do registro de evolução clínica do paciente.",
        "Entradas assinadas digitalmente trazem a indicação do certificado do profissional responsável.",
    ]:
        c.drawString(margem, y, linha)
        y -= 10
    c.setFillGray(0)
    y -= 10
    c.line(margem, y, largura - margem, y)
    y -= 20

    if not evolucoes:
        c.setFont("Helvetica", 10)
        c.drawString(margem, y, "Nenhuma evolução clínica registrada para este paciente.")
    else:
        for e in evolucoes:
            garantir_espaco(6)
            c.setFont("Helvetica-Bold", 10)
            c.drawString(margem, y, f"{e.criado_em.strftime('%d/%m/%Y %H:%M')} — {e.autor.nome}")
            y -= 14

            c.setFont("Helvetica", 9)
            for linha_texto in (e.texto or "").split("\n"):
                # Quebra simples por largura de caractere — suficiente para
                # um documento de referência (não é um layout tipográfico).
                max_chars = 100
                while linha_texto:
                    garantir_espaco(1)
                    pedaco, linha_texto = linha_texto[:max_chars], linha_texto[max_chars:]
                    c.drawString(margem, y, pedaco)
                    y -= 12

            sinais = []
            if e.peso_kg:
                sinais.append(f"Peso: {e.peso_kg} kg")
            if e.altura_cm:
                sinais.append(f"Altura: {e.altura_cm} cm")
            if e.pressao_arterial:
                sinais.append(f"PA: {e.pressao_arterial} mmHg")
            if e.frequencia_cardiaca_bpm:
                sinais.append(f"FC: {e.frequencia_cardiaca_bpm} bpm")
            if e.temperatura_celsius:
                sinais.append(f"Temp.: {e.temperatura_celsius} °C")
            if sinais:
                garantir_espaco(1)
                c.setFont("Helvetica-Oblique", 8)
                c.setFillGray(0.35)
                c.drawString(margem, y, "  ·  ".join(sinais))
                c.setFillGray(0)
                y -= 12

            garantir_espaco(1)
            c.setFont("Helvetica-Oblique", 8)
            if e.assinada:
                confere = verificar_assinatura(e)
                if confere:
                    c.setFillGray(0.2)
                    texto_assinatura = (
                        f"Assinado digitalmente — certificado: {e.assinatura_certificado_titular or '-'} "
                        f"(serial {e.assinatura_certificado_serial}), em "
                        f"{e.assinado_em.strftime('%d/%m/%Y %H:%M') if e.assinado_em else '-'}. Assinatura conferida."
                    )
                else:
                    c.setFillGray(0.6)
                    texto_assinatura = (
                        "ATENÇÃO: esta entrada tem uma assinatura registrada, mas ela NÃO confere mais "
                        "com o conteúdo atual — verificar."
                    )
            else:
                c.setFillGray(0.6)
                texto_assinatura = "Sem assinatura digital (registro nível NGS2, sem certificado do profissional)."
            c.drawString(margem, y, texto_assinatura)
            c.setFillGray(0)
            y -= 18

            garantir_espaco(1)
            c.line(margem, y, largura - margem, y)
            y -= 14

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer
