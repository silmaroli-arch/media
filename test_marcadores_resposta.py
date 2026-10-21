"""Dados pessoais nas respostas sem ir para o FAQ (pedido do Silvan,
2026-10-21). A IA escreve marcadores ({data_exame}, {hora_exame},
{N dias antes}, {N horas antes}); o FAQ guarda o texto genérico e o
sistema preenche com o agendamento de cada paciente na hora de enviar.
Também: o contexto das IAs não leva mais nenhuma data (antes levava o
agendamento mais recente do TIPO de exame, de qualquer paciente)."""
from datetime import datetime
from unittest.mock import patch

from app import create_app, db
from app.ia_preparo import PROMPT_SISTEMA, _formatar_contexto_preparo
from app.marcadores_resposta import preencher_marcadores
from app.models import Agendamento, Exame, FaqItem, Grupo, Paciente, PerguntaPendente
from app.whatsapp_conversa import processar_mensagem

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


TEXTO = "Chegue antes ({hora_exame} do dia {data_exame}). Suspenda a partir de {3 dias antes}; jejum desde {12 horas antes}."

with app.app_context():
    ag = Agendamento(data_hora=datetime(2026, 10, 29, 11, 40))
    checar("Preenche com o agendamento",
           preencher_marcadores(TEXTO, ag) == "Chegue antes (11:40 do dia 29/10/2026). Suspenda a partir de 26/10/2026; jejum desde 28/10/2026 às 23:40.")
    checar("Sem agendamento, vira texto genérico",
           preencher_marcadores("Suspenda a partir de {3 dias antes}.", None) == "Suspenda a partir de 3 dias antes do exame.")
    checar("Texto sem marcador fica igual", preencher_marcadores("Pode tomar água.", ag) == "Pode tomar água.")

    checar("O prompt ensina os marcadores e proíbe datas concretas",
           "{data_exame}" in PROMPT_SISTEMA and "NUNCA escreva uma data ou um horário concreto" in PROMPT_SISTEMA)
    colono = Exame.query.filter_by(nome="Colonoscopia").first()
    contexto = _formatar_contexto_preparo(colono)
    checar("O contexto das IAs não tem nenhuma data (só prazos relativos)",
           "/20" not in contexto and "Data/hora do exame" not in contexto and "dias antes do exame" in contexto)

    # Dois pacientes, mesmo exame, horários diferentes, mesma pergunta no FAQ.
    vitoria = Grupo.query.filter_by(nome="Clínica Vitória").first()
    hemograma = Exame.query.filter_by(nome="Hemograma completo").first()
    pedro = Paciente.query.filter_by(nome="Pedro Souza").first()
    joao = Paciente.query.filter_by(nome="João Pereira").first()
    for ag in Agendamento.query.filter_by(paciente_id=pedro.id, exame_id=hemograma.id).all():
        ag.data_hora = datetime(2026, 11, 10, 8, 0)
    db.session.add(Agendamento(grupo_id=vitoria.id, paciente_id=joao.id, exame_id=hemograma.id,
                               medico_id=hemograma.medico_id, data_hora=datetime(2026, 11, 20, 14, 30)))
    db.session.add(FaqItem(grupo_id=vitoria.id, exame_id=hemograma.id, pergunta="Que horas devo chegar?",
                           resposta="Chegue 30 minutos antes ({hora_exame} do dia {data_exame}).", criado_por="teste"))
    db.session.commit()

    sem_validador = {"classificacao": None, "chamada": None}
    with patch("app.whatsapp_conversa.validar_pergunta", return_value=sem_validador):
        processar_mensagem("+5527900001010", "111.222.333-00")
        processar_mensagem("+5527900001010", "25121978")
        resposta_pedro = processar_mensagem("+5527900001010", "Que horas devo chegar?")
    checar("FAQ no WhatsApp: Pedro recebe o horário dele", "08:00 do dia 10/11/2026" in resposta_pedro)

    # João tem dois exames ativos: escolhe o hemograma.
    with patch("app.whatsapp_conversa.validar_pergunta", return_value=sem_validador):
        processar_mensagem("+5527900002020", "123.456.789-00")
        r = processar_mensagem("+5527900002020", "12041985")
        if "Hemograma" in r and "1)" in r:
            linha = next(l for l in r.splitlines() if "Hemograma" in l)
            processar_mensagem("+5527900002020", linha.strip()[0])
        resposta_joao = processar_mensagem("+5527900002020", "Que horas devo chegar?")
    checar("... e João, a mesma pergunta, recebe o horário DELE", "14:30 do dia 20/11/2026" in resposta_joao)
    checar("O FAQ continua genérico", FaqItem.query.filter_by(pergunta="Que horas devo chegar?").first().resposta
           == "Chegue 30 minutos antes ({hora_exame} do dia {data_exame}).")

    # Aprovação pelo médico: FAQ genérico, WhatsApp preenchido.
    pendente = PerguntaPendente(grupo_id=vitoria.id, paciente_id=pedro.id, exame_id=hemograma.id,
                                pergunta="Quando suspendo?", status="aguardando_aprovacao",
                                resposta_sugerida_ia="Suspenda a partir de {3 dias antes}.",
                                telefone_whatsapp="+5527900001010")
    db.session.add(pendente)
    db.session.commit()
    pendente_id = pendente.id

medico = app.test_client()
medico.post("/login", data={"identificador": "medica2@clinicavitoria.com", "senha": "123456"})
with app.app_context():
    vitoria_id = Grupo.query.filter_by(nome="Clínica Vitória").first().id
medico.post("/equipe/clinica", data={"clinica_id": str(vitoria_id)}, follow_redirects=True)
html = medico.get("/equipe/portal").get_data(as_text=True)
checar("Janela de aprovação: lembrete dos marcadores e a data do exame para a prévia",
       "Dados do paciente: use {data_exame}" in html and 'data-agendamento="2026-11-10T08:00"' in html
       and "Como o paciente vai receber" in html)
with patch("app.whatsapp_envio.enviar_mensagem_whatsapp") as envio:
    medico.post(f"/equipe/perguntas/{pendente_id}/responder", data={"resposta": "Suspenda a partir de {3 dias antes}."})
with app.app_context():
    checar("Aprovado: o WhatsApp leva a data do paciente", "Suspenda a partir de 07/11/2026." in envio.call_args[0][1])
    checar("... e o FAQ guarda o texto genérico",
           FaqItem.query.filter_by(pergunta="Quando suspendo?").first().resposta == "Suspenda a partir de {3 dias antes}.")
    checar("... e a área do paciente mostra preenchido",
           PerguntaPendente.query.get(pendente_id).resposta_para_paciente == "Suspenda a partir de 07/11/2026.")

print("\nTodos os testes de marcadores de dados do paciente passaram.")
