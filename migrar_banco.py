"""Aplica as migrações de schema pendentes (ALTER TABLE ... ADD COLUMN IF
NOT EXISTS) no banco - necessário porque db.create_all() só cria tabelas que
ainda não existem, nunca adiciona colunas novas a uma tabela já existente.

Roda automaticamente em TODO deploy (ver
.platform/hooks/predeploy/01_migrar_banco.sh), usando a mesma DATABASE_URL
que o app já usa naquele ambiente - por isso NÃO tem mais a senha do banco
escrita aqui no código.

Para rodar manualmente na sua máquina (ex.: testar uma migração nova antes
de commitar, ou aplicar direto num ambiente sem esperar o próximo deploy):
1. Crie um arquivo ".env" na raiz do projeto (ele já está no .gitignore,
   então nunca vai parar no Git) com uma linha:
       DATABASE_URL=postgresql://postgres:SENHA@HOST:5432/NOME_DO_BANCO?sslmode=verify-full&sslrootcert=global-bundle.pem
   (troque SENHA/HOST/NOME_DO_BANCO pelo ambiente que quiser migrar -
   media_dev, media_qa ou media_prod).
2. Rode: python migrar_banco.py
"""
import os
import sys

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

SQL = """
ALTER TABLE exames ADD COLUMN IF NOT EXISTS duracao_minutos INTEGER;
ALTER TABLE exames ADD COLUMN IF NOT EXISTS preco NUMERIC(10, 2);
ALTER TABLE exames ADD COLUMN IF NOT EXISTS precisa_acompanhante BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE agendamentos ADD COLUMN IF NOT EXISTS acompanhante_nome VARCHAR(150);
ALTER TABLE agendamentos ADD COLUMN IF NOT EXISTS acompanhante_telefone VARCHAR(30);
ALTER TABLE agendamentos ADD COLUMN IF NOT EXISTS notas_atendimento TEXT;
ALTER TABLE agendamentos ADD COLUMN IF NOT EXISTS encerrado_em TIMESTAMP;

ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS cep VARCHAR(10);
ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS rua VARCHAR(200);
ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS numero VARCHAR(20);
ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS complemento VARCHAR(100);
ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS bairro VARCHAR(100);
ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS cidade VARCHAR(100);
ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS uf VARCHAR(2);
ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS contato_emergencia_nome VARCHAR(150);
ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS contato_emergencia_telefone VARCHAR(30);

-- Rascunho de resposta da IA aguardando aprovação do médico (ver
-- PerguntaPendente.resposta_sugerida_ia em app/models.py).
ALTER TABLE perguntas_pendentes ADD COLUMN IF NOT EXISTS resposta_sugerida_ia TEXT;
"""

conn = psycopg.connect(DATABASE_URL, autocommit=True)
for comando in SQL.strip().split(";"):
    comando = comando.strip()
    if comando:
        conn.execute(comando)
print("Migração aplicada com sucesso!")
