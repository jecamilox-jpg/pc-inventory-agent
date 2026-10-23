<#
.SYNOPSIS
    Inicia el PC Inventory Server en puerto 8086.
    Para producción en Windows Server, registrar como servicio con NSSM.

.EXAMPLE
    .\start_server.ps1
    .\start_server.ps1 -Port 8086 -ApiKey "mi-clave-segura"

.EXAMPLE
    # Registrar como servicio Windows con NSSM:
    nssm install PCInventoryServer "C:\Python312\python.exe" "C:\PCInventory\server\app.py"
    nssm set PCInventoryServer AppDirectory "C:\PCInventory\server"
    nssm set PCInventoryServer AppEnvironmentExtra PORT=8086 INVENTORY_API_KEY=mi-clave
    nssm start PCInventoryServer
#>
param(
    [int]$Port = 8086,
    [string]$ApiKey = "cambiar-esta-clave-secreta",
    [string]$AdminUser = "admin",
    [string]$AdminPassword = "",
    [switch]$Debug,
    [switch]$InstallService
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Verificar Python
try {
    $pyVersion = python --version 2>&1
    Write-Host "[OK] $pyVersion" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Python no encontrado" -ForegroundColor Red
    exit 1
}

# Instalar dependencias
Write-Host "Instalando dependencias..." -ForegroundColor Gray
pip install -r "$ScriptDir\requirements.txt" --quiet --break-system-packages 2>$null
pip install -r "$ScriptDir\requirements.txt" --quiet 2>$null

# Variables de entorno
$env:PORT = $Port
$env:INVENTORY_API_KEY = $ApiKey
$env:FLASK_DEBUG = if ($Debug) { "1" } else { "0" }
if ($AdminUser) { $env:ADMIN_USER = $AdminUser }
if ($AdminPassword) { $env:ADMIN_PASSWORD = $AdminPassword }

# Crear directorio de logs
$logDir = Join-Path $ScriptDir "logs"
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

# Registrar como servicio si se solicita
if ($InstallService) {
    $nssmPath = Get-Command nssm -ErrorAction SilentlyContinue
    if (-not $nssmPath) {
        Write-Host "[ERROR] NSSM no encontrado. Descargar de https://nssm.cc" -ForegroundColor Red
        Write-Host "  O instalar con: choco install nssm" -ForegroundColor Yellow
        exit 1
    }

    $pythonPath = (Get-Command python).Source
    $serviceName = "PCInventoryServer"

    & nssm install $serviceName $pythonPath "$ScriptDir\app.py"
    & nssm set $serviceName AppDirectory $ScriptDir
    & nssm set $serviceName AppEnvironmentExtra "PORT=$Port" "INVENTORY_API_KEY=$ApiKey"
    & nssm set $serviceName Description "PC Inventory Server - ITDeskManager"
    & nssm set $serviceName Start SERVICE_AUTO_START
    & nssm set $serviceName AppStdout "$logDir\service_stdout.log"
    & nssm set $serviceName AppStderr "$logDir\service_stderr.log"
    & nssm start $serviceName

    Write-Host ""
    Write-Host "[OK] Servicio '$serviceName' instalado e iniciado" -ForegroundColor Green
    Write-Host "  Administrar con: nssm edit $serviceName" -ForegroundColor Gray
    exit 0
}

# Iniciar servidor
Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  PC Inventory Server" -ForegroundColor Cyan
Write-Host "  Puerto: $Port" -ForegroundColor Cyan
Write-Host "  API Key: $($ApiKey.Substring(0, [Math]::Min(8, $ApiKey.Length)))..." -ForegroundColor Cyan
Write-Host "  URL: http://localhost:$Port" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Presiona Ctrl+C para detener" -ForegroundColor Gray
Write-Host ""

python "$ScriptDir\app.py"
