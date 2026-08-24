# Handoff — Continuação do chat com Claude sobre o projeto Media/MedIA

> Atualizado em 2026-08-19 (2ª rodada). Cole este documento como primeira mensagem em uma nova sessão do Claude (Cowork) para retomar o trabalho de onde parou, incluindo o contexto e as pendências abaixo.

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

## Como continuar

Ao colar este documento em uma nova sessão/conta, a nova conversa não terá acesso automático ao histórico desta sessão nem aos arquivos já abertos aqui — mas com este resumo é possível retomar o trabalho no mesmo ponto. Garanta que a nova sessão tenha acesso ao mesmo repositório Git (branch `dev`) e, se for usar a ponte com o computador, à mesma pasta local do projeto (`C:\app\media\src`).
