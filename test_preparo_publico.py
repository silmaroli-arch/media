"""Testa app.preparo_publico (pedido do Silvan, 2026-09-24 - link no
WhatsApp para uma tela pública, sem login, com o preparo do exame em
formato de documento/linha do tempo, e o PDF baixável dela):

- `montar_link_preparo`: gera um token único na primeira chamada (e
  COMITA imediatamente, ver docstring da função), reaproveita o mesmo
  token nas chamadas seguintes, e o link produzido aponta para a rota
  pública correta (`/paciente/preparo/<token>`).
- `montar_documento_preparo`: junta cortes/medicamentos suspensos/
  alimentos proibidos-com-prazo numa única `linha_do_tempo`, ORDENADA
  cronologicamente, e separa o que não tem prazo calculável (medicamento
  mantido, alimento permitido) em `sem_data` - sem perder nenhum item.
- `gerar_pdf_preparo`: não derruba com texto livre contendo "&"/"<"/">"
  (ver `_texto_seguro`) e devolve uma resposta Flask com
  `mimetype="application/pdf"`.

Testado com app_context + banco de verdade (create_app/seed.py), sem
mockar nada (não depende de nenhuma IA) - usa o agendamento de
colonoscopia do João (grupo Vitória, ver seed.py), que tem cortes,
medicamentos suspensos, medicamentos mantidos e alimentos proibidos/
permitidos cadastrados."""
from app import create_app, db
from app.models import Agendamento, Exame
from app.preparo_publico import gerar_pdf_preparo, montar_documento_preparo, montar_link_preparo


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


app = create_app()

with app.app_context():
    exame_colonoscopia = Exame.query.filter_by(nome="Colonoscopia").first()
    checar("Existe o exame 'Colonoscopia' cadastrado pelo seed.py", exame_colonoscopia is not None)

    agendamento = Agendamento.query.filter_by(exame_id=exame_colonoscopia.id).first()
    checar(
        "Existe um agendamento de colonoscopia cadastrado pelo seed.py (com preparo completo)",
        agendamento is not None,
    )

    # --- montar_link_preparo ---
    checar(
        "Agendamento começa sem token público (seed.py não gera um)",
        agendamento.token_preparo_publico is None,
    )
    link1 = montar_link_preparo(agendamento)
    token_gerado = agendamento.token_preparo_publico
    checar("Token foi gerado na primeira chamada", token_gerado is not None)
    checar("Link contém a rota pública correta com o token", link1.endswith(f"/paciente/preparo/{token_gerado}"))

    link2 = montar_link_preparo(agendamento)
    checar("Segunda chamada reaproveita o MESMO token (não gera outro)", agendamento.token_preparo_publico == token_gerado)
    checar("Segunda chamada devolve o mesmo link", link1 == link2)

    # Sobrevive a um reload direto do banco (confirma que o commit da
    # primeira chamada realmente persistiu o token).
    db.session.expire(agendamento)
    agendamento_recarregado = Agendamento.query.get(agendamento.id)
    checar("Token persistido no banco (sobrevive a um reload do objeto)", agendamento_recarregado.token_preparo_publico == token_gerado)

    # --- montar_documento_preparo ---
    documento = montar_documento_preparo(agendamento)
    checar("Documento tem preparo (exame de colonoscopia tem PreparoModelo associado)", documento["preparo"] is not None)

    titulos_linha_do_tempo = [item["titulo"] for item in documento["linha_do_tempo"]]
    checar(
        "Linha do tempo inclui os cortes (ex.: 'Alimentos sólidos')",
        any("Alimentos sólidos" in t for t in titulos_linha_do_tempo),
    )
    checar(
        "Linha do tempo inclui os medicamentos suspensos (ex.: Xarelto)",
        any("Suspender" in t and "Xarelto" in t for t in titulos_linha_do_tempo),
    )
    checar(
        "Linha do tempo inclui os alimentos proibidos com prazo (ex.: Leite e derivados)",
        any("Leite e derivados" in t for t in titulos_linha_do_tempo),
    )
    checar(
        "Linha do tempo termina com o próprio exame ('Exame: Colonoscopia')",
        titulos_linha_do_tempo[-1] == "Exame: Colonoscopia",
    )
    checar(
        "Linha do tempo está ordenada cronologicamente (cada item <= o próximo)",
        all(
            documento["linha_do_tempo"][i]["quando"] <= documento["linha_do_tempo"][i + 1]["quando"]
            for i in range(len(documento["linha_do_tempo"]) - 1)
        ),
    )

    nomes_mantidos = [mm.nome for mm in documento["sem_data"]["medicamentos_mantidos"]]
    checar("Medicamento mantido (ex.: AAS) vai para 'sem_data', não para a linha do tempo", "AAS" in nomes_mantidos)
    checar("Medicamento mantido NÃO aparece na linha do tempo", not any("AAS" in t for t in titulos_linha_do_tempo))

    nomes_permitidos = [a.nome for a in documento["sem_data"]["alimentos_permitidos"]]
    checar("Alimento permitido (ex.: Água de coco) vai para 'sem_data'", "Água de coco" in nomes_permitidos)

    # --- gerar_pdf_preparo ---
    resposta_pdf = gerar_pdf_preparo(documento)
    checar("gerar_pdf_preparo devolve mimetype application/pdf", resposta_pdf.mimetype == "application/pdf")
    checar("PDF gerado não está vazio", len(resposta_pdf.get_data()) > 500)

    # Texto livre com caracteres que quebrariam um Paragraph do reportlab
    # sem o escape (ver _texto_seguro) - garante que a geração do PDF não
    # derruba com esse conteúdo.
    preparo_original_instrucoes = documento["preparo"].instrucoes
    documento["preparo"].instrucoes = "Evite chá & água < 2h antes do exame > horário marcado"
    try:
        resposta_pdf_com_caracteres_especiais = gerar_pdf_preparo(documento)
        checar(
            "PDF não quebra com '&'/'<'/'>' no texto livre das instruções (_texto_seguro escapa antes do Paragraph)",
            resposta_pdf_com_caracteres_especiais.mimetype == "application/pdf",
        )
    finally:
        documento["preparo"].instrucoes = preparo_original_instrucoes

    # Agendamento sem preparo nenhum não pode quebrar nem a montagem do
    # documento nem a geração do PDF - desassocia temporariamente o
    # modelo de preparo do exame (só em memória, nunca comitado - ver
    # db.session.rollback() no fim deste arquivo) para simular esse caso
    # sem precisar de um exame "sem preparo" dedicado no seed.py.
    preparo_modelo_id_original = exame_colonoscopia.preparo_modelo_id
    exame_colonoscopia.preparo_modelo_id = None
    exame_colonoscopia.preparo_modelo = None
    try:
        documento_sem_preparo = montar_documento_preparo(agendamento)
        checar(
            "Agendamento sem preparo: linha do tempo vazia, sem quebrar",
            documento_sem_preparo["linha_do_tempo"] == [],
        )
        resposta_pdf_sem_preparo = gerar_pdf_preparo(documento_sem_preparo)
        checar(
            "PDF de agendamento sem preparo ainda é gerado normalmente (mensagem de fallback)",
            resposta_pdf_sem_preparo.mimetype == "application/pdf",
        )
    finally:
        exame_colonoscopia.preparo_modelo_id = preparo_modelo_id_original

    db.session.rollback()

print("\nTodos os testes do preparo público (link/documento/PDF) passaram.")
