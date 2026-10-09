#!/bin/bash
# ============================================
#  PC Inventory Agent - Installer para macOS
#  Configura el agente con launchd (equivalente
#  a Windows Scheduled Tasks en Mac)
# ============================================

set -e

AGENT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
AGENT_SCRIPT="$AGENT_DIR/agent/inventory_agent.py"
CONFIG_PATH="$AGENT_DIR/agent/config.json"
PLIST_NAME="com.itdeskmanager.pcinventoryagent"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"
LOG_FILE="$AGENT_DIR/agent/agent.log"

# Colores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# --- Parsear argumentos ---
SERVER_URL=""
API_KEY=""
INTERVAL_HOURS=5
UNINSTALL=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --server-url) SERVER_URL="$2"; shift 2 ;;
        --api-key) API_KEY="$2"; shift 2 ;;
        --interval) INTERVAL_HOURS="$2"; shift 2 ;;
        --uninstall) UNINSTALL=true; shift ;;
        --help)
            echo "Uso: $0 [opciones]"
            echo ""
            echo "Opciones:"
            echo "  --server-url URL    URL del servidor (ej: https://mi-servidor.up.railway.app)"
            echo "  --api-key KEY       API key del servidor"
            echo "  --interval HORAS    Intervalo en horas (default: 5)"
            echo "  --uninstall         Desinstalar el agente"
            echo "  --help              Mostrar esta ayuda"
            exit 0
            ;;
        *) echo "Opcion desconocida: $1"; exit 1 ;;
    esac
done

# --- Desinstalar ---
if [ "$UNINSTALL" = true ]; then
    echo -e "\n${YELLOW}Desinstalando agente...${NC}"
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    rm -f "$PLIST_PATH"
    echo -e "${GREEN}[OK] Agente desinstalado.${NC}\n"
    exit 0
fi

# --- Verificar Python ---
echo ""
PYTHON_PATH=$(which python3 2>/dev/null || which python 2>/dev/null)
if [ -z "$PYTHON_PATH" ]; then
    echo -e "${RED}[ERROR] Python no encontrado.${NC}"
    echo "  Instalalo con: brew install python3"
    echo "  O descargalo de https://python.org"
    exit 1
fi
echo -e "${GREEN}[OK] Python: $PYTHON_PATH${NC}"

# --- Instalar dependencias ---
echo -e "\n[1/4] Instalando dependencias..."
"$PYTHON_PATH" -m pip install psutil requests --quiet --break-system-packages 2>/dev/null \
    || "$PYTHON_PATH" -m pip install psutil requests --quiet 2>/dev/null \
    || "$PYTHON_PATH" -m pip install --user psutil requests --quiet
echo -e "${GREEN}[OK] Dependencias instaladas.${NC}"

# --- Configurar ---
echo "[2/4] Configurando agente..."
INTERVAL_SECONDS=$((INTERVAL_HOURS * 3600))

if [ -n "$SERVER_URL" ] || [ -n "$API_KEY" ]; then
    cat > "$CONFIG_PATH" << CONF
{
    "server_url": "${SERVER_URL:-http://localhost:5050}",
    "api_key": "${API_KEY:-change-me-on-first-run}",
    "interval_seconds": $INTERVAL_SECONDS,
    "send_software": true,
    "send_updates": true,
    "log_file": "agent.log",
    "verify_ssl": true
}
CONF
    echo -e "${GREEN}[OK] config.json actualizado con servidor: ${SERVER_URL:-http://localhost:5050}${NC}"
elif [ ! -f "$CONFIG_PATH" ]; then
    "$PYTHON_PATH" "$AGENT_SCRIPT" --init-config
    echo -e "${YELLOW}[INFO] config.json creado con valores por defecto. Editalo con la URL de tu servidor.${NC}"
fi

# --- Probar recoleccion ---
echo "[3/4] Probando recoleccion de inventario..."
TEST_RESULT=$("$PYTHON_PATH" "$AGENT_SCRIPT" --print 2>&1 | head -5)
if echo "$TEST_RESULT" | grep -q '"hostname"'; then
    echo -e "${GREEN}[OK] Inventario recopilado exitosamente.${NC}"
else
    echo -e "${YELLOW}[WARN] Posible problema. Revisa permisos.${NC}"
fi

# --- Crear LaunchAgent ---
echo "[4/4] Configurando ejecucion automatica con launchd..."

# Descargar plist anterior si existe
launchctl unload "$PLIST_PATH" 2>/dev/null || true

mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST_PATH" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_NAME</string>

    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_PATH</string>
        <string>$AGENT_SCRIPT</string>
        <string>--once</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$AGENT_DIR/agent</string>

    <key>StartInterval</key>
    <integer>$INTERVAL_SECONDS</integer>

    <key>RunAtLoad</key>
    <true/>

    <key>StandardOutPath</key>
    <string>$LOG_FILE</string>

    <key>StandardErrorPath</key>
    <string>$LOG_FILE</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    </dict>
</dict>
</plist>
PLIST

# Cargar el agente
launchctl load "$PLIST_PATH"

echo ""
echo -e "${CYAN}============================================${NC}"
echo -e "${GREEN}  INSTALACION COMPLETADA (macOS)${NC}"
echo -e "${CYAN}============================================${NC}"
echo ""
echo "  Agente:    $PLIST_NAME"
echo "  Ejecuta:   Al iniciar sesion + cada $INTERVAL_HOURS horas"
echo "  Script:    $AGENT_SCRIPT"
echo "  Config:    $CONFIG_PATH"
echo "  Logs:      $LOG_FILE"
echo "  Plist:     $PLIST_PATH"
echo ""
echo -e "${YELLOW}  Comandos utiles:${NC}"
echo "    Ver estado:     launchctl list | grep pcinventory"
echo "    Ejecutar ahora: launchctl start $PLIST_NAME"
echo "    Ver logs:       tail -f $LOG_FILE"
echo "    Desinstalar:    ./setup_mac.sh --uninstall"
echo ""

# Ejecutar primera vez
echo -e "${YELLOW}Ejecutando primera recopilacion...${NC}"
launchctl start "$PLIST_NAME"
echo -e "${GREEN}[OK] Primera ejecucion lanzada.${NC}"
echo ""
