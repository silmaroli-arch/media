# Handoff — Continuação do chat com Claude sobre o projeto Media/MedIA

> Atualizado em 2026-08-18. Cole este documento como primeira mensagem em uma nova sessão do Claude (Cowork) para retomar o trabalho de onde parou, incluindo o contexto e as pendências abaixo.

## Contexto do projeto

- **Media / MedIA**: SaaS de saúde (Flask + PostgreSQL) para clínicas/médicos, com assistente de IA para o paciente tirar dúvidas sobre preparo de exames, agora também via WhatsApp.
- Repositório: `https://github.com/silmaroli-arch/media.git`, branch de trabalho `dev`.
- Deploy em AWS Elastic Beanstalk: `media-dev`, `media-qa`, `media-prod` (região `sa-east-1`). Push em `dev` dispara deploy automático no `media-dev` via GitHub Actions (`einaregilsson/beanstalk-deploy@v22`).
- O computador do Silvan tem um processo de auto-commit que sincroniza a pasta local `C:\app\media\src` com o GitHub — arquivos entregues nessa pasta acabam indo para produção sozinhos.
- Sem framework de migração: alterações de schema são feitas manualmente em `migrar_banco.py` (comandos `ALTER TABLE ... ADD/DROP COLUMN IF [NOT] EXISTS`, idempotentes), executados automaticamente a cada deploy via `.platform/hooks/predeploy/01_migrar_banco.sh`.
  - **Cuidado**: o parser desse script quebra o SQL por `;` de forma simples. Comentários no arquivo NÃO podem conter `;` no meio do texto, ou o deploy quebra com `psycopg.errors.SyntaxError`.
- Testes: arquivos `test_*.py` na raiz do repo, executados diretamente com `python test_arquivo.py` (não é pytest) contra um banco Postgres local recriado do zero + `seed.py` (popula dados de demonstração).
- **Nunca** compartilhar o conteúdo do `.env` (chaves Twilio/OpenAI/Anthropic, string de conexão do banco) fora dos canais seguros da empresa.

## Migração de arquitetura (Fatias 1–6) — concluída em sua maior parte

O sistema passou por uma reformulação profunda: o conceito antigo de "Empresa/Clínica" (tenancy legado) foi **removido por completo** do código. A estrutura "Grupo" (grupo de trabalho compartilhado) é hoje a única entidade organizadora — cobrança, permissões, pacientes, exames, agenda, tudo passa por `Grupo`/`GrupoMembro`/`GrupoConvite`/`GrupoPaciente`.

- **Fatias 1–5**: concluídas. Removidos Financeiro, Prontuário, Agenda com status/confirmação; `Exame`/`PreparoModelo`/`Agendamento`/`PerguntaPendente`/`FaqItem` migrados para `grupo_id`; `Empresa`/`Clinica`/`ClinicaMembro`/`ConviteVinculo` removidos de `app/models.py`. Suíte de testes inteira (42 arquivos) reescrita e passando.
- **Fatia 6 (em andamento)**: desacoplar a criação de conta de usuário da criação automática de um Grupo — hoje todo cadastro solo já nasce com um Grupo "pessoal" invisível nos bastidores; a ideia é isso só passar a existir quando a pessoa de fato convidar alguém.
- **Fatia 7 (WhatsApp)**: frente mais recente e ativa — ver detalhe abaixo.
- Débito técnico conhecido da Fatia 5: a antiga tela de editar membro da equipe (nome/CPF/endereço/CRM) não tem equivalente ainda no fluxo novo baseado em Grupo.

## Fatia 7 — Área de WhatsApp (em andamento)

Objetivo: paciente se identifica e conversa pelo WhatsApp (via Twilio) para tirar dúvidas sobre preparo de exame; a equipe responde pelo `/equipe/perguntas` o que a IA não resolveu, e a resposta pode voltar automaticamente pelo WhatsApp.

### O que já está pronto e funcionando em `media-dev`

1. **Schema e webhook**: `ConversaWhatsapp` (estado da conversa por telefone, expira em 4h), `ChatMensagem.canal`, validação de assinatura Twilio no webhook (`app/routes_whatsapp.py`).
2. **Identificação do paciente em duas mensagens**: primeiro CPF (aceita com ou sem máscara, valida só formato — não dígito verificador, pois é busca de cadastro já existente), depois data de nascimento.
3. **Menu de opções dinâmico** (`app/whatsapp_conversa.py`, função `_menu_opcoes`):
   - "1) Ver informações do preparo"
   - "2) Fazer uma pergunta"
   - "3) Trocar de exame" — só aparece se o paciente tiver mais de um exame ativo.
   - **Enquanto o paciente tem uma `PerguntaPendente` sem resposta** (status `pendente` ou `aguardando_aprovacao`), o menu inteiro fica escondido — qualquer mensagem recebe só o aviso "Sua pergunta ainda está sendo respondida pela equipe..." (`_tem_pergunta_pendente`). O menu volta a aparecer normalmente assim que a pergunta é respondida.
4. **Fazer uma pergunta pelo WhatsApp** reaproveita o mesmo motor de IA/FAQ da área web do paciente (`_responder_pergunta`): tenta IA primeiro (fica pendente de aprovação da equipe), depois FAQ cadastrada, depois respostas prontas de alimento/medicamento, por último cria `PerguntaPendente` para resposta manual.
5. **Resposta automática de volta pelo WhatsApp** (`app/whatsapp_envio.py` + `app/routes_medico.py:perguntas_responder`): quando a equipe responde uma pergunta que veio do WhatsApp, tenta mandar a resposta de volta automaticamente pelo mesmo número via API de envio da Twilio.
   - Usa um **Content Template** da Twilio (`TWILIO_CONTENT_SID_RESPOSTA`) porque o WhatsApp exige template aprovado pela Meta para mensagens iniciadas pela empresa fora da janela de 24h da última mensagem do paciente. Texto livre (`body`) só funciona dentro dessa janela — mantido como fallback.
   - **O texto exibido ao paciente quando o template está configurado vem do próprio template aprovado na Twilio, não do parâmetro `body` do código.** O template atual (`resposta_whatsapp`) só tem `{{1}}`=pergunta e `{{2}}`=resposta — **não inclui o menu**; adicionar o menu exigiria um novo template com 3 variáveis e nova rodada de aprovação da Meta (decisão do Silvan: adiado por ora). O paciente volta a ver o menu normalmente na próxima mensagem que mandar depois da resposta.

### Infraestrutura configurada

- HTTPS habilitado no ALB do `media-dev` — certificado ACM para `dev.media.med.br`.
- DNS migrado do Registro.br para Cloudflare (Registro.br não permite CNAMEs customizados e redirect automático ao mesmo tempo).
- Webhook do Twilio Sandbox apontando para `https://dev.media.med.br/whatsapp/webhook`.
- Variáveis de ambiente no `media-dev`: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM` (`whatsapp:+14155238886`, número do Sandbox), `TWILIO_CONTENT_SID_RESPOSTA` (`HXbba11dc2527b1f4eae732b03c199bd10`).

### Pendência ativa (bloqueador atual)

O template `resposta_whatsapp` (SID acima) foi criado no Twilio Console e enviado para aprovação da Meta, mas ainda está pendente ("WhatsApp business initiated" com ícone de informação, não check verde — conferir em https://console.twilio.com/us1/develop/sms/content-template-builder). Enquanto não aprovar, o envio automático falha com "The ContentSid is Invalid"/"ContentSid Required". Pode levar de minutos a ~24h. **O sistema funciona normalmente sem isso** — a resposta sempre fica disponível ao paciente na área web também.

### Próximos passos sugeridos

- Confirmar aprovação do Content Template e testar de ponta a ponta (perguntar pelo WhatsApp → responder em `/equipe/perguntas` → confirmar chegada automática).
- Repetir configuração de HTTPS/variáveis de ambiente para `media-qa` e `media-prod` quando for hora de promover essa fatia.
- Continuar a Fatia 6.
- Avaliar se vale criar um segundo Content Template com o menu embutido (3 variáveis) para reaprovação futura.

## Como continuar

Ao colar este documento em uma nova sessão/conta, a nova conversa não terá acesso automático ao histórico desta sessão nem aos arquivos já abertos aqui — mas com este resumo é possível retomar o trabalho no mesmo ponto. Garanta que a nova sessão tenha acesso ao mesmo repositório Git (branch `dev`) e, se for usar a ponte com o computador, à mesma pasta local do projeto (`C:\app\media\src`).
