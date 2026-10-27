@echo off
title PC Inventory Agent - Despliegue Remoto
color 0A

:: ============================================
::  PC Inventory Agent - Despliegue Masivo
::  Ejecutar desde TU PC como Administrador
::  Requiere: acceso admin remoto a los PCs
:: ============================================

:: === CONFIGURACION ===
set "EXE_LOCAL=C:\PCInventoryAgent\PCInventoryAgent.exe"
set "LISTA_PCS=pcs.txt"
set "INSTALL_DIR=C$\PCInventoryAgent"
set "SERVER_URL=http://172.16.100.4:8086"
set "API_KEY=change-me-on-first-run"
set "TASK_NAME=PCInventoryAgent"

echo.
echo ============================================
echo   PC Inventory Agent - Despliegue Masivo
echo ============================================
echo.

:: Verificar que existe el EXE local
if not exist "%EXE_LOCAL%" (
    echo [ERROR] No se encontro %EXE_LOCAL%
    echo   Coloca el PCInventoryAgent.exe en C:\PCInventoryAgent\
    pause
    exit /b 1
)

:: Verificar que existe la lista de PCs
if not exist "%LISTA_PCS%" (
    echo [ERROR] No se encontro %LISTA_PCS%
    echo.
    echo   Crea un archivo llamado "pcs.txt" en esta carpeta
    echo   con un nombre de PC o IP por linea:
    echo.
    echo     PC-CONTABILIDAD
    echo     PC-RECEPCION
    echo     192.168.1.50
    echo     192.168.1.51
    echo.
    pause
    exit /b 1
)

set TOTAL=0
set OK=0
set FAIL=0

echo Leyendo lista de equipos...
echo.

for /f "usebackq tokens=* delims=" %%P in ("%LISTA_PCS%") do (
    set /a TOTAL+=1
    echo --- Procesando: %%P ---

    :: Verificar conectividad
    ping -n 1 -w 1000 %%P >nul 2>&1
    if errorlevel 1 (
        echo   [FAIL] %%P no responde al ping. Saltando.
        set /a FAIL+=1
    ) else (
        :: Crear directorio remoto
        if not exist "\\%%P\%INSTALL_DIR%" mkdir "\\%%P\%INSTALL_DIR%" >nul 2>&1

        :: Copiar EXE
        copy /y "%EXE_LOCAL%" "\\%%P\%INSTALL_DIR%\PCInventoryAgent.exe" >nul 2>&1
        if errorlevel 1 (
            echo   [FAIL] No se pudo copiar a %%P. Verifica permisos admin.
            set /a FAIL+=1
        ) else (
            :: Crear config.json remoto
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
            ) > "\\%%P\%INSTALL_DIR%\config.json"

            :: Crear tarea programada remotamente
            schtasks /create /s %%P /tn "%TASK_NAME%" /tr "\"C:\PCInventoryAgent\PCInventoryAgent.exe\" --once" /sc hourly /mo 1 /ru SYSTEM /rl HIGHEST /f >nul 2>&1
            if errorlevel 1 (
                echo   [FAIL] No se pudo crear tarea en %%P.
                set /a FAIL+=1
            ) else (
                :: Ejecutar primera vez
                schtasks /run /s %%P /tn "%TASK_NAME%" >nul 2>&1
                echo   [OK] %%P instalado correctamente.
                set /a OK+=1
            )
        )
    )
    echo.
)

echo ============================================
echo   DESPLIEGUE COMPLETADO
echo ============================================
echo.
echo   Total equipos:   %TOTAL%
echo   Exitosos:        %OK%
echo   Fallidos:        %FAIL%
echo.
pause
