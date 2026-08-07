import psycopg

DATABASE_URL = "postgresql://postgres:silQ1A2Z3@database-1.cdyi4mii61i0.sa-east-1.rds.amazonaws.com:5432/media_dev?sslmode=verify-full&sslrootcert=global-bundle.pem"

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