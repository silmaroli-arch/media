# MedIA como plataforma multi-módulo — registro de decisão (sem implementação)

> Este documento é só um registro de raciocínio de uma conversa entre o Silvan e o
> Claude em 2026-08-24. **Nenhum código foi alterado por causa dele.** Serve de ponto de
> partida para quando o segundo módulo da plataforma sair do papel.

## Contexto

O Silvan trouxe uma reformulação de como pensar o produto: o **MedIA** deixa de ser "o
sistema de preparo de exames" e passa a ser uma **plataforma** que vai hospedar várias
aplicações médicas ao longo do tempo. Tudo que foi construído até hoje (exames, preparo,
agendamento, perguntas do paciente, chat com IA, WhatsApp) corresponde ao que ele chamou
de módulo **"Preparo+"** — o primeiro módulo da plataforma, não a plataforma inteira.

Ainda não existe um segundo módulo definido — é só a visão geral por enquanto. O modelo
comercial já está decidido: **módulos avulsos** — um cliente (grupo/clínica) poderá
contratar só o Preparo+, sem os módulos futuros, pagando por módulo separadamente.

## Decisões e observações desta conversa

1. **Separação plataforma vs. módulo.** Boa parte do que já existe no código já está,
   sem ter sido pensado assim na época, na camada certa para isso:
   - **Plataforma (compartilhado entre módulos futuros)**: cadastro de paciente
     (`Paciente`/`GrupoPaciente` — já é global desde a Fatia 5, um CPF não pertence a
     nenhum módulo específico), conta de usuário da equipe (`Usuario`), a unidade de
     cliente/cobrança (`Grupo`), gestão de equipe/convite (`GrupoConvite`/`GrupoMembro`),
     login, o painel do dono (`/dono`), e a infraestrutura de IA (`app/custo_ia.py`, o
     despacho pra Claude/ChatGPT/Gemini em `app/ia_preparo.py`) — um módulo futuro que
     também usar IA vai precisar do mesmo controle de custo e do mesmo painel de billing.
   - **Preparo+ (específico deste módulo)**: `Exame`, `PreparoModelo`, `Agendamento`,
     `PerguntaPendente`, `ChatMensagem`, `FaqItem`, e a lógica de conversa do WhatsApp
     (`app/whatsapp_conversa.py` — o menu "ver preparo/fazer pergunta" é 100% Preparo+,
     ainda que o webhook/envio pela Meta em si, `app/routes_whatsapp.py`/
     `app/whatsapp_envio.py`, pudesse um dia virar canal compartilhado por outro módulo).
   - Implicação prática (não implementada): conforme surgir um segundo módulo, a
     navegação deveria refletir essa separação (um bloco "Plataforma" com
     paciente/equipe, separado de um bloco "Preparo+" com exame/preparo/agenda) — hoje
     tudo aparece misturado no mesmo menu porque só existe um módulo.

2. **Pergunta em aberto pra quando isso importar de verdade** (produto, não técnica):
   quando dois módulos existirem e um cliente comprar só um deles, a lista de pacientes
   continua mostrando todo mundo do grupo (já que o cadastro é compartilhado), ou deveria
   filtrar só quem tem histórico relevante no módulo que aquele usuário está usando no
   momento?

3. **Seleção de módulo no cadastro, ligada à cobrança.** Ideia do Silvan: no futuro, o
   cadastro do médico deveria perguntar quais módulos ele quer usar — isso tanto orienta
   a experiência quanto já direciona a cobrança de licença por módulo.
   - Nuance de encanamento identificada: hoje a cobrança mora no `Grupo`, não na conta
     (`Usuario`) — um médico sozinho já tem um Grupo pessoal por baixo dos panos, só que
     invisível na tela (decisão da Fatia 6, pra não confundir quem nunca vai convidar
     ninguém). "Escolher módulo no cadastro" deveria gravar a escolha nesse Grupo pessoal
     desde o primeiro momento, não como um campo solto em `Usuario` — assim, se esse
     médico um dia convidar alguém e o Grupo "aparecer" de verdade na tela, a licença
     contratada já vem junto, sem perguntar de novo.

## Recomendação: NÃO implementar agora

Avaliado e decidido nesta conversa: não vale a pena construir a seleção de
módulo/licença ainda. Motivos:

- **Não há segunda opção de verdade.** Com um módulo só, a tela de "escolha seus
  módulos" seria um único checkbox sempre marcado, sem escolha real — não testa nada e
  só deixa o cadastro mais confuso, sem ganho nenhum hoje.
- **O formato de cobrança do módulo 2 é desconhecido.** Não sabemos se vai ser por
  médico (como o Preparo+ hoje), por uso, por paciente, valor fixo — cada formato pede
  uma modelagem de tabela de licença diferente. Desenhar isso agora, baseado só no
  Preparo+, arrisca acertar a modelagem errada e ter que refazer quando o módulo 2
  aparecer de verdade.
- **Risco de empilhar mudança em cima de mudança.** A Fatia 6 (desacoplar conta de
  usuário da criação automática de Grupo) ainda está em andamento, bem na mesma área
  onde essa ideia pousaria — melhor não somar uma segunda decisão de arquitetura nova
  ali antes da primeira fechar.
- **Não há dívida técnica real em esperar.** Quando o módulo 2 nascer, marcar "todo
  Grupo existente já tem Preparo+ contratado" é uma migração de uma linha só — o custo
  de adiar essa decisão é baixo.

## Sequência sugerida para quando o módulo 2 sair do papel

1. Desenhar o módulo 2 o suficiente para saber seu modelo de cobrança real (por médico?
   por uso? fixo?) — só então desenhar a tabela de licença por módulo (algo como
   `GrupoModulo`: qual grupo, qual módulo, desde quando, a que preço), tendo dois casos
   reais para basear a modelagem em vez de um só.
2. Migração de backfill: marcar todo `Grupo` existente como tendo o Preparo+ ativo
   (idempotente, mesmo padrão dos outros scripts `migrar_*.py` já usados no projeto).
3. Tela de seleção de módulo no cadastro (`app/routes_auth.py:cadastro()`), gravando a
   escolha no Grupo pessoal do usuário (existente ou recém-criado) — não em `Usuario`.
4. Gate de acesso por módulo nas rotas/templates de cada módulo (checagem "este Grupo
   tem este módulo ativo?") — hoje não existe esse conceito porque nunca foi necessário.
5. Reorganizar a navegação em blocos "Plataforma" vs. módulo, refletindo a separação já
   descrita acima.
6. Só então decidir a questão de produto em aberto (item 2 acima): lista de paciente
   filtrada por módulo ou sempre completa.

## Fora do escopo deste documento

- Naming/branding de como "MedIA" (plataforma) e "Preparo+" (módulo) aparecem um pro
  outro na tela de login/PWA — ainda em aberto, o Silvan não decidiu como quer
  posicionar isso visualmente.
- Qualquer definição do que seria o módulo 2 — ainda não existe nem uma ideia concreta.
