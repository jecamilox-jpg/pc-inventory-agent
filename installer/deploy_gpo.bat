@echo off
:: ============================================
::  PC Inventory Agent - Script para GPO
::  Asignar como: Startup Script en GPO
::  Ruta de red con el EXE: \\servidor\netlogon\PCInventoryAgent\
:: ============================================

set "INSTALL_DIR=C:\PCInventoryAgent"
set "NETWORK_SRC=\\172.16.100.4\netlogon\PCInventoryAgent"
set "EXE_NAME=PCInventoryAgent.exe"
set "TASK_NAME=PCInventoryAgent"
set "SERVER_URL=http://172.16.100.4:8086"
set "API_KEY=change-me-on-first-run"

:: Si ya esta instalado y el EXE existe, no hacer nada
if exist "%INSTALL_DIR%\%EXE_NAME%" (
    schtasks /query /tn "%TASK_NAME%" >nul 2>&1
    if %errorlevel% equ 0 (
        exit /b 0
    )
)

:: Crear directorio local
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"

:: Copiar EXE desde la red
copy /y "%NETWORK_SRC%\%EXE_NAME%" "%INSTALL_DIR%\%EXE_NAME%" >nul 2>&1
if not exist "%INSTALL_DIR%\%EXE_NAME%" (
    exit /b 1
)

:: Crear config.json
(
echo {
echo   "server_url": "%SERVER_URL%",
echo   "api_key": "%API_KEY%",
echo   "interval_seconds": 3600,
echo   "send_software": true,
echo   "send_updates": true,
echo   "log_file": "agent.log",
echo   "verify_ssl": true
echo }
) > "%INSTALL_DIR%\config.json"

:: Eliminar tarea anterior
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

:: Crear tarea programada cada 1 hora
schtasks /create /tn "%TASK_NAME%" /tr "\"%INSTALL_DIR%\%EXE_NAME%\" --once" /sc hourly /mo 1 /ru SYSTEM /rl HIGHEST /f >nul 2>&1

:: Ejecutar primera recoleccion
"%INSTALL_DIR%\%EXE_NAME%" --once >nul 2>&1

exit /b 0
