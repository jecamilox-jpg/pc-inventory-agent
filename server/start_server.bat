@echo off
title PC Inventory Server - ITDeskManager
echo ============================================================
echo   PC Inventory Server - ITDeskManager
echo   Puerto: 8086
echo ============================================================
echo.

:: Verificar Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python no encontrado. Instalar desde python.org
    pause
    exit /b 1
)

:: Instalar dependencias si es necesario
echo Verificando dependencias...
pip install -r requirements.txt --quiet

:: Variables de entorno (editar aqui)
set PORT=8086
set INVENTORY_API_KEY=cambiar-esta-clave-secreta
set FLASK_DEBUG=0
:: set ADMIN_USER=admin
:: set ADMIN_PASSWORD=tu-password-seguro

echo.
echo Iniciando servidor en http://0.0.0.0:8086
echo Presiona Ctrl+C para detener
echo.

python app.py

pause
