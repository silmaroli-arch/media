# Plataforma de Controle de Preparo de Exames (multi-empresa / multi-filial)

Plataforma SaaS para clínicas/consultórios controlarem o preparo de
pacientes para exames. Qualquer pessoa pode cadastrar sua **empresa** pela
web, com sua primeira **filial**, e um "dono da plataforma" gerencia todas
as empresas cadastradas (status de pagamento, bloqueio de acesso, cobrança
por médico).

## Empresas e filiais

- Uma **empresa** é quem contrata a plataforma. Ela pode ter **uma ou mais
  filiais** (unidades físicas) — ex.: uma rede de clínicas com sede e
  outras unidades.
- Pacientes, exames, preparo, agenda e perguntas continuam isolados **por
  filial**: cada filial tem seus próprios pacientes e sua própria agenda,
  mesmo que pertença à mesma empresa que outra filial.
- O controle de pagamento (trial/ativa/inadimplente/bloqueada) é feito no
  nível da **empresa** — bloquear a empresa bloqueia o acesso de todas as
  suas filiais de uma vez.
- Um médico ou secretária pode estar vinculado a várias filiais (da mesma
  empresa ou de empresas diferentes) e escolhe em qual está trabalhando ao
  logar, como já acontecia antes entre clínicas diferentes.
- A cobrança é baseada no **número de médicos distintos vinculados à
  empresa** (contando uma única vez cada médico, mesmo que atue em mais de
  uma filial dela) multiplicado por um **valor por médico negociado
  individualmente com cada empresa**. Continua sendo um controle manual —
  a plataforma só calcula e mostra o valor estimado para o dono, não emite
  cobrança automática.

## Permissões administrativas

Nem toda clínica tem uma secretária — por isso, as ações administrativas
(cadastrar pacientes, gerenciar a equipe, gerenciar filiais e editar os
dados da clínica) não são fixas por papel ('secretaria' vs. 'medico').
Cada pessoa da equipe tem 4 permissões individuais, que valem para a conta
como um todo (em todas as filiais em que ela atua):

- **Cadastrar pacientes**
- **Gerenciar equipe**
- **Gerenciar filiais**
- **Editar dados da clínica**

Quem cria a empresa pela tela pública (`/cadastro`) — seja médico(a) ou
secretário(a) — recebe automaticamente todas as 4 permissões, já que pode
não haver mais ninguém para administrar a clínica. Ao cadastrar novas
pessoas na equipe depois, quem tem a permissão "Gerenciar equipe" escolhe
quais dessas permissões a nova pessoa recebe, e pode ajustá-las depois
pela tela "Permissões" na lista de equipe.

## Modelos de preparo

O preparo de um exame não é mais cadastrado direto dentro do exame — cada
exame aponta para um **modelo de preparo** (tela "Modelos de preparo", em
Controle), que pode ser reaproveitado por mais de um exame. Isso evita
duplicar o cadastro quando várias variações do mesmo exame usam
exatamente o mesmo preparo (ex.: os diferentes substratos de um teste
respiratório, que precisam ser agendados em dias separados). Editar um
modelo atualiza automaticamente o preparo mostrado para todos os exames
que o usam.

Cada modelo tem, além do texto livre de instruções:

- **Cortes de alimentação/líquido**, definidos em horas antes do horário
  marcado do exame (não um horário fixo do relógio) — o paciente vê a
  data/hora exata calculada a partir do agendamento dele (ex.: "jejum
  total: até 09/08 às 20:00", "líquidos claros: até 10/08 às 06:00").
- **Medicamentos a suspender**, com o prazo em dias antes do exame — o
  paciente vê a data exata calculada a partir do agendamento (ex.:
  "Ozempic/Mounjaro/Trulicity ou similares: suspender até 27/07/2026").
  Os medicamentos vêm de um catálogo compartilhado por toda a
  plataforma (`Medicamento`), reaproveitado entre empresas diferentes —
  se o medicamento digitado ainda não existir no catálogo, ele é criado
  na hora.

**Importar de um PDF**: na tela de Modelos de preparo, o botão "Importar
de um PDF" extrai o texto de um PDF de preparo existente e tenta
pré-preencher automaticamente o nome do modelo, o texto de instruções, os
cortes de jejum/líquido e a lista de medicamentos a suspender, usando
reconhecimento por palavras-chave (sem inteligência artificial, sem custo
de API). A extração é heurística e pode errar ou deixar passar alguma
regra — por isso nada é salvo automaticamente: o formulário abre
pré-preenchido para revisão, e só é gravado no banco quando alguém
confirma e clica em "Salvar".

## Perfis de usuário

**Dono da plataforma** (`tipo = 'dono'`)
- Vê todas as empresas cadastradas na plataforma, com o número de filiais
  e de médicos de cada uma
- Gerencia o status de pagamento de cada empresa (trial, ativa,
  inadimplente, bloqueada), a data de vencimento da mensalidade e o valor
  por médico negociado com ela
- Pode bloquear/desbloquear o acesso de uma empresa com um clique — isso
  afeta todas as filiais dela de uma vez (equipe e pacientes)
- Configura quantos dias de trial uma empresa nova recebe ao se cadastrar;
  quando o trial vence, a empresa vira "inadimplente" automaticamente
  (sem bloquear sozinha — bloquear continua sendo decisão do dono)

**Secretária** (`tipo = 'secretaria'`)
- Qualquer pessoa pode criar uma empresa nova pela tela de cadastro público
  (`/cadastro`), já com a primeira filial, escolhendo se é médico(a) ou
  secretário(a) — quem cria recebe todas as permissões administrativas
- Com a permissão "Gerenciar filiais", cadastra novas filiais da mesma
  empresa (aba "Filiais") — útil quando a empresa abre uma nova unidade
- Com a permissão "Gerenciar equipe", cadastra os médicos e outras
  secretárias (aba "Equipe"), escolhendo em qual filial cada um vai atuar
  e quais permissões administrativas essa pessoa vai ter
- Com a permissão "Cadastrar pacientes", cadastra pacientes (gera login de
  acesso para o paciente)
- Cadastra exames e instruções de preparo em nome de qualquer médico da
  filial (escolhe o médico responsável no formulário)
- Vê e gerencia a agenda, os pacientes, os exames e as perguntas pendentes
  de **todos** os médicos da filial atual — tem visão completa
- Fila de perguntas que a IA não conseguiu responder — ao responder, a
  pergunta e a resposta são salvas na base de conhecimento automaticamente
- Base de conhecimento (FAQ) com histórico de uso, podendo também ser
  alimentada manualmente (perguntas gerais, sem exame associado, só a
  secretária cadastra/vê)

**Médico** (`tipo = 'medico'`)
- Depois de cadastrado pela equipe, o médico tem seu próprio espaço dentro
  da clínica; se tiver recebido permissões administrativas (por exemplo,
  numa clínica sem secretária), também acessa as telas de Pacientes,
  Equipe, Filiais e Dados da clínica
- Cadastra e edita **os seus próprios exames**, escolhendo o modelo de
  preparo de cada um (ver seção "Modelos de preparo") — cada exame
  pertence exclusivamente a um médico
- Acompanha **somente os seus próprios pacientes**: um paciente "é seu"
  quando tem algum agendamento vinculado a um dos seus exames (o
  agendamento herda automaticamente o médico responsável a partir do
  exame escolhido)
- Vê a agenda, a fila de perguntas pendentes e a base de conhecimento
  filtradas para os seus próprios exames/pacientes
- Sem essas permissões, não cadastra pacientes novos nem gerencia a
  equipe/filiais/dados da clínica
- Um mesmo médico pode estar vinculado a **mais de uma clínica** (ex.: um
  médico que atende em dois lugares) — nesse caso, ao logar, escolhe em
  qual clínica quer trabalhar naquele momento, e pode trocar depois pelo
  link "trocar" na barra de navegação

**Paciente**
- Visualização dos exames agendados e do histórico
- Tela de preparo detalhada para cada exame
- Chat para tirar dúvidas ("Posso comer batata?"): o sistema procura a
  resposta na base de conhecimento da clínica; se não encontrar com
  confiança suficiente, encaminha a pergunta para a secretaria e avisa o
  paciente

**Motor de perguntas e respostas**
Não depende de nenhuma API externa de IA: compara a pergunta do paciente
com as perguntas já cadastradas (dentro da mesma clínica) usando
similaridade de texto e sobreposição de palavras-chave (veja
`app/faq_engine.py`). Isso mantém o protótipo simples, sem custo de API, e
100% em português. Se no futuro quiserem uma IA mais sofisticada (ex.:
usando a API da Claude para responder com base na documentação de
preparo), a estrutura de dados já está pronta para isso.

## Controle de pagamento das clínicas

Por decisão do time, o controle de pagamento é **manual** por enquanto: não
há integração com gateway de pagamento (Stripe, Mercado Pago, etc.). O dono
da plataforma marca no próprio painel se uma clínica está em dia, vencida ou
bloqueada. Uma integração real de cobrança recorrente pode ser adicionada
depois sem precisar mudar o modelo de dados (o campo `Clinica.status` já
existe e é só passar a ser atualizado automaticamente pelo webhook do
gateway escolhido).

## Banco de dados: PostgreSQL

O sistema usa PostgreSQL desde o ambiente de desenvolvimento (via
SQLAlchemy, então trocar de provedor no futuro é só mudar a string de
conexão). Ele **não** vem com um banco embutido — você precisa ter um
PostgreSQL acessível e informar a conexão pela variável de ambiente
`DATABASE_URL`.

### 1. Provisione um banco PostgreSQL

Qualquer opção abaixo funciona, já que só precisamos de uma string de
conexão no formato `postgresql://usuario:senha@host:porta/banco`:

- **Banco gerenciado na nuvem** (o que vocês definiram para o dev): crie um
  projeto/instância no provedor escolhido (ex.: Neon, Supabase, Azure
  Database for PostgreSQL, Amazon RDS) e copie a *connection string* que o
  próprio painel fornece.
- **Docker local**, se preferirem testar sem depender da nuvem:
  ```bash
  docker run --name preparo-postgres -e POSTGRES_PASSWORD=preparo_dev_pw \
    -e POSTGRES_USER=preparo_dev -e POSTGRES_DB=preparo_exames_dev \
    -p 5432:5432 -d postgres:16
  ```

### 2. Configure o `.env`

Copie `.env.example` para `.env` e preencha com os dados reais:

```bash
cp .env.example .env
```

```
DATABASE_URL=postgresql://usuario:senha@host:5432/preparo_exames_dev
SECRET_KEY=uma-chave-aleatoria-só-sua
```

O arquivo `.env` já está no `.gitignore` — nunca commitem a senha real do
banco no Git.

### 3. Instale as dependências e rode

```bash
cd preparo_exames
pip install -r requirements.txt
python seed.py      # cria as tabelas no PostgreSQL e popula com dados de exemplo
python run.py        # inicia o servidor em http://localhost:5000
```

Depois acesse `http://localhost:5000` no navegador.

> Se `DATABASE_URL` não estiver definida, o sistema cai de volta para um
> SQLite local só como rede de segurança (com um aviso no console) — não é
> o modo esperado de uso agora que o time decidiu usar PostgreSQL desde o
> dev.

### Contas de demonstração (criadas pelo seed.py)

| Perfil                                            | E-mail                          | Senha  |
|----------------------------------------------------|----------------------------------|--------|
| Dono da plataforma                                 | dono@plataforma.com             | 123456 |
| Secretária — empresa Clínica Vitória                | secretaria@clinicavitoria.com   | 123456 |
| Dr. Carlos (médico, nas empresas Vitória e SP)     | medico@clinicavitoria.com       | 123456 |
| Dra. Fernanda (médica, só na empresa Vitória)      | medica2@clinicavitoria.com      | 123456 |
| Paciente do Dr. Carlos                             | joao@paciente.com               | 123456 |
| Paciente da Dra. Fernanda                          | pedro@paciente.com              | 123456 |
| Secretária — empresa Clínica São Paulo              | secretaria@clinicasp.com        | 123456 |
| Paciente — empresa Clínica São Paulo                | maria@paciente.com              | 123456 |
| Secretária — Grupo Saúde Total (2 filiais)         | secretaria@gruposaude.com       | 123456 |
| Médico — Grupo Saúde Total (atua nas 2 filiais)    | medico@gruposaude.com           | 123456 |

O `seed.py` cria três empresas de exemplo: "Clínica Vitória" e "Clínica São
Paulo" (cada uma com uma única filial, para testar o isolamento de dados
entre empresas diferentes — João é paciente do Dr. Carlos; Pedro é
paciente da Dra. Fernanda) e o "Grupo Saúde Total" (com **duas filiais**,
Centro e Praia, e um valor de R$150 por médico configurado) para você testar
a funcionalidade de filiais e a cobrança por médico — o médico do grupo
atua nas duas filiais mas conta uma única vez na cobrança estimada.

**Atenção:** `python seed.py` **apaga e recria todas as tabelas**
(`db.drop_all()` + `db.create_all()`). É perfeito para resetar seu ambiente
de testes, mas nunca rode isso apontando para um banco com dados reais de
clínicas/pacientes — combine com o time antes de usar em qualquer ambiente
compartilhado.

## Deploy na AWS (banco + aplicação)

Passo a passo usando o console da AWS (sem precisar de linha de comando,
exceto no passo final de subir o código). Usaremos **RDS** para o banco
PostgreSQL e **Elastic Beanstalk** para hospedar a aplicação Flask — é a
combinação mais direta para esse tipo de app.

### Parte 1 — Criar o banco (Amazon RDS)

1. No [Console da AWS](https://console.aws.amazon.com/rds/), vá em **RDS →
   Create database**.
2. Em **Choose a database creation method**, deixe "Standard create".
3. Em **Engine options**, escolha **PostgreSQL**.
4. Em **Templates**, escolha **Free tier** (assim você usa a cota gratuita
   dos 12 meses, se a conta for elegível).
5. Em **Settings**: dê um nome à instância (ex.: `preparo-exames-db`),
   defina o usuário master (ex.: `preparo_admin`) e uma senha forte — anote
   essa senha, ela não aparece de novo depois.
6. Em **Instance configuration**, o Free tier já seleciona `db.t3.micro` ou
   `db.t4g.micro` automaticamente.
7. Em **Connectivity**: em "Public access" escolha **Yes** só para
   facilitar os testes iniciais (depois, quando ligar à aplicação na mesma
   VPC, o ideal é mudar para **No** e restringir por *security group* — veja
   a nota de segurança abaixo).
8. Em **Additional configuration**, defina o nome do banco inicial:
   `preparo_exames` (campo "Initial database name").
9. Clique em **Create database**. Leva de 5 a 10 minutos para ficar
   disponível.
10. Quando o status virar "Available", clique na instância e copie o
    **Endpoint** (algo como `preparo-exames-db.xxxxxxxxx.sa-east-1.rds.amazonaws.com`)
    e a **Port** (normalmente `5432`).
11. Monte a `DATABASE_URL`:
    ```
    postgresql://preparo_admin:SUA_SENHA@SEU_ENDPOINT:5432/preparo_exames
    ```

**Nota de segurança:** depois que a aplicação estiver rodando (Parte 2),
edite o *security group* do RDS para permitir a porta 5432 **somente** a
partir do security group da aplicação (Elastic Beanstalk), em vez de
"Public access". Isso evita que o banco fique acessível para qualquer
IP da internet — importante já que o sistema vai lidar com dados de
pacientes (LGPD).

### Parte 2 — Criar a aplicação (Elastic Beanstalk)

O projeto já vem preparado para o Elastic Beanstalk: o arquivo
`application.py` na raiz expõe o app Flask no formato que o Beanstalk
espera, e `.ebextensions/01_flask.config` garante que ele seja encontrado
corretamente.

1. Instale a CLI do Elastic Beanstalk na sua máquina (uma vez só):
   ```bash
   pip install awsebcli --break-system-packages
   ```
2. Dentro da pasta do projeto, rode:
   ```bash
   eb init
   ```
   Escolha a região (ex.: `sa-east-1` — São Paulo), dê um nome à
   aplicação (ex.: `preparo-exames`), e quando perguntar a plataforma,
   escolha **Python**.
3. Crie o ambiente:
   ```bash
   eb create preparo-exames-dev
   ```
   Isso já cria a instância EC2, o load balancer e sobe a aplicação — leva
   alguns minutos.
4. Configure as variáveis de ambiente (a `DATABASE_URL` montada na Parte 1
   e uma `SECRET_KEY` só sua):
   ```bash
   eb setenv DATABASE_URL="postgresql://preparo_admin:SUA_SENHA@SEU_ENDPOINT:5432/preparo_exames" SECRET_KEY="uma-chave-aleatoria"
   ```
5. Rode o `seed.py` uma vez para criar as tabelas no banco. Como o
   Beanstalk não te dá acesso direto de terminal à instância por padrão, o
   mais simples é rodar o `seed.py` da sua máquina local, apontando para o
   mesmo `DATABASE_URL` (ele vai criar as tabelas direto no RDS, de onde
   você estiver):
   ```bash
   export DATABASE_URL="postgresql://preparo_admin:SUA_SENHA@SEU_ENDPOINT:5432/preparo_exames"
   python seed.py
   ```
   (Para isso funcionar, o RDS precisa estar com "Public access" habilitado
   nesse momento, ou você precisa estar numa rede com acesso à VPC.)
6. Abra a aplicação:
   ```bash
   eb open
   ```

Para atualizações futuras, depois de alterar o código, basta rodar
`eb deploy` de dentro da pasta do projeto.

### Alternativa mais simples: AWS App Runner

Se preferirem não usar a CLI do Elastic Beanstalk, o **AWS App Runner** é
uma opção mais nova e simples: você conecta o repositório Git (GitHub, por
exemplo) e ele builda e sobe a aplicação automaticamente a cada push,
parecido com Render/Railway. O trade-off é que ele espera um `Dockerfile`
ou usa buildpacks automáticos — funciona bem com o `requirements.txt` que
já temos, mas exige criar um repositório Git para o projeto (o Elastic
Beanstalk não exige isso, aceita upload direto do `.zip`).

## Estrutura do projeto

```
preparo_exames/
├── app/
│   ├── __init__.py         # criação do app Flask
│   ├── models.py            # modelos do banco (Usuario, Paciente, Exame, ...)
│   ├── extensions.py        # instâncias do SQLAlchemy e Flask-Login
│   ├── faq_engine.py         # motor de busca de perguntas/respostas
│   ├── routes_auth.py        # login/logout
│   ├── routes_medico.py      # rotas da secretaria/médico
│   ├── routes_paciente.py    # rotas do paciente
│   ├── templates/            # templates HTML (Bootstrap 5)
│   └── static/                # CSS
├── seed.py                    # cria as tabelas e popula o banco com dados de exemplo
├── run.py                     # inicia o servidor localmente (desenvolvimento)
├── application.py              # ponto de entrada esperado pelo AWS Elastic Beanstalk
├── .ebextensions/               # configuração extra do Elastic Beanstalk
├── test_smoke.py               # testes rápidos dos principais fluxos
├── .env.example                # modelo de configuração (copiar para .env)
└── requirements.txt
```

## Próximos passos sugeridos

- Automatizar migrações de schema com Flask-Migrate/Alembic (hoje o
  `seed.py` cria as tabelas com `db.create_all()`, que serve para
  desenvolvimento, mas não gerencia alterações incrementais de schema em
  produção)
- Enviar notificações por e-mail/WhatsApp quando um agendamento é criado ou
  quando a secretaria responde uma pergunta pendente
- Adicionar recuperação de senha e exigir troca de senha no primeiro acesso
  do paciente
- Caso desejem uma IA mais avançada, integrar a API da Claude usando a base
  de preparo e o histórico de FAQ como contexto (RAG), mantendo o mesmo
  fluxo de "perguntas não respondidas vão para a secretaria"
- Implantar em um servidor com HTTPS antes de usar com dados reais de
  pacientes (LGPD)
- Integrar um gateway de pagamento (Stripe, Mercado Pago) para automatizar
  a cobrança recorrente das clínicas e o bloqueio automático em caso de
  inadimplência — hoje isso é feito manualmente pelo dono da plataforma
- Adicionar recuperação de senha também para a equipe/dono (hoje só existe
  cadastro; troca de senha esquecida precisa ser feita direto no banco)
- Página de erro dedicada para "clínica bloqueada" em vez de só uma
  mensagem no login, com instruções de como regularizar
