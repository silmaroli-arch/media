"""Apaga TUDO no banco (inclusive as tabelas órfãs que sobraram de modelos
já removidos do código - empresas, clinicas, clinica_membros,
convite_vinculos, evolucoes_clinicas, procedimento_gastro,
procedimento_polipos, logs_acesso_prontuario, medico_horarios,
medico_bloqueios etc. - este projeto não usa Flask-Migrate, então nenhuma
delas nunca foi removida automaticamente quando a classe saiu do
app/models.py) e recria o schema do zero, só com as tabelas que o
app/models.py de HOJE realmente usa. O banco fica VAZIO no final -
não roda seed.py sozinho.

Usa DROP SCHEMA ... CASCADE em vez de tentar apagar tabela por tabela
(db.drop_all() só conhece as tabelas do models.py ATUAL - nunca apagaria
as tabelas órfãs, que é exatamente o que este script existe pra resolver).

ATENÇÃO - ISSO NÃO TEM VOLTA. Apaga TODOS os dados do banco apontado por
DATABASE_URL, sem exceção. Só rode isso contra um banco de
desenvolvimento/teste (media-dev) - nunca contra media-prod (nem
media-qa, se algum dia tiver dado de verdade) sem um plano de migração
de dados à parte.

Para rodar:
1. Confirme que o arquivo ".env" na raiz do projeto aponta pro banco
   CERTO (DATABASE_URL=... do media-dev) - o script imprime host e nome
   do banco antes de fazer qualquer coisa, exatamente para você conferir
   antes de confirmar.
2. Rode: python recriar_banco_do_zero.py
3. Digite "APAGAR" quando for pedido para confirmar (proteção contra
   rodar sem querer no banco errado).
4. Depois de rodar, se quiser os dados de demonstração de sempre, rode
   separadamente: python seed.py
"""
import os
import sys
from urllib.parse import urlparse

from dotenv import load_dotenv
import psycopg

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print(
        "DATABASE_URL não está definida. Crie um arquivo .env na raiz do "
        "projeto com DATABASE_URL=... (veja as instruções no topo deste "
        "arquivo) ou exporte a variável antes de rodar."
    )
    sys.exit(1)

info = urlparse(DATABASE_URL)
nome_banco = (info.path or "").lstrip("/")

print(f"Banco alvo: host={info.hostname}  porta={info.port}  banco={nome_banco}")
print()
print("Isso vai APAGAR TODAS AS TABELAS deste banco (inclusive tabelas")
print("órfãs de modelos antigos) e recriar do zero, VAZIO - sem os dados")
print("de demonstração. Essa ação NÃO TEM VOLTA.")
print()
resposta = input('Digite "APAGAR" (sem aspas) para confirmar: ').strip()
if resposta != "APAGAR":
    print("Cancelado - nada foi feito.")
    sys.exit(1)

print("\nConectando...")
conn = psycopg.connect(DATABASE_URL, autocommit=True)
with conn.cursor() as cur:
    print("Apagando o schema 'public' inteiro (todas as tabelas, sequências, etc.)...")
    cur.execute("DROP SCHEMA public CASCADE")
    print("Recriando o schema 'public' vazio...")
    cur.execute("CREATE SCHEMA public")
conn.close()

print("Criando as tabelas a partir de app/models.py (db.create_all())...")
from app import create_app, db  # import tardio - só depois de já ter apagado o schema

app = create_app()
with app.app_context():
    db.create_all()

print("\nConcluído. Banco recriado do zero, só com as tabelas atuais, vazio.")
print('Se quiser os dados de demonstração, rode agora: python seed.py')
