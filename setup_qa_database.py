"""Cria o banco de dados e o usuário exclusivos do ambiente media-qa dentro
da MESMA instância RDS que já hospeda o media-dev (database-1) - decisão
tomada para economizar uma instância RDS, já que a conta AWS tem um limite
de instâncias simultâneas.

Como rodar:
1. No terminal (cmd/PowerShell), na pasta do projeto (C:\\app\\media\\src):
   set ADMIN_DATABASE_URL=postgresql://postgres:SENHA_MESTRE@database-1.SEU_HOST.sa-east-1.rds.amazonaws.com:5432/postgres
   (troque SENHA_MESTRE e SEU_HOST pelos valores reais do DATABASE_URL que
   você já viu na tela de Configuration > Software do ambiente media-dev -
   é a mesma string, só que terminando em "/postgres" em vez do nome do
   banco do dev, porque aqui conectamos no banco de administração padrão
   para poder criar um banco novo.)

2. python setup_qa_database.py

O script gera uma senha aleatoria nova para o usuario media_qa_admin (nunca
reaproveita a senha mestre), cria o banco "media_qa" e imprime no final a
DATABASE_URL completa que você deve colar nas variáveis de ambiente do
media-qa no Elastic Beanstalk. Essa senha só aparece na SUA tela - não é
enviada para lugar nenhum.
"""
import os
import secrets
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

NOVO_BANCO = "media_qa"
NOVO_USUARIO = "media_qa_admin"
NOVA_SENHA = secrets.token_urlsafe(24)

conn = psycopg2.connect(ADMIN_DATABASE_URL)
conn.autocommit = True  # CREATE DATABASE não pode rodar dentro de uma transação
cur = conn.cursor()

# Evita erro se já existir de uma tentativa anterior.
cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (NOVO_USUARIO,))
if cur.fetchone():
    print(f"Usuário {NOVO_USUARIO} já existe - trocando a senha dele.")
    cur.execute(f'ALTER ROLE "{NOVO_USUARIO}" WITH LOGIN PASSWORD %s', (NOVA_SENHA,))
else:
    cur.execute(f'CREATE ROLE "{NOVO_USUARIO}" WITH LOGIN PASSWORD %s', (NOVA_SENHA,))
    print(f"Usuário {NOVO_USUARIO} criado.")

# No RDS, o usuário mestre (ex.: "postgres") não é um superusuário de
# verdade - para criar um banco com outro usuário como OWNER, ele precisa
# primeiro receber a membership desse papel (senão dá
# "psycopg2.errors.InsufficientPrivilege: must be able to SET ROLE").
cur.execute(f'GRANT "{NOVO_USUARIO}" TO CURRENT_USER')

cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (NOVO_BANCO,))
if cur.fetchone():
    print(f"Banco {NOVO_BANCO} já existe - não recriando.")
else:
    cur.execute(f'CREATE DATABASE "{NOVO_BANCO}" OWNER "{NOVO_USUARIO}"')
    print(f"Banco {NOVO_BANCO} criado, de propriedade de {NOVO_USUARIO}.")

# Remove a membership temporária concedida acima - não precisamos mais dela
# depois que o banco já foi criado com o owner certo.
cur.execute(f'REVOKE "{NOVO_USUARIO}" FROM CURRENT_USER')

cur.close()
conn.close()

# Extrai host:porta da ADMIN_DATABASE_URL original para montar a URL final.
host_porta = ADMIN_DATABASE_URL.split("@", 1)[1].split("/", 1)[0]
nova_url = f"postgresql://{NOVO_USUARIO}:{NOVA_SENHA}@{host_porta}/{NOVO_BANCO}"

print("\n==== Cole isto na variável DATABASE_URL do ambiente media-qa ====")
print(nova_url)
print("===================================================================")
