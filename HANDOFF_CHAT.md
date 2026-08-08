# Handoff — Continuação do chat com Claude sobre o projeto Media/MedIA

> Gerado em 2026-08-08. Cole este documento como primeira mensagem em uma nova sessão do Claude (Cowork) para retomar o trabalho de onde parou, incluindo o contexto e as pendências abaixo.

## Contexto do projeto

- **Media / MedIA**: SaaS de saúde (Flask + PostgreSQL) para clínicas/médicos, com assistente de IA para o paciente tirar dúvidas sobre preparo de exames.
- Deploy em AWS Elastic Beanstalk: `media-dev`, `media-qa`, `media-prod`. Branches `dev` → `qualidade` → `main` disparam deploy via GitHub Actions (`einaregilsson/beanstalk-deploy@v22`).
- Sem framework de migração: alterações de schema são feitas manualmente em `migrar_banco.py` (comandos `ALTER TABLE ... ADD/DROP COLUMN IF [NOT] EXISTS`, idempotentes), executados automaticamente a cada deploy via `.platform/hooks/predeploy/01_migrar_banco.sh`.
  - **Cuidado**: o parser desse script quebra o SQL por `;` de forma simples (`SQL.strip().split(";")`). Comentários no arquivo NÃO podem conter `;` no meio do texto, ou o deploy quebra com `psycopg.errors.SyntaxError`. Sempre simular esse split antes de commitar mudanças nesse arquivo.
- Após cada ajuste concluído, é escrito um resumo em português em `ultima_mudanca.txt` (raiz do repo) — consumido por um script externo (`auto_commit_push.bat`, roda na máquina Windows do usuário) e pela geração de mensagens de commit do `.github/workflows/deploy.yml`.
- O sistema **ainda não está em uso em produção** — o usuário já autorizou fazer alterações estruturais (ex.: renomear/remover campos fiscais antigos) sem se preocupar em manter compatibilidade com dados reais de produção.

## O que foi feito nesta sessão (mais recente primeiro)

1. **Nota fiscal — migração de NFC-e para NFS-e** (nota de serviço, não de produto):
   - `app/models.py`: removidos campos antigos de NFC-e em `Clinica` (`fiscal_nfce_serie`, `fiscal_nfce_proximo_numero`, `fiscal_csc_id_token`, `fiscal_csc_codigo_cripto`); adicionados campos de NFS-e (`fiscal_inscricao_municipal`, `fiscal_codigo_servico`, `fiscal_aliquota_iss`, `fiscal_rps_serie`, `fiscal_rps_proximo_numero`).
   - `Pagamento`: adicionados `nfse_status`, `nfse_numero_dps`, `nfse_numero`, `nfse_codigo_verificacao`, `nfse_xml_assinado`, `nfse_erro`, `nfse_emitida_em`.
   - Novo módulo `app/nfse_nacional.py`: monta o XML da DPS (Declaração de Prestação de Serviço), assina digitalmente (XML-DSig real, via `signxml`, testado de ponta a ponta com certificado de teste) e tenta enviar para o ADN (Ambiente de Dados Nacional). Se falhar o envio, mantém o XML assinado salvo com status `assinada_pendente_envio` — nunca finge sucesso.
   - `requirements.txt`: adicionados `lxml`, `signxml`, `requests`.
   - Telas atualizadas: `clinica_configuracoes.html` (campos de NFS-e) e `pagamento_comprovante.html` (card de emissão de nota).
   - **Pendente de decisão do usuário**: seguir aprimorando a integração direta com o ADN (mais trabalho, mais controle) ou usar um provedor terceiro (Focus NFe, NFe.io, Tecnospeed — mais rápido de deixar realmente funcional, pois eles já têm homologação com os municípios). Expliquei as diferenças mas o usuário não decidiu ainda.
   - **Falta**: testar a emissão real contra o ADN, o que exige um certificado e-CNPJ válido — fora do alcance do ambiente de sandbox.

2. **Reorganização do painel do médico** (várias rodadas, guiadas por prints de tela):
   - "Próximos agendamentos" movido para depois do calendário no Painel.
   - Na "Lista completa" do Painel, restou só o botão "Pagamento" (removidos dropdown de status e botões Atendimento/Resultado).
   - Esses controles removidos foram para a tela "Meus exames agendados" (menu Médico), que agora só mostra exames com `status = confirmado` (sem filtro de data) e não tem mais a aba "Agendamentos anteriores (últimos 30)".
   - Novo item de menu **Financeiro → Receber pagamento**: lista agendamentos com `status = realizado` e sem pagamento registrado.
   - Calendário "Agenda de exames" (painel do médico) e "Próximos exames" (painel do paciente) agora só mostram `status` em `solicitado`, `agendado` ou `confirmado`; `cancelado`/`realizado` vão para o Histórico do paciente independente da data.
   - Seletor de exame na tela de dúvidas do paciente (`/paciente/chat`) agora só lista agendamentos com `status` em `agendado` ou `confirmado`.

3. **Tela de Atendimento (médico)**:
   - "Observações da consulta" agora ocupa a largura total.
   - "Histórico de atendimentos anteriores deste paciente" virou uma lista de expand panels (accordion aninhado) — **um painel por atendimento anterior**, e ao abrir cada um aparecem só as perguntas/dúvidas feitas para aquela consulta específica.
   - Foi criado um vínculo real no banco: `ChatMensagem.agendamento_id` (FK para `agendamentos.id`), substituindo a aproximação anterior por data/exame. O formulário de dúvidas do paciente agora envia o `agendamento_id` exato. Mantida compatibilidade com o `test_smoke.py`, que ainda envia só `exame_id`.

## Pendências abertas (não decididas / não implementadas)

- **Status "Agendado"**: o usuário começou a dizer "Vamos retirar o status 'Agendado'. Ao confirmar a consulta já passa para 'Confirmado'" mas a mensagem foi interrompida e reformulada como um pedido menor (filtro de dropdown). A remoção do status "Agendado" como conceito **não foi implementada** — precisa ser confirmada e escopada separadamente se ainda for do interesse do usuário.
- **NFS-e — caminho de emissão**: decidir entre integração direta com o ADN ou provedor terceiro (ver item 1 acima).
- **Validação real da emissão de NFS-e**: precisa de certificado e-CNPJ real e ambiente de homologação/produção do ADN.

## Pendências mais antigas (de sessões anteriores, ainda não retomadas)

- Cadastrar anticoagulantes reais (Lixiana, Xarelto, Eliquis, Marevan, Pradaxa) com os protocolos de suspensão de dias da clínica — precisa de dados do usuário.
- Configurar a variável de ambiente `CHAVE_CRIPTOGRAFIA_FISCAL` em `media-dev`, `media-qa` e `media-prod`.
- Rotacionar a senha master do RDS `database-1`.
- Confirmar que `python test_smoke.py` passa.
- Testar acesso externo ao `media-prod` sem VPN.
- Limpar 3 Elastic IPs não associados na AWS.
- Obter do usuário a pasta "arquivos recebidos"/planilha do modelo de preparo.

## Como continuar

Ao colar este documento em uma nova sessão/conta, a nova conversa não terá acesso automático ao histórico desta sessão nem aos arquivos já abertos aqui — mas com este resumo é possível retomar o trabalho no mesmo ponto. Recomenda-se garantir que a nova sessão tenha acesso ao mesmo repositório Git (`github.com/silmaroli-arch/media`, branch `dev`) e, se for usar a ponte com o computador, à mesma pasta local do projeto.
