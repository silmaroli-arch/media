"""Utilitário usado só pelos scripts de linha de comando (seed.py,
init_db.py) para resetar o banco do zero.

`db.drop_all()` só apaga as tabelas que existem no metadata atual (ou
seja, que ainda têm um model Python correspondente). Se o schema do banco
mudar (uma tabela for removida/renomeada no código, como aconteceu com
`preparos_exame` ao virar `preparo_modelos`), a tabela antiga fica
esquecida no banco — e no Postgres isso pode travar o `drop_all()` das
tabelas atuais, porque a tabela esquecida ainda tem uma constraint de
chave estrangeira apontando para elas (erro
`DependentObjectsStillExist`).

Para não depender de manter as duas listas de tabelas em sincronia, o
reset dá um jeito mais direto: no Postgres, apaga e recria o schema
inteiro (`DROP SCHEMA ... CASCADE`); no SQLite, o `drop_all()` já resolve
sem esse problema (SQLite não tem esse tipo de trava entre tabelas
esquecidas)."""
from sqlalchemy import text


def resetar_banco(db):
    if db.engine.dialect.name == "postgresql":
        with db.engine.begin() as conexao:
            conexao.execute(text("DROP SCHEMA public CASCADE"))
            conexao.execute(text("CREATE SCHEMA public"))
    else:
        db.drop_all()
    db.create_all()
