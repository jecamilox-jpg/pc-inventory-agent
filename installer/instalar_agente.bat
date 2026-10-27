@echo off
title PC Inventory Agent - Instalador
color 0A

:: ============================================
::  PC Inventory Agent - Instalador Automatico
::  Ejecutar como Administrador
::  Ruta fija: C:\PCInventoryAgent
:: ============================================

:: Verificar privilegios de administrador
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Ejecuta este archivo como Administrador.
    echo   Click derecho ^> Ejecutar como administrador
    echo.
    pause
    exit /b 1
)

set "INSTALL_DIR=C:\PCInventoryAgent"
set "EXE_NAME=PCInventoryAgent.exe"
set "CONFIG_NAME=config.json"
set "TASK_NAME=PCInventoryAgent"
set "SERVER_URL=http://172.16.100.4:8086"
set "API_KEY=change-me-on-first-run"
set "INTERVAL_HOURS=1"

echo.
echo ============================================
echo   PC Inventory Agent - Instalador
echo ============================================
echo.
echo   Servidor:  %SERVER_URL%
echo   Intervalo: Cada %INTERVAL_HOURS% hora(s)
echo   Ruta:      %INSTALL_DIR%
echo.

:: Verificar que el EXE existe
if not exist "%INSTALL_DIR%\%EXE_NAME%" (
    echo [ERROR] No se encontro %EXE_NAME% en %INSTALL_DIR%
    echo   Copia %EXE_NAME% a %INSTALL_DIR% e intenta de nuevo.
    echo.
    pause
    exit /b 1
)
echo [OK] Ejecutable encontrado: %INSTALL_DIR%\%EXE_NAME%

:: Crear config.json si no existe
echo [1/4] Configurando agente...
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
) > "%INSTALL_DIR%\%CONFIG_NAME%"
echo [OK] config.json creado.

:: Eliminar tarea anterior si existe
echo [2/4] Eliminando tarea anterior si existe...
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

:: Crear tarea programada
echo [3/4] Creando tarea programada...
schtasks /create /tn "%TASK_NAME%" /tr "\"%INSTALL_DIR%\%EXE_NAME%\" --once" /sc hourly /mo %INTERVAL_HOURS% /ru SYSTEM /rl HIGHEST /f >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] No se pudo crear la tarea programada.
    pause
    exit /b 1
)
echo [OK] Tarea creada: cada %INTERVAL_HOURS% hora(s) + al iniciar Windows.

:: Agregar trigger de inicio de Windows
schtasks /change /tn "%TASK_NAME%" /enable >nul 2>&1

:: Ejecutar primera recoleccion
echo [4/4] Ejecutando primera recoleccion...
"%INSTALL_DIR%\%EXE_NAME%" --once
if %errorlevel% equ 0 (
    echo [OK] Primera recoleccion enviada exitosamente.
) else (
    echo [WARN] Posible problema en la primera ejecucion. Revisa la conexion al servidor.
)

echo.
echo ============================================
echo   INSTALACION COMPLETADA
echo ============================================
echo.
echo   Tarea:      %TASK_NAME%
echo   Ejecuta:    Cada %INTERVAL_HOURS% hora(s)
echo   Usuario:    SYSTEM
echo   Ejecutable: %INSTALL_DIR%\%EXE_NAME%
echo   Config:     %INSTALL_DIR%\%CONFIG_NAME%
echo.
echo   Para desinstalar:
echo     schtasks /delete /tn %TASK_NAME% /f
echo.
pause
