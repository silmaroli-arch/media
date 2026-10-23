"""Apagar todos os dados (Painel do dono > zona de risco): tabelas criadas
depois desta limpeza (contador de perguntas/dia, pedidos de exclusão,
"Fale com a gente", notificações) travavam o Postgres por chave
estrangeira - 500 em /dono/limpar-dados (relatado pelo Silvan,
2026-10-23). Aqui as chaves estrangeiras são ligadas também no SQLite,
para pegar esse tipo de erro."""
from datetime import date

from sqlalchemy import event

from app import create_app, db
from app.limpar_dados import apagar_todos_os_dados
from app.models import (
    BaseConhecimentoArquivo, BaseConhecimentoItem, BaseConhecimentoSugestao, ContagemPerguntasDia, Exame, MensagemSuporte, Notificacao, Paciente,
    SolicitacaoExclusao, TipoExame, Usuario,
)

app = create_app()


def checar(nome, condicao):
    status = "OK" if condicao else "FALHOU"
    print(f"[{status}] {nome}")
    assert condicao, nome


with app.app_context():
    event.listen(db.engine, "connect", lambda conexao, _: conexao.execute("PRAGMA foreign_keys=ON"))
    db.session.remove()
    db.engine.dispose()

    medico = Usuario.query.filter_by(tipo="medico").first()
    dono = Usuario.query.filter_by(tipo="dono").first()
    paciente, exame = Paciente.query.first(), Exame.query.first()
    tipo = TipoExame.query.first() or TipoExame(nome="Tipo teste")
    db.session.add(tipo)
    db.session.flush()
    db.session.add_all([
        Notificacao(usuario_id=medico.id, titulo="Aviso", mensagem="Texto"),
        Notificacao(usuario_id=dono.id, titulo="Do dono", mensagem="Fica"),
        MensagemSuporte(usuario_id=medico.id, mensagem="Oi"),
        SolicitacaoExclusao(usuario_id=medico.id, nome=medico.nome, motivo="Teste"),
        ContagemPerguntasDia(paciente_id=paciente.id, exame_id=exame.id, data=date.today(), quantidade=1),
        BaseConhecimentoItem(tipo_exame_id=tipo.id, pergunta="P?", resposta="R.", origem="medico",
                             autor_usuario_id=medico.id, autor_nome=medico.nome),
        BaseConhecimentoItem(tipo_exame_id=tipo.id, pergunta="Do dono?", resposta="Fica.", origem="dono"),
        BaseConhecimentoArquivo(item_id_original=999, tipo_exame_id=tipo.id, pergunta="Arquivada?", resposta="Fica."),
    ])
    db.session.flush()
    db.session.add(BaseConhecimentoSugestao(item_id=BaseConhecimentoItem.query.filter_by(pergunta="P?").first().id,
                                            tipo_exame_id=tipo.id, pergunta="P?", resposta="Outra.",
                                            autor_usuario_id=medico.id, autor_nome=medico.nome))
    db.session.commit()

    apagar_todos_os_dados(dono)
    db.session.commit()
    checar("Apaga sem erro de chave estrangeira e só o dono fica",
           [u.tipo for u in Usuario.query.all()] == ["dono"])
    checar("Contador, pedidos de exclusão e 'Fale com a gente' saem",
           ContagemPerguntasDia.query.count() == 0 and SolicitacaoExclusao.query.count() == 0
           and MensagemSuporte.query.count() == 0)
    checar("Notificações do dono ficam", Notificacao.query.count() == 1)
    checar("FAQ do MedIA: saem os itens de médicos e as sugestões (2026-10-23)",
           BaseConhecimentoItem.query.filter_by(origem="medico").count() == 0 and BaseConhecimentoSugestao.query.count() == 0)
    checar("... e ficam os do dono e o arquivo da carga inicial",
           BaseConhecimentoItem.query.filter_by(pergunta="Do dono?").count() == 1
           and BaseConhecimentoArquivo.query.filter_by(pergunta="Arquivada?").count() == 1)

print("\nTodos os testes de apagar todos os dados passaram.")
