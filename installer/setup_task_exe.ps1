# ============================================
#  PC Inventory Agent - Tarea Programada (EXE)
#  Para equipos SIN Python instalado
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
$ExePath = Join-Path $AgentDir "PCInventoryAgent.exe"
$ConfigPath = Join-Path $AgentDir "config.json"

# --- Verificar privilegios ---
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

# --- Verificar EXE ---
if (-not (Test-Path $ExePath)) {
    Write-Host "[ERROR] No se encontro PCInventoryAgent.exe" -ForegroundColor Red
    Write-Host "  Descargalo de GitHub Releases o compilalo con: python build_exe.py" -ForegroundColor Yellow
    pause
    exit 1
}
Write-Host "[OK] Ejecutable: $ExePath" -ForegroundColor Green

# --- Configurar ---
Write-Host "`n[1/3] Configurando agente..."
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
    # Crear config por defecto
    $defaultConfig = @{
        server_url       = "http://localhost:5050"
        api_key          = "change-me-on-first-run"
        interval_seconds = 18000
        send_software    = $true
        send_updates     = $true
        log_file         = "agent.log"
        verify_ssl       = $true
    }
    $defaultConfig | ConvertTo-Json | Set-Content -Path $ConfigPath -Encoding UTF8
    Write-Host "[INFO] config.json creado con valores por defecto. Editalo con la URL de tu servidor." -ForegroundColor Yellow
}

# --- Probar recoleccion ---
Write-Host "[2/3] Probando recoleccion de inventario..."
$testResult = & $ExePath --print 2>&1 | Select-Object -First 5
if ($testResult -match '"hostname"') {
    Write-Host "[OK] Inventario recopilado exitosamente." -ForegroundColor Green
} else {
    Write-Host "[WARN] Posible problema. Revisa permisos." -ForegroundColor Yellow
}

# --- Crear tarea programada ---
Write-Host "[3/3] Creando tarea programada..."

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

$Action = New-ScheduledTaskAction `
    -Execute $ExePath `
    -Argument "--once" `
    -WorkingDirectory $AgentDir

$TriggerLogon = New-ScheduledTaskTrigger -AtLogOn
$TriggerLogon.Delay = "PT2M"

$TriggerRepeat = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Hours $IntervalHours) `
    -RepetitionDuration (New-TimeSpan -Days 365)

$TriggerBoot = New-ScheduledTaskTrigger -AtStartup
$TriggerBoot.Delay = "PT3M"

$Triggers = @($TriggerBoot, $TriggerLogon, $TriggerRepeat)

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Triggers `
    -Settings $Settings `
    -User "SYSTEM" `
    -RunLevel Highest `
    -Description "PC Inventory Agent - Envia inventario cada $IntervalHours horas y al iniciar (modo EXE, sin Python)." `
    -Force | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Write-Host "`n============================================" -ForegroundColor Cyan
    Write-Host "  INSTALACION COMPLETADA (EXE)" -ForegroundColor Green
    Write-Host "============================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Tarea:     $TaskName"
    Write-Host "  Ejecuta:   Al iniciar Windows + cada $IntervalHours horas"
    Write-Host "  Usuario:   SYSTEM"
    Write-Host "  Ejecutable: $ExePath"
    Write-Host "  Config:    $ConfigPath"
    Write-Host ""
    Write-Host "  NO requiere Python instalado!" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Comandos utiles:" -ForegroundColor Yellow
    Write-Host "    Ver tarea:      Get-ScheduledTask -TaskName $TaskName"
    Write-Host "    Ejecutar ahora: Start-ScheduledTask -TaskName $TaskName"
    Write-Host "    Desinstalar:    .\setup_task_exe.ps1 -Uninstall"
    Write-Host ""

    Write-Host "Ejecutando primera recopilacion..." -ForegroundColor Yellow
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "[OK] Primera ejecucion lanzada.`n" -ForegroundColor Green
} else {
    Write-Host "[ERROR] No se pudo crear la tarea." -ForegroundColor Red
}

pause
