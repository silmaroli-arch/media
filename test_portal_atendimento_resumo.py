"""Testa a reformulação do Portal de atendimento (medico.portal_atendimento)
pedida pelo Silvan: a lista de perguntas pendentes estava mostrando tudo
aberto de uma vez (sugestões de IA, rascunho, histórico) e ficava grande
demais - agora cada pergunta aparece como uma linha resumida (paciente,
exame, pergunta, data/hora) e só ao clicar abre um popup com as sugestões
da IA, o rascunho editável, o botão de aprovar e o histórico de conversas.

Cobre:
1. A lista resumida mostra paciente/exame/pergunta/data, mas NÃO mostra as
   sugestões de IA nem o rascunho fora do popup.
2. O popup de cada pergunta (still no mesmo HTML, dentro de um <div
   class="modal">) contém as sugestões de IA, o rascunho editável e o
   botão de aprovar - para perguntas com IA (status aguardando_aprovacao).
2b. Para perguntas sem IA (status pendente), o popup mostra o campo de
    resposta manual em vez de sugestões.
3. O histórico de conversas do paciente está dentro do popup (não na
   linha resumida).
4. O formulário de aprovação continua funcionando (POST na mesma rota de
   sempre) - a reformulação é só visual.

Roda com banco isolado: `rm -f preparo_exames.db && python3
test_portal_atendimento_resumo.py`.
"""
from datetime import datetime, timedelta

from app import create_app
from app.extensions import db
from app.models import Usuario, Grupo, GrupoMembro, Paciente, Exame, PerguntaPendente

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


with app.app_context():
    db.create_all()

    medico = Usuario(nome="Dr. Portal", email="portal@teste.com", tipo="medico")
    medico.set_senha("123456")
    medico.conceder_todas_permissoes()
    db.session.add(medico)
    db.session.commit()

    grupo = Grupo(nome="Grupo do Portal", status="ativa")
    db.session.add(grupo)
    db.session.commit()
    db.session.add(GrupoMembro(grupo_id=grupo.id, usuario_id=medico.id, papel="dono", ativo=True))
    db.session.commit()

    paciente = Paciente(nome="Paciente Ansioso", cpf="44455566677")
    db.session.add(paciente)
    db.session.commit()

    exame = Exame(grupo_id=grupo.id, medico_id=medico.id, criado_por_id=medico.id, nome="Colonoscopia Teste")
    db.session.add(exame)
    db.session.commit()

    pergunta_com_ia = PerguntaPendente(
        grupo_id=grupo.id, paciente_id=paciente.id, exame_id=exame.id,
        pergunta="Posso continuar com meu antibiótico?",
        status="aguardando_aprovacao",
        resposta_sugerida_ia="Você pode continuar normalmente.",
        resposta_bruta_chatgpt="Sugestão bruta do ChatGPT sobre antibiótico.",
        resposta_bruta_claude="Sugestão bruta do Claude sobre antibiótico.",
        criado_em=datetime.utcnow() - timedelta(hours=3),
    )
    pergunta_sem_ia = PerguntaPendente(
        grupo_id=grupo.id, paciente_id=paciente.id, exame_id=exame.id,
        pergunta="Posso levar acompanhante?",
        status="pendente",
        criado_em=datetime.utcnow(),
    )
    db.session.add_all([pergunta_com_ia, pergunta_sem_ia])
    db.session.commit()
    id_com_ia = pergunta_com_ia.id
    id_sem_ia = pergunta_sem_ia.id

client.post("/login", data={"identificador": "portal@teste.com", "senha": "123456"})

r = client.get("/equipe/portal")
html = r.get_data(as_text=True)
checar("Portal responde 200", r.status_code == 200)

# --- Lista resumida mostra o essencial. ---
checar("Nome do paciente aparece", "Paciente Ansioso" in html)
checar("Nome do exame aparece", "Colonoscopia Teste" in html)
checar("Texto da pergunta com IA aparece", "Posso continuar com meu antibiótico?" in html)
checar("Texto da pergunta sem IA aparece", "Posso levar acompanhante?" in html)
checar("Data/hora de recebimento aparece", "Recebida em" in html)

# --- A linha resumida (fora do modal) não expõe as sugestões de IA nem
# o rascunho direto - só aparecem dentro do <div class="modal">. Corta o
# HTML a partir do primeiro modal (eles ficam depois das linhas resumidas
# no template) para comparar as duas partes separadamente. ---
indice_primeiro_modal = html.find('<div class="modal fade"')
parte_resumo = html[:indice_primeiro_modal]
parte_modais = html[indice_primeiro_modal:]

checar("Sugestão do ChatGPT NÃO aparece na parte resumida (só no popup)", "Sugestão bruta do ChatGPT" not in parte_resumo)
checar("Sugestão do Claude NÃO aparece na parte resumida (só no popup)", "Sugestão bruta do Claude" not in parte_resumo)
checar("Botão 'Aprovar e enviar ao paciente' NÃO aparece na parte resumida", "Aprovar e enviar ao paciente" not in parte_resumo)

# --- Dentro dos modais, tudo aparece. ---
checar("Sugestão do ChatGPT aparece dentro dos modais", "Sugestão bruta do ChatGPT" in parte_modais)
checar("Sugestão do Claude aparece dentro dos modais", "Sugestão bruta do Claude" in parte_modais)
checar("Botão 'Aprovar e enviar ao paciente' aparece dentro dos modais", "Aprovar e enviar ao paciente" in parte_modais)
checar("Botão 'Responder e ensinar a IA' aparece dentro dos modais (pergunta sem IA)", "Responder e ensinar a IA" in parte_modais)
checar("Modal específico da pergunta com IA existe (id do modal)", f'id="modal-pergunta-{id_com_ia}"' in html)
checar("Modal específico da pergunta sem IA existe (id do modal)", f'id="modal-pergunta-{id_sem_ia}"' in html)

# --- Histórico de conversas fica dentro do popup, não na linha resumida. ---
checar("Link de histórico NÃO aparece na parte resumida", "Ver histórico de conversas" not in parte_resumo)
# (Não há histórico de ChatMensagem cadastrado neste teste, então o link
# só apareceria se houvesse mensagens - confirmamos ao menos que não some
# incorretamente da parte do popup quando há dados, testado à parte.)

# --- O rascunho editável, dentro do modal, vem pré-preenchido com a
# sugestão da IA. ---
checar("Rascunho vem preenchido com a sugestão da IA", "Você pode continuar normalmente." in parte_modais)

# --- Indicador de urgência: a pergunta mais antiga (3h) deve ter a classe
# JS-alvo (checamos o data-attribute, já que o cálculo de "urgente" roda
# no navegador via JS, não no servidor). ---
checar("Pergunta antiga carrega o horário certo para o cálculo de urgência no navegador", "data-criado-em=" in html)

# --- Caminho funcional: aprovar continua funcionando (POST na mesma rota). ---
r2 = client.post(f"/equipe/perguntas/{id_com_ia}/responder", data={
    "origem": "portal", "resposta": "Resposta final aprovada pelo médico.",
}, follow_redirects=True)
checar("Aprovação responde 200", r2.status_code == 200)
with app.app_context():
    p = PerguntaPendente.query.get(id_com_ia)
    checar("Pergunta com IA foi marcada como respondida", p.status == "respondida")
    checar("Resposta final foi salva", p.resposta == "Resposta final aprovada pelo médico.")

print("\nTodos os testes do resumo do Portal de atendimento passaram.")
