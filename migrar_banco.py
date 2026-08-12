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

-- Cadastro público (modo "empresa") deixou de criar a primeira filial
-- automaticamente - agora cria só a Empresa, e a pessoa cadastra o
-- primeiro local de atendimento depois, ao entrar no app. Até lá, ela
-- não tem nenhum ClinicaMembro ainda, então precisa de uma âncora direta
-- com a empresa que criou (ver Usuario.empresa_fundadora_id em
-- app/models.py e empresas_do_usuario() em app/clinica_utils.py).
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS empresa_fundadora_id INTEGER REFERENCES empresas(id);

-- O cadastro genérico de exame (medico.exames_novo) preenche medico_id com
-- um valor técnico/provisório só pra passar pela constraint NOT NULL (não
-- existe "médico principal" assumido automaticamente) - esta flag marca se
-- esse valor já foi CONFIRMADO de propósito em "Exames por filial" (ver
-- Exame.medico_confirmado em app/models.py). Registros JÁ EXISTENTES viram
-- TRUE (DEFAULT TRUE aqui) porque foram criados quando o cadastro de exame
-- ainda pedia o médico de verdade no próprio formulário - só os exames
-- criados a partir de agora, pelo cadastro genérico, nascem FALSE (isso é
-- feito explicitamente no código, em exames_novo, não por este default).
ALTER TABLE exames ADD COLUMN IF NOT EXISTS medico_confirmado BOOLEAN NOT NULL DEFAULT TRUE;

-- Cadastrar exame deixou de criar associação: o cadastro genérico vira só
-- um item de CATÁLOGO (associado=FALSE, feito no código em exames_novo) e
-- a associação real nasce na tela "Associar exames". Registros existentes
-- viram TRUE (eram associações de verdade) - ver Exame.associado em
-- app/models.py.
ALTER TABLE exames ADD COLUMN IF NOT EXISTS associado BOOLEAN NOT NULL DEFAULT TRUE;

-- O paciente passou a pertencer à EMPRESA, não a uma filial ("o cliente é
-- só cliente" - ver Paciente.empresa_id em app/models.py): a filial só é
-- escolhida na hora de marcar cada consulta (Agendamento.clinica_id).
-- Passos: cria a coluna nova. Copia a empresa da filial legada para os
-- cadastros existentes. Solta o NOT NULL da filial legada (cadastros novos
-- não a preenchem mais). E remove a unicidade de CPF por-filial - a
-- unicidade nova, por-empresa, é criada mais abaixo (na parte em Python),
-- só se não houver CPF repetido entre filiais da mesma empresa; se houver,
-- fica só a checagem da aplicação (ver medico.pacientes_novo), sem quebrar
-- o deploy. ATENÇÃO: nada de ";" no MEIO de uma frase de comentário aqui
-- dentro - o executor abaixo divide o bloco por ";" (agora ele descarta
-- linhas de comentário antes de executar, mas não custa manter o hábito).
ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS empresa_id INTEGER REFERENCES empresas(id);
UPDATE pacientes SET empresa_id = (SELECT empresa_id FROM clinicas WHERE clinicas.id = pacientes.clinica_id) WHERE empresa_id IS NULL AND clinica_id IS NOT NULL;
ALTER TABLE pacientes ALTER COLUMN clinica_id DROP NOT NULL;
ALTER TABLE pacientes DROP CONSTRAINT IF EXISTS uq_clinica_cpf;

-- O papel "configurador" existiu por pouco tempo e foi removido (só criou
-- confusão). Qualquer conta criada com ele vira "secretaria" (o papel
-- administrativo equivalente) para não ficar trancada fora do sistema.
UPDATE usuarios SET tipo = 'secretaria' WHERE tipo = 'configurador';

-- O link de auto-cadastro de paciente passou a ser da EMPRESA (o paciente
-- é da empresa) e fica no Painel - ver Empresa.codigo_cadastro_paciente em
-- app/models.py e auth.cadastro_paciente. Cada empresa herda o código da
-- primeira filial que já tinha um (assim o link que a clínica já divulgou
-- continua funcionando igual). Os códigos legados por filial continuam
-- válidos como fallback em links antigos.
ALTER TABLE empresas ADD COLUMN IF NOT EXISTS codigo_cadastro_paciente VARCHAR(20);
UPDATE empresas SET codigo_cadastro_paciente = (SELECT c.codigo_cadastro_paciente FROM clinicas c WHERE c.empresa_id = empresas.id AND c.codigo_cadastro_paciente IS NOT NULL ORDER BY c.id LIMIT 1) WHERE codigo_cadastro_paciente IS NULL;

-- Código mestre do médico (identidade portátil dele na plataforma) e os
-- convites de vínculo por código - ver Usuario.codigo_mestre e
-- ConviteVinculo em app/models.py. A coluna nasce vazia e o preenchimento
-- para médicos existentes é feito mais abaixo (na parte em Python) porque
-- a geração do código usa aleatoriedade e checagem de unicidade.
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS codigo_mestre VARCHAR(20);
-- Vínculo equipe x filial deixou de ser apagado na remoção - agora é
-- ENCERRADO (ativo=FALSE + encerrado_em), preservando o histórico de quem
-- atendeu onde - ver ClinicaMembro.encerrado_em em app/models.py.
ALTER TABLE clinica_membros ADD COLUMN IF NOT EXISTS encerrado_em TIMESTAMP;
CREATE TABLE IF NOT EXISTS convites_vinculo (
    id SERIAL PRIMARY KEY,
    clinica_id INTEGER NOT NULL REFERENCES clinicas(id),
    medico_id INTEGER NOT NULL REFERENCES usuarios(id),
    criado_por_id INTEGER REFERENCES usuarios(id),
    status VARCHAR(20) NOT NULL DEFAULT 'pendente',
    criado_em TIMESTAMP,
    decidido_em TIMESTAMP
);
"""

# Trilha de auditoria de acesso ao prontuario (ver LogAcessoProntuario em
# app/models.py e app/auditoria_clinica.py): a tabela em si e criada
# automaticamente pelo db.create_all() na inicializacao da aplicacao, nao
# precisa de ALTER/CREATE aqui.

conn = psycopg.connect(DATABASE_URL, autocommit=True)

# Remove as linhas de comentário ("-- ...") ANTES de dividir por ";".
# Sem isso, um comentário com ";" no meio da frase quebra o split: o
# pedaço depois do ";" perde o prefixo "--" e é executado como se fosse
# SQL, derrubando a migração inteira (e o deploy) com erro de sintaxe -
# foi exatamente o que aconteceu no deploy #137.
sql_sem_comentarios = "\n".join(
    linha for linha in SQL.splitlines() if not linha.strip().startswith("--")
)
for comando in sql_sem_comentarios.strip().split(";"):
    comando = comando.strip()
    if comando:
        conn.execute(comando)


def _remover_constraint_unica(conn, tabela, coluna):
    """Remove qualquer constraint UNIQUE (nome pode variar por ambiente -
    foi criada implicitamente pelo unique=True do SQLAlchemy no primeiro
    db.create_all(), então não dá pra confiar num nome fixo) na coluna
    indicada. Feito em Python (não como texto dentro de SQL, ver comando
    abaixo) porque o "SQL.strip().split(';')" usado para rodar o script
    acima quebraria um bloco DO $$ ... $$ do Postgres em pedaços, já que
    esse bloco também usa ';' internamente entre EXECUTE/END LOOP/END."""
    linhas = conn.execute(
        """
        SELECT tc.constraint_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
        WHERE tc.table_name = %s AND tc.constraint_type = 'UNIQUE' AND kcu.column_name = %s
        """,
        (tabela, coluna),
    ).fetchall()
    for (nome_constraint,) in linhas:
        conn.execute(f'ALTER TABLE "{tabela}" DROP CONSTRAINT "{nome_constraint}"')
        print(f"Constraint única removida: {tabela}.{coluna} ({nome_constraint})")


# Telefone e e-mail de "usuarios" deixaram de ser globalmente únicos: a
# mesma pessoa pode ser paciente em clínicas diferentes com o mesmo
# telefone/e-mail (cada clínica tem sua própria conta Usuario para aquele
# paciente) - ver comentário em app/models.py (classe Usuario) e as rotas
# app/routes_medico.py:pacientes_novo / app/routes_auth.py:cadastro_paciente
# (unicidade agora é garantida por clínica, na aplicação) e
# app/routes_auth.py:login_paciente (que lida com telefone+nascimento
# batendo em mais de uma clínica, deixando o paciente escolher qual).
_remover_constraint_unica(conn, "usuarios", "telefone")
_remover_constraint_unica(conn, "usuarios", "email")

# Substitui a unicidade global de e-mail por uma que só vale pra quem não
# é paciente (dono/secretária/médico continuam com e-mail único, já que é
# a credencial de login deles) - índice único parcial, suportado tanto em
# Postgres quanto em SQLite (db.create_all() cria o mesmo índice em bases
# novas, ver __table_args__ em app/models.py).
conn.execute(
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_usuarios_email_nao_paciente "
    "ON usuarios (email) WHERE tipo <> 'paciente'"
)

# O CPF do paciente agora é único por EMPRESA, não mais por filial (o
# paciente é da empresa - ver Paciente.empresa_id e o bloco de ALTERs de
# "pacientes" no SQL acima). Antes de criar o índice único novo, confere
# se a base migrada não tem a mesma pessoa cadastrada em duas filiais da
# mesma empresa (era possível no modelo antigo): se tiver, o índice não é
# criado (a unicidade fica garantida só pela aplicação, ver
# medico.pacientes_novo) e o deploy segue normalmente - unificar esses
# cadastros duplicados é uma tarefa manual, avisada no log.
duplicados = conn.execute(
    "SELECT empresa_id, cpf, COUNT(*) FROM pacientes "
    "WHERE empresa_id IS NOT NULL GROUP BY empresa_id, cpf HAVING COUNT(*) > 1"
).fetchall()
if duplicados:
    print(
        f"AVISO: {len(duplicados)} CPF(s) aparecem em mais de um cadastro na mesma empresa "
        "(paciente repetido entre filiais, do modelo antigo). O índice único uq_empresa_cpf "
        "NÃO foi criado - unifique esses cadastros e rode a migração de novo."
    )
else:
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_empresa_cpf ON pacientes (empresa_id, cpf)")

# O código de auto-cadastro da empresa é único (em bases novas o
# db.create_all() já cria isso pelo unique=True do modelo - aqui é para as
# bases que só ganharam a coluna via ALTER TABLE acima).
conn.execute(
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_empresas_codigo_cadastro "
    "ON empresas (codigo_cadastro_paciente)"
)

# Código mestre para os médicos que já existiam antes da coluna (ver
# Usuario.codigo_mestre em app/models.py): gera um código único por médico,
# no mesmo formato usado pela aplicação (MED- + 5 caracteres de um alfabeto
# sem ambiguidade 0/O, 1/I/L). Idempotente: só preenche quem está sem.
import secrets as _secrets

_ALFABETO_CODIGO = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
medicos_sem_codigo = conn.execute(
    "SELECT id FROM usuarios WHERE tipo = 'medico' AND codigo_mestre IS NULL"
).fetchall()
for (medico_id,) in medicos_sem_codigo:
    for _ in range(20):
        codigo = "MED-" + "".join(_secrets.choice(_ALFABETO_CODIGO) for _ in range(5))
        existe = conn.execute(
            "SELECT 1 FROM usuarios WHERE codigo_mestre = %s", (codigo,)
        ).fetchone()
        if not existe:
            conn.execute(
                "UPDATE usuarios SET codigo_mestre = %s WHERE id = %s", (codigo, medico_id)
            )
            break
if medicos_sem_codigo:
    print(f"Código mestre gerado para {len(medicos_sem_codigo)} médico(s) existente(s).")

# Unicidade do código mestre (bases novas ganham pelo unique=True do
# modelo - aqui é para as bases que só ganharam a coluna via ALTER acima).
conn.execute(
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_usuarios_codigo_mestre "
    "ON usuarios (codigo_mestre)"
)

print("Migração aplicada com sucesso!")
