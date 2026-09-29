"""Testa a BASE DE CONHECIMENTO compartilhada - fatia 2 da "terceira IA"
(pedido do Silvan, 2026-09-29) - ver app.base_conhecimento,
app.base_conhecimento_padrao e as rotas dono.base_conhecimento*.

Cobre:
1. Carga inicial: semear e idempotente e nao altera item editado pelo dono.
2. Interruptor desligado por padrao / config so pelo dono / provedor invalido.
3. Dono adiciona, edita (guarda historico, marca revisado), desfaz, inativa.
4. Busca por palavra-chave: acha o parecido, respeita tipo de exame, ignora inativo.
5. Busca por embeddings (provedor simulado, sem rede): usa so vetores do mesmo
   modelo, calcula vetores em lote e cai para palavra-chave se a API falhar.
6. Medico excluido: o item que ele escreveu NAO some (autoria vira so texto).
7. Medico comum nao acessa a tela do dono.

Sem rede. Para rodar:
    DATABASE_URL=sqlite:///teste_base_conhecimento.db python test_base_conhecimento.py
"""
from app import create_app
from app.extensions import db
from app.db_utils import resetar_banco
from app.models import Usuario, TipoExame, BaseConhecimentoItem, PlataformaConfig
from app.tipos_exame_padrao import semear_tipos_exame
from app.base_conhecimento_padrao import semear_base_conhecimento, ITENS_PADRAO
import app.base_conhecimento as bc
from app.exclusao_usuario import excluir_usuario_e_dados

app = create_app()
client = app.test_client()

with app.app_context():
    resetar_banco(db)
    db.session.commit()


def checar(nome, condicao):
    print(f"[{'OK' if condicao else 'FALHOU'}] {nome}")
    assert condicao, nome


r = client.post("/cadastro", data={
    "nome": "Dr. Base Teste", "papel": "medico", "cpf": "111.222.333-97", "crm_numero": "88888",
    "crm_uf": "ES", "data_nascimento": "10/05/1980", "email": "base.medico@example.com", "senha": "123456",
}, follow_redirects=True)
client.get("/logout")

with app.app_context():
    dono = Usuario.query.filter_by(tipo="dono").first()
    if not dono:
        dono = Usuario(nome="Dono Teste", email="dono.teste@example.com", tipo="dono")
        dono.set_senha("123456")
        db.session.add(dono)
        db.session.commit()
    dono_email = dono.email
    medico_id = Usuario.query.filter_by(email="base.medico@example.com").first().id


def login(email):
    client.get("/logout")
    client.post("/login", data={"identificador": email, "senha": "123456"})


# ---------- 1. Carga inicial ----------
with app.app_context():
    semear_tipos_exame(db, TipoExame)
    criados = semear_base_conhecimento(db)
    checar("Carga inicial cria todos os itens", criados == len(ITENS_PADRAO) and BaseConhecimentoItem.query.count() == len(ITENS_PADRAO))
    item = BaseConhecimentoItem.query.filter_by(pergunta="A colonoscopia dói?").first()
    item.resposta = "Resposta editada pelo dono."
    db.session.commit()
    checar("Semear de novo nao cria nada", semear_base_conhecimento(db) == 0)
    checar("Item editado foi preservado", BaseConhecimentoItem.query.get(item.id).resposta == "Resposta editada pelo dono.")
    checar("Itens da internet entram nao revisados", all(not i.revisado and i.origem == "internet" for i in BaseConhecimentoItem.query.all()))
    colono_id = TipoExame.query.filter_by(nome="Colonoscopia").first().id
    endo_id = TipoExame.query.filter_by(nome="Endoscopia digestiva alta").first().id
    checar("Nenhum texto da carga tem prazo em horas ou dias", not any(
        any(t in (i[1] + i[2]).lower() for t in (" horas", " hora ", " dias", " dia antes")) for i in ITENS_PADRAO
    ))

# ---------- 7. Medico comum ----------
login("base.medico@example.com")
r = client.get("/dono/base-conhecimento")
checar("Medico comum nao acessa a base do dono", r.status_code in (302, 401, 403))
r = client.post("/dono/base-conhecimento/config", data={"base_busca_provedor": "openai"})
checar("Medico comum nao altera a configuracao", r.status_code in (302, 401, 403))

# ---------- 2. Config ----------
login(dono_email)
with app.app_context():
    cfg = PlataformaConfig.obter()
    checar("Base desligada por padrao", cfg.base_conhecimento_ativa is False and cfg.base_busca_provedor == "palavra_chave")
html = client.get("/dono/base-conhecimento").get_data(as_text=True)
checar("Tela do dono abre e lista itens", "Base de conhecimento (terceira IA)" in html and "A colonoscopia dói?" in html)
r = client.post("/dono/base-conhecimento/config", data={"base_busca_provedor": "inexistente", "base_conhecimento_ativa": "on"}, follow_redirects=True)
checar("Provedor invalido e recusado", "Provedor de busca inválido" in r.get_data(as_text=True))
with app.app_context():
    checar("Nada mudou com provedor invalido", PlataformaConfig.obter().base_conhecimento_ativa is False)
client.post("/dono/base-conhecimento/config", data={"base_busca_provedor": "palavra_chave", "base_conhecimento_ativa": "on"}, follow_redirects=True)
with app.app_context():
    checar("Dono ligou a base", bc.base_ativa() is True and bc.provedor_configurado() == "palavra_chave")

# ---------- 3. CRUD ----------
client.post("/dono/base-conhecimento/novo", data={
    "tipo_exame_id": str(endo_id), "pergunta": "Posso tomar café antes da endoscopia?",
    "resposta": "Siga a restrição de líquidos do seu preparo.", "fonte_nome": "Teste",
}, follow_redirects=True)
with app.app_context():
    novo = BaseConhecimentoItem.query.filter_by(pergunta="Posso tomar café antes da endoscopia?").first()
    checar("Dono adicionou item (revisado, origem dono)", novo is not None and novo.revisado and novo.origem == "dono" and novo.autor_nome == "Dono Teste")
    novo_id = novo.id
r = client.post("/dono/base-conhecimento/novo", data={"tipo_exame_id": str(endo_id), "pergunta": "", "resposta": ""}, follow_redirects=True)
checar("Item sem pergunta/resposta e recusado", "obrigatórias" in r.get_data(as_text=True))

client.post(f"/dono/base-conhecimento/{novo_id}/editar", data={
    "tipo_exame_id": str(endo_id), "pergunta": "Posso tomar café antes da endoscopia?",
    "resposta": "Resposta nova.", "fonte_nome": "Teste",
}, follow_redirects=True)
with app.app_context():
    it = BaseConhecimentoItem.query.get(novo_id)
    checar("Edicao mudou a resposta", it.resposta == "Resposta nova.")
    checar("Edicao guardou a versao anterior no historico", len(it.historico) == 1 and it.historico[0].resposta == "Siga a restrição de líquidos do seu preparo.")
client.post(f"/dono/base-conhecimento/{novo_id}/desfazer", follow_redirects=True)
with app.app_context():
    it = BaseConhecimentoItem.query.get(novo_id)
    checar("Desfazer voltou a resposta anterior", it.resposta == "Siga a restrição de líquidos do seu preparo.")
    checar("Desfazer tambem guardou a versao desfeita", len(it.historico) == 2)

# ---------- 4. Busca por palavra-chave ----------
with app.app_context():
    achou = bc.buscar_na_base("posso dirigir depois do exame", tipo_exame_id=colono_id)
    checar("Acha a pergunta parecida", achou and achou[0]["item"].pergunta == "Posso dirigir depois da colonoscopia?")
    checar("Metodo e palavra-chave", achou[0]["metodo"] == "palavra_chave")
    outros = bc.buscar_na_base("posso dirigir depois do exame", tipo_exame_id=endo_id)
    checar("Respeita o tipo de exame", outros and all(x["item"].tipo_exame_id == endo_id for x in outros)
           and all("colonoscopia" not in x["item"].pergunta.lower() for x in outros))
    checar("Pergunta sem relacao nao acha nada", bc.buscar_na_base("qual a cor do gatorade", tipo_exame_id=colono_id) == [])
    checar("Pergunta vazia nao acha nada", bc.buscar_na_base("   ") == [])
client.post(f"/dono/base-conhecimento/{novo_id}/alternar", follow_redirects=True)
with app.app_context():
    checar("Item inativo nunca e usado na busca", all(x["item"].id != novo_id for x in bc.buscar_na_base("posso tomar café antes da endoscopia", tipo_exame_id=endo_id)))
client.post(f"/dono/base-conhecimento/{novo_id}/alternar", follow_redirects=True)
html = client.get("/dono/base-conhecimento?teste=posso+dirigir+depois&teste_tipo=" + str(colono_id)).get_data(as_text=True)
checar("Testar busca mostra o resultado", "Posso dirigir depois da colonoscopia?" in html and "similaridade" in html)

# ---------- 5. Embeddings (simulados) ----------
VETORES = {
    "A colonoscopia dói?": [1.0, 0.0, 0.0],
    "Posso dirigir depois da colonoscopia?": [0.0, 1.0, 0.0],
    "O laxante não fez efeito, o que eu faço?": [0.0, 0.0, 1.0],
    "o remedio para limpar o intestino nao funcionou": [0.05, 0.05, 0.99],
    "pergunta sem parecido": [0.7, 0.7, 0.0],
}
falhar = {"ligado": False}
original = bc.gerar_embedding


def embedding_falso(texto, provedor):
    if falhar["ligado"] or provedor not in ("openai", "gemini"):
        return None
    vetor = VETORES.get(texto)
    return (vetor, "openai:teste") if vetor else ([0.0, 0.0, 0.0], "openai:teste")


bc.gerar_embedding = embedding_falso
try:
    client.post("/dono/base-conhecimento/config", data={"base_busca_provedor": "openai", "base_conhecimento_ativa": "on"}, follow_redirects=True)
    with app.app_context():
        sem_vetor_antes = BaseConhecimentoItem.query.filter(BaseConhecimentoItem.embedding.is_(None)).count()
        checar("Ao trocar para embeddings, itens antigos ainda nao tem vetor", sem_vetor_antes > 0)
    r = client.post("/dono/base-conhecimento/calcular-vetores", follow_redirects=True)
    checar("Calcular vetores responde com resumo", "vetor(es) calculado(s)" in r.get_data(as_text=True))
    with app.app_context():
        checar("Vetores foram gravados", BaseConhecimentoItem.query.filter(BaseConhecimentoItem.embedding.isnot(None)).count() > 0)
        achou = bc.buscar_na_base("o remedio para limpar o intestino nao funcionou", tipo_exame_id=colono_id)
        checar("Embedding acha por SENTIDO (palavras diferentes)", achou and achou[0]["item"].pergunta.startswith("O laxante não fez efeito") and achou[0]["metodo"] == "openai")
        checar("Pergunta sem parecido nao passa do limiar", bc.buscar_na_base("pergunta sem parecido", tipo_exame_id=colono_id) == [])
        # vetor de OUTRO modelo nunca e comparado
        alvo = BaseConhecimentoItem.query.filter_by(pergunta="O laxante não fez efeito, o que eu faço?").first()
        alvo.embedding_modelo = "gemini:outro"
        db.session.commit()
        checar("Vetor de outro modelo e ignorado", bc.buscar_na_base("o remedio para limpar o intestino nao funcionou", tipo_exame_id=colono_id) == [])
    falhar["ligado"] = True
    with app.app_context():
        r_fallback = bc.buscar_na_base("posso dirigir depois do exame", tipo_exame_id=colono_id)
        checar("Se a API falhar, cai para palavra-chave", r_fallback and r_fallback[0]["metodo"] == "palavra_chave")
finally:
    bc.gerar_embedding = original

# ---------- 6. Medico excluido: o item fica ----------
with app.app_context():
    medico = Usuario.query.get(medico_id)
    db.session.add(BaseConhecimentoItem(
        tipo_exame_id=colono_id, pergunta="Pergunta do medico", resposta="Resposta do medico",
        origem="medico", revisado=False, autor_usuario_id=medico_id, autor_nome=medico.nome,
    ))
    db.session.commit()
    excluir_usuario_e_dados(medico)
    db.session.commit()
    sobrou = BaseConhecimentoItem.query.filter_by(pergunta="Pergunta do medico").first()
    checar("Item do medico excluido NAO foi apagado", sobrou is not None)
    checar("Vinculo com o usuario foi solto", sobrou.autor_usuario_id is None)
    checar("Nome do autor ficou guardado como texto", sobrou.autor_nome == "Dr. Base Teste")

print("\nTodos os testes passaram.")
