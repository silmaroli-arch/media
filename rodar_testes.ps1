# rodar_testes.ps1
# Roda toda a suite de testes (test_*.py) do projeto Media/MedIA contra um
# banco SQLite local descartavel (nunca toca no Postgres real do .env) e
# imprime um resumo no final: quantos passaram, quantos falharam, e o nome
# de cada um que falhou (com a mensagem de erro).
#
# Como usar (PowerShell, na pasta do projeto C:\app\media\src):
#   .\rodar_testes.ps1
#
# Rodar so um arquivo especifico:
#   .\rodar_testes.ps1 -Arquivo test_pix_licenca.py

param(
    [string]$Arquivo = ""
)

$ErrorActionPreference = "Continue"
$pastaTestes = if ($Arquivo) { @($Arquivo) } else { Get-ChildItem -Filter "test_*.py" -Name }

$passaram = @()
$falharam = @()

Write-Host "Encontrados $($pastaTestes.Count) arquivo(s) de teste.`n" -ForegroundColor Cyan

foreach ($arquivo in $pastaTestes) {
    Write-Host "==> $arquivo" -ForegroundColor Yellow
    $dbFile = "teste_$($arquivo.Replace('.py',''))_" + ".db"
    if (Test-Path $dbFile) { Remove-Item $dbFile -Force }

    $env:DATABASE_URL = "sqlite:///$dbFile"
    $saida = python $arquivo 2>&1
    $codigoSaida = $LASTEXITCODE

    if ($codigoSaida -eq 0) {
        Write-Host "   OK`n" -ForegroundColor Green
        $passaram += $arquivo
    } else {
        Write-Host "   FALHOU (codigo $codigoSaida)`n" -ForegroundColor Red
        $falharam += @{ arquivo = $arquivo; saida = $saida }
    }

    Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
    if (Test-Path $dbFile) { Remove-Item $dbFile -Force -ErrorAction SilentlyContinue }
}

Write-Host "`n========== RESUMO ==========" -ForegroundColor Cyan
Write-Host "Passaram: $($passaram.Count)" -ForegroundColor Green
Write-Host "Falharam: $($falharam.Count)" -ForegroundColor $(if ($falharam.Count -gt 0) { "Red" } else { "Green" })

if ($falharam.Count -gt 0) {
    Write-Host "`nArquivos que falharam:" -ForegroundColor Red
    foreach ($f in $falharam) {
        Write-Host "`n--- $($f.arquivo) ---" -ForegroundColor Red
        Write-Host ($f.saida | Select-Object -Last 25)
    }
}
