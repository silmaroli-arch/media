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
-- "usuarios" ganhou as permissões administrativas por pessoa (perm_*)
-- depois de já ter contas cadastradas.
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS perm_pacientes BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS perm_equipe BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS perm_filiais BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS perm_dados_clinica BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE exames ADD COLUMN IF NOT EXISTS duracao_minutos INTEGER;
ALTER TABLE exames ADD COLUMN IF NOT EXISTS precisa_acompanhante BOOLEAN NOT NULL DEFAULT FALSE;
-- exames.preco saiu do modelo (não há mais controle financeiro no sistema) -
-- a coluna fica órfã em bancos já existentes, mesmo tratamento das remoções
-- anteriores (sem DROP COLUMN, sem Flask-Migrate neste projeto).

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

-- Auto-cadastro do paciente pelo app (link publico) e aprovacao pela
-- equipe antes de poder agendar (ver auth.cadastro_paciente e
-- medico.pacientes_solicitacoes). O código de auto-cadastro em si mudou
-- de "clinicas"/"empresas" (removidas na Fatia 5) para "grupos" - ver
-- bloco da Fatia 5 mais abaixo (grupos.codigo_cadastro_paciente).
ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS status_cadastro VARCHAR(20) NOT NULL DEFAULT 'aprovado';

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

-- Paciente.empresa_id/clinica_id: campos legados/de exibição, mantidos
-- como coluna solta (sem FK) desde a Fatia 5 - "empresas"/"clinicas" não
-- existem mais como tabela, e a associação real de paciente com grupo de
-- trabalho é por GrupoPaciente (ver app/models.py). Aqui só garante que a
-- coluna existe em bases antigas que a criaram antes desta mudança.
ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS empresa_id INTEGER;
ALTER TABLE pacientes ALTER COLUMN clinica_id DROP NOT NULL;
ALTER TABLE pacientes DROP CONSTRAINT IF EXISTS uq_clinica_cpf;

-- O papel "configurador" existiu por pouco tempo e foi removido (só criou
-- confusão). Qualquer conta criada com ele vira "secretaria" (o papel
-- administrativo equivalente) para não ficar trancada fora do sistema.
UPDATE usuarios SET tipo = 'secretaria' WHERE tipo = 'configurador';

-- DONO do conteúdo clínico: exame e modelo de preparo registram quem os
-- criou (criado_por_id). Se o criador é um MÉDICO, só ele edita - e só
-- ele pode ser associado ao exame. Registros antigos ficam NULL (sem
-- dono) e seguem o comportamento antigo - ver pode_ser_editado_por em
-- app/models.py.
ALTER TABLE exames ADD COLUMN IF NOT EXISTS criado_por_id INTEGER REFERENCES usuarios(id);
ALTER TABLE preparo_modelos ADD COLUMN IF NOT EXISTS criado_por_id INTEGER REFERENCES usuarios(id);

-- Código mestre do médico (identidade portátil dele na plataforma) -
-- mecanismo de convite por código foi removido na Fatia 5 (convite hoje é
-- por CPF, via GrupoConvite), mas a coluna em si (Usuario.codigo_mestre)
-- continua existindo no modelo - mantida aqui só por compatibilidade de
-- schema em bases antigas.
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS codigo_mestre VARCHAR(20);

-- CPF e endereço PESSOAL de quem trabalha na plataforma, e CRM (só
-- médico) - ver Usuario.cpf/cep/.../crm_numero/crm_uf em app/models.py.
-- Coletados no cadastro (auth.cadastro) e no cadastro/edição de membros
-- da equipe (medico.equipe_novo/equipe_editar).
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS cpf VARCHAR(20);
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS cep VARCHAR(10);
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS rua VARCHAR(200);
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS numero VARCHAR(20);
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS complemento VARCHAR(100);
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS bairro VARCHAR(100);
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS cidade VARCHAR(100);
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS uf VARCHAR(2);
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS crm_numero VARCHAR(20);
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS crm_uf VARCHAR(2);

-- Fatia 4 da migração para Grupo: Exame/PreparoModelo/Agendamento/
-- PerguntaPendente/FaqItem passam a ser escopados por grupo_id em vez de
-- clinica_id (ver comentário em cada classe, app/models.py). clinica_id
-- vira campo legado/de exibição (nunca mais usado para busca/filtro). O
-- pareamento Clinica<->Grupo era feito por migrar_grupo_por_clinica.py,
-- que cumpriu seu papel e foi removido junto com a classe Clinica (Fatia 5).
ALTER TABLE exames ALTER COLUMN clinica_id DROP NOT NULL;
ALTER TABLE exames ADD COLUMN IF NOT EXISTS grupo_id INTEGER REFERENCES grupos(id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_grupo_exame_nome ON exames (grupo_id, nome);

ALTER TABLE preparo_modelos ALTER COLUMN clinica_id DROP NOT NULL;
ALTER TABLE preparo_modelos ADD COLUMN IF NOT EXISTS grupo_id INTEGER REFERENCES grupos(id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_grupo_preparo_modelo_nome ON preparo_modelos (grupo_id, nome);

ALTER TABLE agendamentos ALTER COLUMN clinica_id DROP NOT NULL;
ALTER TABLE agendamentos ADD COLUMN IF NOT EXISTS grupo_id INTEGER REFERENCES grupos(id);

ALTER TABLE perguntas_pendentes ALTER COLUMN clinica_id DROP NOT NULL;
ALTER TABLE perguntas_pendentes ADD COLUMN IF NOT EXISTS grupo_id INTEGER REFERENCES grupos(id);

ALTER TABLE faq_itens ALTER COLUMN clinica_id DROP NOT NULL;
ALTER TABLE faq_itens ADD COLUMN IF NOT EXISTS grupo_id INTEGER REFERENCES grupos(id);

-- Fatia 5 da migração para Grupo: cobrança/endereço/fiscal deixam de ser
-- de Empresa/Clinica e passam a ser POR GRUPO (cada Grupo já é uma unidade
-- autônoma desde a Fatia 4 - 1 Grupo por Clinica/filial). Só o schema;
-- a cópia dos dados de Empresa/Clinica existentes para o Grupo pareado é
-- feita à parte, manualmente, por migrar_empresa_para_grupo.py.
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS razao_social VARCHAR(200);
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS cnpj VARCHAR(20);
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS email_contato VARCHAR(150);
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS telefone VARCHAR(30);
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS logo_url VARCHAR(300);
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS cep VARCHAR(10);
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS rua VARCHAR(200);
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS numero VARCHAR(20);
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS complemento VARCHAR(100);
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS bairro VARCHAR(100);
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS cidade VARCHAR(100);
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS uf VARCHAR(2);
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'trial';
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS data_vencimento DATE;
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS observacoes_pagamento TEXT;
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS valor_por_medico NUMERIC(10, 2);
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS codigo_cadastro_paciente VARCHAR(20);
CREATE UNIQUE INDEX IF NOT EXISTS uq_grupos_codigo_cadastro_paciente ON grupos (codigo_cadastro_paciente);
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS inscricao_estadual VARCHAR(30);
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS regime_tributario VARCHAR(50);
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS cnae VARCHAR(20);
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS codigo_ibge_municipio VARCHAR(10);
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS fiscal_ambiente VARCHAR(20) NOT NULL DEFAULT 'homologacao';
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS fiscal_modo_simulacao BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS fiscal_simular_falha_conexao BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS fiscal_certificado_pfx BYTEA;
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS fiscal_certificado_senha_cripto BYTEA;
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS fiscal_certificado_cnpj VARCHAR(20);
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS fiscal_certificado_validade DATE;
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS fiscal_provedor_emissao VARCHAR(50) NOT NULL DEFAULT 'nenhum';
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS fiscal_provedor_token_cripto BYTEA;
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS fiscal_inscricao_municipal VARCHAR(30);
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS fiscal_codigo_servico VARCHAR(20);
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS fiscal_aliquota_iss NUMERIC(5, 2);
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS fiscal_rps_serie VARCHAR(10);
ALTER TABLE grupos ADD COLUMN IF NOT EXISTS fiscal_rps_proximo_numero INTEGER;

-- Fatia 6: uma conta pode existir e ser plenamente usável sem NUNCA ter um
-- Grupo (conta solo) - Exame/PreparoModelo já tinham criado_por_id (dono
-- pessoal do conteúdo clínico, de uma feature anterior); Agendamento/
-- PerguntaPendente/FaqItem ganham o mesmo campo agora, e Paciente ganha
-- cadastrado_por_id (não existe dono individual nenhum até aqui - só
-- GrupoPaciente, que exige Grupo). Usado só no fallback de escopo pessoal
-- quando a pessoa não tem nenhum Grupo - ver
-- clinica_utils.filtro_escopo_atual() e migrar_dados_pessoais_para_grupo().
ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS cadastrado_por_id INTEGER REFERENCES usuarios(id);
ALTER TABLE agendamentos ADD COLUMN IF NOT EXISTS criado_por_id INTEGER REFERENCES usuarios(id);
ALTER TABLE perguntas_pendentes ADD COLUMN IF NOT EXISTS criado_por_id INTEGER REFERENCES usuarios(id);
ALTER TABLE faq_itens ADD COLUMN IF NOT EXISTS criado_por_id INTEGER REFERENCES usuarios(id);

-- Fatia 7 (WhatsApp): de onde veio cada pergunta do histórico de chat -
-- "web" é o padrão (cobre todo o histórico existente), "whatsapp" identifica
-- as recebidas pelo número único da aplicação. A tabela nova
-- "conversas_whatsapp" não precisa de ALTER TABLE - é criada automaticamente
-- pelo db.create_all() por não existir ainda em nenhum ambiente.
ALTER TABLE chat_mensagens ADD COLUMN IF NOT EXISTS canal VARCHAR(20) NOT NULL DEFAULT 'web';
"""

conn = psycopg.connect(DATABASE_URL, autocommit=True)

# Este script só faz sentido contra um banco que já tem o schema de uma
# versão ANTERIOR da aplicação (é isso que "migrar" quer dizer aqui) - ele
# assume que tabelas centrais como "usuarios" já existem, e só ajusta o
# que mudou desde então (ALTER TABLE ADD/DROP COLUMN, backfill, etc.).
# Num ambiente genuinamente NOVO (banco vazio, primeiro deploy de sempre -
# ex.: um staging recém-criado), não existe schema antigo nenhum pra
# migrar: db.create_all() (que roda depois deste hook, na inicialização da
# aplicação) já cria TODAS as tabelas certinho a partir do models.py atual
# - rodar as instruções abaixo contra um banco vazio só quebraria o deploy
# com "relation does not exist" na primeira tabela referenciada. Detecta
# esse caso checando se "usuarios" (a tabela mais antiga/central) já
# existe, e simplesmente não faz nada se não existir.
tabela_usuarios_existe = conn.execute("SELECT to_regclass('public.usuarios')").fetchone()[0]
if tabela_usuarios_existe is None:
    print(
        "Banco vazio (nenhuma tabela 'usuarios' ainda) - nada para migrar. "
        "O schema completo será criado do zero por db.create_all() na "
        "inicialização da aplicação."
    )
    sys.exit(0)

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

# Fatia 5: o paciente deixou de ser "da empresa" e virou uma identidade
# GLOBAL única por CPF (ver Paciente em app/models.py - empresa_id/
# clinica_id agora são só campos legados/de exibição; a associação real
# com cada clínica é por GrupoPaciente, ver app/models.py e
# migrar_paciente_para_grupo.py). O índice antigo por-empresa
# (uq_empresa_cpf) não impedia duplicidade nenhuma pra cadastros globais
# (empresa_id NULL não colide consigo mesmo num índice único), então sai
# de cena - substituído pelo índice único por CPF sozinho.
# uq_empresa_cpf é uma constraint (não um índice solto) - o Postgres recusa
# "DROP INDEX" no índice que a sustenta enquanto a constraint existir
# (psycopg.errors.DependentObjectsStillExist, com a dica "DROP CONSTRAINT").
# _remover_constraint_unica já resolve isso corretamente: ela busca qualquer
# UNIQUE constraint que toque a coluna "cpf" (inclusive uq_empresa_cpf, que é
# composta por (empresa_id, cpf)) e remove via ALTER TABLE ... DROP
# CONSTRAINT, que também derruba o índice interno junto.
_remover_constraint_unica(conn, "pacientes", "cpf")

# Mesmo padrão de cautela do índice antigo: só cria a unicidade nova se a
# base já não tiver o mesmo CPF em cadastros (Paciente) diferentes - o que
# é esperado em toda base que ainda não rodou migrar_paciente_para_grupo.py
# (o script que deduplica por CPF, reaponta agendamentos/perguntas/chat pro
# cadastro sobrevivente e cria os GrupoPaciente que faltarem). Enquanto
# isso não roda, a checagem de unicidade fica só na aplicação (ver
# medico.pacientes_novo/pacientes_importar) e o deploy segue normalmente.
duplicados_cpf = conn.execute(
    "SELECT cpf, COUNT(*) FROM pacientes GROUP BY cpf HAVING COUNT(*) > 1"
).fetchall()
if duplicados_cpf:
    print(
        f"AVISO: {len(duplicados_cpf)} CPF(s) aparecem em mais de um cadastro de paciente "
        "(modelo antigo, por empresa). O índice único uq_pacientes_cpf NÃO foi criado - rode "
        "migrar_paciente_para_grupo.py para unificar esses cadastros e rode a migração de novo."
    )
else:
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_pacientes_cpf ON pacientes (cpf)")

# O código de auto-cadastro (hoje por Grupo, ver bloco da Fatia 5 mais
# acima) já ganha seu índice único ali mesmo (uq_grupos_codigo_cadastro_
# paciente) - o antigo índice sobre "empresas" foi removido junto com essa
# tabela (Fatia 5).

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

# CONTA ÚNICA do paciente: a mesma conta (Usuario) passa a poder ter um
# cadastro (Paciente) por empresa - a unicidade antiga de
# pacientes.usuario_id precisa cair (o nome da constraint varia por
# ambiente, então é removida pelo helper que consulta o catálogo).
_remover_constraint_unica(conn, "pacientes", "usuario_id")
conn.execute("DROP INDEX IF EXISTS pacientes_usuario_id_key")

# CONTA ÚNICA do paciente (ver encontrar_conta_paciente em app/models.py):
# antes, a mesma pessoa que frequentava duas empresas tinha DUAS contas
# (Usuario) de paciente - uma por empresa. Unifica: agrupa as contas de
# paciente ativas por (telefone, data de nascimento) - o mesmo par usado
# no login - e, quando há mais de uma, mantém a conta mais antiga como a
# canônica, reapontando os cadastros (Paciente.usuario_id) das demais
# para ela e desativando as contas duplicadas (ativo=FALSE, preservadas
# como histórico - registros antigos que apontem para elas continuam
# válidos). Os dados clínicos não se misturam: cada Paciente segue na
# sua empresa - muda só a conta de LOGIN, que passa a ver os cadastros
# todos. Idempotente: depois de unificar, o grupo deixa de existir.
grupos_duplicados = conn.execute(
    """
    SELECT u.telefone, p.data_nascimento, array_agg(DISTINCT u.id ORDER BY u.id) AS ids
    FROM usuarios u
    JOIN pacientes p ON p.usuario_id = u.id
    WHERE u.tipo = 'paciente' AND u.ativo AND u.telefone IS NOT NULL
    GROUP BY u.telefone, p.data_nascimento
    HAVING COUNT(DISTINCT u.id) > 1
    """
).fetchall()
for _telefone, _nascimento, ids in grupos_duplicados:
    canonico, duplicados = ids[0], ids[1:]
    conn.execute(
        "UPDATE pacientes SET usuario_id = %s WHERE usuario_id = ANY(%s)",
        (canonico, duplicados),
    )
    conn.execute("UPDATE usuarios SET ativo = FALSE WHERE id = ANY(%s)", (duplicados,))
if grupos_duplicados:
    print(f"Contas de paciente unificadas: {len(grupos_duplicados)} pessoa(s) tinham conta duplicada.")

# Garantia da conta do DONO da plataforma: versões antigas do
# "/dev/limpar-base" apagavam a conta do dono junto com o resto - se a
# base ficou sem nenhum dono, ninguém consegue mais entrar no painel da
# plataforma (o dono não é recriado pelo cadastro público). Recria a
# conta padrão (dono@plataforma.com / 123456). Idempotente: só age se não
# existir NENHUM usuário tipo 'dono'.
tem_dono = conn.execute("SELECT 1 FROM usuarios WHERE tipo = 'dono' LIMIT 1").fetchone()
if not tem_dono:
    from werkzeug.security import generate_password_hash as _gerar_hash_senha

    conn.execute(
        "INSERT INTO usuarios (nome, email, senha_hash, tipo, ativo, "
        "perm_pacientes, perm_equipe, perm_filiais, perm_dados_clinica) "
        "VALUES ('Dono da Plataforma', 'dono@plataforma.com', %s, 'dono', TRUE, "
        "FALSE, FALSE, FALSE, FALSE)",
        (_gerar_hash_senha("123456"),),
    )
    print("Conta do dono recriada (dono@plataforma.com / 123456) - a base estava sem nenhum dono.")

print("Migração aplicada com sucesso!")
