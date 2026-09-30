# Roteiro de validação - Terceira IA (base de conhecimento) e demais mudanças em `dev`

Tudo abaixo foi escrito sem poder executar o Flask no ambiente do assistente. Nenhum teste novo foi rodado.
Rode na sua máquina, na pasta do projeto, com o ambiente virtual ativado.

## 1. Testes automatizados

Cada teste usa um banco SQLite próprio. Os que precisam de `seed.py` têm essa linha marcada.
Em PowerShell, defina antes de cada bloco: `$env:DATABASE_URL="sqlite:///teste_x.db"`.

| Ordem | Teste | Precisa de seed? | O que valida |
|---|---|---|---|
| 1 | `test_licenca_gerar_link_medico.py` | não | botão "Gerar link de pagamento" do médico |
| 2 | `test_licencas_aplicar_valor_padrao.py` | não | botão "Aplicar valor padrão a todos" do dono |
| 3 | `test_tipos_exame.py` | não | lista de tipos de exame e campo no preparo |
| 4 | `test_base_conhecimento.py` | não | carga inicial (331 itens), tela do dono, busca, histórico |
| 5 | `test_base_integracao_chat.py` | **sim** | base como terceira voz no chat |
| 6 | `test_base_aprendizado.py` | **sim** | aprendizado com respostas dos médicos |
| 7 | `test_base_sugestoes.py` | não | especialidade, base por especialidade e sugestões |

Exemplo para os testes com seed:

    $env:DATABASE_URL="sqlite:///teste_base_integracao.db"
    python seed.py
    python test_base_integracao_chat.py

Regressão (mudei cadastro, chat, perguntas, licença e Meus dados):
`test_pix_licenca.py`, `test_licenca_pagamento_valor_e_gateway.py`, `test_preparo_form_abas_importar.py`,
`test_dono_conteudo_clinico.py`, `test_whatsapp_pergunta.py`, `test_perguntas_medico_so_do_proprio_exame.py`,
`test_meus_dados.py`, `test_cadastro_aprovacao_pergunta.py`, `test_cadastro_global_importar_cpf.py`.

Se algum falhar: cole a saída aqui. Erros mais prováveis: nome de campo/rota diferente do que assumi, ou uma
checagem de teste antigo que não previa o campo novo (tipo de exame, especialidade).

## 2. Conferência em `dev` (Render `media-dev`)

O deploy roda `migrar_banco.py`, que cria as tabelas novas, faz as colunas novas e carrega os 66 tipos e os 331 itens.

1. Abra o log do deploy e confirme que a migração terminou sem erro.
2. Dono > **Tipos de exame**: 66 tipos. Ajuste especialidades se quiser.
3. Dono > **Base de conhecimento**: 331 itens, todos "não revisados". Use o filtro por tipo e revise.
4. Cadastre um médico novo escolhendo a especialidade. Em Meus dados, altere.
5. No médico: **Base compartilhada** mostra só os tipos da especialidade. Envie uma sugestão de alteração e uma de item novo.
6. Dono > Base > **Ver sugestões**: aprove uma (histórico do item deve guardar a versão anterior), rejeite outra.
7. Edite um preparo existente e escolha o **Tipo de exame** (a base só atende preparos que têm tipo).

## 3. Calibrar a busca ANTES de ligar

Na tela da base, use **Testar busca** com 10 a 15 perguntas reais de pacientes, em cada provedor:
- perguntas com o mesmo sentido do item (ex.: "posso dirigir depois?") devem passar do limiar
- perguntas sem relação (ex.: "qual a cor do gatorade") não devem
- paráfrases ("o remédio para limpar o intestino não funcionou") tendem a falhar em palavra-chave: teste OpenAI/Gemini e use **Calcular vetores**

Limiares atuais são palpites: palavra-chave 0,60, embeddings 0,70, "mesma pergunta" para atualizar 0,85.

## 4. Ligar aos poucos (em dev)

1. Ligue **Usar a base** (interruptor 1). Faça 5 a 10 perguntas de paciente em preparos com tipo definido.
2. Confira na tela de perguntas do médico: 4ª coluna "Base", badge "Base diverge", alerta correto.
3. Só depois ligue **Aprender com as respostas dos médicos** (interruptor 2). Responda algumas perguntas e confira no dono os itens novos ("origem: médico", não revisado) e os textos generalizados. Não pode sobrar nome, telefone ou valor.
4. O custo de IA extra aparece em Custo de IA (arbitragem e generalização).

## 5. Produção (`media-prod`)

- [ ] Configurar o Mercado Pago (token, webhook) em media-prod
- [ ] Clicar em **Aplicar valor padrão a todos** na tela Licenças (o médico com "valor não definido")
- [ ] Definir tipo de exame nos preparos dos médicos ativos
- [ ] Interruptores da base: manter DESLIGADOS até revisar o conteúdo, ligar por último

## 6. Pull request `dev` -> `main`

1. GitHub: `silmaroli-arch/media`, PR de `dev` para `main`.
2. Na descrição: 31 commits automáticos, 16 arquivos, mais as mudanças da terceira IA (fatias 1 a 5).
3. **Manter o `render.yaml` da `main`** (não trazer o da `dev`). Verifique isso no diff antes de mergear.
4. Depois do merge, acompanhe o deploy de prod e a migração.
5. Se algo der errado, os dois interruptores da base desligam a terceira IA sem desfazer o deploy.
