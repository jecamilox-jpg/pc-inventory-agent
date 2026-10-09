@echo off
title PC Inventory Agent - Installer
echo ============================================
echo   PC Inventory Agent - Instalador
echo ============================================
echo.

:: Verificar Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python no esta instalado o no esta en el PATH.
    echo         Descargalo de https://python.org
    pause
    exit /b 1
)

echo [1/3] Instalando dependencias...
pip install psutil requests WMI pywin32 --quiet
if %errorlevel% neq 0 (
    echo [ERROR] Fallo instalando dependencias.
    pause
    exit /b 1
)

echo [2/3] Generando configuracion...
cd /d "%~dp0\..\agent"
python inventory_agent.py --init-config

echo [3/3] Probando recoleccion de inventario...
python inventory_agent.py --print > test_output.json 2>nul
if %errorlevel% equ 0 (
    echo [OK] Inventario recopilado exitosamente.
    del test_output.json 2>nul
) else (
    echo [WARN] Hubo problemas recopilando. Revisa los permisos.
)

echo.
echo ============================================
echo   Instalacion completada!
echo ============================================
echo.
echo Pasos siguientes:
echo   1. Edita agent\config.json con la URL de tu servidor
echo   2. Prueba: python agent\inventory_agent.py --once
echo   3. Instalar servicio: python agent\inventory_agent.py --install
echo.
pause
