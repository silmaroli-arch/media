-- Migração para o PostgreSQL de produção (RDS) referente à implementação
-- das 11 novas funcionalidades. As NOVAS TABELAS já são criadas
-- automaticamente pelo `db.create_all()` no próximo deploy/restart da
-- aplicação — mas as NOVAS COLUNAS em tabelas existentes precisam ser
-- aplicadas manualmente (ver README, seção sobre migrações).
--
-- Rode este script uma única vez contra o banco de produção, de
-- preferência logo antes (ou logo depois) de publicar o novo código.

-- 1) Duração, preço e acompanhante do exame
ALTER TABLE exames ADD COLUMN IF NOT EXISTS duracao_minutos INTEGER;
ALTER TABLE exames ADD COLUMN IF NOT EXISTS preco NUMERIC(10, 2);
ALTER TABLE exames ADD COLUMN IF NOT EXISTS precisa_acompanhante BOOLEAN NOT NULL DEFAULT FALSE;

-- 2) Agendamento: acompanhante, atendimento (observações/encerramento)
ALTER TABLE agendamentos ADD COLUMN IF NOT EXISTS acompanhante_nome VARCHAR(150);
ALTER TABLE agendamentos ADD COLUMN IF NOT EXISTS acompanhante_telefone VARCHAR(30);
ALTER TABLE agendamentos ADD COLUMN IF NOT EXISTS notas_atendimento TEXT;
ALTER TABLE agendamentos ADD COLUMN IF NOT EXISTS encerrado_em TIMESTAMP;
-- (o status "solicitado" usa a mesma coluna VARCHAR(20) que já existia — nada a alterar aqui)

-- 3) Endereço e contato de emergência do paciente
ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS cep VARCHAR(10);
ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS rua VARCHAR(200);
ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS numero VARCHAR(20);
ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS complemento VARCHAR(100);
ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS bairro VARCHAR(100);
ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS cidade VARCHAR(100);
ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS uf VARCHAR(2);
ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS contato_emergencia_nome VARCHAR(150);
ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS contato_emergencia_telefone VARCHAR(30);

-- 4) Novas tabelas (criadas automaticamente pelo db.create_all(), listadas
--    aqui só de referência — não precisa rodar manualmente se o deploy já
--    vai reiniciar a aplicação):
--    - medico_horarios      (horário de atendimento por médico/filial/dia)
--    - chat_mensagens       (histórico de perguntas do paciente no app)
--    - resultados_exame     (PDF de resultado anexado pela equipe)
--    - descontos_config     (percentuais de desconto cadastrados)
--    - pagamentos           (pagamento registrado por agendamento)
