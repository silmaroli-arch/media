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
-- "empresas" e "clinicas" ganharam vários campos (dados fiscais, endereço,
-- cobrança) depois de já estarem em uso em produção - sem estes ALTER
-- TABLE, salvar uma empresa/clínica nova falha com "column does not
-- exist", porque o INSERT gerado pelo SQLAlchemy inclui todas as colunas
-- do modelo atual.
ALTER TABLE empresas ADD COLUMN IF NOT EXISTS cnpj VARCHAR(20);
ALTER TABLE empresas ADD COLUMN IF NOT EXISTS email_contato VARCHAR(150);
ALTER TABLE empresas ADD COLUMN IF NOT EXISTS telefone VARCHAR(30);
ALTER TABLE empresas ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'trial';
ALTER TABLE empresas ADD COLUMN IF NOT EXISTS data_vencimento DATE;
ALTER TABLE empresas ADD COLUMN IF NOT EXISTS observacoes_pagamento TEXT;
ALTER TABLE empresas ADD COLUMN IF NOT EXISTS valor_por_medico NUMERIC(10, 2);

ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS razao_social VARCHAR(200);
ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS cnpj VARCHAR(20);
ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS email_contato VARCHAR(150);
ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS telefone VARCHAR(30);
ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS logo_url VARCHAR(300);
ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS cep VARCHAR(10);
ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS rua VARCHAR(200);
ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS numero VARCHAR(20);
ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS complemento VARCHAR(100);
ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS bairro VARCHAR(100);
ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS cidade VARCHAR(100);
ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS uf VARCHAR(2);
ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS inscricao_estadual VARCHAR(30);
ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS regime_tributario VARCHAR(50);
ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS cnae VARCHAR(20);
ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS codigo_ibge_municipio VARCHAR(10);

-- "usuarios" ganhou as permissões administrativas por pessoa (perm_*)
-- depois de já ter contas cadastradas.
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS perm_pacientes BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS perm_equipe BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS perm_filiais BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS perm_dados_clinica BOOLEAN NOT NULL DEFAULT FALSE;

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

-- Emissão fiscal de NFS-e (nota fiscal de serviço eletrônica — padrão
-- NFS-e Nacional / ADN), na tela "Dados da clínica". Senha do certificado
-- e token do provedor ficam gravados só criptografados (ver
-- app/cripto_fiscal.py) — nunca em texto puro.
ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS fiscal_ambiente VARCHAR(20) NOT NULL DEFAULT 'homologacao';
ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS fiscal_modo_simulacao BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS fiscal_simular_falha_conexao BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS fiscal_certificado_pfx BYTEA;
ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS fiscal_certificado_senha_cripto BYTEA;
ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS fiscal_certificado_cnpj VARCHAR(20);
ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS fiscal_certificado_validade DATE;
ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS fiscal_provedor_emissao VARCHAR(50) NOT NULL DEFAULT 'nenhum';
ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS fiscal_provedor_token_cripto BYTEA;

-- Campos específicos de NFS-e (substituem os antigos campos de NFC-e —
-- série/número de nota e CSC não se aplicam a serviço, e o app ainda não
-- estava em uso em produção, então as colunas antigas são removidas).
ALTER TABLE clinicas DROP COLUMN IF EXISTS fiscal_nfce_serie;
ALTER TABLE clinicas DROP COLUMN IF EXISTS fiscal_nfce_proximo_numero;
ALTER TABLE clinicas DROP COLUMN IF EXISTS fiscal_csc_id_token;
ALTER TABLE clinicas DROP COLUMN IF EXISTS fiscal_csc_codigo_cripto;
ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS fiscal_inscricao_municipal VARCHAR(30);
ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS fiscal_codigo_servico VARCHAR(20);
ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS fiscal_aliquota_iss NUMERIC(5,2);
ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS fiscal_rps_serie VARCHAR(10);
ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS fiscal_rps_proximo_numero INTEGER;

-- Guarda a resposta "crua" de cada IA (Claude e ChatGPT) separada do
-- rascunho final, pra tela de aprovação mostrar as duas lado a lado além
-- da junção (ver app.ia_preparo.responder_com_ia e medico/perguntas.html).
ALTER TABLE perguntas_pendentes ADD COLUMN IF NOT EXISTS resposta_bruta_claude TEXT;
ALTER TABLE perguntas_pendentes ADD COLUMN IF NOT EXISTS resposta_bruta_chatgpt TEXT;

-- Vinculo entre pergunta do chat e o agendamento/consulta especifico
-- (ver ChatMensagem.agendamento_id em app/models.py) - permite ao medico
-- ver exatamente quais perguntas pertencem a qual consulta no historico
-- de atendimentos.
ALTER TABLE chat_mensagens ADD COLUMN IF NOT EXISTS agendamento_id INTEGER REFERENCES agendamentos(id);

-- Emissao de NFS-e por pagamento (ver app/nfse_nacional.py).
ALTER TABLE pagamentos ADD COLUMN IF NOT EXISTS nfse_status VARCHAR(30) DEFAULT 'nao_emitida';
ALTER TABLE pagamentos ADD COLUMN IF NOT EXISTS nfse_numero_dps INTEGER;
ALTER TABLE pagamentos ADD COLUMN IF NOT EXISTS nfse_numero VARCHAR(30);
ALTER TABLE pagamentos ADD COLUMN IF NOT EXISTS nfse_codigo_verificacao VARCHAR(60);
ALTER TABLE pagamentos ADD COLUMN IF NOT EXISTS nfse_xml_assinado TEXT;
ALTER TABLE pagamentos ADD COLUMN IF NOT EXISTS nfse_erro TEXT;
ALTER TABLE pagamentos ADD COLUMN IF NOT EXISTS nfse_emitida_em TIMESTAMP;

-- Auto-cadastro do paciente pelo app (link publico por clinica) e
-- aprovacao pela equipe antes de poder agendar (ver auth.cadastro_paciente
-- e medico.pacientes_solicitacoes).
ALTER TABLE clinicas ADD COLUMN IF NOT EXISTS codigo_cadastro_paciente VARCHAR(20);
CREATE UNIQUE INDEX IF NOT EXISTS uq_clinicas_codigo_cadastro_paciente ON clinicas (codigo_cadastro_paciente) WHERE codigo_cadastro_paciente IS NOT NULL;
ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS status_cadastro VARCHAR(20) NOT NULL DEFAULT 'aprovado';

-- Prontuario eletronico "sem papel" (NGS2/NGS3, Resolucao CFM 1.821/2007):
-- certificado digital pessoal do medico (ver app/assinatura_clinica.py e
-- medico.certificado_digital) para assinar as evolucoes clinicas.
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS certificado_digital_pfx BYTEA;
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS certificado_digital_senha_cripto BYTEA;
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS certificado_digital_titular VARCHAR(200);
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS certificado_digital_validade DATE;

-- Evolucao clinica passa a ser criptografada em repouso (ver
-- app/cripto_clinico.py e EvolucaoClinica.texto em app/models.py) e ganha
-- campos de assinatura digital por evolucao.
--
-- ATENCAO - RISCO DE PERDA DE DADOS: o "DROP COLUMN IF EXISTS texto" abaixo
-- apaga permanentemente a coluna antiga em texto plano. Se alguma evolucao
-- clinica real ja foi registrada em algum ambiente (dev/qa/prod) ANTES
-- desta migracao rodar la, o conteudo dela sera perdido (nao e possivel
-- recuperar o texto original a partir da coluna nova, que so passa a ser
-- preenchida a partir de agora). Rode este migrar_banco.py o quanto antes,
-- antes que evolucoes reais se acumulem em texto plano nesses ambientes.
-- IF EXISTS aqui (alem do IF NOT EXISTS/IF EXISTS de cada coluna) porque
-- evolucoes_clinicas e uma tabela NOVA: no primeiro deploy que a introduz,
-- ela ainda nao existe quando este script roda (o predeploy hook roda
-- ANTES da aplicacao subir e criar a tabela via db.create_all()) - sem o
-- IF EXISTS aqui, esse primeiro deploy quebra com "relation does not
-- exist" e o deploy inteiro falha.
ALTER TABLE IF EXISTS evolucoes_clinicas ADD COLUMN IF NOT EXISTS texto_cripto BYTEA;
ALTER TABLE IF EXISTS evolucoes_clinicas DROP COLUMN IF EXISTS texto;
ALTER TABLE IF EXISTS evolucoes_clinicas ADD COLUMN IF NOT EXISTS assinatura_base64 TEXT;
ALTER TABLE IF EXISTS evolucoes_clinicas ADD COLUMN IF NOT EXISTS assinatura_certificado_titular VARCHAR(200);
ALTER TABLE IF EXISTS evolucoes_clinicas ADD COLUMN IF NOT EXISTS assinatura_certificado_serial VARCHAR(80);
ALTER TABLE IF EXISTS evolucoes_clinicas ADD COLUMN IF NOT EXISTS assinatura_certificado_pem TEXT;
ALTER TABLE IF EXISTS evolucoes_clinicas ADD COLUMN IF NOT EXISTS assinatura_hash_sha256 VARCHAR(64);
ALTER TABLE IF EXISTS evolucoes_clinicas ADD COLUMN IF NOT EXISTS assinado_em TIMESTAMP;
"""

# Trilha de auditoria de acesso ao prontuario (ver LogAcessoProntuario em
# app/models.py e app/auditoria_clinica.py): a tabela em si e criada
# automaticamente pelo db.create_all() na inicializacao da aplicacao, nao
# precisa de ALTER/CREATE aqui.

conn = psycopg.connect(DATABASE_URL, autocommit=True)
for comando in SQL.strip().split(";"):
    comando = comando.strip()
    if comando:
        conn.execute(comando)
print("Migração aplicada com sucesso!")
