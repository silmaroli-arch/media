@echo off
REM Auto-commit e push do repositorio Media para a branch dev (ambiente de
REM teste media-dev) - NUNCA para main/qualidade (produção/QA).
REM So commita/publica quando ha alguma alteracao de fato - caso contrario nao faz nada.
REM Pensado para ser chamado periodicamente pelo Agendador de Tarefas do Windows.
REM Tudo que acontece fica registrado em auto_commit_push.log, porque a janela
REM fecha rápido demais (ou nem abre, quando é o Agendador que chama) para dar
REM tempo de ler na tela.

cd /d C:\app\media\src

set LOG=C:\app\media\src\auto_commit_push.log

echo. >> "%LOG%"
echo ==== [%date% %time%] Iniciando ==== >> "%LOG%"

REM Se um lock ficou travado de uma execucao anterior que nao terminou
REM corretamente (ex.: duas execucoes do agendador se sobrepondo), ele
REM trava TODAS as proximas tentativas de commit/push para sempre. Como
REM aqui é a sua própria máquina (não o ambiente restrito do Claude), dá
REM para simplesmente apagar esses arquivos de controle antes de tentar -
REM eles so existem enquanto um git de verdade esta rodando, e o Agendador
REM nunca deixa dois de verdade rodando ao mesmo tempo (ver configuracao
REM "Nao iniciar uma nova instancia").
if exist ".git\index.lock" (
    echo [%date% %time%] Removendo index.lock travado >> "%LOG%"
    del /f /q ".git\index.lock" >> "%LOG%" 2>&1
)
if exist ".git\HEAD.lock" (
    echo [%date% %time%] Removendo HEAD.lock travado >> "%LOG%"
    del /f /q ".git\HEAD.lock" >> "%LOG%" 2>&1
)
if exist ".git\refs\heads\main.lock" (
    echo [%date% %time%] Removendo refs\heads\main.lock travado >> "%LOG%"
    del /f /q ".git\refs\heads\main.lock" >> "%LOG%" 2>&1
)
if exist ".git\refs\heads\dev.lock" (
    echo [%date% %time%] Removendo refs\heads\dev.lock travado >> "%LOG%"
    del /f /q ".git\refs\heads\dev.lock" >> "%LOG%" 2>&1
)

REM Este script SEMPRE trabalha na branch dev (ambiente de teste
REM media-dev) - nunca na main (producao/media-prod) nem na qualidade
REM (media-qa) - mesmo que a pasta esteja momentaneamente em outra
REM branch (ex.: por um checkout manual feito no TortoiseGit).
git fetch origin >> "%LOG%" 2>&1

git rev-parse --verify dev >nul 2>&1
if not %errorlevel%==0 (
    echo [%date% %time%] Branch local dev nao existe - criando a partir de origin/dev >> "%LOG%"
    git checkout -b dev origin/dev >> "%LOG%" 2>&1
) else (
    git checkout dev >> "%LOG%" 2>&1
)

if not %errorlevel%==0 (
    echo [%date% %time%] ERRO ao trocar para a branch dev - veja acima. Nada foi comitado. >> "%LOG%"
    exit /b 1
)

REM Traz o que tiver de novo no dev remoto antes de comitar por cima -
REM evita o erro "rejected (fetch first)" quando alguem/alguma automacao
REM avancou o dev remoto nesse meio tempo. O --autostash guarda de lado
REM (e devolve depois) qualquer alteracao local ainda nao comitada antes de
REM sincronizar - sem isso, o "pull --rebase" recusa rodar quando ja existem
REM arquivos modificados na pasta (que e exatamente o caso normal aqui,
REM ja que o script so roda quando ha algo nao comitado para publicar).
git pull --rebase --autostash origin dev >> "%LOG%" 2>&1
if not %errorlevel%==0 (
    echo [%date% %time%] ERRO ao sincronizar com origin/dev - veja acima. Resolva manualmente antes da proxima execucao. >> "%LOG%"
    exit /b 1
)

REM Se o Claude deixou um resumo de uma linha do que acabou de implementar
REM em ultima_mudanca.txt, usa ele como mensagem do commit - senao, cai na
REM mensagem generica de sempre. O arquivo e apagado ANTES do "git add -A"
REM (nao depois), entao ele nunca entra no historico do git - so serve
REM como um bilhete de passagem unica pra essa mensagem.
set "MENSAGEM_COMMIT=Auto-commit: sincronizacao automatica"
set "ARQUIVO_MENSAGEM=ultima_mudanca.txt"

if exist "%ARQUIVO_MENSAGEM%" (
    for /f "usebackq delims=" %%L in ("%ARQUIVO_MENSAGEM%") do (
        set "MENSAGEM_COMMIT=%%L"
        goto :mensagem_lida
    )
)
:mensagem_lida

if exist "%ARQUIVO_MENSAGEM%" (
    del /f /q "%ARQUIVO_MENSAGEM%" >> "%LOG%" 2>&1
)

git add -A >> "%LOG%" 2>&1

git diff --cached --quiet
if %errorlevel%==0 (
    echo [%date% %time%] Nada para comitar. >> "%LOG%"

    REM Mesmo sem nada novo para comitar agora, pode existir um commit local
    REM de uma execucao anterior que nunca chegou a ser enviado (ex.: um
    REM commit feito manualmente, ou por outra ferramenta, fora deste
    REM script) - sem esta checagem, ele fica preso local para sempre, ja
    REM que so chegamos a rodar "git push" mais abaixo quando ESTE script
    REM acabou de criar um commit novo.
    set "COMMITS_PENDENTES=0"
    for /f %%A in ('git rev-list --count origin/dev..dev') do set "COMMITS_PENDENTES=%%A"
    if "%COMMITS_PENDENTES%"=="0" (
        exit /b 0
    )

    echo [%date% %time%] Existem %COMMITS_PENDENTES% commits locais ainda nao enviados - publicando agora. >> "%LOG%"
    git push origin dev >> "%LOG%" 2>&1
    if not %errorlevel%==0 (
        echo [%date% %time%] ERRO no push - veja acima. >> "%LOG%"
        exit /b 1
    )
    echo [%date% %time%] Push dos commits pendentes concluido com sucesso na branch dev. >> "%LOG%"
    exit /b 0
)

git commit -m "%MENSAGEM_COMMIT%" >> "%LOG%" 2>&1
if not %errorlevel%==0 (
    echo [%date% %time%] ERRO no commit - veja acima. >> "%LOG%"
    exit /b 1
)

git push origin dev >> "%LOG%" 2>&1
if not %errorlevel%==0 (
    echo [%date% %time%] ERRO no push - veja acima. >> "%LOG%"
    exit /b 1
)

echo [%date% %time%] Commit e push concluidos com sucesso na branch dev. >> "%LOG%"
