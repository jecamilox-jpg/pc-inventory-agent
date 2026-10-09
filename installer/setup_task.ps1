# ============================================
#  PC Inventory Agent - Configurar Tarea Programada
#  Ejecutar como Administrador
# ============================================

param(
    [string]$ServerUrl = "",
    [string]$ApiKey = "",
    [int]$IntervalHours = 5,
    [switch]$Uninstall
)

$TaskName = "PCInventoryAgent"
$AgentDir = Split-Path -Parent $PSScriptRoot
$AgentScript = Join-Path $AgentDir "agent\inventory_agent.py"
$PythonPath = (Get-Command python -ErrorAction SilentlyContinue).Source

# --- Verificaciones ---
if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Host "`n[ERROR] Ejecuta este script como Administrador." -ForegroundColor Red
    Write-Host "  Click derecho en PowerShell -> Ejecutar como administrador`n"
    pause
    exit 1
}

# --- Desinstalar ---
if ($Uninstall) {
    Write-Host "`nDesinstalando tarea '$TaskName'..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "[OK] Tarea eliminada.`n" -ForegroundColor Green
    exit 0
}

# --- Validar Python ---
if (-not $PythonPath) {
    Write-Host "[ERROR] Python no encontrado. Instalalo desde https://python.org" -ForegroundColor Red
    pause
    exit 1
}
Write-Host "[OK] Python: $PythonPath" -ForegroundColor Green

# --- Instalar dependencias ---
Write-Host "`n[1/4] Instalando dependencias..."
& $PythonPath -m pip install psutil requests WMI pywin32 --quiet --break-system-packages 2>$null
if ($LASTEXITCODE -ne 0) {
    & $PythonPath -m pip install psutil requests WMI pywin32 --quiet
}
Write-Host "[OK] Dependencias instaladas." -ForegroundColor Green

# --- Configurar ---
Write-Host "[2/4] Configurando agente..."
$ConfigPath = Join-Path $AgentDir "agent\config.json"

if ($ServerUrl -or $ApiKey) {
    $config = @{
        server_url       = if ($ServerUrl) { $ServerUrl } else { "http://localhost:5050" }
        api_key          = if ($ApiKey) { $ApiKey } else { "change-me-on-first-run" }
        interval_seconds = $IntervalHours * 3600
        send_software    = $true
        send_updates     = $true
        log_file         = "agent.log"
        verify_ssl       = $true
    }
    $config | ConvertTo-Json | Set-Content -Path $ConfigPath -Encoding UTF8
    Write-Host "[OK] config.json actualizado con servidor: $($config.server_url)" -ForegroundColor Green
} elseif (-not (Test-Path $ConfigPath)) {
    & $PythonPath $AgentScript --init-config
    Write-Host "[INFO] config.json creado con valores por defecto. Editalo con la URL de tu servidor." -ForegroundColor Yellow
}

# --- Probar recoleccion ---
Write-Host "[3/4] Probando recoleccion de inventario..."
$testResult = & $PythonPath $AgentScript --print 2>&1 | Select-Object -First 5
if ($testResult -match '"hostname"') {
    Write-Host "[OK] Inventario recopilado exitosamente." -ForegroundColor Green
} else {
    Write-Host "[WARN] Posible problema. Revisa permisos." -ForegroundColor Yellow
}

# --- Crear tarea programada ---
Write-Host "[4/4] Creando tarea programada..."

# Eliminar tarea existente
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

# Accion: ejecutar python inventory_agent.py --once
$Action = New-ScheduledTaskAction `
    -Execute $PythonPath `
    -Argument """$AgentScript"" --once" `
    -WorkingDirectory (Join-Path $AgentDir "agent")

# Triggers: al iniciar sesion + cada N horas
$TriggerLogon = New-ScheduledTaskTrigger -AtLogOn
$TriggerLogon.Delay = "PT2M"  # 2 min despues del login

$TriggerRepeat = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Hours $IntervalHours) `
    -RepetitionDuration (New-TimeSpan -Days 365)

# Al iniciar el sistema (antes de login)
$TriggerBoot = New-ScheduledTaskTrigger -AtStartup
$TriggerBoot.Delay = "PT3M"  # 3 min despues del boot

$Triggers = @($TriggerBoot, $TriggerLogon, $TriggerRepeat)

# Settings
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5)

# Registrar
Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Triggers `
    -Settings $Settings `
    -User "SYSTEM" `
    -RunLevel Highest `
    -Description "PC Inventory Agent - Envia inventario de hardware/software al servidor central cada $IntervalHours horas y al iniciar el equipo." `
    -Force | Out-Null

# Verificar
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Write-Host "`n============================================" -ForegroundColor Cyan
    Write-Host "  INSTALACION COMPLETADA" -ForegroundColor Green
    Write-Host "============================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Tarea:     $TaskName"
    Write-Host "  Ejecuta:   Al iniciar Windows + cada $IntervalHours horas"
    Write-Host "  Usuario:   SYSTEM"
    Write-Host "  Script:    $AgentScript"
    Write-Host "  Config:    $ConfigPath"
    Write-Host ""
    Write-Host "  Comandos utiles:" -ForegroundColor Yellow
    Write-Host "    Ver tarea:      Get-ScheduledTask -TaskName $TaskName"
    Write-Host "    Ejecutar ahora: Start-ScheduledTask -TaskName $TaskName"
    Write-Host "    Desinstalar:    .\setup_task.ps1 -Uninstall"
    Write-Host ""

    # Ejecutar primera vez
    Write-Host "Ejecutando primera recopilacion..." -ForegroundColor Yellow
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "[OK] Primera ejecucion lanzada.`n" -ForegroundColor Green
} else {
    Write-Host "[ERROR] No se pudo crear la tarea." -ForegroundColor Red
}

pause
