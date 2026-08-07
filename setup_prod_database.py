"""Cria o banco de dados "media_prod" dentro da instância RDS dedicada
media-prod-db (diferente do QA, aqui não precisamos criar um usuário
separado - a própria instância já é exclusiva para produção e o usuário
mestre "media_prod_admin" já é o usuário certo para a aplicação usar).

Como rodar:
1. No terminal (cmd/PowerShell), na pasta do projeto (C:\\app\\media\\src):
   set ADMIN_DATABASE_URL=postgresql://media_prod_admin:SENHA@media-prod-db.SEU_HOST.sa-east-1.rds.amazonaws.com:5432/postgres
   (troque SENHA e SEU_HOST pelos valores reais que voce copiou em
   "Visualizar detalhes da conexao" na criacao do media-prod-db - a senha e
   o endpoint. O banco no final da URL deve ser "postgres", o banco
   administrativo padrao, ja que ainda vamos criar o "media_prod".)

2. python setup_prod_database.py

O script cria o banco "media_prod" (se ainda nao existir) e imprime no
final a DATABASE_URL completa - a mesma senha do usuario mestre, so
trocando o nome do banco no final. Copie essa URL para a variavel
DATABASE_URL do ambiente media-prod no Elastic Beanstalk.
"""
import os
import sys

try:
    import psycopg2
except ImportError:
    print("Faltando a biblioteca psycopg2. Rode: pip install psycopg2-binary")
    sys.exit(1)

ADMIN_DATABASE_URL = os.environ.get("ADMIN_DATABASE_URL")
if not ADMIN_DATABASE_URL:
    print("Defina a variável ADMIN_DATABASE_URL antes de rodar este script (veja o topo do arquivo).")
    sys.exit(1)

NOVO_BANCO = "media_prod"

conn = psycopg2.connect(ADMIN_DATABASE_URL)
conn.autocommit = True  # CREATE DATABASE não pode rodar dentro de uma transação
cur = conn.cursor()

cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (NOVO_BANCO,))
if cur.fetchone():
    print(f"Banco {NOVO_BANCO} já existe - não recriando.")
else:
    cur.execute(f'CREATE DATABASE "{NOVO_BANCO}"')
    print(f"Banco {NOVO_BANCO} criado com sucesso.")

cur.close()
conn.close()

# Monta a URL final trocando só o nome do banco (o resto - usuário, senha,
# host, porta - é o mesmo do ADMIN_DATABASE_URL, já que aqui o usuário
# mestre É o usuário certo para a aplicação usar).
usuario_senha_host = ADMIN_DATABASE_URL.rsplit("/", 1)[0]
nova_url = f"{usuario_senha_host}/{NOVO_BANCO}"

print("\n==== Cole isto na variável DATABASE_URL do ambiente media-prod ====")
print(nova_url)
print("=====================================================================")
