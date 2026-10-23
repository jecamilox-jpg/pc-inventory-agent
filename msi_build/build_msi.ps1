<#
.SYNOPSIS
    Build script para generar el MSI del PC Inventory Agent.
    Ejecutar como Administrador en un PC Windows con Python instalado.

.DESCRIPTION
    1. Compila el agente Python a .exe con PyInstaller
    2. Genera el MSI con WiX Toolset
    3. El MSI resultante se puede desplegar por GPO, SCCM, o manualmente

.PARAMETER ServerUrl
    URL del servidor de inventario

.PARAMETER ApiKey
    API Key para autenticación

.PARAMETER IntervalHours
    Intervalo de reporte en horas (default: 5)

.EXAMPLE
    .\build_msi.ps1 -ServerUrl "http://172.16.100.4:8086" -ApiKey "mi-clave-123"
#>

param(
    [Parameter(Mandatory=$true)]
    [string]$ServerUrl,

    [Parameter(Mandatory=$true)]
    [string]$ApiKey,

    [int]$IntervalHours = 5,

    [switch]$SkipExeBuild,
    [switch]$SkipMsiBuild
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$AgentDir = Join-Path $ProjectRoot "agent"
$DistDir = Join-Path $ScriptDir "dist"
$BuildDir = Join-Path $ScriptDir "build"

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  PC Inventory Agent - MSI Builder" -ForegroundColor Cyan
Write-Host "  ITDeskManager" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# ---------- Verificar requisitos ----------
function Test-Command($cmd) {
    try { Get-Command $cmd -ErrorAction Stop | Out-Null; return $true }
    catch { return $false }
}

# Python
if (-not (Test-Command "python")) {
    Write-Host "[ERROR] Python no encontrado. Instalar desde python.org" -ForegroundColor Red
    exit 1
}
Write-Host "[OK] Python encontrado: $(python --version 2>&1)" -ForegroundColor Green

# ---------- Paso 1: Instalar dependencias de build ----------
Write-Host ""
Write-Host "--- Paso 1: Instalando dependencias ---" -ForegroundColor Yellow

python -m pip install --quiet --upgrade pip
python -m pip install --quiet pyinstaller
python -m pip install --quiet -r (Join-Path $AgentDir "requirements.txt")

Write-Host "[OK] Dependencias instaladas" -ForegroundColor Green

# ---------- Paso 2: Compilar .exe con PyInstaller ----------
if (-not $SkipExeBuild) {
    Write-Host ""
    Write-Host "--- Paso 2: Compilando .exe con PyInstaller ---" -ForegroundColor Yellow

    # Limpiar build anterior
    if (Test-Path $DistDir) { Remove-Item $DistDir -Recurse -Force }
    if (Test-Path $BuildDir) { Remove-Item $BuildDir -Recurse -Force }

    New-Item -ItemType Directory -Path $DistDir -Force | Out-Null

    $pyinstallerArgs = @(
        "--onefile",
        "--name", "PCInventoryAgent",
        "--distpath", $DistDir,
        "--workpath", $BuildDir,
        "--specpath", $BuildDir,
        "--clean",
        "--noupx",
        "--hidden-import", "psutil",
        "--hidden-import", "requests",
        "--hidden-import", "wmi",
        "--hidden-import", "win32com",
        "--hidden-import", "win32api",
        "--hidden-import", "win32com.client",
        "--hidden-import", "pywintypes",
        "--hidden-import", "pythoncom",
        "--hidden-import", "socket",
        "--hidden-import", "struct",
        "--add-data", "$AgentDir\collector.py;.",
        "--add-data", "$AgentDir\config.py;.",
        "--add-data", "$AgentDir\wol.py;.",
        (Join-Path $AgentDir "inventory_agent.py")
    )

    # Agregar icono si existe
    $iconPath = Join-Path $ScriptDir "agent.ico"
    if (Test-Path $iconPath) {
        $pyinstallerArgs = @("--icon", $iconPath) + $pyinstallerArgs
    }

    Write-Host "  Ejecutando PyInstaller..." -ForegroundColor Gray
    & python -m PyInstaller @pyinstallerArgs

    if (-not (Test-Path (Join-Path $DistDir "PCInventoryAgent.exe"))) {
        Write-Host "[ERROR] Falló la compilación del .exe" -ForegroundColor Red
        exit 1
    }

    $exeSize = (Get-Item (Join-Path $DistDir "PCInventoryAgent.exe")).Length / 1MB
    Write-Host "[OK] PCInventoryAgent.exe compilado ($([math]::Round($exeSize, 1)) MB)" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "--- Paso 2: Saltado (SkipExeBuild) ---" -ForegroundColor Gray
}

# ---------- Paso 3: Generar archivos auxiliares ----------
Write-Host ""
Write-Host "--- Paso 3: Generando archivos de configuración ---" -ForegroundColor Yellow

# Config.json con los parámetros proporcionados
$intervalSeconds = $IntervalHours * 3600
$config = @{
    server_url      = $ServerUrl
    api_key         = $ApiKey
    interval_seconds = $intervalSeconds
    send_software   = $true
    send_updates    = $true
    verify_ssl      = $true
    log_file        = "logs\agent.log"
} | ConvertTo-Json -Depth 2

$config | Out-File -FilePath (Join-Path $DistDir "config.json") -Encoding UTF8

# README
@"
PC Inventory Agent v1.1.0
========================
ITDeskManager - Agente de Inventario

Este agente recopila informacion del hardware y software del equipo
y la envia periodicamente al servidor central.

Servidor: $ServerUrl
Intervalo: Cada $IntervalHours horas

Tareas programadas creadas:
  - PCInventoryAgent: Se ejecuta al iniciar el sistema (modo daemon)
  - PCInventoryAgent_Login: Se ejecuta al iniciar sesion (reporte unico)

Directorio de logs: C:\Program Files\PCInventoryAgent\logs\

Para desinstalar: Panel de Control > Programas > PC Inventory Agent
"@ | Out-File -FilePath (Join-Path $DistDir "README.txt") -Encoding UTF8

Write-Host "[OK] config.json y README.txt generados" -ForegroundColor Green

# ---------- Paso 4: Crear icono si no existe ----------
$iconDist = Join-Path $ScriptDir "agent.ico"
if (-not (Test-Path $iconDist)) {
    Write-Host "  [AVISO] agent.ico no encontrado, creando icono básico..." -ForegroundColor Gray
    # Crear un ICO minimo (1x1 pixel) como placeholder
    $icoBytes = [byte[]]@(
        0,0,1,0,1,0,16,16,0,0,1,0,32,0,104,4,0,0,22,0,0,0,
        40,0,0,0,16,0,0,0,32,0,0,0,1,0,32,0,0,0,0,0,0,4,0,0,
        0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
    )
    # Rellenar con pixels verdes (color ITDeskManager)
    $pixels = New-Object byte[] 1024
    for ($i = 0; $i -lt 1024; $i += 4) {
        $pixels[$i] = 0x4C     # B
        $pixels[$i+1] = 0xAF   # G
        $pixels[$i+2] = 0x50   # R
        $pixels[$i+3] = 0xFF   # A
    }
    $mask = New-Object byte[] 64
    $fullIco = $icoBytes + $pixels + $mask
    [System.IO.File]::WriteAllBytes($iconDist, $fullIco)
    Write-Host "  [OK] Icono placeholder creado" -ForegroundColor Gray
}

# ---------- Paso 5: Generar licencia ----------
$licensePath = Join-Path $ScriptDir "license.rtf"
if (-not (Test-Path $licensePath)) {
    @"
{\rtf1\ansi\deff0
{\fonttbl{\f0\fswiss\fcharset0 Arial;}}
\viewkind4\uc1\pard\lang1033\f0\fs20
\b PC Inventory Agent - ITDeskManager\b0\par
\par
Este software recopila informaci\'f3n del hardware y software de este equipo y la env\'eda al servidor central de inventario de su organizaci\'f3n.\par
\par
Informaci\'f3n recopilada:\par
- Nombre del equipo, sistema operativo, usuario\par
- Procesador, memoria RAM, discos duros\par
- Tarjeta gr\'e1fica, adaptadores de red\par
- Software instalado y actualizaciones de Windows\par
\par
Los datos se env\'edan \'fanicamente al servidor configurado por su administrador de TI.\par
\par
\b Al instalar este software, usted acepta que la informaci\'f3n del equipo ser\'e1 recopilada y enviada al servidor de inventario.\b0\par
}
"@ | Out-File -FilePath $licensePath -Encoding ASCII
    Write-Host "[OK] Licencia RTF generada" -ForegroundColor Green
}

# ---------- Paso 6: Compilar MSI con WiX ----------
if (-not $SkipMsiBuild) {
    Write-Host ""
    Write-Host "--- Paso 6: Compilando MSI con WiX Toolset ---" -ForegroundColor Yellow

    # Buscar WiX Toolset
    $wixPaths = @(
        "${env:WIX}bin",
        "C:\Program Files (x86)\WiX Toolset v3.14\bin",
        "C:\Program Files (x86)\WiX Toolset v3.11\bin",
        "C:\Program Files (x86)\WiX Toolset v4.0\bin",
        "$env:USERPROFILE\.dotnet\tools"
    )

    $candlePath = $null
    $lightPath = $null

    foreach ($p in $wixPaths) {
        if (Test-Path (Join-Path $p "candle.exe")) {
            $candlePath = Join-Path $p "candle.exe"
            $lightPath = Join-Path $p "light.exe"
            break
        }
    }

    # Intentar con dotnet tool (WiX v4+)
    $useWix4 = $false
    if (-not $candlePath) {
        if (Test-Command "wix") {
            $useWix4 = $true
            Write-Host "  Usando WiX v4+ (dotnet tool)" -ForegroundColor Gray
        }
    }

    if ($candlePath) {
        Write-Host "  WiX encontrado en: $(Split-Path $candlePath)" -ForegroundColor Gray

        # Compilar .wxs -> .wixobj
        $wxsFile = Join-Path $ScriptDir "PCInventoryAgent.wxs"
        $wixobjFile = Join-Path $ScriptDir "PCInventoryAgent.wixobj"
        $msiFile = Join-Path $ScriptDir "PCInventoryAgent.msi"

        & $candlePath $wxsFile `
            -out $wixobjFile `
            -dServerUrl="$ServerUrl" `
            -dApiKey="$ApiKey" `
            -ext WixUtilExtension `
            -ext WixUIExtension

        if ($LASTEXITCODE -ne 0) {
            Write-Host "[ERROR] Falló candle.exe (compilación WiX)" -ForegroundColor Red
            Write-Host "  Revisa el archivo .wxs" -ForegroundColor Red
            exit 1
        }

        # Linkar .wixobj -> .msi
        & $lightPath $wixobjFile `
            -out $msiFile `
            -ext WixUtilExtension `
            -ext WixUIExtension `
            -cultures:es-ES `
            -sice:ICE61 `
            -sice:ICE91

        if ($LASTEXITCODE -ne 0) {
            Write-Host "[ERROR] Falló light.exe (linkeo WiX)" -ForegroundColor Red
            exit 1
        }

        $msiSize = (Get-Item $msiFile).Length / 1MB
        Write-Host "[OK] MSI generado: $msiFile ($([math]::Round($msiSize, 1)) MB)" -ForegroundColor Green

        # Limpiar temporales
        Remove-Item $wixobjFile -Force -ErrorAction SilentlyContinue

    } elseif ($useWix4) {
        $wxsFile = Join-Path $ScriptDir "PCInventoryAgent.wxs"
        $msiFile = Join-Path $ScriptDir "PCInventoryAgent.msi"

        & wix build $wxsFile -o $msiFile -ext WixToolset.Util.wixext -ext WixToolset.UI.wixext

        if ($LASTEXITCODE -eq 0) {
            Write-Host "[OK] MSI generado: $msiFile" -ForegroundColor Green
        } else {
            Write-Host "[ERROR] Falló la compilación WiX v4" -ForegroundColor Red
            exit 1
        }
    } else {
        Write-Host ""
        Write-Host "[AVISO] WiX Toolset no encontrado." -ForegroundColor Yellow
        Write-Host "  Para generar el MSI, instalar WiX Toolset:" -ForegroundColor Yellow
        Write-Host "  Opción 1: https://wixtoolset.org/docs/wix3/" -ForegroundColor White
        Write-Host "  Opción 2: dotnet tool install --global wix" -ForegroundColor White
        Write-Host ""
        Write-Host "  Mientras tanto, puedes usar el .exe directamente:" -ForegroundColor Yellow
        Write-Host "  $DistDir\PCInventoryAgent.exe" -ForegroundColor White
        Write-Host ""
        Write-Host "  O usar el instalador alternativo sin WiX..." -ForegroundColor Yellow

        # ---------- Generar instalador alternativo (puro PowerShell) ----------
        Generate-StandaloneInstaller
    }
} else {
    Write-Host ""
    Write-Host "--- Paso 6: Saltado (SkipMsiBuild) ---" -ForegroundColor Gray
}

# ---------- Paso 7: Generar instalador silencioso alternativo ----------
Write-Host ""
Write-Host "--- Generando instalador alternativo (sin WiX) ---" -ForegroundColor Yellow

$installerScript = @'
<#
.SYNOPSIS
    Instalador silencioso del PC Inventory Agent (alternativa sin MSI)
    Ejecutar como Administrador.

.PARAMETER Uninstall
    Desinstala el agente

.EXAMPLE
    # Instalar (silencioso para GPO):
    powershell -ExecutionPolicy Bypass -File install_silent.ps1

    # Desinstalar:
    powershell -ExecutionPolicy Bypass -File install_silent.ps1 -Uninstall
#>
param([switch]$Uninstall)

$ErrorActionPreference = "Stop"
$InstallDir = "C:\Program Files\PCInventoryAgent"
$TaskName = "PCInventoryAgent"
$TaskNameLogin = "PCInventoryAgent_Login"
$ServiceName = "PCInventoryAgent"

if ($Uninstall) {
    Write-Host "Desinstalando PC Inventory Agent..."

    # Detener proceso
    Stop-Process -Name "PCInventoryAgent" -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2

    # Eliminar tareas
    schtasks /delete /tn $TaskName /f 2>$null
    schtasks /delete /tn $TaskNameLogin /f 2>$null

    # Eliminar directorio
    if (Test-Path $InstallDir) {
        Remove-Item $InstallDir -Recurse -Force
    }

    # Eliminar registro
    Remove-Item "HKLM:\SOFTWARE\ITDeskManager\PCInventoryAgent" -Force -ErrorAction SilentlyContinue

    Write-Host "[OK] PC Inventory Agent desinstalado." -ForegroundColor Green
    exit 0
}

# ---------- Instalar ----------
Write-Host "Instalando PC Inventory Agent..."

# Verificar admin
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[ERROR] Ejecutar como Administrador" -ForegroundColor Red
    exit 1
}

# Crear directorio
New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
New-Item -ItemType Directory -Path "$InstallDir\logs" -Force | Out-Null

# Copiar archivos (desde el mismo directorio del script)
$ScriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
Copy-Item "$ScriptPath\PCInventoryAgent.exe" "$InstallDir\" -Force
Copy-Item "$ScriptPath\config.json" "$InstallDir\" -Force

# Crear tareas programadas
schtasks /create /tn $TaskName /tr "`"$InstallDir\PCInventoryAgent.exe`" --daemon" /sc onstart /ru SYSTEM /rl HIGHEST /f | Out-Null
schtasks /create /tn $TaskNameLogin /tr "`"$InstallDir\PCInventoryAgent.exe`" --once" /sc onlogon /ru SYSTEM /rl HIGHEST /f | Out-Null

# Registro (para Add/Remove Programs)
$regPath = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PCInventoryAgent"
New-Item -Path $regPath -Force | Out-Null
Set-ItemProperty -Path $regPath -Name "DisplayName" -Value "PC Inventory Agent"
Set-ItemProperty -Path $regPath -Name "Publisher" -Value "ITDeskManager"
Set-ItemProperty -Path $regPath -Name "DisplayVersion" -Value "1.1.0"
Set-ItemProperty -Path $regPath -Name "InstallLocation" -Value $InstallDir
Set-ItemProperty -Path $regPath -Name "UninstallString" -Value "powershell -ExecutionPolicy Bypass -File `"$InstallDir\uninstall.ps1`""
Set-ItemProperty -Path $regPath -Name "NoModify" -Value 1
Set-ItemProperty -Path $regPath -Name "NoRepair" -Value 1

# Copiar script de desinstalación
$uninstallScript = @"
param([switch]`$Force)
`$InstallDir = "$InstallDir"
Stop-Process -Name "PCInventoryAgent" -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
schtasks /delete /tn "PCInventoryAgent" /f 2>`$null
schtasks /delete /tn "PCInventoryAgent_Login" /f 2>`$null
Remove-Item "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PCInventoryAgent" -Force -ErrorAction SilentlyContinue
Remove-Item `$InstallDir -Recurse -Force
Write-Host "[OK] Desinstalado." -ForegroundColor Green
"@
$uninstallScript | Out-File -FilePath "$InstallDir\uninstall.ps1" -Encoding UTF8

# Primera ejecución
Start-Process -FilePath "$InstallDir\PCInventoryAgent.exe" -ArgumentList "--once" -WindowStyle Hidden

# Registro ITDeskManager
$regItd = "HKLM:\SOFTWARE\ITDeskManager\PCInventoryAgent"
New-Item -Path $regItd -Force | Out-Null
Set-ItemProperty -Path $regItd -Name "Installed" -Value 1
Set-ItemProperty -Path $regItd -Name "Version" -Value "1.1.0"
Set-ItemProperty -Path $regItd -Name "InstallPath" -Value $InstallDir
Set-ItemProperty -Path $regItd -Name "InstallDate" -Value (Get-Date -Format "yyyy-MM-dd HH:mm:ss")

Write-Host ""
Write-Host "[OK] PC Inventory Agent instalado exitosamente" -ForegroundColor Green
Write-Host "  Directorio: $InstallDir" -ForegroundColor Gray
Write-Host "  Tareas: $TaskName, $TaskNameLogin" -ForegroundColor Gray
Write-Host "  Primera recolección ejecutándose en segundo plano..." -ForegroundColor Gray
'@

$installerScript | Out-File -FilePath (Join-Path $DistDir "install_silent.ps1") -Encoding UTF8
Write-Host "[OK] install_silent.ps1 generado" -ForegroundColor Green

# ---------- Resumen ----------
Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  BUILD COMPLETADO" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Archivos generados en: $DistDir" -ForegroundColor White
Write-Host ""
Write-Host "  PCInventoryAgent.exe   - Agente compilado" -ForegroundColor Gray
Write-Host "  config.json            - Configuración" -ForegroundColor Gray
Write-Host "  install_silent.ps1     - Instalador silencioso" -ForegroundColor Gray
Write-Host "  README.txt             - Documentación" -ForegroundColor Gray
Write-Host ""

if (Test-Path (Join-Path $ScriptDir "PCInventoryAgent.msi")) {
    Write-Host "  PCInventoryAgent.msi   - Instalador MSI" -ForegroundColor Green
    Write-Host ""
    Write-Host "Despliegue por GPO (MSI):" -ForegroundColor Yellow
    Write-Host '  msiexec /i PCInventoryAgent.msi /qn SERVER_URL="https://..." API_KEY="..." INTERVAL_HOURS="5"' -ForegroundColor White
} else {
    Write-Host "Despliegue por GPO (PowerShell):" -ForegroundColor Yellow
    Write-Host '  powershell -ExecutionPolicy Bypass -File \\server\share\install_silent.ps1' -ForegroundColor White
}

Write-Host ""
Write-Host "Despliegue manual:" -ForegroundColor Yellow
Write-Host "  1. Copiar la carpeta dist\ al PC destino" -ForegroundColor White
Write-Host "  2. Ejecutar install_silent.ps1 como Administrador" -ForegroundColor White
Write-Host ""
