# Handoff — Continuação do chat com Claude sobre o projeto Media/MedIA

> Atualizado em 2026-09-11 (6ª rodada — ver seção "6ª rodada" perto do final). Cole este documento como primeira mensagem em uma nova sessão do Claude (Cowork) para retomar o trabalho de onde parou, incluindo o contexto e as pendências abaixo.
>
> **A partir da 6ª rodada, toda alteração de código feita numa sessão precisa ser documentada aqui** (pedido explícito do Silvan) - não só ao final da sessão.
>
> **Mudança de infraestrutura importante desde a 4ª rodada (registrada abaixo, mas avisando já aqui em cima porque afeta TUDO que este documento diz sobre AWS/Elastic Beanstalk)**: o ambiente `media-dev` foi migrado do AWS Elastic Beanstalk para o **Render** (`render.yaml` na raiz do repo, serviço `media-dev` em dashboard.render.com). Onde as seções abaixo mencionam "Elastic Beanstalk", "`.platform/hooks/predeploy/`" ou variáveis de ambiente configuradas "no Elastic Beanstalk", leia como "no Render" — o mecanismo mudou (ver 5ª rodada), mas a lista de variáveis e o propósito de cada uma continuam os mesmos.

## Contexto do projeto

- **Media / MedIA**: SaaS de saúde (Flask + PostgreSQL) para clínicas/médicos, com assistente de IA para o paciente tirar dúvidas sobre preparo de exames, agora também via WhatsApp.
- Repositório: `https://github.com/silmaroli-arch/media.git`, branch de trabalho `dev`.
- Deploy em AWS Elastic Beanstalk: `media-dev`, `media-qa`, `media-prod` (região `sa-east-1`). Push em `dev` dispara deploy automático no `media-dev` via GitHub Actions (`einaregilsson/beanstalk-deploy@v22`).
- O computador do Silvan tem um processo de auto-commit que sincroniza a pasta local `C:\app\media\src` com o GitHub — arquivos entregues nessa pasta acabam indo para produção sozinhos.
- Sem framework de migração: alterações de schema são feitas manualmente em `migrar_banco.py` (comandos `ALTER TABLE ... ADD/DROP COLUMN IF [NOT] EXISTS`, idempotentes), executados automaticamente a cada deploy via `.platform/hooks/predeploy/01_migrar_banco.sh`.
  - **Cuidado**: o parser desse script quebra o SQL por `;` de forma simples. Comentários no arquivo NÃO podem conter `;` no meio do texto, ou o deploy quebra com `psycopg.errors.SyntaxError`.
- Testes: arquivos `test_*.py` na raiz do repo, executados diretamente com `python test_arquivo.py` (não é pytest) contra um banco Postgres local recriado do zero + `seed.py` (popula dados de demonstração).
- **Nunca** compartilhar o conteúdo do `.env` (chaves Meta/OpenAI/Anthropic/Gemini, string de conexão do banco) fora dos canais seguros da empresa.

## Migração de arquitetura (Fatias 1–6) — concluída em sua maior parte

O sistema passou por uma reformulação profunda: o conceito antigo de "Empresa/Clínica" (tenancy legado) foi **removido por completo** do código. A estrutura "Grupo" (grupo de trabalho compartilhado) é hoje a única entidade organizadora — cobrança, permissões, pacientes, exames, agenda, tudo passa por `Grupo`/`GrupoMembro`/`GrupoConvite`/`GrupoPaciente`.

- **Fatias 1–5**: concluídas. Removidos Financeiro, Prontuário, Agenda com status/confirmação; `Exame`/`PreparoModelo`/`Agendamento`/`PerguntaPendente`/`FaqItem` migrados para `grupo_id`; `Empresa`/`Clinica`/`ClinicaMembro`/`ConviteVinculo` removidos de `app/models.py`. Suíte de testes inteira (42 arquivos) reescrita e passando.
- **Fatia 6 (em andamento)**: desacoplar a criação de conta de usuário da criação automática de um Grupo — hoje todo cadastro solo já nasce com um Grupo "pessoal" invisível nos bastidores; a ideia é isso só passar a existir quando a pessoa de fato convidar alguém.
- **Fatia 7 (WhatsApp)**: frente mais recente e ativa — ver detalhe abaixo.
- Débito técnico conhecido da Fatia 5: a antiga tela de editar membro da equipe (nome/CPF/endereço/CRM) não tem equivalente ainda no fluxo novo baseado em Grupo.

## Fatia 7 — Área de WhatsApp (em andamento — migrada para Meta Cloud API direta)

Objetivo: paciente se identifica e conversa pelo WhatsApp para tirar dúvidas sobre preparo de exame; a equipe responde pelo `/equipe/perguntas` o que a IA não resolveu, e a resposta pode voltar automaticamente pelo WhatsApp.

**Decisão tomada (sessão de 2026-08-24)**: depois de comparar custo/complexidade
(Twilio cobra uma sobretaxa própria por mensagem além da tarifa da Meta; indo direto à
Meta essa sobretaxa some, mas a aplicação passa a lidar sozinha com retry/templates/erros
que a Twilio abstraía), **o Silvan decidiu migrar de Twilio para a Meta Cloud API
direta**. A migração já foi **implementada e testada nesta sessão** (código pronto,
42/42 testes passando) — ver `PLANO_WHATSAPP.md`, seção "Migração para Meta Cloud API
direta", para o detalhe técnico completo. Falta só o Silvan configurar a conta/
credenciais reais na Meta (checklist abaixo) para o recurso voltar a funcionar em
`media-dev` — sem essas variáveis, o envio/recebimento fica pulado (mesmo comportamento
de "falha aberta" que já existia com a Twilio), não quebra o resto do sistema.

### O que já está pronto (lógica de conversa, inalterada pela migração de provedor)

1. **Schema**: `ConversaWhatsapp` (estado da conversa por telefone, expira em 4h), `ChatMensagem.canal`.
2. **Identificação do paciente em duas mensagens**: primeiro CPF (aceita com ou sem máscara, valida só formato — não dígito verificador, pois é busca de cadastro já existente), depois data de nascimento.
3. **Menu de opções dinâmico** (`app/whatsapp_conversa.py`, função `_menu_opcoes`):
   - "1) Ver informações do preparo"
   - "2) Fazer uma pergunta"
   - "3) Trocar de exame" — só aparece se o paciente tiver mais de um exame ativo.
   - **Enquanto o paciente tem uma `PerguntaPendente` sem resposta** (status `pendente` ou `aguardando_aprovacao`), o menu inteiro fica escondido — qualquer mensagem recebe só o aviso "Sua pergunta ainda está sendo respondida pela equipe..." (`_tem_pergunta_pendente`). O menu volta a aparecer normalmente assim que a pergunta é respondida.
4. **Fazer uma pergunta pelo WhatsApp** reaproveita o mesmo motor de IA/FAQ da área web do paciente (`_responder_pergunta`): tenta IA primeiro (fica pendente de aprovação da equipe), depois FAQ cadastrada, depois respostas prontas de alimento/medicamento, por último cria `PerguntaPendente` para resposta manual.
5. **Resposta automática de volta pelo WhatsApp** (`app/whatsapp_envio.py` + `app/routes_medico.py:perguntas_responder`): quando a equipe responde uma pergunta que veio do WhatsApp, tenta mandar a resposta de volta automaticamente pelo mesmo número, agora via Graph API da Meta direto.
   - Usa um **template aprovado na Meta** (`WHATSAPP_META_TEMPLATE_RESPOSTA`) porque o WhatsApp exige template aprovado para mensagens iniciadas pela empresa fora da janela de 24h da última mensagem do paciente. Texto livre só funciona dentro dessa janela — mantido como fallback.
   - **O texto exibido ao paciente quando o template está configurado vem do próprio template aprovado, não do texto livre do código.** O template atual (2 variáveis: `{{1}}`=pergunta, `{{2}}`=resposta) **não inclui o menu**; adicionar o menu exigiria um novo template com 3 variáveis e nova rodada de aprovação (decisão do Silvan: adiado por ora). O paciente volta a ver o menu normalmente na próxima mensagem que mandar depois da resposta.

### O que MUDOU na migração desta sessão (Twilio → Meta direta)

- `app/routes_whatsapp.py` (webhook): agora precisa de handshake **GET** de verificação
  (`hub.mode`/`hub.verify_token`/`hub.challenge`), e valida a assinatura de cada **POST**
  via `X-Hub-Signature-256` (HMAC-SHA256 do corpo cru, com o App Secret) em vez do
  `X-Twilio-Signature` da Twilio. Payload é JSON aninhado
  (`entry[].changes[].value.messages[]`), não mais form-encoded.
- `app/whatsapp_envio.py` (envio): chama `POST /{phone_number_id}/messages` na Graph API
  da Meta direto (usando `requests`, já uma dependência do projeto), em vez do SDK
  `twilio` (removido de `requirements.txt`).
- `app/whatsapp_conversa.py`: `normalizar_telefone_whatsapp` ajustado — a Meta manda o
  remetente como dígitos puros (ex.: `"5527999998888"`), sem o prefixo `"whatsapp:"` que
  a Twilio usava.
- Testes: `test_whatsapp_webhook_assinatura.py` reescrito para o novo formato (GET de
  verificação + POST com HMAC-SHA256); `test_whatsapp_identificacao.py` e
  `test_whatsapp_pergunta.py` não mudaram (testam só a lógica de conversa, que é
  independente do provedor). Suíte inteira: 42/42 passando.
- Variáveis de ambiente novas (ver `.env.example`): `WHATSAPP_META_VERIFY_TOKEN`,
  `WHATSAPP_META_APP_SECRET`, `WHATSAPP_META_ACCESS_TOKEN`,
  `WHATSAPP_META_PHONE_NUMBER_ID`, `WHATSAPP_META_TEMPLATE_RESPOSTA` (opcional),
  `WHATSAPP_META_TEMPLATE_IDIOMA` (opcional), `WHATSAPP_META_API_VERSION` (opcional). As
  antigas `TWILIO_*`/`WHATSAPP_URL_PUBLICA` deixam de ser usadas.

### Infraestrutura já configurada (continua valendo)

- HTTPS habilitado no ALB do `media-dev` — certificado ACM para `dev.media.med.br`.
- DNS migrado do Registro.br para Cloudflare (Registro.br não permite CNAMEs customizados e redirect automático ao mesmo tempo).

### Pendência ativa (bloqueador atual)

O código da migração está pronto, mas **nenhuma credencial real da Meta existe ainda** —
o Silvan precisa (não pode ser feito por mim, política de segurança):

1. Criar um app Meta (tipo "Business") em developers.facebook.com, adicionar o produto WhatsApp.
2. Completar a verificação do Meta Business Manager (CNPJ etc.) e vincular o número de telefone dedicado.
3. Gerar o token de acesso permanente (System User) e anotar o Phone Number ID.
4. Pegar o App Secret do app e escolher um Verify Token.
5. Cadastrar a URL do webhook + Verify Token em WhatsApp Manager > Webhooks, assinando o campo `messages`.
6. Registrar e esperar aprovação do template de resposta (mesmo texto/variáveis do template antigo da Twilio).
7. Configurar as 4-7 variáveis de ambiente novas no Elastic Beanstalk (`media-dev`, depois `media-qa`/`media-prod`).

Ver checklist completo com links e detalhe de cada passo em `PLANO_WHATSAPP.md`.

### Próximos passos sugeridos

- Silvan completar o checklist de credenciais Meta acima.
- Depois de configurado: teste de ponta a ponta em `media-dev` (mandar mensagem real de
  um celular de teste, confirmar handshake do webhook, confirmar resposta automática).
- Repetir configuração de variáveis de ambiente para `media-qa` e `media-prod` quando for hora de promover essa fatia.
- Continuar a Fatia 6.
- Avaliar se vale criar um segundo Content Template com o menu embutido (3 variáveis) para reaprovação futura.

## Fatia 8 — PWA da equipe com notificação push (nova nesta sessão)

Enquanto o bloqueio do WhatsApp (acima) fica em aberto, o Silvan pediu uma forma alternativa de avisar a equipe sem depender do WhatsApp de volta: um **PWA (Progressive Web App)** que o médico instala no celular e recebe notificação push nativa do navegador quando chega uma pergunta nova de paciente — o paciente continua conversando 100% pelo WhatsApp normalmente, só a notificação do lado da equipe é que passou a ter esse canal extra.

### Decisões de escopo (confirmadas com o Silvan)

- Só para a equipe (médico), nunca para o paciente — o paciente não usa o PWA.
- Notificação push nativa do navegador (Web Push / VAPID), não um app nativo de loja de aplicativos.
- **Só médico recebe, nunca secretária/administrativo** — mesmo que ambos tenham vínculo ativo no mesmo Grupo.
- **Dentro dos médicos, só quem é responsável pelo exame daquela pergunta específica é avisado** — a mesma regra que já existia para decidir o que aparece na tela `/equipe/perguntas` de cada médico (`_restringir_perguntas_para_medico`): médico principal do exame (`Exame.medico_id`) + médicos extra (`Exame.medicos_extra`); para pergunta geral (sem exame vinculado), só médicos com a permissão `perm_pacientes`.

### O que foi implementado

- **`app/models.py`**: novo modelo `PushSubscription` (guarda a inscrição push de cada navegador/aparelho: `endpoint`, `p256dh`, `auth`, vinculado a um `usuario_id`).
- **`app/push_notificacoes.py`** (novo): módulo central — `notificar_equipe_nova_pergunta(pergunta)` calcula quem deve ser avisado (`_usuarios_para_notificar`, replicando a regra de `_restringir_perguntas_para_medico`) e envia a notificação via `pywebpush`. Chamado logo após criar uma `PerguntaPendente` nova em `app/routes_paciente.py` (chat do paciente) e `app/whatsapp_conversa.py` (pergunta feita pelo WhatsApp). Sem as variáveis VAPID configuradas, a função não faz nada (mesmo padrão de "falha aberta" já usado no envio de WhatsApp).
- **`app/static/manifest.json`** e **`app/static/sw.js`** (novos): manifesto do PWA (nome, ícones, tela inicial `/equipe/perguntas`) e o service worker (registra o push, mostra a notificação, abre a tela certa ao clicar). Ícones gerados em `app/static/img/pwa/`.
- **`app/routes_medico.py`**: 3 rotas novas (`/equipe/push/vapid-public-key`, `/equipe/push/subscribe`, `/equipe/push/unsubscribe`) para o navegador buscar a chave pública e registrar/remover a inscrição.
- **`app/templates/base.html`**: para usuários com `tipo == "medico"`, carrega o manifesto do PWA, registra o service worker e mostra um banner ("Ative as notificações para ser avisado no celular...") com botão para autorizar — nada disso aparece para secretária/administrativo.
- **`app/__init__.py`**: rotas `/sw.js` e `/manifest.json` na raiz (exigência técnica do padrão PWA) e leitura das 3 variáveis de ambiente VAPID.
- **`gerar_chaves_vapid.py`** (novo, raiz do repo): script para gerar o par de chaves VAPID (rodar só uma vez — gerar de novo invalida toda inscrição já feita pela equipe).
- **`migrar_banco.py`**: criação da tabela `push_subscriptions`.
- **`requirements.txt`**: `pywebpush` (mais `setuptools<71`, necessário para uma dependência dele instalar corretamente).

### Configuração feita em `media-dev`

3 variáveis de ambiente novas no Elastic Beanstalk, geradas pelo script acima e **já configuradas pelo Silvan** ("Chaves salvas"):
- `VAPID_PUBLIC_KEY`
- `VAPID_PRIVATE_KEY`
- `VAPID_CLAIM_EMAIL` (`mailto:contato@inflor.com.br`)

### Como instalar no celular (orientado ao Silvan)

- **Android/Chrome**: abrir o site, tocar em "Ativar notificações" no banner (ou usar o menu do navegador → "Adicionar à tela inicial" para instalar como app). Push funciona mesmo só pelo navegador, sem precisar instalar.
- **iPhone/Safari**: **precisa iOS 16.4 ou mais recente E o PWA precisa estar de fato instalado na tela de início** via Safari → botão Compartilhar → "Adicionar à Tela de Início". Só abrir pelo navegador (sem instalar) **não** ativa push no iOS — diferente do Android.

### Pendência ativa (bug reportado, NÃO resolvido)

O Silvan testou no iPhone (mandou uma pergunta de teste) e a notificação **não chegou**. Isso ficou sem diagnóstico — a conversa foi interrompida antes de conseguir confirmar com ele: (a) se o banner "Ativar notificações" foi tocado e a permissão foi de fato concedida; (b) a versão do iOS; (c) se o PWA estava genuinamente instalado na tela de início (não só aberto numa aba do Safari); (d) — possibilidade nova, depois do ajuste fino de escopo por exame — se a pergunta de teste era de um exame do qual aquele médico específico não é responsável, caso em que a notificação corretamente NÃO deveria disparar (esse ajuste de escopo foi implementado depois do teste do Silvan, então a ordem dos eventos importa para o diagnóstico). **Retomar esse diagnóstico é o próximo passo mais importante desta fatia.**

## Licença individual do médico + calendário de pagamento (nova nesta sessão)

> Nota de numeração: o código chama isso de "Fatia 8" nos comentários (`app/models.py`, `app/routes_auth.py`, `app/routes_medico.py`, `app/routes_dono.py`, `migrar_banco.py`, `seed.py`) — mesmo número já usado acima pelo PWA de notificação push. É só uma coincidência de numeração entre sessões diferentes (cada uma não via o trabalho da outra), **não é a mesma fatia** e não há conflito de código entre elas. Vale ajustar a numeração dos comentários numa limpeza futura, se incomodar.

O Silvan pediu que o médico tenha acesso a quando sua licença vai vencer, com um menu para isso inclusive no app mobile. Ao esclarecer o escopo, ele corrigiu uma suposição importante: **a cobrança é por médico, não por Grupo** (`Grupo.valor_por_medico × medicos_distintos` continua existindo, mas é só uma estimativa) — a licença vale a partir do momento do cadastro, independente de o médico estar ou não vinculado a um Grupo de trabalho.

### Licença individual (`Usuario.licenca_status` / `licenca_vencimento`)

- **`app/models.py`**: `Usuario` ganhou `licenca_status` (`trial`/`ativa`/`inadimplente`/`bloqueada`, mesmo vocabulário de `Grupo.status`) e `licenca_vencimento` (Date), mais o método `verificar_vencimento_licenca()` (mesma regra de `Grupo.verificar_vencimento_trial()` — trial vencido vira `inadimplente`, sem commit automático).
- **Só informativo por enquanto**: vencer a licença **não bloqueia** o acesso (decisão explícita do Silvan) — fica para uma iteração futura se for o caso.
- **`app/routes_auth.py`** (`cadastro()`) e **`app/routes_grupo.py`** (`convidar()`, ação "criar_conta"): todo médico novo já nasce com `licenca_vencimento` = hoje + `PlataformaConfig.trial_dias` (o mesmo parâmetro configurável que os Grupos usam para o trial deles) — não é uma constante de negócio nova.
- **`app/routes_medico.py`**: nova rota `/equipe/minha-licenca` (`minha_licenca()`), só para médico (secretária é redirecionada). Template `medico/minha_licenca.html` mostra status + vencimento.
- **`app/templates/base.html`**: item de menu "Minha licença" — **visível tanto no desktop quanto no celular** (sem `d-md-none` nem `oculto_no_celular_do_medico`), atendendo ao pedido explícito de acesso mobile.
- **`app/routes_dono.py`** / **`app/templates/dono/usuarios.html`**: nova coluna "Licença" na lista de usuários, com badge de status e um formulário rápido (status + data) para o dono editar a licença de cada médico. Rota: `POST /dono/usuarios/<id>/licenca`.

### Calendário de pagamento mensal (`LicencaPagamento`)

Pedido seguinte do Silvan: "criar um calendário de pagamento mensal" e mostrar na tela de licença se o médico pagou ou não. Esclarecido antes de implementar: controle **100% manual** do dono (não existe gateway de pagamento integrado), histórico dos últimos meses (não só o mês atual), e esse calendário **convive** com `licenca_status`/`licenca_vencimento` — não substitui.

- **`app/models.py`**: novo modelo `LicencaPagamento` (`usuario_id`, `mes` — sempre dia 1 do mês, `pago`, `pago_em`), com `UniqueConstraint(usuario_id, mes)`. Função `garantir_meses_licenca(usuario)` gera as linhas que faltam, como "não pago", desde o mês do cadastro do médico até o mês atual — chamada sempre que a tela é aberta (médico ou dono), ninguém precisa "abrir o mês" manualmente.
- **`medico/minha_licenca.html`**: passou a mostrar o histórico completo, mês a mês, com badge Pago/Não pago.
- **`app/routes_dono.py`**: `/dono/usuarios` mostra se o mês atual está pago ou não pago de cada médico; link "ver calendário" leva para `/dono/usuarios/<id>/licenca/pagamentos` (novo template `dono/usuario_licenca_pagamentos.html`), onde o dono marca cada mês como pago/não pago com um botão (`POST /dono/usuarios/<id>/licenca/pagamentos/<pagamento_id>/marcar`).
- **`migrar_banco.py`**: as colunas novas de `Usuario` (`licenca_status`/`licenca_vencimento`) precisaram de `ALTER TABLE`, com backfill (`licenca_status='ativa'` para médicos já existentes sem `licenca_vencimento`, já que essa migração deixaria de fazer sentido pra contas anteriores à fatia). A tabela `licenca_pagamentos` é nova e **não precisou de `ALTER TABLE`** — é criada sozinha pelo `db.create_all()` que já roda no topo do script (mesmo caso de `push_subscriptions`).
- **`seed.py`**: 3 médicos de demonstração cobrindo os três cenários (ativa/pago, trial prestes a vencer, inadimplente/não pago).

### Testes e commits

- `test_licenca_medico.py` (novo): 34 checagens cobrindo cadastro → trial automático, tela do médico, visibilidade no menu mobile, vencimento automático sem bloqueio, edição pelo dono, geração automática dos meses, marcação de pagamento e isolamento entre usuários (não deixa marcar pagamento de outro médico).
- Suíte completa rodada isolada (banco limpo por arquivo) depois de cada mudança — sem regressão em relação à baseline já existente (as falhas pré-existentes de outros arquivos `test_*.py` não têm relação com esta fatia, confirmado com `git stash` antes de começar).
- Dois commits locais em `dev` (ainda não passaram pelo push manual/automático do Silvan até a data deste documento): `577dd7c` (licença individual) e `817ed11` (calendário de pagamento), cada um com exatamente os arquivos daquela parte — nenhum dos outros arquivos com mudanças em andamento na pasta (WhatsApp, PWA, etc.) foi tocado ou incluído nesses commits.

### Fora do escopo desta fatia (perguntado ao Silvan, ainda sem decisão) — **todos os 3 itens abaixo já foram implementados em rodadas seguintes, ver seção "Extensões da licença individual" logo adiante**

- ~~Mostrar um **valor de cobrança por médico** na tela de licença (hoje só status/vencimento/pago-não pago, sem valores).~~
- ~~**Aviso automático para o dono** quando um médico ficar sem pagar por mais de N meses (hoje é só uma indicação visual passiva na lista/calendário, sem notificação nenhuma).~~
- ~~Gateway de pagamento real (cartão/PIX/boleto processado automaticamente) — decisão explícita do Silvan de manter 100% manual por enquanto.~~

## Extensões da licença individual (novas nesta sessão)

Depois do calendário de pagamento (seção anterior), o Silvan pediu para seguir com os 3 itens que tinham ficado "fora do escopo" — em rodadas separadas, cada uma com commit próprio.

### 1. Valor de cobrança por médico (`Usuario.valor_licenca_mensal`)

- **`app/models.py`**: `Usuario` ganhou `valor_licenca_mensal` (Numeric(10,2), opcional) — valor mensal **negociado individualmente com cada médico** (varia por médico, não é um valor único da plataforma).
- Editável só em **`/dono/usuarios`** (`POST /dono/usuarios/<id>/licenca`, mesmo formulário de status/vencimento) — decisão explícita do Silvan de **não** mostrar esse valor na tela do próprio médico nem guardar um valor por mês no calendário nessa rodada (isso só veio depois, ver item 3 abaixo).
- Aceita vírgula decimal (mesmo padrão de `Grupo.valor_por_medico`); deixar o campo em branco limpa para `None`.
- Commit: `8eb7721`.

### 2. Aviso automático de inadimplência (`Usuario.aviso_inadimplencia_meses`)

- Pedido: avisar o dono quando um médico acumula meses seguidos sem pagar. Ao esclarecer o escopo, o Silvan pediu explicitamente um **limite configurável por médico** ("eu configuro por médico"), não um número fixo pra plataforma toda — e só **destaque visual no painel**, sem e-mail.
- **`app/models.py`**: `Usuario.aviso_inadimplencia_meses` (Integer, NOT NULL, padrão 2 meses, editável por médico); `meses_consecutivos_sem_pagar(usuario)` conta quantos meses seguidos, a partir do mês atual pra trás, o médico está sem pagar (para no primeiro mês pago ou sem registro).
- **`/dono/usuarios`**: banner vermelho no topo listando quem passou do próprio limite, linha destacada (`table-danger`) e badge "X mês(es) seguido(s) sem pagar" ao lado do badge de mês atual pago/não pago; o próprio limite é editável no mesmo formulário inline de cada médico.
- Commit: `895c0bc`.

### 3. Valor por mês no calendário + valor na tela do médico + gateway Mercado Pago

Pedido seguinte do Silvan, junto com os itens 1 e 2 revisitados: mostrar o valor também na tela do próprio médico, guardar o valor cobrado em cada mês do calendário (não só na tela do dono), e — o maior dos três — integrar um gateway de pagamento real. Provedor escolhido pelo Silvan: **Mercado Pago**, com escopo de **cobrança automática com webhook** (confirmação de pagamento sem o dono precisar marcar manualmente).

- **Valor na tela do médico**: `medico/minha_licenca.html` passou a mostrar `current_user.valor_licenca_mensal` junto com status/vencimento (sem mudança de rota — `current_user` já está disponível no template).
- **Valor por mês (`LicencaPagamento.valor`)**: nova coluna Numeric(10,2), uma **fotografia** do valor do médico no momento em que o mês nasce (`garantir_meses_licenca`) — não muda retroativamente se o valor do médico mudar depois (é como uma fatura já emitida). Aparece tanto no calendário do dono (`dono/usuario_licenca_pagamentos.html`) quanto no do médico (`medico/minha_licenca.html`).
- **Gateway Mercado Pago (`app/mercadopago_integration.py`, novo módulo)**:
  - `criar_preferencia_pagamento(pagamento)`: cria uma preferência de pagamento (Checkout Pro) pra UM mês específico, gravando `mp_preference_id`/`mp_status`/`mp_init_point` no `LicencaPagamento` e travando o `valor` cobrado nessa preferência.
  - `_assinatura_valida(data_id, request_id)` + `consultar_pagamento(payment_id)`: validação da assinatura do webhook (cabeçalho `X-Signature`, HMAC-SHA256 sobre um "manifest" `id:...;request-id:...;ts:...;`, conforme documentação oficial do Mercado Pago) e confirmação do pagamento **direto na API** antes de marcar qualquer mês como pago (nunca confia só no conteúdo da notificação recebida).
  - Nova rota **pública** `POST /webhooks/mercadopago` (`app/routes_pagamentos_webhook.py`, blueprint próprio) — recebe a notificação, valida a assinatura (falha fechada: sem `MERCADOPAGO_WEBHOOK_SECRET` configurado, recusa tudo — mesmo princípio do webhook de WhatsApp em `app/routes_whatsapp.py`), e sempre responde 200 (mesmo quando recusa) pra não fazer o Mercado Pago reentregar a notificação.
  - Nova rota `POST /dono/usuarios/<id>/licenca/pagamentos/<pagamento_id>/cobrar` — o dono gera (ou regenera) a cobrança real de um mês específico; o link fica visível tanto pro dono (`dono/usuario_licenca_pagamentos.html`) quanto pro médico ("Pagar agora" em `medico/minha_licenca.html`).
  - **Isto é uma camada ADITIVA** — o controle manual (`usuario_licenca_pagamento_marcar`) continua existindo do lado do dono, útil pra Pix fora do sistema ou acordos informais (decisão do Silvan de manter os dois caminhos, não substituir um pelo outro).
  - **Configuração necessária** (variáveis de ambiente, documentadas em `.env.example`, nunca em código): `MERCADOPAGO_ACCESS_TOKEN` (comece pelas credenciais de TESTE) e `MERCADOPAGO_WEBHOOK_SECRET` (gerado em Suas integrações > Webhooks > Configurar notificações). **Sem essas variáveis configuradas em produção, o botão "Gerar cobrança" falha com uma mensagem clara** — nada quebra, mas nenhuma cobrança real é gerada até o Silvan cadastrar as credenciais.
  - **Testado só com chamadas de API simuladas** (monkeypatch de `requests.post`/`requests.get`, sem nenhuma chamada de rede de verdade) — o fluxo de ponta a ponta com o Mercado Pago de verdade (credenciais de teste, webhook configurado apontando pra `media-dev`) ainda precisa ser validado manualmente pelo Silvan antes de confiar no gateway em produção.
- Migração: `migrar_banco.py` ganhou `ALTER TABLE licenca_pagamentos ADD COLUMN` para `valor`, `mp_preference_id`, `mp_payment_id`, `mp_status`, `mp_init_point`.
- Testes: novo arquivo `test_licenca_pagamento_valor_e_gateway.py` (26 checagens) — valor na tela do médico, fotografia do valor por mês (incluindo não mudar retroativamente), geração de cobrança sem credenciais (mensagem de erro) e com credenciais (preferência simulada), link "Pagar agora" na tela do médico, webhook com assinatura válida (marca como pago) e inválida (recusa, sem processar), e webhook sem secret configurado (falha fechada).
- Commit: pendente ao final desta rodada (ver `git log` em `dev`).

## Otimização de custo — importação de PDF de preparo

O Silvan notou que importar um PDF para virar modelo de preparo estava consumindo muitos tokens de IA, e perguntou se importar uma imagem PNG em vez de PDF seria mais leve. A causa raiz real (diferente da hipótese inicial) é que a Claude processa cada página de um PDF nativo de forma parecida com uma imagem por baixo dos panos — PNG teria custo igual ou pior, não menor.

**Correção implementada (1ª rodada)** em `app/ia_pdf_preparo.py`: antes de mandar o PDF inteiro pra IA, o sistema passou a tentar extrair o texto puro do PDF de graça (reaproveitando `app.pdf_preparo.extrair_texto`, que já existia) e manda **só o texto** para a IA — bem mais barato. O PDF nativo (caminho caro) só é usado como fallback quando o texto extraído vier vazio ou quase vazio (sinal de PDF escaneado/imagem, sem texto selecionável).

**Troca de motor de IA (2ª rodada, mesma sessão)**: mesmo com a otimização acima, o Silvan achou o custo ainda alto (~US$0,10 por extração) e pediu para trocar de provedor. `app/ia_pdf_preparo.py` foi reescrito para usar **Google Gemini** (`gemini-2.5-flash`, pacote `google-genai`) em vez da Claude/Anthropic — só nessa tarefa de extração de PDF, mantendo a mesma estratégia de custo (texto primeiro, PDF nativo só como fallback). **O chat de dúvidas do paciente (`app/ia_preparo.py`) continua na Claude/ChatGPT sem nenhuma mudança** — são módulos independentes, decisão explícita do Silvan de não migrar essa parte (o reconhecimento de marca comercial de medicamento que o chat depende funciona melhor na Claude).

- Nova variável de ambiente: `GEMINI_API_KEY` (chave gerada em aistudio.google.com/apikey, projeto "Default Gemini Project" do Silvan) — **já configurada por ele no `media-dev`**. Opcionalmente `GEMINI_MODEL` para trocar o modelo (padrão `gemini-2.5-flash`). Sem a chave, cai na extração heurística por regex (`app.pdf_preparo`), nada quebra.
- `requirements.txt` ganhou `google-genai>=1.0`.
- Atenção ao nível de faturamento da chave do Silvan (aparecia como "Pagamento do Firebase, Nível 1 · Pré-pagamento") — contas gratuitas do Gemini têm limite de taxa baixo; vale confirmar se o faturamento está mesmo ativo se o volume de importação de PDF crescer.

### Bug encontrado e corrigido: 502 Bad Gateway ao importar PDF grande

Logo depois da troca para o Gemini, o Silvan tentou importar um PDF real (baixado da internet) e a aplicação inteira caiu com **502 Bad Gateway** em `media-dev` — não só a rota de importar PDF, o site inteiro parou de responder por alguns minutos.

**Diagnóstico** (via logs do Elastic Beanstalk, `eb-engine.log` e `web.stdout.log`/nginx `error.log`): a extração via Gemini funcionou normalmente. O erro real foi `nginx: upstream sent too big header while reading response header from upstream`, precedido por um aviso do Flask: `The 'session' cookie is too large (...) The final size was 4408 bytes but the limit is 4093 bytes`. Causa raiz: a rota de importação (`app/routes_medico.py:preparo_modelos_importar_xlsx`) guardava a sugestão extraída inteira (incluindo todo o texto de "instruções") em `session["preparo_sugestao_importada"]` — um cookie assinado pelo Flask — para sobreviver a um redirect até a tela de revisão. Isso já existia antes da troca de IA, mas o texto extraído desse PDF específico (mais longo que os testados antes) estourou o limite de 4KB por cookie, e o nginx recusa qualquer resposta cujo cabeçalho (incluindo o `Set-Cookie`) passe do limite dele — daí o 502 na aplicação inteira, não um erro amigável.

**Correção**: a rota agora renderiza a tela de revisão (`medico/preparo_modelo_form.html`) **diretamente na resposta do próprio upload do PDF**, em vez de guardar a sugestão na sessão e redirecionar — elimina a dependência do cookie para esse caminho, então PDFs longos não têm mais esse limite. (Os outros dois pontos que usam a mesma sessão — importação de Excel com uma aba, e escolha de aba quando há várias — não foram alterados, pois planilhas tendem a gerar sugestões bem menores; se algum dia um Excel muito grande também estourar o cookie, aplicar a mesma correção lá.)

Suíte de testes completa (43 arquivos) rodada de novo depois de cada mudança desta seção — sem regressão.

## 5ª rodada (2026-09-10) — migração para Render, fix crítico de "Apagar dados", templates WhatsApp aprovados, ajustes de cadastro/menu

### Migração de infraestrutura: Elastic Beanstalk → Render

O `media-dev` deixou de rodar no AWS Elastic Beanstalk e passou a rodar no **Render**
(decisão do Silvan, 2026-09-04) — plano Free, serviço `media-dev` + banco Postgres
`media-dev-db`, ambos descritos em `render.yaml` na raiz do repo. Push em `dev` continua
disparando deploy automático, agora direto pelo Render (`autoDeploy: true`), sem passar
mais pelo GitHub Actions para este ambiente.

- **`startCommand`** (substitui o hook `.platform/hooks/predeploy/01_migrar_banco.sh` do
  Elastic Beanstalk): `python gerar_deploy_info_render.py; python migrar_banco.py &&
  gunicorn application:application --timeout 120`. Migração de schema continua em
  `migrar_banco.py` (inalterado, mesma disciplina de `ALTER TABLE ... ADD COLUMN IF NOT
  EXISTS`), só passou a rodar encadeado no `startCommand` porque o plano Free do Render
  não suporta `preDeployCommand`.
- **`gerar_deploy_info_render.py`** (novo): como o GitHub Actions não roda mais para a
  branch `dev`, esse script tampa o buraco gerando `deploy_info.json` localmente no
  próprio Render (usa `git rev-parse`/`git log` no checkout, que o build do Render
  mantém) — é isso que alimenta o "versão X (HEAD) · último deploy" na tela de login e o
  histórico de versões (tabela `HistoricoDeploy`, ver `_registrar_deploy_atual` em
  `app/__init__.py`).
- **Variáveis de ambiente**: mesma lista de sempre (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`,
  `OPENAI_API_KEY`, `WHATSAPP_META_*`, `MERCADOPAGO_*`, `VAPID_*`), agora configuradas em
  Render → media-dev → Environment (em vez do console do Elastic Beanstalk).
- **Plano Free do Render — limitações relevantes para depuração futura**: sem Shell (pede
  upgrade para o plano Starter), sem Zero Downtime, sem Persistent Disks, a instância
  "dorme" após inatividade (aviso "Your free instance will spin down..." visível no
  dashboard).
- **Armadilha descoberta nesta rodada, guardar para o futuro**: depois de um deploy do
  commit certo aparecer como "Deploy succeeded | Live" no Render (log confirmando
  `deploy_info.json gerado: commit <hash certo>` e "Your service is live"), o site em
  produção continuou servindo o código de um commit ANTERIOR por um bom tempo — mesmo
  testado em aba anônima, com `?cache-bust` na URL, e depois de descartar hipóteses de
  serviço duplicado, domínio customizado extra, múltiplas instâncias e processo travado
  (todas checadas e descartadas nas Settings/Events do Render). **A causa foi o build
  cache do Render ficando obsoleto entre deploys consecutivos** — resolvido fazendo um
  **Manual Deploy → limpar o cache de build** pelo dashboard. Se isso voltar a acontecer
  (deploy "Live" no Render mas comportamento/versão antigos no site), esse é o primeiro
  atalho a tentar, antes de qualquer investigação mais profunda.

### Fix crítico: "Apagar todos os dados" quebrava com FK violation no Postgres

A função de wipe-all-data (`app/limpar_dados.py`, usada pelo dono para zerar a conta)
apagava `PreparoModelo` (e tabelas dependentes: `PreparoCorte`, `PreparoInfoGeral`,
`PreparoAlimento`, `PreparoExameAnterior`, `PreparoMedicamentoSuspenso`,
`PreparoMedicamentoMantido`) **antes** de apagar `Exame` — só que `Exame.preparo_modelo_id`
é uma FK para `preparo_modelos.id`, então em Postgres real (não em SQLite, usado nos
testes locais) isso sempre lançava `ForeignKeyViolation`, quebrando a função no primeiro
uso em produção. Corrigido invertendo a ordem: `Exame` (e a tabela de associação
`exame_medicos_associados`) agora é apagado **antes** dos `PreparoModelo`/tabelas
dependentes. Validado contra um Postgres 16 local de teste (não só SQLite) antes de
confiar na correção — reproduzir bugs de FK exige testar no mesmo motor de banco da
produção, SQLite é frouxo demais com integridade referencial para pegar isso sozinho.
Commit: `df11641`. Confirmado funcionando em produção pelo Silvan.

### WhatsApp: 3 templates Meta aprovados/em aprovação + ajuste de variável vazia

Continuação da Fatia 7 (seção acima) — as credenciais Meta já estavam configuradas desde
a 4ª rodada; nesta rodada o Silvan efetivamente criou e submeteu os templates que
faltavam em WhatsApp Manager:

- `boas_vindas_clinica` (2 variáveis) — mensagem de boas-vindas ao paciente/médico
  recém-cadastrado.
- `preparo_cadastrado_medico` (1 variável) — aviso ao médico de que já pode testar a IA,
  depois de cadastrar um modelo de preparo.
- `resposta_duvida_paciente` (2 variáveis, **novo nesta rodada**) — resposta a uma
  pergunta do paciente quando a janela de 24h já fechou (variável
  `WHATSAPP_META_TEMPLATE_RESPOSTA` já existia desde a migração para Meta direta, mas o
  template em si só foi criado agora).

Todos passaram por reclassificação automática da Meta de "Utilidade" para "Marketing"
(aceito, a pedido do Silvan, em vez de tentar reescrever para forçar "Utilidade") e pela
regra de que uma variável de template não pode ficar no início/fim do corpo da mensagem
(contornado reescrevendo o texto para sempre ter conteúdo fixo depois da última
variável). As 3 variáveis de ambiente correspondentes
(`WHATSAPP_META_TEMPLATE_BOAS_VINDAS`, `WHATSAPP_META_TEMPLATE_MEDICO_PREPARO_CADASTRADO`,
`WHATSAPP_META_TEMPLATE_RESPOSTA`) foram configuradas no Render.

Ajuste de código relacionado: `enviar_boas_vindas_whatsapp` (`app/whatsapp_envio.py`)
passou a mandar `aviso_extra.strip() or " "` em vez de string vazia — a Graph API da Meta
rejeita variável de template vazia, então quando não há aviso extra manda um espaço em
branco só para satisfazer a API.

Pendência: os 3 templates estavam "Em análise" na Meta ao final desta rodada — falta
confirmar aprovação e testar de ponta a ponta (mensagem de boas-vindas chegando de fato
no WhatsApp de um cadastro novo).

### Cadastro público: campos obrigatórios + confirmação de senha + menu "Meus dados"

Pedido do Silvan, com este escopo confirmado por ele (só se aplica a cadastros NOVOS, sem
checagem retroativa em contas existentes):

- **Todo o formulário de cadastro** (`app/routes_auth.py:cadastro()`,
  `app/templates/auth/cadastro.html`) virou obrigatório, **exceto Complemento** — antes só
  nome/e-mail/senha/CPF eram exigidos de verdade; telefone e CEP só validavam formato
  quando preenchidos (podiam ficar em branco); o resto do endereço (rua, número, bairro,
  cidade, UF) não tinha validação nenhuma (campos inclusive ficavam `readonly`,
  preenchidos só via ViaCEP).
- **Confirmação de senha** (campo `senha_confirmacao`, digitar duas vezes) — só na tela de
  cadastro inicial, não nas demais telas de senha (ex.: `trocar_senha`), a pedido
  explícito do Silvan.
- **Novo item de menu "Meus dados"** na barra lateral (`app/templates/base.html`) — leva
  para a tela `auth.meus_dados` que já existia (antes só acessível pelo menu de usuário no
  canto superior direito), permitindo ao médico/secretária ver e editar os próprios dados
  (ex.: telefone, se trocou de número) sem precisar abrir aquele menu secundário.
- 6 arquivos de teste (`test_medico_independente.py`, `test_licenca_medico.py`,
  `test_licenca_pagamento_valor_e_gateway.py`, `test_painel_agenda_do_medico.py`,
  `test_smoke.py`, `test_smoke_final.py`) tiveram os `POST /cadastro` atualizados para
  incluir os novos campos obrigatórios.

### Reorganização do menu lateral + máscaras em "Meus dados"

Pedido seguinte do Silvan, com uma ordem específica definida por ele (ver planilha que
ele compartilhou): **Painel, Meus dados, Exames & preparo, Pacientes, Meus exames
agendados, Médico + IA, Grupos de trabalho, Minha licença** — o item **"Primeiros
passos" saiu do menu** (a rota `medico.primeiros_passos` continua existindo, só sem link
fixo na barra lateral).

Além disso, na tela "Meus dados" (`app/templates/auth/meus_dados.html`), o telefone (e
também CPF, data de nascimento e CEP, que tinham o mesmo problema) apareciam **sem
formatação** na primeira abertura da tela — a máscara (`aplicarMascaraFixa`) só entrava
em ação a partir do próximo caractere digitado pelo usuário, não no valor já carregado do
banco. Corrigido para formatar o valor assim que a página abre.

### Paciente de teste do WhatsApp, visível (só) na tela "Meus pacientes"

Descoberta desta rodada: quando um médico se cadastra com telefone preenchido, o sistema
já cria automaticamente um `Paciente` sintético com `eh_teste=True` (usa o próprio
CPF/telefone/data de nascimento do médico) — usado como âncora técnica da tela "Testar IA
nos meus preparos" (ver `_paciente_teste_do_medico` em `app/routes_medico.py`). Esse
cadastro é **de propósito** excluído de toda lista/contagem/relatório de paciente real
(`_filtro_pacientes_da_empresa()`, comentário explícito no código: "não deve aparecer em
NENHUMA lista, contagem ou relatório de paciente de verdade") — por isso a tela "Meus
pacientes" aparecia vazia mesmo o médico já tendo se cadastrado, o que gerou uma dúvida do
Silvan sobre se o cadastro automático realmente existia.

Depois de confirmar com ele o escopo exato, a solução implementada foi cirúrgica: o
paciente de teste passou a aparecer **só na tela "Meus pacientes"** (`pacientes_lista()`
em `app/routes_medico.py` + `app/templates/medico/pacientes_lista.html`), numa linha
visualmente destacada com badges "Teste" / "Paciente de teste (Testar IA)", sem link de
"Detalhes" (a rota `pacientes_detalhe` também usa `_filtro_pacientes_da_empresa()` e daria
404 para esse cadastro) — **sem** alterar `_filtro_pacientes_da_empresa()` em si, então
contagens, relatórios e as demais listas continuam exatamente como antes, sem o paciente
de teste contaminando métrica nenhuma.

### Observação sobre o ambiente de trabalho desta rodada

Durante toda esta 5ª rodada, a ferramenta de terminal remoto (`device_bash`) esteve
indisponível ("no Plan9 drive shares mounted") para as pastas conectadas
(`media--src`, `oseupreparo--src`) — todo código foi entregue por cópia de arquivo
(`device_commit_files`) direto na pasta `C:\app\media\src`, nunca por comando git direto
na máquina do Silvan. Isso significa que **eu nunca rodo `git add`/`commit`/`push`
sozinho** — cabe sempre ao Silvan (manualmente no PowerShell, ou via o serviço Windows de
auto-commit/push mencionado na seção "Contexto do projeto" acima) revisar com `git diff`
e commitar o que foi entregue. Em pelo menos duas ocasiões desta rodada, uma primeira
tentativa de `device_commit_files` reportou sucesso mas o arquivo não apareceu de fato no
`git diff` do Silvan — resolvido re-enviando com a opção de forçar sobrescrita; vale
tentar isso primeiro se um arquivo "entregue" não aparecer no `git status` da máquina
dele.

**Atenção para a próxima sessão**: ao rodar `git status`/`git add` depois desta rodada, a
pasta local pode conter outras modificações não relacionadas ao que está documentado aqui
(por exemplo, em `app/models.py`, `app/routes_dono.py`,
`app/templates/dono/dashboard.html`, `render.yaml`, `migrar_banco.py`) — não foram feitas
nesta rodada, possivelmente sobras do serviço de auto-commit ou de trabalho anterior
ainda não commitado. Antes de comitar em bloco, confirmar com o Silvan o que cada arquivo
extra contém.

### Mudança de arquitetura (mesma rodada, depois da entrega acima): o médico agora TAMBÉM é um paciente real

Pergunta do Silvan que motivou isso: "Tomando como base que agora o médico se torna uma
paciente também, eu poderia realizar um agendamento de uma consulta para o paciente
médico para fins de testes?" — resposta original era não, porque o Paciente sintético
criado no cadastro do médico (`_paciente_teste_do_medico`, ver seção acima) tinha
`eh_teste=True` e era excluído de propósito de **toda** lista/contagem/relatório/agenda
via `_filtro_pacientes_da_empresa()`, então nem aparecia como opção para agendar.

**Decisão do Silvan, com o alcance todo confirmado explicitamente antes de implementar**:
em vez de só destravar o agendamento, ele preferiu simplificar o conceito por completo —
o Paciente criado no cadastro do médico **deixou de ser "de teste"** e passou a ser um
**paciente real desde a criação**, com o nome/CPF de verdade do médico (antes usava
`"Paciente de teste (uso interno de <nome>)"` e, sem CPF, um sintético
`TESTE-IA-<id>`). Consequência aceita conscientemente pelo Silvan: esse paciente-médico
agora aparece na agenda de qualquer secretária/médico da própria clínica, conta em
relatórios de volume/faturamento, e pode ser importado por outra clínica pelo CPF dele —
exatamente como qualquer outro paciente.

**O que mudou, por arquivo:**
- `app/models.py`: nada removido (a coluna `Paciente.eh_teste` continua existindo no
  banco por compatibilidade com dados históricos), mas ninguém mais grava `True` nela.
- `app/routes_medico.py`:
  - `_filtro_pacientes_da_empresa()` não exclui mais `eh_teste=True` — é esse filtro
    central, usado em quase toda tela de paciente/relatório, que agora inclui o
    paciente-médico automaticamente em tudo.
  - `_paciente_teste_do_medico()` (nome da função mantido por não valer a pena renomear
    tudo) foi **reescrita** — usa o CPF real do médico como identidade (com um CPF
    temporário sintético `SEM-CPF-<usuario.id>` só para médicos cadastrados antes do CPF
    virar obrigatório, até eles completarem o cadastro). Nova classe de exceção
    `PacienteMedicoConflitanteError`: se o CPF do médico já pertencer a OUTRO paciente
    (outro `cadastrado_por_id`), a função **recusa** criar/atualizar em vez de tentar
    resolver sozinha — decisão deliberada de segurança, ver próximo parágrafo.
  - `pacientes_lista()` não busca mais o paciente-médico à parte — ele já vem incluído
    normalmente na lista principal.
- `app/routes_auth.py` (`cadastro()`): captura `PacienteMedicoConflitanteError` em
  silêncio (não bloqueia a criação da CONTA do médico, que já foi commitada antes desse
  ponto) — só pula a criação do paciente-espelho e a mensagem de boas-vindas nesse caso
  raro.
- `app/templates/medico/pacientes_lista.html`: removido o bloco visual que mostrava o
  paciente-de-teste separado com badge "Teste" (implementado horas antes, na mesma
  rodada) — ficou obsoleto, já que ele agora está na lista normal com botão "Detalhes"
  funcionando (antes dava 404 de propósito).
- `test_testar_ia_smoke.py`: os dois asserts que checavam `eh_teste=True` e "não aparece
  na lista normal" foram invertidos para checar o oposto (paciente real, aparece
  normalmente).

**Por que a trava manual (`PacienteMedicoConflitanteError`) em vez de resolver sozinho**:
o mecanismo ANTIGO (documentado na seção "5ª rodada" acima, antes desta revisão) sabia
que podia sobrescrever o CPF de um "órfão" de recadastro porque ele era descartável
(`eh_teste=True`, sem histórico que importasse). Isso deixou de ser seguro: o
paciente-médico agora pode ter agendamentos e mensagens reais associados. Se um médico
recadastrar a própria conta (apagar e criar de novo) e o CPF colidir com o
paciente-espelho do cadastro anterior (ou com qualquer outro paciente real), o sistema
agora **recusa** e não mexe em nada sozinho — cabe a alguém (Silvan/dono) investigar
manualmente esse CPF duplicado. Isso ainda não foi testado em um cenário de recadastro
de verdade nesta rodada — vale ficar de olho se acontecer em produção.

**Achado à parte, não corrigido nesta rodada** (registrado para investigação futura): o
`Agendamento` sintético que `_garantir_agendamento_teste()` cria/atualiza para a tela
"Testar IA" usa `data_hora=agora` (não nulo) e não tem qualquer filtro que o esconda de
`Agendamento.query` — ele já podia estar aparecendo na lista de "próximos agendamentos"
e na contagem do dashboard do médico mesmo ANTES desta mudança de arquitetura (esse
comportamento é independente do que foi decidido aqui, é mais uma consequência de como
"Testar IA" sempre funcionou). Vale confirmar visualmente em produção e decidir se isso é
aceitável ou se esse agendamento-âncora deveria ganhar algum sinalizador.

Testes rodados depois desta mudança (banco limpo + seed por arquivo, mesma disciplina de
sempre): `test_testar_ia_smoke.py` (reescrito), `test_medico_independente.py`,
`test_licenca_medico.py`, `test_painel_agenda_do_medico.py`, `test_meus_dados.py`,
`test_conta_unica_paciente.py`, `test_grupo_agendamento.py`, `test_paciente_da_empresa.py`,
`test_cadastro_global_importar_cpf.py`, `test_status_lista_pacientes.py`,
`test_reverter_status_cadastro_paciente.py`, `test_dono_conteudo_clinico.py` — todos
passando. `test_smoke.py` continua com a mesma falha pré-existente já documentada em
rodadas anteriores (`colonoscopia_id = colonoscopia.id`, linha ~1298), confirmada
independente desta mudança rodando com e sem o código novo (`git stash`). Também validado
manualmente (script ad-hoc, depois apagado) que um médico cadastrado com CPF consegue de
fato ter uma consulta agendada de verdade via `medico.agenda_novo` (POST) usando o
próprio cadastro de paciente — o pedido original do Silvan nesta rodada.

### Novo aviso de WhatsApp: paciente é avisado quando um agendamento é criado (mesma rodada)

Pedido do Silvan (2026-09-10, mesmo dia): quando um agendamento REAL é criado para um
paciente, ele deve receber um aviso pelo WhatsApp com a data/hora do exame, e ser
lembrado de que aquele número é o canal para tirar dúvidas sobre o preparo.

**Implementado**:
- Nova função `enviar_agendamento_criado_whatsapp(agendamento)` em
  `app/whatsapp_envio.py`, seguindo exatamente o mesmo padrão das outras (fail-open,
  usa `paciente.telefone`, template Meta próprio via nova variável de ambiente
  `WHATSAPP_META_TEMPLATE_AGENDAMENTO_CRIADO`, COM TRÊS variáveis: `{{1}}` nome do
  paciente, `{{2}}` nome do exame, `{{3}}` data/hora formatada
  (`dd/mm/aaaa às HH:MM`)).
- Texto sugerido do corpo do template (aprovado pelo Silvan nesta rodada, "curto e
  direto"): *"Olá {{1}}, tudo bem? Seu exame {{2}} foi agendado para {{3}}. Salve este
  número: é por aqui que você tira dúvidas sobre o preparo do exame."* — respeita a
  regra da Meta de não iniciar/terminar o corpo com uma variável. **Este template
  ainda não existe no WhatsApp Manager** — precisa ser criado e submetido para
  aprovação pelo Silvan, como os 3 anteriores (`boas_vindas_clinica`,
  `preparo_cadastrado_medico`, `resposta_duvida_paciente`), categoria "Marketing",
  idioma `pt_BR`.
- Chamada inserida em `app/routes_medico.py`, dentro de `medico.agenda_novo` (POST),
  logo após o `db.session.commit()` que cria o `Agendamento` — **confirmado por
  investigação nesta rodada que esse é o ÚNICO lugar do sistema onde um agendamento
  REAL é criado** (o grep por `Agendamento(` em `app/routes_grupo.py` não encontrou
  nenhuma criação lá, só um comentário; agendamento por Grupo passa pela mesma rota
  `medico.agenda_novo`, só varia a filial). Dispara **só na criação inicial** — não
  há hoje uma rota de edição/reagendamento que precisasse do mesmo aviso (decisão do
  Silvan: só criação, por enquanto).
- **Importante, para não confundir com o achado do parágrafo anterior**: este aviso
  NÃO é (e não deve ser) disparado para o agendamento sintético que
  `_garantir_agendamento_teste()` cria para a tela "Testar IA" — só o agendamento
  real feito por `medico.agenda_novo` chama `enviar_agendamento_criado_whatsapp`.
  Testado explicitamente (script ad-hoc com mock, depois apagado): a chamada acontece
  exatamente uma vez ao criar um agendamento real via POST, e zero vezes ao usar
  "Testar IA".

**Pendências para o Silvan**:
1. Criar o template `WHATSAPP_META_TEMPLATE_AGENDAMENTO_CRIADO` no WhatsApp Manager
   (nome sugerido: `agendamento_criado`) com o corpo de 3 variáveis acima, submeter
   para aprovação da Meta.
2. Depois de aprovado, configurar a variável de ambiente
   `WHATSAPP_META_TEMPLATE_AGENDAMENTO_CRIADO` em `media-dev` (Render → Environment)
   com o nome exato do template aprovado — sem essa variável configurada, o envio é
   só pulado (mesmo padrão de falha aberta de sempre), o agendamento em si nunca
   quebra por causa disso.
3. Validar em produção, depois de configurado, que a mensagem chega corretamente ao
   criar um agendamento de verdade.

Testes rodados depois desta mudança (banco limpo + seed): `test_testar_ia_smoke.py`
(sem alterações necessárias, continua passando) e um script ad-hoc
(`test_whatsapp_agendamento_criado.py`, criado e apagado nesta rodada) que usa
`unittest.mock.patch` para confirmar a chamada e seus parâmetros sem depender de
credenciais reais da Meta — todos passando. `test_smoke.py` continua com a mesma
falha pré-existente já documentada (`colonoscopia_id`, linha ~1298), não relacionada
a esta mudança.

### Atalho "Agendar exame" no menu lateral (mesma rodada)

Pedido do Silvan (2026-09-10, mesmo dia, com print do menu): faltava um jeito direto
de chegar em "Novo agendamento" pelo menu — antes só se chegava lá pelo botão dentro
do Painel ("agenda completa"). Adicionado item **"Agendar exame"** (ícone
`bi-calendar-plus`) em `app/templates/base.html`, logo abaixo de "Pacientes" e acima
de "Meus exames agendados" — aponta para a rota já existente `medico.agenda_novo`
(a mesma que ganhou o aviso de WhatsApp acima), sem nenhuma mudança de backend.
Segue o mesmo padrão dos outros itens (fica oculto no menu reduzido do celular do
médico, `oculto_no_celular_do_medico`, igual a "Pacientes" e "Exames & preparo").
Confirmado manualmente que o link aparece e aponta pra URL certa.

### "Meus dados", "Pacientes" e "Agendar exame" também no menu reduzido do celular (mesma rodada)

Pedido do Silvan (2026-09-10, mesmo dia): esses 3 itens também precisavam aparecer no
"portal do médico" — esclarecido via pergunta ao Silvan que isso significa o **menu
reduzido do celular do médico** (não a tela `portal/atendimento.html` em si), que hoje
só tinha "Portal de atendimento", "Testar IA nos meus preparos" e "Minha licença"
fixos (o resto do menu completo fica escondido nesse tamanho de tela via a classe
`oculto_no_celular_do_medico`, ver `app/templates/base.html`).

Adicionadas 3 cópias mobile (`d-md-none`) em `app/templates/base.html`, logo antes do
link mobile de "Portal de atendimento" já existente — mesmo padrão já usado ali:
- "Meus dados" (ícone `bi-person-circle`) → `auth.meus_dados`
- "Pacientes" (ícone `bi-journal-plus`) → `medico.pacientes_lista`
- "Agendar exame" (ícone `bi-calendar-plus`) → `medico.agenda_novo` (o mesmo item
  novo do menu completo, ver seção anterior)

Nenhuma mudança de backend/rota — só navegação. Confirmado manualmente (requisição
HTTP direta, sem abrir de fato num celular) que os 3 links aparecem duplicados no
HTML (uma cópia "completa" oculta em telas grandes, uma cópia mobile), o que é o
comportamento esperado desse padrão de menu.

### "Exames & preparo" no celular do médico leva a um aviso, não à tela real (mesma rodada)

Pedido do Silvan (2026-09-10, mesmo dia): "Exames & preparo" também devia aparecer no
menu reduzido do celular do médico, mas essa configuração é "mais delicada" - o
Silvan prefere que só seja feita pela versão web (computador), porque se o médico for
testar o sistema pelo celular ele poderia se perder tentando editar um preparo ali.

**Implementado**:
- Nova rota `medico.preparo_modelos_aviso_mobile`
  (`GET /equipe/preparo-modelos/aviso-mobile`, mesmos decoradores
  `@login_required @staff_required` do resto do arquivo) em
  `app/routes_medico.py`, logo antes de `medico.preparo_modelos_lista` - só
  renderiza um aviso, não bloqueia nem redireciona a rota real (que continua
  acessível normalmente pelo computador, e por link direto se precisar).
- Novo template `app/templates/medico/preparo_modelos_aviso_mobile.html`: um
  `alert alert-warning` com o texto *"A configuração de exames e preparos é mais
  delicada e só pode ser feita pela versão web (computador) do MedIA. Acesse pelo
  navegador do computador para cadastrar ou editar um preparo."* e um botão
  "Voltar ao Painel".
- Em `app/templates/base.html`, o item mobile (`d-md-none`) de "Exames & preparo"
  no menu reduzido do celular aponta para este NOVO aviso
  (`medico.preparo_modelos_aviso_mobile`), diferente do item "completo" (visível em
  tablet/desktop, oculto no celular via `oculto_no_celular_do_medico`), que continua
  apontando direto para `medico.preparo_modelos_lista` (a tela real).

Confirmado manualmente (requisição HTTP direta): a rota de aviso responde 200 com a
mensagem e o botão de volta, e os dois links de "Exames & preparo" no HTML apontam
para URLs diferentes (`/equipe/preparo-modelos` no item desktop,
`/equipe/preparo-modelos/aviso-mobile` no item mobile), como esperado.

### Licença anual, como alternativa à mensal (mesma rodada)

Pedido do Silvan (2026-09-10, mesmo dia): "Vamos colocar o valor da licença anual. O
usuário poderá optar em pagar anualmente ou mensalmente." Decisões tomadas com o
Silvan antes de implementar: (1) o valor anual é **independente** do mensal (não é
calculado como desconto - o dono digita o valor à parte); (2) ao pagar anual, o
calendário mensal continua existindo (12 registros), só que todos nascem "pago=True"
de uma vez, com o valor anual **rateado** em 12 (só para exibição, ver
`origem_anual`); (3) o **próprio médico** escolhe o ciclo (mensal/anual) na tela
"Minha licença", sem depender do dono; (4) a cobrança real no Mercado Pago é
**pagamento único** (Checkout Pro, mesmo mecanismo já usado no mensal - decisão
explícita do Silvan de NÃO usar assinatura recorrente/Preapproval por enquanto); (5)
o médico só pode trocar de ciclo (em qualquer direção) quando **não há mês anterior
ao vigente em aberto** - o mês vigente em aberto não atrapalha a troca; (6) ao
escolher "anual", o **próprio médico gera a cobrança na hora** (autoatendimento -
diferente do fluxo mensal, onde é sempre o dono quem gera a cobrança).

**Modelo de dados** (`app/models.py`, migração em `migrar_banco.py`):
- `PlataformaConfig.valor_licenca_anual_padrao` - valor anual padrão global,
  configurado pelo dono em Configurações (ao lado do valor mensal padrão já
  existente). Em branco até o dono preencher (nenhum médico pode escolher "anual"
  enquanto estiver vazio, nem globalmente nem individualmente).
- `Usuario.ciclo_licenca` (`"mensal"` por padrão, ou `"anual"`) e
  `Usuario.valor_licenca_anual` (mesmo padrão de "fotografia" de
  `valor_licenca_mensal` - nasce do padrão global no momento em que o médico escolhe
  "anual" pela primeira vez, dono pode reajustar depois em `/dono/usuarios`).
- `Usuario.pode_trocar_ciclo_licenca()` - True quando não há `LicencaPagamento` não
  pago com `mes` anterior ao mês vigente.
- `LicencaPagamento.origem_anual` (boolean) - True nos 12 meses gerados por um
  pagamento anual confirmado, para diferenciar de um mês pago avulso no calendário.
- `gerar_ciclo_anual_pago(usuario, mes_inicio, valor_anual)` - cria/atualiza os 12
  `LicencaPagamento` a partir de `mes_inicio` como pagos, com o valor rateado.

**Fluxo de cobrança** (mesmo padrão "fotografia"/pagamento único já usado no
mensal, ver `app/mercadopago_integration.py`):
- `criar_preferencia_pagamento_anual(pagamento, valor_anual)` - gera uma preferência
  Checkout Pro cobrando o valor anual de uma vez, usando o `LicencaPagamento` do
  PRIMEIRO mês do ciclo (o mês vigente) como "âncora" (mesmo registro que guarda
  `mp_preference_id`/`mp_init_point`) - `external_reference` no formato
  `"licenca_anual:<id>"` (diferente de `"licenca_pagamento:<id>"` do mensal), para o
  webhook (`app/routes_pagamentos_webhook.py`) saber que precisa chamar
  `gerar_ciclo_anual_pago` para os 12 meses ao confirmar, em vez de marcar só aquele
  registro como pago.
- Nova rota do médico: `POST /equipe/minha-licenca/ciclo`
  (`medico.licenca_escolher_ciclo`) - troca `ciclo_licenca` (com a checagem de
  `pode_trocar_ciclo_licenca()`) e, ao trocar PARA "anual", já chama
  `criar_preferencia_pagamento_anual` na hora (autoatendimento) - se o Mercado Pago
  não estiver configurado ou a chamada falhar, o ciclo ainda assim muda para
  "anual" (com aviso), e o link pode ser gerado depois reabrindo a tela, ou pelo
  dono (ver abaixo).
- Nova rota do dono (fallback, caso o médico não consiga gerar sozinho): `POST
  /dono/usuarios/<id>/licenca/pagamentos/<id>/cobrar-anual`
  (`dono.usuario_licenca_pagamento_cobrar_anual`), só habilitada quando
  `usuario.ciclo_licenca == "anual"`.

**Telas atualizadas**:
- `medico/minha_licenca.html`: mostra o ciclo atual, um botão para trocar (ou o
  motivo de não poder trocar, se houver pendência anterior), o valor anual/mensal
  conforme o ciclo, um card de "Pagamento anual pendente" com o botão "Pagar agora"
  quando aplicável, e um badge "Anual" nos meses do calendário pagos via ciclo
  anual.
- `dono/dashboard.html` (aba Configurações, print enviado pelo Silvan): novo campo
  "Valor anual padrão (R$)" ao lado do "Valor mensal padrão" já existente.
- `dono/usuarios.html`: novo campo de valor anual individual (ao lado do mensal) e
  um badge mostrando o ciclo de cada médico.
- `dono/usuario_licenca_pagamentos.html`: badge do ciclo do médico, botão "Gerar
  cobrança anual" (em vez do mensal) quando o médico está em ciclo anual, e o mesmo
  badge "Anual" nos meses pagos por essa via.

Testes rodados (scripts ad-hoc criados e apagados nesta rodada, sem depender de
credenciais reais da Meta/Mercado Pago): confirmam que (1) o médico consegue trocar
para anual e o valor nasce do padrão global; (2) sem Mercado Pago configurado, a
troca de ciclo acontece mesmo assim, só com aviso; (3) a troca de volta para mensal
funciona sem pendência; (4) `gerar_ciclo_anual_pago` (simulando o webhook) marca
corretamente os 12 meses como pagos, com `origem_anual=True` e o valor rateado; (5)
com um mês ANTERIOR ao vigente em aberto, a troca de ciclo é recusada com a
mensagem correta, e `ciclo_licenca` não muda. Também rodadas as suítes já
existentes de licença (`test_licenca_medico.py`,
`test_licenca_pagamento_valor_e_gateway.py`) - todas passando, sem regressão no
fluxo mensal. `test_smoke.py` continua com a mesma falha pré-existente já
documentada (`colonoscopia_id`, linha ~1298), não relacionada a esta mudança.

**Pendência para o Silvan**: configurar o valor anual padrão em
Configurações (aba do print que você mandou) antes que qualquer médico consiga ver
a opção de cobrança anual em "Minha licença" - hoje esse campo nasce vazio.

## Setup do Mercado Pago (teste) - BLOQUEADO em 2026-09-11, ver antes de mexer

Depois de implementada a feature de licença anual, o Silvan começou a configurar o
Mercado Pago de verdade (Checkout Pro, API de Preferências) para testar o fluxo de
ponta a ponta. Ao longo do processo foram encontrados e corrigidos vários problemas
de configuração (documentados abaixo, já resolvidos), mas o teste final ficou
travado num erro que parece ser do lado do Mercado Pago, não do código nem da
configuração local. Isto NÃO é um bug no código do MedIA - os testes automatizados
de `criar_preferencia_pagamento`/`criar_preferencia_pagamento_anual` e do webhook
continuam passando.

**Problemas já identificados e corrigidos nesta configuração:**
1. Erro "Uma das partes com as quais você está tentando efetuar o pagamento é de
   teste" ao pagar com o cartão de teste estando logado com a conta REAL do
   Mercado Pago (como comprador) ou usando um Access Token da conta principal do
   Silvan (mesmo gerado como "de teste") como vendedor. Correção: é preciso usar
   duas identidades de teste dedicadas, criadas em Developers → "Contas de teste" -
   uma "Seller Test User" (vendedor) e uma "Buyer Test User" (comprador). O token
   correto a usar no `MERCADOPAGO_ACCESS_TOKEN` do Render é o token de **produção**
   de uma aplicação criada estando LOGADO como a Seller Test User (a própria
   identidade já é sandbox, então usa-se a credencial "de produção" dela, não a
   "de teste" dela). O Mercado Pago criou automaticamente uma aplicação
   "TestApp-33941ca7" sob essa identidade.
2. Webhook da aplicação de teste veio com a URL incompleta
   (`https://media-dev.onrender.com/webhooks/`, faltando `/mercadopago`) e com o
   evento errado marcado ("Vinculação de aplicações"/"Alertas de fraude"; o correto
   para o código atual, que consulta `GET /v1/payments/{id}`, é **"Pagamentos
   (legacy)"** - NÃO "Order (Mercado Pago)", que é da API de Orders, incompatível
   com o código atual). Corrigido manualmente na tela de Webhooks da aplicação, e a
   "Assinatura secreta" gerada foi colocada em `MERCADOPAGO_WEBHOOK_SECRET` no
   Render.
3. Preference-id "grudado": reabrir uma aba/link de pagamento antigo depois de
   trocar o Access Token no Render continua usando a preferência antiga (criada
   com o token errado) - é preciso gerar uma cobrança NOVA depois de qualquer troca
   de credencial para obter um preference-id novo.
4. Cartão de teste usado: Visa `4235 6477 2802 5682`, validade `11/30`, CVV `123`,
   nome do titular `APRO` (simula aprovação), CPF `123.456.789-09`.

**Bloqueio atual (não resolvido):** com a Seller Test User configurada
corretamente e uma cobrança nova gerada, a tela de checkout ("Revise o seu
pagamento") sempre trava com o botão "Pagar" desabilitado (cinza), mesmo com todos
os dados do cartão preenchidos corretamente. O console do navegador (F12) mostra
sempre o mesmo erro, em qualquer situação testada:

```
Executing inline script violates the following Content Security Policy directive
'script-src ... strict-dynamic ... unsafe-eval https: unsafe-inline ...'. Note
that 'unsafe-inline' is ignored if either a hash or nonce value is present in
the source list. The action has been blocked.
```

Às vezes acompanhado de `requestStorageAccessFor: ... Permission denied.` Esse
erro parece ser a própria página do Mercado Pago servindo um header de CSP
incompatível com o script inline que ela mesma tenta executar para habilitar o
botão de pagamento - ou seja, um problema do lado da infraestrutura de teste do
Mercado Pago, não algo configurável por nós.

Hipóteses já testadas e DESCARTADAS como causa (todas reproduzem o mesmo erro):
- Cartão de teste diferente (testado Visa e outro bandeira/cartão salvo) - mesmo
  erro.
- Extensões do Chrome - removidas 3 extensões sinalizadas como malware pelo
  próprio Chrome (Downloader de vídeo definitivo, Tube Video Downloader, Video
  Downloader Pro) por segurança, mas não eram a causa (já estavam desativadas).
- Política de TI da INFLOR no Chrome (`chrome://policy`) - conferida, só tem
  regras inofensivas de rede local e restauração de abas, nada de proxy/CSP/DLP.
- Navegador: mesmo erro no Chrome E no Edge.
- Janela anônima/InPrivate vs. janela normal (perfil separado) - mesmo erro nos
  dois casos.
- Recriar a aplicação do zero na Seller Test User (nova aplicação, novo Access
  Token, novo Webhook) - mesmo erro persiste na aplicação nova.
- Testado também no celular (fora da rede da empresa, dados móveis) - lá o erro
  foi diferente ("é de teste", por ter tentado pagar como convidado sem logar como
  Buyer Test User primeiro), então esse teste específico não foi conclusivo sobre
  o CSP, mas todos os testes no computador (onde o login como Buyer Test User FOI
  feito corretamente antes de abrir o link) bateram nesse mesmo erro de CSP.

**Próximos passos sugeridos (ainda não feitos, decisão pausada pelo Silvan em
2026-09-11):**
- Abrir chamado no suporte oficial do Mercado Pago relatando esse erro de CSP
  específico (prints e texto do erro disponíveis).
- Como teste adicional (não feito ainda): tentar com um Access Token de PRODUÇÃO
  real (fora do sandbox/Seller Test User) para confirmar se o problema é exclusivo
  do ambiente de teste deles ou acontece também fora dele.
- Não há nada pendente do lado do código do MedIA para este bloqueio - é
  puramente uma questão de configuração/infra do lado do Mercado Pago a ser
  resolvida com o suporte deles antes de retomar o teste end-to-end.

## 6ª rodada (2026-09-11) — causa raiz da boas-vindas sem chegar, menu do WhatsApp simplificado, aviso ao médico

### Convenção nova a partir desta rodada: documentar toda alteração aqui

Pedido explícito do Silvan: a partir de agora, **toda alteração de código feita numa sessão precisa ser documentada neste arquivo** (`HANDOFF_CHAT.md`), não só quando a sessão está prestes a terminar - é assim que ele acompanha o que foi mudado e retoma o trabalho em qualquer sessão nova.

### Diagnóstico: mensagem de boas-vindas do WhatsApp não chegava

Silvan cadastrou um médico de teste (`/cadastro`, papel "Médico(a)") com telefone preenchido e a mensagem de boas-vindas (`enviar_boas_vindas_whatsapp`, template `boas_vindas_clinica`) não chegou. Investigação, sem nenhuma mudança de código (o problema era de configuração na Meta, não no MedIA):

- As variáveis de ambiente no Render (`WHATSAPP_META_ACCESS_TOKEN`, `WHATSAPP_META_PHONE_NUMBER_ID`, `WHATSAPP_META_TEMPLATE_BOAS_VINDAS` e as demais) já estavam todas configuradas corretamente.
- O código (`app/whatsapp_envio.py`) só registra log quando a chamada à Graph API FALHA (HTTP >= 400) ou quando falta configuração - quando a Meta aceita a chamada (retorna sucesso), a função não loga nada, o que sozinho não prova sucesso de entrega.
- **Causa raiz encontrada**: a conta comercial do WhatsApp ("Silmaroli", em developers.facebook.com → Media → Casos de uso → Conectar no WhatsApp → Personalizar → Etapa 2. Configuração da produção) não tinha **forma de pagamento cadastrada**. Mensagens **iniciadas pela empresa** (todo template fora da janela de 24h, que é o caso de TODOS os avisos proativos do MedIA - boas-vindas, preparo cadastrado, agendamento criado, resposta de pergunta fora da janela) exigem forma de pagamento configurada na conta - sem isso, a Meta aceita a chamada da API mas a mensagem não é entregue, sem gerar erro nenhum do lado do código.
- **Resolvido**: Silvan cadastrou um cartão em "Adicione informações de pagamento para enviar mensagens iniciadas pela empresa" (Billing Hub do Meta Business Manager). Item concluído com sucesso ("Pagamento adicionado").
- **Pendência**: falta repetir um cadastro de teste (ou usar o testador "Enviar mensagem" da própria tela da Meta) para confirmar que a mensagem chega agora. Se algum dia isso voltar a "sumir" sem erro no log, verificar de novo essa forma de pagamento antes de qualquer outra hipótese.

### WhatsApp do paciente: menu numerado removido, pergunta direta

Pedido do Silvan: depois que o paciente se identifica (CPF + data de nascimento) e o exame em foco é resolvido, ele não deve mais ver um menu ("1) Ver informações do preparo / 2) Fazer uma pergunta / 3) Trocar de exame") - deve poder digitar a pergunta diretamente.

**Implementado em `app/whatsapp_conversa.py`**:
- Removidas as opções "1) Ver informações do preparo" e "2) Fazer uma pergunta" (e toda a lógica de estado "aguardando_pergunta" que dependia de digitar "2" antes) - a mensagem mostrada depois de identificado (ou depois de escolher o exame, quando há mais de um) já convida direto: "Pode digitar sua pergunta sobre o preparo deste exame."
- "3) Trocar de exame" continua existindo (só aparece/funciona quando o paciente tem mais de um exame ativo), mas agora é acionado pela palavra **"trocar"** (case-insensitive) digitada como mensagem, em vez do número "3" de um menu que não existe mais. Quando há mais de um exame ativo, a mensagem de convite à pergunta menciona esse comando; com um só exame ativo, não menciona (não faz sentido).
- Coluna `ConversaWhatsapp.aguardando_pergunta` no banco não foi removida (evita migração), só deixou de ser usada no código - mesmo padrão já usado antes com `Paciente.eh_teste`.
- `MENSAGEM_PEDIR_PERGUNTA` (antiga mensagem de "digite sua pergunta, ou 0 para cancelar") foi removida; `_texto_pedir_pergunta()` (nova função) monta essa mensagem dinamicamente, com ou sem a menção ao comando "trocar".
- Testes reescritos: `test_whatsapp_identificacao.py` e `test_whatsapp_pergunta.py` (removidos os testes do menu antigo/"2"/"0" de cancelar; adicionados testes do convite direto e do comando "trocar", incluindo em maiúsculas).

**Pendência**: a suíte de testes não pôde ser executada nesta sessão porque o `device_bash` na máquina do Silvan estava indisponível (mesmo bug do Windows já visto antes) - os dois arquivos de teste foram reescritos "no papel" (lógica revisada com cuidado, sem rodar de verdade) e entregues direto na pasta via `device_commit_files`. **Rodar localmente antes de subir para produção**: `python test_whatsapp_identificacao.py`, `python test_whatsapp_pergunta.py`, e a suíte completa (`test_smoke.py`/`test_smoke_final.py`) para garantir que nada mais dependia do menu antigo.

### Aviso ao médico por WhatsApp quando chega pergunta nova (complementa o push do PWA)

Pedido do Silvan: quando um paciente faz uma pergunta (por WhatsApp ou pela área web), o médico responsável deve ser avisado também por WhatsApp, não só pelo push do PWA (Fatia 8, ver seção acima - que continua existindo, sem mudança).

**Implementado em `app/push_notificacoes.py`** (mesmo ponto central já chamado pelos três lugares que criam `PerguntaPendente`: `app/routes_paciente.py` x2 e `app/whatsapp_conversa.py`):
- `notificar_equipe_nova_pergunta(pergunta)` agora, além do push (se VAPID configurado), também chama a nova função `_notificar_whatsapp_medicos(pergunta, usuarios_ids)` - os dois canais são independentes (um falhar ou estar desconfigurado não afeta o outro).
- Reaproveita a MESMA lista de destinatários já calculada por `_usuarios_para_notificar` (só o(s) médico(s) responsável(is) pelo exame da pergunta, ou médicos com `perm_pacientes` para pergunta geral - a mesma regra que decide o que cada médico vê em `/equipe/perguntas`).
- **Decisão explícita do Silvan**: por ora, texto livre (sem template aprovado na Meta) - `enviar_mensagem_whatsapp(medico.telefone, texto=...)` sem `content_variables`, forçando o caminho de texto livre. Isso significa que esse aviso só chega de fato se o médico tiver mandado mensagem para o número da clínica nas últimas 24h - fora dessa janela (o caso mais comum), a Meta recusa e o aviso simplesmente não sai, sem quebrar nada (mesmo padrão de "falha aberta" do resto do projeto).
- **Se isso se mostrar pouco confiável na prática**: o próximo passo é criar um template aprovado dedicado (mesmo padrão dos outros avisos: boas-vindas, preparo cadastrado, agendamento criado), o que exige submeter à Meta e esperar aprovação antes de funcionar de forma confiável independente da janela de 24h.

### Correção: pergunta repetida (já cadastrada na base de conhecimento) ia pra IA de novo, em vez de responder automático

Silvan reparou que, mesmo com a pergunta já cadastrada na base de conhecimento (FAQ) - inclusive uma essencialmente idêntica a uma já respondida antes -, o sistema chamava a IA de novo a cada vez, e a pergunta ficava pendente de aprovação do médico de novo, em vez de devolver a resposta já cadastrada direto pro paciente.

**Causa**: por decisão de design de uma rodada anterior (documentada em comentário no próprio código), a IA era **sempre** consultada primeiro - a base de conhecimento (FAQ) e as respostas prontas de alimento/medicamento só entravam como alternativa quando a IA não estava configurada ou não respondia. Isso significava que toda pergunta ia pra IA, mesmo repetindo uma já respondida, gastando uma chamada de IA à toa e exigindo aprovação do médico de novo.

**Corrigido** (inverte a ordem - pedido explícito do Silvan) em `app/routes_paciente.py` (`chat()`, POST) e `app/whatsapp_conversa.py` (`_responder_pergunta`): agora a base de conhecimento (`app.faq_engine.buscar_resposta`) e as respostas prontas de alimento/medicamento são consultadas **primeiro**; só quando nada bate é que a IA é consultada (com a resposta dela continuando pendente de aprovação do médico, como antes - isso não mudou). Vale lembrar uma regra que já existia em `app.faq_engine.buscar_resposta` e continua valendo: FAQs geradas pela própria IA só são reaproveitadas por **igualdade exata** (depois de normalizar acentuação/pontuação/maiúsculas) - nunca por semelhança aproximada, porque a resposta da IA costuma depender de um detalhe específico da pergunta original (ex.: "gatorade de uva" vs "gatorade de limão"). FAQs cadastradas manualmente pela equipe continuam usando a comparação por semelhança normal.

- Testes: `test_whatsapp_pergunta.py` ganhou dois testes novos ao final, usando `unittest.mock.patch` em `app.whatsapp_conversa.responder_com_ia` para confirmar o ordenamento na prática (a IA não é chamada quando a FAQ já responde; a IA é chamada quando nada bate) - sem o mock não dava pra provar isso, já que a IA sempre retorna `None` no ambiente de teste (sem `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`) independente da ordem.
- **Pendência**: só o lado do WhatsApp ganhou esse teste de regressão com mock; o equivalente para `app/routes_paciente.py:chat()` (área web) não foi testado automaticamente nesta rodada (exigiria simular a rota HTTP completa, com login de paciente) - a mudança de código lá é estruturalmente a mesma, mas vale confirmar manualmente ou pedir um teste dedicado numa próxima rodada.
- Como na correção anterior desta rodada, a suíte completa não pôde ser executada aqui (`device_bash` continua indisponível na máquina do Silvan) - rodar localmente antes de subir pra produção: `python test_whatsapp_pergunta.py`, `python test_whatsapp_identificacao.py`, e a suíte completa (`test_smoke.py`/`test_smoke_final.py`).

### Correção: conversa de WhatsApp "ficava logada" no primeiro exame quando um segundo exame surgia depois

Silvan reportou (com prints da conversa de WhatsApp e do painel "Meu painel", mostrando 2 exames agendados) que o WhatsApp sempre continuava se referindo ao primeiro exame resolvido na identificação, mesmo já tendo um segundo exame agendado. Hipótese dele para resolver: pedir CPF + data de nascimento de novo a cada mensagem.

**Causa**: `ConversaWhatsapp.agendamento_id` é fixado uma vez (na identificação, ou depois do comando "trocar") e não é limpo automaticamente. Isso por si só é o comportamento correto e intencional (é o que dá a conveniência de não precisar se identificar de novo a cada mensagem) - o problema real é que, quando um SEGUNDO exame passa a existir DEPOIS que a conversa já tinha fixado o primeiro (sessão ainda não expirou), o único aviso de que agora existe outro exame era um lembrete genérico ("Ou digite *trocar* para mudar de exame"), fácil de não notar - nada nomeava explicitamente qual era esse outro exame.

**Descartada a sugestão do próprio Silvan** ("sempre pedir CPF e data de nascimento") - isso destruiria a conveniência da sessão já identificada (o paciente teria que se reidentificar em toda mensagem) só para resolver um problema de aviso pouco visível.

**Corrigido em `app/whatsapp_conversa.py`** (`_texto_pedir_pergunta`, chamada tanto depois de escolher/fixar um exame quanto depois de responder qualquer pergunta): agora, sempre que houver mais de um exame ativo, a mensagem **nomeia explicitamente** o(s) outro(s) exame(s) ("Você também tem agendado: Teste de Hidrogênio / Metano Expirado — 16/09/2026. Digite *trocar* para falar sobre outro exame."), em vez de só mencionar o comando genericamente. A lista de exames ativos é recalculada do banco a cada mensagem (`_agendamentos_ativos`, já existia assim) - então um exame novo aparece automaticamente nomeado na primeira resposta depois de ser criado, sem precisar de nenhuma ação extra do paciente nem reidentificação.

- Testes: `test_whatsapp_identificacao.py` ganhou um novo bloco (item "4b") que identifica o paciente com um só exame ativo, cria um segundo agendamento DEPOIS (simulando exatamente o cenário relatado), e confirma que a próxima pergunta (a) não volta a pedir CPF, (b) nomeia o novo exame explicitamente com sua data, e (c) mantém o `agendamento_id` fixado no exame antigo até o paciente digitar "trocar".
- **Pendência**: mesma limitação de sempre - `device_bash` continua indisponível na máquina do Silvan nesta rodada, então o teste foi escrito "no papel" (revisado com cuidado, sem execução real). Rodar localmente antes de subir pra produção: `python test_whatsapp_identificacao.py` (e a suíte completa, `test_whatsapp_pergunta.py` / `test_smoke.py` / `test_smoke_final.py`).

### Texto da mensagem de boas-vindas reescrito (diferente para médico e para paciente real)

Pedido do Silvan: trocar o texto da mensagem de boas-vindas que o médico recebe no próprio cadastro (print da conversa + print da tela de edição do template na Meta) para um texto mais detalhado, explicando o fluxo de teste (cadastrar preparo via PDF, criar agendamento para o paciente de teste). Esclarecido antes de implementar: esse texto novo é específico do médico (fala de "seus pacientes", "paciente de teste") e não faz sentido para um paciente de verdade - a solução manteve os dois públicos com o MESMO template, só variando o trecho da 2ª variável.

**Implementado em `app/whatsapp_envio.py`** (`enviar_boas_vindas_whatsapp`) - texto refinado pelo Silvan numa segunda passada na mesma rodada (com quebras de linha e lista numerada):
- O corpo fixo do template (que precisa ser reeditado/reaprovado na Meta) muda de "Este é o WhatsApp da clínica - salve este número..." para (`\n` = quebra de linha real, a Meta aceita isso no corpo do template):
  ```
  Olá, {{1}}! Tudo bem?
  Este é o WhatsApp da MedIA — {{2}}

  Qualquer dúvida, estamos por aqui!
  ```
- `aviso_extra` (a 2ª variável) passou a ter um padrão não-vazio (`_AVISO_PADRAO_PACIENTE` = "Salve este número para tirar dúvidas sobre o preparo dos seus exames.") em vez de mandar um espaço em branco - antes esse texto ficava FIXO no corpo, agora ele mora na variável para o caso do paciente real.
- Chamada do lado do médico (`app/routes_auth.py:cadastro()`) atualizada com o texto final pedido pelo Silvan (com lista numerada, e depois ajustada de novo na mesma rodada para nomear os menus certos - "Exames & preparo" e "Agendar exame", exatamente como aparecem na barra lateral, ver `app/templates/base.html`):
  ```
  seus pacientes irão conversar com este número pelo WhatsApp.
  O MedIA já criou um paciente de teste no sistema com os dados necessários para você realizar os testes. Agora você deverá:

  1. Cadastrar um modelo de preparo, importando um PDF no menu "Exames & preparo".
  2. Criar um agendamento para o seu paciente de teste no menu "Agendar exame".
  ```
  Mensagem final vista pelo médico (juntando corpo fixo + esse trecho): "Olá, Silvan Oliveira! Tudo bem?\nEste é o WhatsApp da MedIA — seus pacientes irão conversar com este número pelo WhatsApp.\nO MedIA já criou um paciente de teste no sistema com os dados necessários para você realizar os testes. Agora você deverá:\n\n1. Cadastrar um modelo de preparo, importando um PDF no menu \"Exames & preparo\".\n2. Criar um agendamento para o seu paciente de teste no menu \"Agendar exame\".\n\nQualquer dúvida, estamos por aqui!"
- Chamada do lado do paciente real (`app/routes_auth.py:cadastro_paciente_global`, sem passar `aviso_extra`) não precisou mudar - cai automaticamente no padrão novo.

**Pendência para o Silvan**: editar o template `boas_vindas_clinica` no WhatsApp Manager da Meta (mesma tela do print) com o novo corpo acima (com as quebras de linha), e reenviar para aprovação (edição de corpo de template sempre exige reaprovação). Amostras de variável sugeridas para a Meta analisar: `{{1}}` = "João Silva", `{{2}}` = "Salve este número para tirar dúvidas sobre o preparo dos seus exames." (o texto do paciente, mais neutro para a análise da Meta do que o do médico).

- Sem teste automatizado dedicado nesta rodada (é só uma troca de texto/parâmetro, sem lógica condicional nova para cobrir) - confirmar visualmente depois que o template for reaprovado: cadastro de médico com telefone deve receber o texto novo (com a lista numerada do fluxo de teste), cadastro de paciente real deve receber o texto padrão (sem falar de teste/PDF/agendamento).
- Template enviado para análise na Meta nesta rodada (via `boas_vindas_clinica`, corpo/amostras conferidos junto com o Silvan antes do envio).

### Tela "Novo modelo de preparo": só aparece a opção de importar por PDF (Excel escondido)

Pedido do Silvan (print da tela): o botão de importação mostrava "Importar de um Excel ou PDF" e o campo de arquivo aceitava `.xlsx` ou `.pdf` - ele quer que só apareça a opção de PDF.

**Implementado em `app/templates/medico/preparo_modelo_form.html`** - mudança só de front-end/apresentação, nada de backend:
- Texto do botão que abre o popup: "Importar de um Excel ou PDF" → "Importar de um PDF".
- Título do popup e o parágrafo de instrução dentro dele: removida toda menção a planilha/`.xlsx`/colunas - agora só fala do PDF (lido direto por IA).
- `accept` do campo de arquivo (`input type="file"`): de `".xlsx,.pdf"` para só `".pdf"` - o seletor de arquivo do navegador não deixa mais escolher uma planilha nessa tela.
- **Nada mudou no backend** (`app.routes_medico.preparo_modelos_importar_xlsx` continua aceitando `.xlsx` normalmente, se algum dia precisar reativar a opção ou alguém chamar a rota diretamente) - é só a tela que deixou de oferecer essa opção visualmente, a pedido do Silvan.
- O checkbox "Usar IA para extrair" (que só faz sentido para PDF) já tinha lógica de JS que o esconde quando o arquivo não é `.pdf` - como agora só PDF é aceito, ele passa a aparecer sempre, sem precisar de nenhuma mudança nesse trecho.

- Sem teste automatizado (é só texto/atributo HTML) - confirmar visualmente que o botão/popup mostram só a opção de PDF, e que a importação de PDF continua funcionando normalmente (comportamento inalterado, só a apresentação mudou).

### Importar PDF de preparo também pelo celular (menu reduzido do médico)

Pedido do Silvan: hoje "Exames & preparo" no menu reduzido do celular só mostra um aviso dizendo que a configuração deve ser feita pelo computador (decisão da rodada anterior, ver seção "'Exames & preparo' no celular do médico leva a um aviso, não à tela real" acima) - ele quer permitir importar um PDF direto por ali também. Motivo prático: a própria mensagem de boas-vindas do WhatsApp (ver seção "Texto da mensagem de boas-vindas..." acima) já orienta o médico a "cadastrar um modelo de preparo importando um PDF" - e ele lê essa mensagem no celular, não seguido de estar no computador.

**Esclarecido antes de implementar** (pergunta feita ao Silvan): importar um PDF sempre cai na MESMA tela de revisão completa usada no cadastro manual (todas as abas: cortes, medicamentos, alimentos etc.) - não existe uma tela de revisão separada só pra PDF. Confirmado: manter o aviso na tela mobile (o cadastro 100% manual do zero continua desencorajado no celular), mas adicionar um botão "Importar de um PDF" nela - ao usar, o médico é levado pra tela de revisão completa mesmo assim (inevitável), só que chegando lá com um rascunho já preenchido pelo PDF, bem mais simples do que preencher tudo à mão no celular.

**Implementado:**
- **Novo partial `app/templates/medico/_importar_preparo_pdf.html`**: o popup "Importar de um PDF" (modal + campo de arquivo) e toda a lógica de JS de extração (streaming de progresso da IA, fallback de extração local com pdfjs, tratamento de erro de rede/timeout - ~230 linhas) foram extraídos de `preparo_modelo_form.html` para este partial, pra poder ser incluído em mais de uma tela sem duplicar o JS. Quem inclui o partial só precisa ter, antes dele, um botão que abra o modal (`data-bs-toggle="modal" data-bs-target="#modal-importar-xlsx"`).
- **`app/templates/medico/preparo_modelo_form.html`**: o popup e o JS de importação foram substituídos por `{% include "medico/_importar_preparo_pdf.html" %}` (dentro do mesmo `{% if not modelo %}` de antes) - comportamento na tela do computador **inalterado**, só deixou de duplicar o código.
- **`app/templates/medico/preparo_modelos_aviso_mobile.html`**: ganhou um botão "Importar de um PDF" (mesmo estilo do botão já usado em `preparo_modelo_form.html`) e o `{% include %}` do mesmo partial. O texto do aviso foi ajustado para deixar claro que só o cadastro manual do zero continua exigindo o computador ("Cadastrar ou editar um preparo do zero é mais delicado... Mas se você já tem o preparo em um PDF, pode importá-lo direto por aqui.").
- Ao concluir a extração com sucesso, o próprio JS do partial substitui a página inteira (`document.write`) pelo HTML que o servidor devolve (a tela de revisão) - funciona independente de qual tela (computador ou aviso mobile) chamou a importação, já que é a página toda que troca, não um trecho dela.
- Nenhuma mudança de backend/rota (`medico.preparo_modelos_importar_xlsx` continua exatamente igual) - é só uma mudança de onde, no front-end, o botão de importar aparece.

**Pendência**: sem teste automatizado dedicado (é extração de template/JS, sem lógica de servidor nova) - confirmar visualmente pelo celular: o botão "Importar de um PDF" aparece na tela de aviso, abre o popup, a extração funciona e leva pra tela de revisão completa; e, pelo computador, confirmar que `preparo_modelo_form.html` continua funcionando exatamente como antes (nada deveria ter mudado ali, é só reaproveitamento de código).

### Texto da mensagem "preparo cadastrado" também reescrito (orienta a cadastrar um agendamento, não mais a testar a IA)

Pedido do Silvan: agora que o fluxo do médico é cadastrar preparo → cadastrar agendamento (ver mudanças da mensagem de boas-vindas, acima), a mensagem que ele recebe assim que cadastra o modelo de preparo (`preparo_cadastrado_medico`) também precisava mudar - antes orientava a "testar o assistente de IA fazendo uma pergunta de teste em Testar IA nos meus preparos", agora deve orientar a cadastrar um agendamento para o paciente de teste.

**Implementado em `app/whatsapp_envio.py`** (`enviar_preparo_cadastrado_whatsapp`):
- Novo corpo aprovado: **"Boa notícia, {{1}}! Seu modelo de preparo foi cadastrado com sucesso.\nAgora você já pode continuar os seus testes: cadastre um agendamento para o paciente de teste (criado com o seu nome)."** - continua com uma única variável ({{1}} = nome do médico), só o texto fixo mudou.
- Sem mudança de assinatura/chamada - `app.routes_medico.preparo_modelos_novo` continua chamando igual, nada de código fora deste arquivo precisou mudar.

**Pendência para o Silvan**: editar o template `preparo_cadastrado_medico` no WhatsApp Manager da Meta com o novo corpo acima (com a quebra de linha) e reenviar para aprovação - mesmo procedimento já feito para `boas_vindas_clinica` nesta mesma rodada.

- Sem teste automatizado dedicado (é só troca de texto, sem lógica condicional nova) - confirmar visualmente depois que o template for reaprovado.

### Aviso de "pergunta nova" ao médico agora inclui link clicável direto para a tela

Pedido do Silvan (a partir dos prints do aviso "Nova pergunta de Silvan Oliveira: ... Responda em /equipe/perguntas."): incluir o link da APP e do menu direto nesse aviso de WhatsApp, em vez de só o caminho relativo (que não é clicável fora do site).

**Implementado em `app/push_notificacoes.py`**:
- Nova função `_link_perguntas()`: monta `f"{APP_URL_PUBLICA}/equipe/perguntas"` quando a env var opcional `APP_URL_PUBLICA` estiver configurada (ex.: `https://dev.media.med.br`); sem ela, cai no caminho relativo de sempre (`/equipe/perguntas`, sem link clicável) - mesmo padrão de falha aberta do resto do módulo.
- `_notificar_whatsapp_medicos` agora usa `_link_perguntas()` no texto, em vez do caminho relativo fixo.
- **Decisão técnica**: não usei `url_for(_external=True)` porque o projeto não tem `ProxyFix`/`SERVER_NAME`/`PREFERRED_URL_SCHEME` configurado (confirmado por busca no código) - atrás do proxy do Render, isso poderia gerar um link `http://` em vez de `https://`. Uma env var explícita evita esse risco.

**Pendência para o Silvan**: configurar a nova variável de ambiente `APP_URL_PUBLICA=https://dev.media.med.br` (ou o domínio correto do ambiente) no Render, para o link passar a aparecer no aviso - documentado também em `.env.example`. Sem essa variável configurada, o aviso continua chegando igual, só sem o link clicável (nada quebra).

- Sem teste automatizado dedicado (é só montagem de texto, sem lógica condicional nova) - confirmar visualmente depois que a env var for configurada em produção.

### Correção: resposta automática de alimento/medicamento (a partir do preparo) também passa a exigir aprovação do médico

Silvan reparou (com prints de uma conversa de WhatsApp em que "posso comer mandioca?" foi respondido na hora) que a resposta pronta de alimento/medicamento (calculada direto do preparo cadastrado, ver `app/faq_engine.py`) ia direto pro paciente sem passar por aprovação nenhuma - diferente da IA, que sempre fica pendente até o médico revisar. Explicação inicial dada a ele: essa resposta não vem da tela "Base de conhecimento da IA" (tabela `FaqItem`, que realmente estava vazia) e sim de um mecanismo separado que lê a lista de alimentos/medicamentos do preparo - não é vazamento de dados entre médicos, é o preparo do próprio médico. Mesmo assim, pedido explícito do Silvan: **por segurança, toda resposta - mesmo vindo certeira do preparo - deve passar por aprovação do médico na primeira vez; só pula a aprovação quando a pergunta já está na base de conhecimento (FAQ) dele**, ou seja, quando já foi aprovada antes (por ele ou por outra pergunta idêntica).

**Implementado** em `app/whatsapp_conversa.py` (`_responder_pergunta`) e `app/routes_paciente.py` (`chat()`, POST) - mesma mudança nos dois lugares (o do WhatsApp e o da área web do paciente):
- Antes: `faq_item` → responde direto; `resposta_alimento`/`resposta_medicamento` → também respondia direto; só a IA ficava pendente.
- Agora: só `faq_item` responde direto (é a única fonte já revisada por um humano antes). `resposta_alimento`/`resposta_medicamento` agora criam uma `PerguntaPendente` com `status="aguardando_aprovacao"` e a resposta pronta já preenchida em `resposta_sugerida_ia` (mesmo campo que a IA usa) - o paciente recebe a mensagem padrão de "recebemos sua pergunta, aguarde", e só depois que o médico aprovar (tela `medico/perguntas.html`, sem nenhuma mudança de fluxo aí) é que a resposta some para o paciente e a pergunta entra na FAQ - a partir daí, a mesma pergunta responde direto (pelo caminho `faq_item`).
- `ChatMensagem.origem` ganhou dois valores novos: `"alimento_aguard"` e `"medicamento_aguard"` (nomes encurtados de propósito - a coluna é `String(20)` e "medicamento_aguardando" por extenso não caberia). Valores antigos `"alimento"`/`"medicamento"` continuam existindo em registros anteriores a esta mudança (comentário atualizado em `app/models.py`).
- `app/templates/paciente/chat.html`: a mensagem de "já tenho uma resposta, mas precisa ser revisada pelo médico" (antes só para `origem == 'ia_aguardando'`) agora também vale para `alimento_aguard`/`medicamento_aguard` - sem isso o paciente veria a mensagem genérica de "não sei responder", que ficaria errada nesse caso (o sistema JÁ sabe a resposta, só está aguardando aprovação).
- `app/templates/medico/perguntas.html`: textos ajustados para não dizer mais que a seção "aguardando aprovação" é sempre da IA (agora pode ser do preparo também) - adicionado um badge "Automática (preparo cadastrado)" nos itens que não têm nenhuma resposta bruta de IA preenchida (`resposta_bruta_claude/chatgpt/gemini` todas vazias), e o rótulo do campo de edição virou genérico ("Resposta sugerida" em vez de "Rascunho final").

**Testes**: `test_whatsapp_pergunta.py` - o "Caminho 2" (pergunta sobre alimento cadastrado) foi reescrito: agora confirma que a pergunta NÃO é respondida na hora (mensagem de "encaminhada"), que cria uma `PerguntaPendente` com `status="aguardando_aprovacao"` e `resposta_sugerida_ia` já preenchida com o texto certo, e que o `ChatMensagem` fica com `origem="alimento_aguard"` e sem resposta ainda - depois marca essa pendente como "respondida" (simulando a aprovação do médico) só para continuar testando os cenários seguintes na mesma conversa, sem o aviso de "aguardando resposta" no meio.

- **Pendência**: o equivalente em `app/routes_paciente.py:chat()` (área web) não ganhou teste automatizado dedicado nesta rodada (mesma lacuna já registrada antes para esse arquivo) - a mudança é estruturalmente igual à do WhatsApp, mas vale confirmar manualmente. Rodar antes de subir pra produção: `python test_whatsapp_pergunta.py`, `python test_whatsapp_identificacao.py`, e a suíte completa (`test_smoke.py`/`test_smoke_final.py`) - `device_bash` continua indisponível na máquina do Silvan, então nada disso foi executado aqui.

### WhatsApp: paciente precisa digitar "1" antes de cada pergunta (evita que uma saudação solta seja tratada como pergunta nova)

Silvan reportou (com prints de uma conversa real) um efeito colateral do convite direto a perguntar (implementado mais acima nesta mesma rodada, quando o menu numerado foi removido): sem nenhuma barreira, digitar qualquer coisa - até um simples "Oi" - era tratado como pergunta nova e encaminhado pra equipe (chegando a avisar o médico por WhatsApp), poluindo a fila de `/equipe/perguntas` sem necessidade. Sugestão do próprio Silvan, que foi a implementada: acrescentar "Digite 1 para fazer uma nova pergunta" ao final do convite, forçando esse passo antes de aceitar qualquer texto como pergunta.

**Implementado em `app/whatsapp_conversa.py`** (`processar_mensagem`, na parte depois da identificação/escolha de exame):
- Reaproveitado o campo `ConversaWhatsapp.aguardando_pergunta` (`Boolean`, já existia no banco - tinha sido criado pro antigo menu numerado "2) Fazer uma pergunta", removido horas antes nesta mesma rodada, e ficou sem uso até agora) - **nenhuma migração de banco foi necessária**.
- `_texto_pedir_pergunta` (o convite mostrado depois de identificar/escolher exame/responder uma pergunta) trocou o texto de "Pode digitar sua pergunta..." para **"Digite *1* para fazer uma pergunta sobre o preparo deste exame."**.
- Nova constante `MENSAGEM_DIGITE_PERGUNTA` ("Pode digitar sua pergunta sobre o preparo deste exame.") - mostrada só depois que o paciente digita "1", confirmando que a PRÓXIMA mensagem será tratada como o texto da pergunta.
- Fluxo: paciente digita "1" → `aguardando_pergunta = True`, mostra `MENSAGEM_DIGITE_PERGUNTA`. Próxima mensagem → tratada como a pergunta em si (mesmo caminho de sempre: FAQ/alimento/medicamento/IA, ver seções acima), e `aguardando_pergunta` volta a `False`. Qualquer mensagem recebida SEM ter digitado "1" antes (saudação, comentário, etc.) só repete o convite - nunca cria `PerguntaPendente` nem `ChatMensagem`, nunca avisa a equipe. O comando "trocar" continua funcionando em qualquer momento (mesmo já tendo digitado "1"), e zera `aguardando_pergunta`.
- `MENSAGEM_PERGUNTA_VAZIA` simplificada para "Não recebi nenhum texto." (o texto anterior, "...Pode digitar sua pergunta sobre o preparo.", ficaria ambíguo agora que existe o passo do "1").
- `app/models.py`: comentário do campo `ConversaWhatsapp.aguardando_pergunta` atualizado pra descrever o novo uso (documentando também a origem/reaproveitamento do campo).

**Testes**: `test_whatsapp_identificacao.py` ganhou dois casos novos (4a e 4b) confirmando que uma saudação solta ("Oi") sem ter digitado "1" só repete o convite sem ativar `aguardando_pergunta`, e que digitar "1" ativa esse estado corretamente - e os testes existentes que checavam o texto antigo do convite ("Pode digitar sua pergunta") foram ajustados pra checar o texto novo ("Digite *1*"). `test_whatsapp_pergunta.py` (todos os caminhos que enviam uma pergunta de verdade) ganhou um `processar_mensagem(telefone, "1")` antes de cada pergunta enviada, pra continuar batendo com o fluxo real.

- **Pendência**: mesma de sempre - rodar `python test_whatsapp_identificacao.py`, `python test_whatsapp_pergunta.py` e a suíte completa antes de subir pra produção (não executados aqui, `device_bash` indisponível). Vale confirmar visualmente com uma conversa real de WhatsApp depois do deploy, já que esse fluxo teve várias idas e voltas no mesmo dia.

### Link do aviso de "pergunta nova" ajustado: /equipe/portal, domínio do Render como padrão

Depois de ver o aviso de WhatsApp em uso (print de uma conversa real), Silvan pediu para trocar o destino do link: em vez de `/equipe/perguntas` (na URL configurada em `APP_URL_PUBLICA`, ex. `https://dev.media.med.br`), o link deve apontar para `https://media-dev.onrender.com/equipe/portal`.

**Implementado em `app/push_notificacoes.py`** (`_link_perguntas`):
- Caminho trocado de `/equipe/perguntas` para `/equipe/portal`.
- A env var `APP_URL_PUBLICA` continua opcional e tem prioridade quando configurada, mas agora o padrão (sem ela) deixou de ser um caminho relativo sem link clicável - passou a ser o domínio de produção do Render (`https://media-dev.onrender.com`), então o aviso sempre chega com um link completo, mesmo sem nenhuma variável configurada.
- `.env.example` atualizado com a mesma explicação.

- Sem teste automatizado dedicado (é só montagem de texto/URL) - confirmar visualmente que o novo aviso de WhatsApp chega com `https://media-dev.onrender.com/equipe/portal` (ou o valor de `APP_URL_PUBLICA`, se configurada) e que o link abre a tela certa.

### Reorganização do menu lateral: "Portal de atendimento rápido", "Últimas respondidas" e "Base de conhecimento" saem do submenu "Médico + IA" - e o próprio menu "Médico + IA" é removido

Pedido do Silvan, em três mensagens ao longo do mesmo dia (com prints do menu lateral cada vez): primeiro tirar "Portal de atendimento rápido" e "Últimas respondidas" de dentro do submenu colapsável "Médico + IA" e colocá-los como itens diretos do menu, logo abaixo de "Meus exames agendados"; depois fazer o mesmo com "Base de conhecimento" (indo pra baixo de "Últimas respondidas"); e por fim, com o submenu já reduzido a só "Perguntas dos pacientes" e "Testar IA nos meus preparos", pediu pra **remover o menu "Médico + IA" por completo, com todos os seus submenus**.

**Implementado em `app/templates/base.html`** (estado final):
- Itens diretos do menu, na ordem: Meus exames agendados → **Portal de atendimento rápido** → **Últimas respondidas** → **Base de conhecimento** → Grupos de trabalho → Minha licença. O submenu "Médico + IA" (toggle colapsável + `#menuIA`) foi removido inteiramente, junto com a variável Jinja `grupo_ia` (usada só para manter esse submenu expandido) e a cópia mobile ("d-md-none") de "Testar IA nos meus preparos" que existia só para compensar o submenu ficar oculto no celular do médico.
- **As rotas não foram removidas** (mesmo padrão já usado em outros itens tirados do menu nesta mesma sessão, ver equipe_lista/filiais_lista) - `medico.perguntas_pendentes`/`perguntas_responder` e `medico.testar_ia` continuam funcionando normalmente por URL direta, só sem nenhum link no menu lateral agora. Quem precisa responder pergunta pendente no dia a dia tem "Portal de atendimento rápido" (mesma lista, mesmo formulário de resposta) ou o link do próprio aviso de WhatsApp/push.
- "Portal de atendimento rápido" continua sendo um dos 2 itens do menu reduzido do celular do médico (junto com "Meus exames agendados") - não leva a classe `oculto_no_celular_do_medico`. "Últimas respondidas" e "Base de conhecimento" continuam só em tablet/desktop (levam essa classe).

- Sem teste automatizado dedicado (é só reorganização de menu/HTML) - confirmar visualmente a nova ordem do menu, que o "Médico + IA" não aparece mais em nenhum tamanho de tela, e que `medico.perguntas_pendentes`/`medico.testar_ia` ainda abrem normalmente por URL direta.

### Encerramento automático da conversa de WhatsApp por inatividade (5 minutos)

Pedido do Silvan: hoje, depois que o paciente é identificado (CPF + data de nascimento) por WhatsApp, a conversa nunca é encerrada de verdade - só existia o `ConversaWhatsapp.expirada()` (4 horas), que é PASSIVO: só reseta a identificação, em silêncio, na PRÓXIMA mensagem que chegar, sem avisar nada e sem nunca apagar a linha se ninguém escrever de novo. Pedido: um timer de 5 minutos que encerra a conversa automaticamente, mandando uma mensagem avisando que está sendo encerrada - e sem precisar de template Meta aprovado para esse aviso.

**Por que não precisa de template**: o aviso de encerramento só é mandado 5 minutos depois da ÚLTIMA mensagem do paciente - ou seja, sempre bem DENTRO da janela de 24h de "reengajamento" da Meta, onde texto livre (`type: "text"`) é aceito sem restrição. Só mensagens iniciadas pela clínica FORA dessa janela (boas-vindas, agendamento criado etc.) precisam de template aprovado - não é o caso aqui.

**Como foi implementado** - como o Render (plano free, `media-dev`) só roda o web service do gunicorn (sem worker/cron job separado no `render.yaml`), a verificação roda como uma thread em segundo plano DENTRO do próprio processo da aplicação, iniciada uma única vez quando o app sobe:

- **`app/models.py`** (`ConversaWhatsapp`): novo `MINUTOS_INATIVIDADE_ENCERRAR = 5` e método `pronta_para_encerrar()` (mesmo padrão do `MINUTOS_EXPIRACAO`/`expirada()` já existentes, mas para este uso diferente - ver comentário no código).
- **`app/whatsapp_encerramento.py`** (novo arquivo): `_encerrar_conversas_vencidas()` varre TODAS as `ConversaWhatsapp` (em qualquer etapa - aguardando CPF, aguardando data de nascimento, ou já identificada), e para cada uma com `pronta_para_encerrar()` verdadeiro, manda a mensagem "Encerrando esta conversa por inatividade..." (texto livre, sem template) e apaga a linha do banco - a próxima mensagem que a pessoa mandar começa do zero (pede CPF de novo), como uma conversa nova. `iniciar_encerramento_automatico(app)` cria uma `threading.Thread` (`daemon=True`) que roda esse "tick" a cada 30 segundos (dorme 30s, verifica, repete) - só é chamada de fato quando `WHATSAPP_META_ACCESS_TOKEN` está configurado (senão nenhuma mensagem de WhatsApp seria enviada mesmo, então não há motivo pra rodar a thread - isso também é o que mantém os testes automatizados, que nunca configuram essa variável, livres dessa thread).
- **`app/__init__.py`** (`create_app`): chama `iniciar_encerramento_automatico(app)` uma vez, depois do `db.create_all()`.
- **`app/whatsapp_conversa.py`**: docstring do módulo atualizada mencionando esse encerramento ativo (para não confundir com o `expirada()` passivo, que continua existindo do mesmo jeito).

**Limite conhecido, aceito para o ambiente atual** (documentado no próprio código): se um dia o Render passar a rodar mais de um worker/processo do gunicorn (hoje só há um, ver `render.yaml`), cada processo teria sua própria thread e, numa janela de corrida rara, a mesma conversa vencida poderia ser encerrada (e avisada) por mais de um worker ao mesmo tempo. Não é um problema hoje.

**Testes**: novo `test_whatsapp_encerramento_automatico.py` - cria uma conversa "vencida" (6 minutos parada, ainda SEM identificação, para confirmar que vale em qualquer etapa) e uma "recente" (1 minuto parada), chama `_encerrar_conversas_vencidas()` (mockando `enviar_mensagem_whatsapp` para não tentar uma chamada de rede de verdade) e confirma: só a vencida recebe o aviso e é apagada, a recente continua intacta; e que `iniciar_encerramento_automatico()` NÃO cria nenhuma thread nova quando `WHATSAPP_META_ACCESS_TOKEN` não está configurado (caso dos testes).

- **Pendência**: mesma de sempre - `device_bash` ficou indisponível nesta rodada também, então nada disso foi executado de fato (só validado por leitura + `ast.parse` no ambiente de nuvem, que não tem as dependências do projeto instaladas). Tentei instalar as dependências (Flask etc.) no ambiente de nuvem para rodar os testes de verdade aqui mesmo, mas o mirror de pacotes disponível aceitou instalar `flask` mas recusou `flask-sqlalchemy` (parece haver uma lista de pacotes liberados, não é a PyPI completa) - sem conseguir instalar todas as dependências, não foi possível rodar nada além do syntax-check. Rodar `python test_whatsapp_encerramento_automatico.py` (e a suíte completa) antes de subir pra produção, e confirmar visualmente com uma conversa real de WhatsApp: iniciar uma conversa, parar de responder por 5+ minutos, e checar se a mensagem de encerramento chega e se a próxima mensagem depois disso volta a pedir CPF do zero.

## Como continuar

Ao colar este documento em uma nova sessão/conta, a nova conversa não terá acesso automático ao histórico desta sessão nem aos arquivos já abertos aqui — mas com este resumo é possível retomar o trabalho no mesmo ponto. Garanta que a nova sessão tenha acesso ao mesmo repositório Git (branch `dev`) e, se for usar a ponte com o computador, à mesma pasta local do projeto (`C:\app\media\src`).
