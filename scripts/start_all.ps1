# Abre uma janela do PowerShell para cada processo do sistema.
# Uso (na raiz do projeto):  powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1

$raiz = Split-Path -Parent $PSScriptRoot

$processos = @(
    @{ Nome = "Estoque";         Cmd = "python estoque/main.py" },
    @{ Nome = "Pagamento";       Cmd = "python pagamento/main.py" },
    @{ Nome = "Entrega";         Cmd = "python entrega/main.py" },
    @{ Nome = "Promocoes";       Cmd = "python promocoes/main.py" },
    @{ Nome = "Mock Pagamento";  Cmd = "python mock_pagamento/main.py" },
    @{ Nome = "API Gateway";     Cmd = "python principal/main.py" },
    @{ Nome = "Frontend";        Cmd = "python -m http.server 8080 --directory frontend" }
)

foreach ($p in $processos) {
    Start-Process powershell -WorkingDirectory $raiz -ArgumentList "-NoExit", "-Command", "`$Host.UI.RawUI.WindowTitle = '$($p.Nome)'; $($p.Cmd)"
    Start-Sleep -Milliseconds 400
}

Write-Host "Tudo iniciado. Abra http://localhost:8080 no navegador."
