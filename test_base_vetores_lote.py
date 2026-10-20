"""Testa o cálculo dos vetores de busca do FAQ do MedIA em lote (pedido do
Silvan, 2026-10-20): antes era uma chamada de API por item e 40 itens por
clique (~22 cliques para a carga inicial); agora é um clique só, com várias
perguntas por chamada (ver app.base_conhecimento.calcular_vetores_pendentes).

A API de embeddings é simulada (sem rede): conta as chamadas e devolve
vetores falsos."""
import json
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import BaseConhecimentoItem, PlataformaConfig, TipoExame
from app.tipos_exame_padrao import semear_tipos_exame
from app.base_conhecimento_padrao import semear_base_conhecimento
import app.base_conhecimento as bc

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False
client = app.test_client()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


chamadas = []


def lote_falso(textos, provedor):
    chamadas.append(len(textos))
    return [[float(len(t)), 1.0, 0.0] for t in textos], bc.modelo_do_provedor(provedor)


def sem_vetor():
    with app.app_context():
        return bc.itens_sem_vetor_query("openai").count()


with app.app_context():
    semear_tipos_exame(db, TipoExame)
    if not BaseConhecimentoItem.query.first():
        semear_base_conhecimento(db)
    config = PlataformaConfig.obter()
    config.base_busca_provedor = "openai"
    config.base_conhecimento_ativa = True
    BaseConhecimentoItem.query.update({"embedding": None, "embedding_modelo": None})
    db.session.commit()
    total = BaseConhecimentoItem.query.filter_by(status="ativo").count()
checar("Base carregada e sem vetores", total > 200 and sem_vetor() == total)

client.post("/login", data={"identificador": "dono@plataforma.com", "senha": "123456"})

# ---------- 1. Um clique calcula TUDO, em poucas chamadas ----------
with patch.object(bc, "gerar_embeddings_em_lote", side_effect=lote_falso):
    r = client.post("/dono/base-conhecimento/calcular-vetores", follow_redirects=True)
html = r.get_data(as_text=True)
lotes_esperados = -(-total // bc.TAMANHO_LOTE_EMBEDDING)
checar(f"Um clique calculou os {total} vetores", sem_vetor() == 0)
checar(f"Em lotes: {len(chamadas)} chamada(s) de API, não {total}", len(chamadas) == lotes_esperados and max(chamadas) <= bc.TAMANHO_LOTE_EMBEDDING)
checar("Mensagem de resumo sem 'clique de novo'", f"{total} vetor(es) calculado(s)" in html and "clique de novo" not in html)
with app.app_context():
    item = BaseConhecimentoItem.query.filter_by(status="ativo").first()
    checar("Vetor gravado com o modelo do provedor atual",
           item.embedding_modelo == bc.modelo_do_provedor("openai") and len(json.loads(item.embedding)) == 3)
r = client.post("/dono/base-conhecimento/calcular-vetores", follow_redirects=True)
checar("Clicar de novo avisa que já está tudo calculado", "Todos os itens ativos já têm vetor" in r.get_data(as_text=True))

# ---------- 2. Vetor de OUTRO modelo conta como pendente ----------
with app.app_context():
    BaseConhecimentoItem.query.filter(BaseConhecimentoItem.id.in_(
        [i.id for i in BaseConhecimentoItem.query.filter_by(status="ativo").limit(5)]
    )).update({"embedding_modelo": "gemini:outro"}, synchronize_session=False)
    db.session.commit()
checar("Vetores de outro modelo aparecem como pendentes", sem_vetor() == 5)
chamadas.clear()
with app.app_context(), patch.object(bc, "gerar_embeddings_em_lote", side_effect=lote_falso):
    ok, falhas, restantes = bc.calcular_vetores_pendentes("openai")
checar("... e são recalculados", (ok, falhas, restantes) == (5, 0, 0) and sem_vetor() == 0)

# ---------- 3. Limite de tempo: para, avisa o que falta, e o próximo clique continua ----------
with app.app_context():
    BaseConhecimentoItem.query.update({"embedding": None, "embedding_modelo": None})
    db.session.commit()
with app.app_context(), patch.object(bc, "gerar_embeddings_em_lote", side_effect=lote_falso):
    ok, falhas, restantes = bc.calcular_vetores_pendentes("openai", tempo_maximo=0)
checar("Com o tempo esgotado, o 1º lote sempre roda (cada clique avança)", ok == bc.TAMANHO_LOTE_EMBEDDING)
checar("... e informa quantos faltam", restantes == total - bc.TAMANHO_LOTE_EMBEDDING and sem_vetor() == restantes)

# ---------- 4. Se o lote falhar, tenta item por item ----------
with app.app_context(), \
        patch.object(bc, "gerar_embeddings_em_lote", return_value=None), \
        patch.object(bc, "gerar_embedding", return_value=([0.1, 0.2, 0.3], bc.modelo_do_provedor("openai"))):
    pendentes_antes = sem_vetor()
    ok, falhas, restantes = bc.calcular_vetores_pendentes("openai")
checar("Lote falhou: calculou um a um", ok == pendentes_antes and falhas == 0 and sem_vetor() == 0)

# ---------- 5. Sem chave de API: avisa e não insiste ----------
with app.app_context():
    BaseConhecimentoItem.query.update({"embedding": None, "embedding_modelo": None})
    db.session.commit()
with patch.object(bc, "gerar_embeddings_em_lote", return_value=None), patch.object(bc, "gerar_embedding", return_value=None):
    r = client.post("/dono/base-conhecimento/calcular-vetores", follow_redirects=True)
checar("Sem chave/credencial: mensagem de erro clara", "Verifique se a chave de API" in r.get_data(as_text=True))

print("\nTodos os testes do cálculo de vetores em lote passaram.")
