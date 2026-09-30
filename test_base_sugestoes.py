"""Testa a fatia 5 da "terceira IA" (2026-09-29): especialidade do medico,
tela "Base compartilhada" filtrada por especialidade, sugestoes de alteracao/
item novo e aprovacao/rejeicao pelo dono.

Sem rede. Banco recriado, sem seed:
    DATABASE_URL=sqlite:///teste_base_sugestoes.db python test_base_sugestoes.py
"""
from app import create_app
from app.extensions import db
from app.db_utils import resetar_banco
from app.models import Usuario, TipoExame, BaseConhecimentoItem, BaseConhecimentoSugestao, BaseConhecimentoHistorico, Notificacao
from app.tipos_exame_padrao import semear_tipos_exame
from app.base_conhecimento_padrao import semear_base_conhecimento
from app.exclusao_usuario import excluir_usuario_e_dados

app = create_app()
client = app.test_client()

# Campos que o cadastro exige hoje (telefone, endereco e confirmacao de senha).
EXTRA_CADASTRO = {
    "senha_confirmacao": "123456", "telefone": "(27) 99999-1234", "cep": "29010-000", "rua": "Rua A",
    "numero": "1", "bairro": "Centro", "cidade": "Vitoria", "uf": "ES",
}

with app.app_context():
    resetar_banco(db)
    db.session.commit()


def checar(nome, condicao):
    print(f"[{'OK' if condicao else 'FALHOU'}] {nome}")
    assert condicao, nome


def login(email):
    client.get("/logout")
    client.post("/login", data={"identificador": email, "senha": "123456"})


# ---------- 1. Cadastro com especialidade ----------
client.post("/cadastro", data={**EXTRA_CADASTRO,
    "nome": "Dr. Gastro", "papel": "medico", "cpf": "111.222.333-97", "crm_numero": "77777", "crm_uf": "ES",
    "data_nascimento": "10/05/1980", "email": "gastro@example.com", "senha": "123456",
    "especialidade": "Gastroenterologia",
}, follow_redirects=True)
client.get("/logout")
client.post("/cadastro", data={**EXTRA_CADASTRO,
    "nome": "Dr. Sem Esp", "papel": "medico", "cpf": "529.982.247-25", "crm_numero": "66666", "crm_uf": "ES",
    "data_nascimento": "10/05/1981", "email": "semesp@example.com", "senha": "123456",
    "especialidade": "Inventada",
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
    g = Usuario.query.filter_by(email="gastro@example.com").first()
    s = Usuario.query.filter_by(email="semesp@example.com").first()
    checar("Especialidade valida gravada", g.especialidade == "Gastroenterologia")
    checar("Especialidade fora da lista ignorada", s.especialidade is None)
    g_id = g.id
    semear_tipos_exame(db, TipoExame)
    semear_base_conhecimento(db)
    colono = TipoExame.query.filter_by(nome="Colonoscopia").first()
    mamo = TipoExame.query.filter(TipoExame.especialidades.ilike("%Mastologia%")).first()
    colono_id, mamo_id = colono.id, mamo.id
    item_colono = BaseConhecimentoItem.query.filter_by(tipo_exame_id=colono_id).first()
    item_colono_id, texto_original = item_colono.id, item_colono.resposta

# ---------- 2. Tela do medico ----------
login("gastro@example.com")
r = client.get("/equipe/base-compartilhada")
checar("Gastro acessa a tela", r.status_code == 200)
checar("Ve itens de colonoscopia", item_colono.pergunta.encode() in r.data or b"colonoscopia" in r.data.lower())
login("semesp@example.com")
r = client.get("/equipe/base-compartilhada")
checar("Sem especialidade: aviso para preencher", b"Meus dados" in r.data)
r = client.post("/equipe/base-compartilhada/sugerir", data={"tipo_exame_id": colono_id, "pergunta": "x", "resposta": "y"})
with app.app_context():
    checar("Sem especialidade nao consegue sugerir", BaseConhecimentoSugestao.query.count() == 0)

# ---------- 3. Sugestoes ----------
login("gastro@example.com")
client.post("/equipe/base-compartilhada/sugerir", data={
    "item_id": item_colono_id, "pergunta": "Pergunta ajustada?", "resposta": "Resposta ajustada pelo medico.", "motivo": "Mais clara",
})
client.post("/equipe/base-compartilhada/sugerir", data={
    "item_id": item_colono_id, "pergunta": "Pergunta ajustada?", "resposta": "Resposta ajustada de novo.",
})
client.post("/equipe/base-compartilhada/sugerir", data={"tipo_exame_id": colono_id, "pergunta": "Item novo?", "resposta": "Resposta nova."})
client.post("/equipe/base-compartilhada/sugerir", data={"tipo_exame_id": mamo_id, "pergunta": "Fora da especialidade?", "resposta": "x"})
with app.app_context():
    sugs = BaseConhecimentoSugestao.query.filter_by(status="pendente").all()
    checar("Reenviar substitui a pendente do mesmo item (2 pendentes)", len(sugs) == 2)
    checar("Base nao muda antes da aprovacao", BaseConhecimentoItem.query.get(item_colono_id).resposta == texto_original)
    checar("Tipo de outra especialidade barrado", not any(x.pergunta == "Fora da especialidade?" for x in sugs))
    alt_id = next(x.id for x in sugs if x.item_id)
    novo_id = next(x.id for x in sugs if not x.item_id)

# ---------- 4. Dono ----------
login("gastro@example.com")
r = client.get("/dono/base-conhecimento/sugestoes")
checar("Medico nao acessa a fila do dono", r.status_code in (302, 401, 403))
login(dono_email)
r = client.get("/dono/base-conhecimento/sugestoes")
checar("Dono ve a fila", r.status_code == 200 and b"Resposta ajustada de novo." in r.data)
client.post(f"/dono/base-conhecimento/sugestoes/{alt_id}/aprovar", data={})
client.post(f"/dono/base-conhecimento/sugestoes/{novo_id}/rejeitar", data={"resposta_dono": "Ja coberto"})
with app.app_context():
    item = BaseConhecimentoItem.query.get(item_colono_id)
    checar("Aprovada: texto aplicado e revisado", item.resposta == "Resposta ajustada de novo." and item.revisado)
    checar("Aprovada: versao anterior no historico", BaseConhecimentoHistorico.query.filter_by(item_id=item.id).count() >= 1)
    checar("Rejeitada nao cria item", not BaseConhecimentoItem.query.filter_by(pergunta="Item novo?").first())
    checar("Medico foi notificado das 2 decisoes", Notificacao.query.filter_by(usuario_id=g_id, tipo="sugestao_base").count() == 2)
client.post(f"/dono/base-conhecimento/sugestoes/{alt_id}/aprovar", data={})
with app.app_context():
    checar("Nao reaprova decidida", BaseConhecimentoHistorico.query.filter_by(item_id=item_colono_id).count() == 1)

# ---------- 5. Item novo aprovado + medico excluido ----------
login("gastro@example.com")
client.post("/equipe/base-compartilhada/sugerir", data={"tipo_exame_id": colono_id, "pergunta": "Item novo 2?", "resposta": "Resposta nova 2."})
with app.app_context():
    nid = BaseConhecimentoSugestao.query.filter_by(pergunta="Item novo 2?").first().id
login(dono_email)
client.post(f"/dono/base-conhecimento/sugestoes/{nid}/aprovar", data={})
with app.app_context():
    novo = BaseConhecimentoItem.query.filter_by(pergunta="Item novo 2?").first()
    checar("Item novo aprovado entra ativo, revisado e com autor", novo and novo.revisado and novo.autor_usuario_id == g_id and novo.origem == "medico")
    excluir_usuario_e_dados(Usuario.query.get(g_id))
    db.session.commit()
    novo = BaseConhecimentoItem.query.filter_by(pergunta="Item novo 2?").first()
    checar("Medico excluido: item e sugestoes continuam", novo is not None and novo.autor_nome == "Dr. Gastro"
           and BaseConhecimentoSugestao.query.filter_by(autor_nome="Dr. Gastro").count() >= 3)

print("\nTodos os testes passaram.")
