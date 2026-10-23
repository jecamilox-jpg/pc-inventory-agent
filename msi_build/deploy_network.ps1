<#
.SYNOPSIS
    Despliega el PC Inventory Agent en múltiples PCs de la red local.
    Ejecutar como Administrador de Dominio.

.DESCRIPTION
    Copia los archivos a cada PC y ejecuta el instalador silencioso.
    Requiere: acceso administrativo remoto (admin shares C$) y
    WinRM habilitado o PsExec disponible.

.PARAMETER ComputerList
    Archivo .txt con un hostname/IP por línea

.PARAMETER Computers
    Lista de hostnames/IPs separados por coma

.PARAMETER SourcePath
    Carpeta con PCInventoryAgent.exe, config.json e install_silent.ps1

.EXAMPLE
    # Desplegar en lista de PCs
    .\deploy_network.ps1 -ComputerList ".\pcs.txt" -SourcePath ".\dist"

    # Desplegar en PCs específicos
    .\deploy_network.ps1 -Computers "PC-CONTA,PC-RRHH,PC-GERENCIA" -SourcePath ".\dist"
#>

param(
    [string]$ComputerList,
    [string]$Computers,
    [Parameter(Mandatory=$true)]
    [string]$SourcePath,
    [switch]$UseWinRM,
    [switch]$DryRun,
    [int]$MaxParallel = 5
)

$ErrorActionPreference = "Continue"

# ---------- Obtener lista de PCs ----------
$targetPCs = @()

if ($ComputerList -and (Test-Path $ComputerList)) {
    $targetPCs = Get-Content $ComputerList | Where-Object { $_.Trim() -ne "" -and -not $_.StartsWith("#") }
} elseif ($Computers) {
    $targetPCs = $Computers -split "," | ForEach-Object { $_.Trim() }
} else {
    Write-Host "[ERROR] Especificar -ComputerList o -Computers" -ForegroundColor Red
    Write-Host ""
    Write-Host "Ejemplo:" -ForegroundColor Yellow
    Write-Host '  .\deploy_network.ps1 -Computers "PC01,PC02,PC03" -SourcePath ".\dist"'
    Write-Host '  .\deploy_network.ps1 -ComputerList ".\pcs.txt" -SourcePath ".\dist"'
    exit 1
}

# Verificar archivos fuente
$requiredFiles = @("PCInventoryAgent.exe", "config.json", "install_silent.ps1")
foreach ($file in $requiredFiles) {
    if (-not (Test-Path (Join-Path $SourcePath $file))) {
        Write-Host "[ERROR] No se encontró: $SourcePath\$file" -ForegroundColor Red
        exit 1
    }
}

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Despliegue de PC Inventory Agent" -ForegroundColor Cyan
Write-Host "  PCs objetivo: $($targetPCs.Count)" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

$results = @()
$successCount = 0
$failCount = 0

foreach ($pc in $targetPCs) {
    Write-Host "[$($targetPCs.IndexOf($pc)+1)/$($targetPCs.Count)] $pc ... " -NoNewline

    if ($DryRun) {
        Write-Host "[DRY RUN] Se copiaría e instalaría" -ForegroundColor Gray
        continue
    }

    # Verificar conectividad
    if (-not (Test-Connection -ComputerName $pc -Count 1 -Quiet -TimeoutSeconds 3)) {
        Write-Host "[OFFLINE]" -ForegroundColor Red
        $results += [PSCustomObject]@{ PC=$pc; Status="Offline"; Error="No responde ping" }
        $failCount++
        continue
    }

    try {
        # Crear directorio temporal en el PC remoto
        $remotePath = "\\$pc\C$\Temp\PCInventoryAgent_Install"

        if (Test-Path $remotePath) {
            Remove-Item $remotePath -Recurse -Force
        }
        New-Item -ItemType Directory -Path $remotePath -Force | Out-Null

        # Copiar archivos
        Copy-Item "$SourcePath\PCInventoryAgent.exe" "$remotePath\" -Force
        Copy-Item "$SourcePath\config.json" "$remotePath\" -Force
        Copy-Item "$SourcePath\install_silent.ps1" "$remotePath\" -Force

        # Ejecutar instalador remotamente
        if ($UseWinRM) {
            # Método WinRM (requiere WinRM habilitado)
            Invoke-Command -ComputerName $pc -ScriptBlock {
                Set-Location "C:\Temp\PCInventoryAgent_Install"
                powershell -ExecutionPolicy Bypass -File ".\install_silent.ps1"
            } -ErrorAction Stop
        } else {
            # Método PsExec / schtasks (más compatible)
            $taskName = "PCInventoryAgent_Deploy"
            $taskCmd = "powershell.exe -ExecutionPolicy Bypass -File C:\Temp\PCInventoryAgent_Install\install_silent.ps1"

            schtasks /create /s $pc /tn $taskName /tr $taskCmd /sc once /st 00:00 /ru SYSTEM /rl HIGHEST /f 2>&1 | Out-Null
            schtasks /run /s $pc /tn $taskName 2>&1 | Out-Null

            Start-Sleep -Seconds 10

            schtasks /delete /s $pc /tn $taskName /f 2>&1 | Out-Null
        }

        # Verificar instalación
        Start-Sleep -Seconds 5
        if (Test-Path "\\$pc\C$\Program Files\PCInventoryAgent\PCInventoryAgent.exe") {
            Write-Host "[OK]" -ForegroundColor Green
            $results += [PSCustomObject]@{ PC=$pc; Status="OK"; Error="" }
            $successCount++
        } else {
            Write-Host "[VERIFICAR]" -ForegroundColor Yellow
            $results += [PSCustomObject]@{ PC=$pc; Status="Verificar"; Error="Exe no encontrado después de instalar" }
            $failCount++
        }

        # Limpiar temporal
        Remove-Item $remotePath -Recurse -Force -ErrorAction SilentlyContinue

    } catch {
        Write-Host "[ERROR] $($_.Exception.Message)" -ForegroundColor Red
        $results += [PSCustomObject]@{ PC=$pc; Status="Error"; Error=$_.Exception.Message }
        $failCount++
    }
}

# ---------- Resumen ----------
Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  RESUMEN DE DESPLIEGUE" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Total: $($targetPCs.Count)" -ForegroundColor White
Write-Host "  Exitosos: $successCount" -ForegroundColor Green
Write-Host "  Fallidos: $failCount" -ForegroundColor $(if ($failCount -gt 0) { "Red" } else { "Gray" })
Write-Host ""

# Guardar reporte
$reportFile = "deploy_report_$(Get-Date -Format 'yyyyMMdd_HHmmss').csv"
$results | Export-Csv -Path $reportFile -NoTypeInformation -Encoding UTF8
Write-Host "Reporte guardado: $reportFile" -ForegroundColor Gray

# Mostrar errores
$errors = $results | Where-Object { $_.Status -ne "OK" }
if ($errors) {
    Write-Host ""
    Write-Host "PCs con problemas:" -ForegroundColor Yellow
    $errors | Format-Table -AutoSize
}
