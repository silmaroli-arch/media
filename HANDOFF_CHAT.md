# Handoff — Continuação do chat com Claude sobre o projeto Media/MedIA

> Atualizado em 2026-09-10 (5ª rodada — ver seção no final). Cole este documento como primeira mensagem em uma nova sessão do Claude (Cowork) para retomar o trabalho de onde parou, incluindo o contexto e as pendências abaixo.
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

## Como continuar

Ao colar este documento em uma nova sessão/conta, a nova conversa não terá acesso automático ao histórico desta sessão nem aos arquivos já abertos aqui — mas com este resumo é possível retomar o trabalho no mesmo ponto. Garanta que a nova sessão tenha acesso ao mesmo repositório Git (branch `dev`) e, se for usar a ponte com o computador, à mesma pasta local do projeto (`C:\app\media\src`).
