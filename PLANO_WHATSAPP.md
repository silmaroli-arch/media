# Fatia 7 — Área de WhatsApp (paciente conversa com um número único da aplicação)

## Contexto e decisões já confirmadas com o Silvan

- O paciente vai poder conversar por WhatsApp com **um único número, da própria
  aplicação** (não um número por clínica/Grupo) para ver informações do preparo e
  fazer perguntas.
- Provedor: **ainda não decidido, o Silvan pediu recomendação** (ver seção
  "Recomendação de provedor" abaixo).
- Credenciais: **nenhuma conta/API configurada ainda** — a criação da conta no
  provedor escolhido e a verificação do negócio (Meta Business) são passos que o
  próprio Silvan (ou alguém do time INFLOR/Remsoft) precisa fazer manualmente; eu não
  posso criar contas de negócio nem provisionar credenciais por política.
- Abordagem: **planejar primeiro**, sem escrever código nesta etapa — este documento é
  para revisão e aprovação antes de qualquer implementação.

## Recomendação de provedor

Pesquisei três caminhos possíveis: a **Meta Cloud API direta**, um **BSP (Business
Solution Provider) internacional** como a Twilio, e **BSPs brasileiros** como Zenvia ou
Message Central. Resumo do que encontrei:

- **Meta Cloud API direta**: sem intermediário, custo por conversa é só a tarifa da
  própria Meta (mais barato no volume), mas exige lidar sozinho com verificação de
  negócio, webhooks, tokens de acesso e toda a infraestrutura de baixo nível — mais
  trabalho de engenharia próprio.
- **Twilio (BSP internacional)**: cobra uma taxa própria por mensagem (cerca de
  US$ 0,005) além da tarifa da Meta, mas entrega uma API e SDKs (incluindo Python) bem
  documentados, webhook e envio de template simplificados, e onboarding mais rápido.
  Fatura em dólar, o que gera alguma fricção cambial para uma operação brasileira.
- **BSPs brasileiros (Message Central, Zenvia, Take Blip)**: cobram em reais, emitem
  nota fiscal, e têm suporte local — mas costumam ter APIs menos maduras/documentadas
  que a Twilio, e o tempo de onboarding varia bastante (de alguns dias a 2-4 semanas
  dependendo do provedor).

**Minha recomendação: começar pela Twilio.** A MedIA já é um projeto Python/Flask
rodando na AWS (Elastic Beanstalk); a Twilio tem o SDK Python mais maduro do mercado
para WhatsApp, exemplos prontos de webhook em Flask, e a integração inicial costuma
sair do zero ao primeiro "hello world" em poucos dias — o que reduz risco de
implementação, que é o gargalo real agora (não o custo por mensagem, que tende a ser
pequeno no volume inicial de uma clínica). A fricção cambial (fatura em USD) é um custo
operacional menor comparado ao ganho de velocidade de desenvolvimento. Se o volume de
mensagens crescer muito no futuro e o custo da taxa da Twilio se tornar relevante, dá
para migrar para a Meta Cloud API direta mais adiante — a maior parte do código de
domínio (resolução de paciente, geração de resposta) não muda, só a camada de
envio/recebimento HTTP muda.

Ponto de atenção: qualquer que seja o provedor, a **verificação de negócio da Meta é
obrigatória** (mesmo via Twilio) — a Twilio guia esse processo, mas quem precisa
fornecer os dados da empresa (CNPJ, site, etc.) é o próprio Silvan/INFLOR, não eu.

## O que o Silvan (ou o time) precisa fazer antes da implementação

Isso não pode ser feito por mim (política de segurança — não crio contas nem manipulo
credenciais):

1. Criar uma conta Twilio e ativar o produto WhatsApp.
2. Completar a verificação de negócio da Meta através do fluxo guiado da Twilio
   (dados da empresa, CNPJ, site, categoria do negócio).
3. Obter (ou portar) o número de telefone dedicado que vai representar a aplicação no
   WhatsApp.
4. Gerar as credenciais (Account SID, Auth Token, número do WhatsApp Sender) e
   disponibilizá-las como variáveis de ambiente no Elastic Beanstalk (nunca em
   código-fonte).
5. Registrar os modelos de mensagem ("templates") que a aplicação vai precisar enviar
   fora da janela de 24h de atendimento (ex.: lembretes proativos) — a Meta exige
   aprovação prévia de cada template usado fora dessa janela; mensagens dentro da
   janela de 24h (resposta a uma mensagem do paciente) não precisam de template
   aprovado.

Assim que essas credenciais existirem, a implementação pode prosseguir.

## Implicações no modelo de dados

Decisão tomada com o Silvan: a identificação do paciente **não depende do número de
telefone cadastrado** — em vez de tentar casar o remetente da mensagem com
`Paciente.telefone` (o que teria o problema de números reciclados, familiares mandando
mensagem pelo número de outra pessoa, ou o paciente simplesmente ter cadastrado um
telefone diferente do que usa no WhatsApp), a conversa **pede CPF + data de nascimento**
logo no início. Esse par já funciona como um fator duplo razoável: reduz bastante o
risco de alguém conseguir puxar informação de preparo de outra pessoa só por saber (ou
adivinhar) o CPF dela, que no Brasil não é exatamente secreto. Isso também simplifica o
desenho, porque elimina o caso de "um telefone bate com mais de um paciente" — a busca
é sempre por CPF (identidade global, único, desde a Fatia 5) validado contra a data de
nascimento do mesmo cadastro.

Pontos a resolver:

- Busca por `Paciente.cpf` (já é a chave global desde a Fatia 5) + conferência de
  `Paciente.data_nascimento` — só libera o restante da conversa se as duas baterem. CPF
  ou data errados: mensagem genérica de "não encontramos esse cadastro, confira os
  dados" (sem indicar qual dos dois está errado, para não ajudar tentativa de
  adivinhação).
- **Um paciente pode ter mais de um `Agendamento`/exame ativo ao mesmo tempo** (em um
  ou mais Grupos, já que o modelo permite `GrupoPaciente` em vários grupos). Depois de
  identificado por CPF+data de nascimento, se houver mais de um exame ativo a conversa
  pergunta qual deles (lista numerada) — o mesmo problema que a tela
  `paciente/chat.html` já resolve com um `<select>` visual.
- Novo conceito: **sessão de conversa de WhatsApp**, um modelo `ConversaWhatsapp`
  (telefone do remetente, paciente_id confirmado, agendamento_id em foco, criado_em,
  atualizado_em) — guarda, para aquele número, "já confirmamos que é este paciente" e
  "sobre qual exame estamos falando agora", para não repetir a pergunta de CPF+data de
  nascimento a cada mensagem dentro da mesma conversa. Expira depois de um tempo de
  inatividade (ex.: algumas horas, a definir) e volta a pedir CPF+data de nascimento na
  próxima mensagem — não fica valendo para sempre, já que o WhatsApp de quem está
  conversando pode não ser sempre a mesma pessoa/aparelho.
- `Paciente.telefone` deixa de ter qualquer papel na identificação — continua existindo
  só como dado de contato de exibição, sem mudança de uso.

## Desenho do fluxo de mensagens

1. **Primeira mensagem de um número sem conversa ativa/confirmada**: responder pedindo
   CPF e data de nascimento (ex.: "Para começar, me envie seu CPF e data de nascimento,
   assim: 000.000.000-00, 01/01/1990").
   - CPF+data não encontram nenhum cadastro, ou encontram mas não conferem entre si:
     mensagem genérica pedindo para checar os dados e tentar de novo.
   - Cadastro confirmado, um agendamento/exame ativo: segue direto para o menu de
     opções.
   - Cadastro confirmado, múltiplos agendamentos ativos: pergunta qual exame (lista
     numerada) antes do menu.
2. **Menu de opções** (uma vez identificado paciente + exame): "1) Ver informações do
   preparo  2) Fazer uma pergunta  3) Trocar de exame".
3. **Ver informações do preparo**: reaproveita a mesma lógica que já gera o texto de
   preparo na tela `paciente/preparo.html` (`app/faq_engine.py`/`.limite()`) — não
   duplicar essa lógica, extrair para uma função compartilhada que tanto a rota web
   quanto o handler do WhatsApp chamam.
4. **Fazer uma pergunta**: reaproveita o pipeline de IA já existente
   (`app/ia_preparo.py`) que hoje responde no chat da área do paciente — a pergunta
   recebida via WhatsApp vira uma `PerguntaPendente`/resposta automática do mesmo
   jeito que a pergunta digitada na web, só a camada de transporte muda.
5. Cada mensagem trocada (recebida e enviada) fica registrada em
   `ChatMensagem` (modelo já existente) com uma origem nova (`canal="whatsapp"`) para
   aparecer no mesmo histórico que a equipe médica já visualiza hoje.

## Infraestrutura necessária

- **Endpoint de webhook público**: uma rota nova, por exemplo
  `POST /whatsapp/webhook`, em um blueprint novo (`whatsapp_bp`), exposta publicamente
  no Elastic Beanstalk (já é HTTPS hoje).
- **Verificação de assinatura**: toda requisição recebida do provedor precisa ser
  validada (a Twilio assina cada webhook com `X-Twilio-Signature`) para garantir que
  não é uma chamada forjada por terceiros — isso é inegociável antes de ir para
  produção.
- **Variáveis de ambiente novas** no Elastic Beanstalk: credenciais do provedor
  (nunca em código nem no repositório).
- **Envio assíncrono**: como o processamento (buscar paciente, montar resposta,
  eventualmente chamar IA) pode levar mais que o timeout aceitável de um webhook, vale
  considerar responder o webhook imediatamente (200 OK) e enviar a resposta de fato via
  chamada de API separada (padrão comum em integrações de WhatsApp) — evita timeouts e
  reentrega duplicada de mensagens pelo provedor.

## O que NÃO está no escopo desta fatia (documentado, não implementado)

- Envio proativo de lembretes de preparo (mensagens iniciadas pela aplicação, fora da
  janela de 24h) — exigiria templates pré-aprovados pela Meta; pode ser uma fatia
  futura separada, depois que o fluxo reativo (paciente inicia a conversa) estiver
  estável.
- Multimídia (paciente enviar foto de exame, ou aplicação enviar PDF do preparo) — o
  texto já cobre o pedido original ("ver toda a informação do preparo e fazer
  perguntas"); mídia fica para uma iteração futura se for pedida.
- Integração com múltiplos números por clínica — explicitamente descartado pelo
  Silvan ("os clientes não terão cada um o seu número").

## Sequência de implementação sugerida (depois da aprovação deste plano)

1. Modelo de dados: `ConversaWhatsapp`, coluna `canal` em `ChatMensagem`. Sem nenhuma
   rota nova ainda — só schema. **Parada segura.**
2. Blueprint `whatsapp_bp` com o webhook recebendo e validando assinatura, só
   logando a mensagem recebida (sem responder nada ainda) — valida a conectividade
   ponta a ponta com o provedor antes de escrever qualquer lógica de negócio.
3. Fluxo de identificação por CPF + data de nascimento, com expiração de sessão.
4. Fluxo de menu + "ver preparo" (reaproveitando `faq_engine`/`.limite()`).
5. Fluxo de "fazer pergunta" (reaproveitando `ia_preparo.py`).
6. Testes automatizados (mesma disciplina do resto do projeto: banco fresco + seed
   por arquivo) simulando payloads de webhook do provedor escolhido.
7. Homologação com número de teste da Twilio antes de qualquer uso com paciente real.

---

## Migração para Meta Cloud API direta (decisão posterior)

A Fatia 7 foi implementada originalmente sobre a Twilio (recomendação original acima).
Depois de já em uso, o Silvan pediu para avaliar a troca para a **WhatsApp Cloud API da
Meta diretamente**, sem intermediário. Comparação levantada nessa conversa:

- **Custo**: a Twilio cobra, além da tarifa da Meta por template entregue, uma sobretaxa
  própria de US$ 0,005 por mensagem (entrada e saída) e US$ 0,001 por mensagem que falha
  — na Meta direta, só a tarifa da Meta. Em volumes baixos (uma clínica) o valor absoluto
  é pequeno; passa a importar de verdade só em alto volume (dezenas de milhares de
  mensagens/mês).
- **Complexidade operacional**: a Twilio entrega SDK maduro, documentação e suporte com
  SLA — indo direto à Meta, a aplicação passa a ser responsável por lógica de retry,
  registro de número, submissão/gestão de templates e tratamento de erros da Graph API,
  sem essa camada de conveniência.
- **Decisão do Silvan**: migrar para a Meta direta mesmo assim, aceitando o trabalho de
  manutenção adicional em troca de eliminar a sobretaxa e a dependência de terceiro.

Implementado nesta sessão: `app/routes_whatsapp.py` (webhook) e `app/whatsapp_envio.py`
(envio) reescritos para falar diretamente com a Graph API da Meta (`graph.facebook.com`),
substituindo por completo a integração com a Twilio (SDK `twilio` removido de
`requirements.txt`). Principais diferenças técnicas da nova integração:

- Webhook precisa de um handshake de verificação via **GET** (`hub.mode`/
  `hub.verify_token`/`hub.challenge`) antes de a Meta aceitar cadastrar a URL —
  Twilio não tinha esse passo.
- Assinatura de cada mensagem recebida (**POST**) vem em `X-Hub-Signature-256`
  (HMAC-SHA256 do corpo cru, com o **App Secret** do app Meta) — diferente do
  `X-Twilio-Signature` (HMAC-SHA1 de URL+parâmetros).
- Payload é JSON, estrutura aninhada `entry[].changes[].value.messages[]` — não mais
  form-encoded.
- A resposta ao paciente **não** volta no corpo da resposta do webhook (como o TwiML da
  Twilio permitia) — é sempre enviada por uma chamada HTTP separada à Graph API
  (`POST /{phone_number_id}/messages`), depois de o webhook já ter devolvido 200.
- Templates de mensagem (fora da janela de 24h) agora são cadastrados e aprovados
  diretamente em **WhatsApp Manager** (Meta Business), não mais no Content Template
  Builder da Twilio.

Novas variáveis de ambiente (ver `.env.example` para a lista completa e explicação de
cada uma): `WHATSAPP_META_VERIFY_TOKEN`, `WHATSAPP_META_APP_SECRET`,
`WHATSAPP_META_ACCESS_TOKEN`, `WHATSAPP_META_PHONE_NUMBER_ID`,
`WHATSAPP_META_TEMPLATE_RESPOSTA` (opcional), `WHATSAPP_META_TEMPLATE_IDIOMA`
(opcional), `WHATSAPP_META_API_VERSION` (opcional). As variáveis `TWILIO_*` e
`WHATSAPP_URL_PUBLICA` (específica da validação de assinatura da Twilio) deixam de ser
usadas e podem ser removidas do Elastic Beanstalk depois do deploy desta mudança.

**O que o Silvan ainda precisa fazer antes disso funcionar em produção** (não pode ser
feito por mim — política de segurança, não crio contas nem manipulo credenciais):

1. Criar um app em [developers.facebook.com](https://developers.facebook.com) (tipo
   "Business"), adicionar o produto **WhatsApp**.
2. Completar a verificação do **Meta Business Manager** (dados da empresa, CNPJ etc.) e
   vincular/portar o número de telefone dedicado da aplicação.
3. Gerar um **token de acesso permanente** de um System User (permissão
   `whatsapp_business_messaging`) e anotar o **Phone Number ID** — WhatsApp Manager >
   Configuração da API.
4. Pegar o **App Secret** do app (Configurações básicas) e escolher uma string qualquer
   para o **Verify Token** do webhook.
5. Cadastrar a URL do webhook (`https://media.inflor.com.br/whatsapp/webhook`) e o Verify
   Token em WhatsApp Manager > Configuração > Webhooks, e assinar o campo `messages`.
6. Registrar e esperar a aprovação do template usado para responder perguntas fora da
   janela de 24h (WhatsApp Manager > Modelos de mensagem) — mesmo texto/variáveis do
   template antigo da Twilio (`{{1}}`=pergunta, `{{2}}`=resposta).
7. Configurar as variáveis de ambiente novas (item acima) no Elastic Beanstalk.

Testado nesta sessão (sem credenciais reais da Meta, que ainda não existem): suíte
inteira (42 arquivos) passando, incluindo os testes de identificação/menu/pergunta por
WhatsApp (inalterados na lógica de conversa) e um teste novo do webhook simulando o
handshake de verificação e a validação de assinatura HMAC-SHA256 com payloads no formato
real da Meta.

---

O plano original (implementação sobre Twilio, seções acima) foi executado e está em
produção. A migração para a Meta Cloud API direta, descrita na seção anterior, também já
foi implementada e testada nesta sessão — falta só o Silvan configurar a conta/credenciais
reais na Meta (checklist acima) para o recurso voltar a funcionar em produção (sem essas
variáveis configuradas, o envio/recebimento por WhatsApp fica pulado, sem quebrar o resto
do sistema — mesmo comportamento de "falha aberta" que já existia com a Twilio).
