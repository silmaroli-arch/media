"""Testa o APRENDIZADO da base de conhecimento - fatia 4 da "terceira IA"
(pedido do Silvan, 2026-09-29) - ver app.base_aprendizado e o gancho em
medico.perguntas_responder.

Cobre:
1. Rede de seguranca: telefone, CPF, e-mail, link e valor em dinheiro barram o texto.
2. Aprendizado desligado (padrao): responder nao mexe na base.
3. Ligado + resposta manual a pergunta nova: cria item (origem medico, nao revisado, autor = medico).
4. Conteudo nao aproveitavel (IA devolve None) ou preparo sem tipo: nao cria nada.
5. Pergunta ja existente: aprovar rascunho SEM editar nao sobrescreve.
6. Medico edita e o arbitro diz "muito diferente" (ignorando prazos): atualiza,
   guarda historico, marca nao revisado. Se o arbitro diz que nao diverge: nada muda.
7. Secretaria (nao medico) nao alimenta a base.
8. Medico excluido: item continua na base.

Sem rede (generalizacao e arbitro simulados). Precisa do banco recriado + seed.py:
    DATABASE_URL=sqlite:///teste_base_aprendizado.db python seed.py
    DATABASE_URL=sqlite:///teste_base_aprendizado.db python test_base_aprendizado.py
"""
from app import create_app
from app.extensions import db
from app.models import (
    Usuario, Grupo, Paciente, Exame, PerguntaPendente, PreparoModelo, TipoExame,
    BaseConhecimentoItem, BaseConhecimentoHistorico, PlataformaConfig,
)
from app.tipos_exame_padrao import semear_tipos_exame
import app.ia_preparo as ia
import app.base_conhecimento as bc
import app.base_aprendizado as ba

app = create_app()
app.config["BASE_APRENDIZADO_SINCRONO"] = True
client = app.test_client()


def checar(nome, condicao):
    print(f"[{'OK' if condicao else 'FALHOU'}] {nome}")
    assert condicao, nome


# ---------- 1. Rede de seguranca ----------
for texto in ("Ligue (27) 99999-1234", "CPF 123.456.789-00", "a@b.com", "veja https://x.com", "custa R$ 50", "www.site.com"):
    checar(f"Barra dado pessoal: {texto}", ba.contem_dado_pessoal(texto))
checar("Texto limpo passa", not ba.contem_dado_pessoal("Evite mascar chiclete durante o preparo."))

with app.app_context():
    semear_tipos_exame(db, TipoExame)
    clinica = Grupo.query.filter_by(nome="Clínica Vitória").first()
    carlos = Usuario.query.filter_by(email="medico@clinicavitoria.com").first()
    paciente = Paciente.query.filter_by(cpf="123.456.789-00").first()
    clinica_id, carlos_id, paciente_id = clinica.id, carlos.id, paciente.id
    colono = TipoExame.query.filter_by(nome="Colonoscopia").first()
    modelo_tipo = PreparoModelo(nome="Preparo Aprend Com Tipo", instrucoes="x", grupo_id=clinica_id,
                                criado_por_id=carlos_id, tipo_exame_id=colono.id)
    modelo_sem = PreparoModelo(nome="Preparo Aprend Sem Tipo", instrucoes="x", grupo_id=clinica_id,
                               criado_por_id=carlos_id)
    db.session.add_all([modelo_tipo, modelo_sem])
    db.session.flush()
    ex_tipo = Exame(grupo_id=clinica_id, medico_id=carlos_id, nome="Exame Aprend Tipo", associado=True,
                    medico_confirmado=True, preparo_modelo_id=modelo_tipo.id)
    ex_sem = Exame(grupo_id=clinica_id, medico_id=carlos_id, nome="Exame Aprend Sem Tipo", associado=True,
                   medico_confirmado=True, preparo_modelo_id=modelo_sem.id)
    db.session.add_all([ex_tipo, ex_sem])
    db.session.commit()
    ex_tipo_id, ex_sem_id, colono_id = ex_tipo.id, ex_sem.id, colono.id
    PlataformaConfig.obter().base_aprendizado_ativo = False
    db.session.commit()

cenario = {"geral": {"pergunta": "Posso mascar chiclete no preparo?", "resposta": "Não, chiclete quebra a restrição do preparo."},
           "achado": None, "diverge": True}
chamadas_arbitro = []
originais = (ba.generalizar_com_ia, bc.buscar_na_base, ia._respostas_divergem, ia._cliente_anthropic)
ba.generalizar_com_ia = lambda p, r, usuario_id=None: cenario["geral"]
bc.buscar_na_base = lambda pergunta, tipo_exame_id=None, limite=3, provedor=None: (
    [cenario["achado"]] if cenario["achado"] else [])


def arbitro_falso(cliente, a, b, paciente_id=None, ignorar_prazos=False):
    chamadas_arbitro.append(ignorar_prazos)
    return cenario["diverge"]


ia._respostas_divergem = arbitro_falso
ia._cliente_anthropic = lambda: object()


def nova_pergunta(exame_id, status="pendente", ia_rascunho=False):
    with app.app_context():
        p = PerguntaPendente(paciente_id=paciente_id, exame_id=exame_id, grupo_id=clinica_id,
                             pergunta="posso mascar chiclete?", status=status)
        if ia_rascunho:
            p.resposta_sugerida_ia = "Rascunho da IA."
            p.resposta_bruta_claude = "Rascunho da IA."
        db.session.add(p)
        db.session.commit()
        return p.id


def login(email):
    client.get("/logout")
    client.post("/login", data={"identificador": email, "senha": "123456"})


def responder(pergunta_id, texto):
    return client.post(f"/equipe/perguntas/{pergunta_id}/responder", data={"resposta": texto}, follow_redirects=True)


def total_itens():
    with app.app_context():
        return BaseConhecimentoItem.query.count()


try:
    login("medico@clinicavitoria.com")

    # ---------- 2. Desligado ----------
    antes = total_itens()
    responder(nova_pergunta(ex_tipo_id), "Não pode.")
    checar("Desligado: base nao muda", total_itens() == antes)

    with app.app_context():
        PlataformaConfig.obter().base_aprendizado_ativo = True
        db.session.commit()

    # ---------- 3. Cria item ----------
    responder(nova_pergunta(ex_tipo_id), "Não pode.")
    with app.app_context():
        novo = BaseConhecimentoItem.query.filter_by(origem="medico").order_by(BaseConhecimentoItem.id.desc()).first()
        checar("Ligado: cria item novo", novo is not None and total_itens() == antes + 1)
        checar("Item vem nao revisado, do tipo e com autor", (not novo.revisado) and novo.tipo_exame_id == colono_id and novo.autor_usuario_id == carlos_id)
        novo_id = novo.id

    # ---------- 4. Nao aproveitavel / sem tipo ----------
    antes = total_itens()
    cenario_geral = cenario["geral"]
    cenario["geral"] = None
    responder(nova_pergunta(ex_tipo_id), "Remarcamos para terça, ligue 27 99999-1234.")
    checar("Conteudo nao aproveitavel: nada criado", total_itens() == antes)
    cenario["geral"] = cenario_geral
    responder(nova_pergunta(ex_sem_id), "Não pode.")
    checar("Preparo sem tipo de exame: nada criado", total_itens() == antes)

    # ---------- 5. Rascunho aprovado sem editar ----------
    cenario["achado"] = None
    with app.app_context():
        cenario["achado"] = {"item": BaseConhecimentoItem.query.get(novo_id), "score": 0.95, "metodo": "palavra_chave"}
    responder(nova_pergunta(ex_tipo_id, "aguardando_aprovacao", ia_rascunho=True), "Rascunho da IA.")
    with app.app_context():
        checar("Aprovar sem editar nao sobrescreve", BaseConhecimentoItem.query.get(novo_id).resposta == "Não, chiclete quebra a restrição do preparo."
               and BaseConhecimentoHistorico.query.filter_by(item_id=novo_id).count() == 0)

    # ---------- 6. Medico edita e diverge ----------
    cenario["geral"] = {"pergunta": "Posso mascar chiclete no preparo?", "resposta": "Pode, desde que sem açúcar."}
    cenario["diverge"] = False
    responder(nova_pergunta(ex_tipo_id, "aguardando_aprovacao", ia_rascunho=True), "Pode, sem açúcar.")
    with app.app_context():
        checar("Editou mas nao diverge: nada muda", BaseConhecimentoItem.query.get(novo_id).resposta.startswith("Não"))
    cenario["diverge"] = True
    chamadas_arbitro.clear()
    with app.app_context():
        BaseConhecimentoItem.query.get(novo_id).revisado = True
        db.session.commit()
    responder(nova_pergunta(ex_tipo_id, "aguardando_aprovacao", ia_rascunho=True), "Pode, sem açúcar.")
    with app.app_context():
        item = BaseConhecimentoItem.query.get(novo_id)
        hist = BaseConhecimentoHistorico.query.filter_by(item_id=novo_id).all()
        checar("Editou e diverge: atualiza a resposta", item.resposta == "Pode, desde que sem açúcar.")
        checar("Guarda a versao anterior no historico", len(hist) == 1 and hist[0].resposta.startswith("Não"))
        checar("Volta a nao revisado", not item.revisado)
        checar("Arbitro foi chamado ignorando prazos", chamadas_arbitro and all(chamadas_arbitro))

    # ---------- 7. Secretaria ----------
    cenario["achado"] = None
    antes = total_itens()
    with app.app_context():
        sec = Usuario.query.filter_by(tipo="secretaria").first()
        sec_email = sec.email if sec else None
    if sec_email:
        cenario["geral"] = {"pergunta": "Pergunta inedita da secretaria?", "resposta": "Resposta qualquer."}
        login(sec_email)
        responder(nova_pergunta(ex_tipo_id), "Resposta qualquer.")
        checar("Secretaria nao alimenta a base", total_itens() == antes)

    # ---------- 8. Medico excluido ----------
    from app.exclusao_usuario import excluir_usuario_e_dados
    with app.app_context():
        medico_teste = Usuario(nome="Dr. Aprende", email="aprende@example.com", tipo="medico")
        medico_teste.set_senha("123456")
        db.session.add(medico_teste)
        db.session.flush()
        item = BaseConhecimentoItem(tipo_exame_id=colono_id, pergunta="Pergunta do medico excluido?",
                                    resposta="Resposta.", origem="medico", autor_usuario_id=medico_teste.id,
                                    autor_nome="Dr. Aprende")
        db.session.add(item)
        db.session.commit()
        item_id, mid = item.id, medico_teste.id
        excluir_usuario_e_dados(Usuario.query.get(mid))
        db.session.commit()
        item = BaseConhecimentoItem.query.get(item_id)
        checar("Medico excluido: item continua na base", item is not None and item.autor_usuario_id is None and item.autor_nome == "Dr. Aprende")
finally:
    ba.generalizar_com_ia, bc.buscar_na_base, ia._respostas_divergem, ia._cliente_anthropic = originais

print("\nTodos os testes passaram.")
