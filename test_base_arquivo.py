"""Carga inicial do FAQ do MedIA vai para uma tabela à parte (pedido do
Silvan, 2026-10-21): as respostas baixadas da internet divergiam muito das
IAs. Ficam guardadas em base_conhecimento_arquivo, sem uso; o FAQ do MedIA
fica só com o que os médicos (e o dono) respondem."""
from app import create_app, db
from app.base_conhecimento_padrao import arquivar_carga_inicial, semear_base_conhecimento
from app.models import (
    BaseConhecimentoArquivo, BaseConhecimentoHistorico, BaseConhecimentoItem, Paciente, PerguntaPendente, TipoExame,
)
from app.tipos_exame_padrao import semear_tipos_exame

app = create_app()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


with app.app_context():
    semear_tipos_exame(db, TipoExame)
    carga = semear_base_conhecimento(db)
    colono = TipoExame.query.filter_by(nome="Colonoscopia").first()
    do_medico = BaseConhecimentoItem(tipo_exame_id=colono.id, pergunta="Que horas chegar?", resposta="Uma hora antes.",
                                     origem="medico", autor_nome="Dr. X")
    db.session.add(do_medico)
    db.session.flush()
    internet = BaseConhecimentoItem.query.filter_by(origem="internet").first()
    internet.embedding, internet.embedding_modelo = "[0.1, 0.2]", "openai:teste:v2"
    db.session.add(BaseConhecimentoHistorico(item_id=internet.id, pergunta="p", resposta="r", alterado_por_nome="dono"))
    pac = Paciente.query.first()
    db.session.add(PerguntaPendente(paciente_id=pac.id, pergunta="x", status="pendente", base_item_id=internet.id))
    db.session.commit()
    internet_id, pergunta_internet = internet.id, internet.pergunta

    movidos = arquivar_carga_inicial(db)
    checar("Move toda a carga inicial", movidos == carga > 0)
    checar("O FAQ do MedIA fica só com o que não veio da internet",
           BaseConhecimentoItem.query.filter_by(origem="internet").count() == 0
           and BaseConhecimentoItem.query.filter_by(pergunta="Que horas chegar?").count() == 1)
    arquivo = BaseConhecimentoArquivo.query.filter_by(item_id_original=internet_id).first()
    checar("O item vai para o arquivo com texto, fonte e vetor",
           arquivo is not None and arquivo.pergunta == pergunta_internet and arquivo.embedding == "[0.1, 0.2]"
           and BaseConhecimentoArquivo.query.count() == movidos)
    checar("Referências soltas: a pergunta da fila perde o vínculo e o histórico sai",
           PerguntaPendente.query.filter_by(pergunta="x").first().base_item_id is None
           and BaseConhecimentoHistorico.query.filter_by(item_id=internet_id).count() == 0)
    checar("Rodar de novo não faz nada", arquivar_carga_inicial(db) == 0 and BaseConhecimentoArquivo.query.count() == movidos)

print("\nTodos os testes do arquivo da carga inicial passaram.")
