"""
PC Inventory Agent v1.4.0
Agente que recopila información del PC y la envía al servidor.
Se instala como programa de Windows con tarea programada cada 30 minutos.
"""

import sys
import os
import time
import json
import logging
import argparse
import shutil
import subprocess
import ctypes
from datetime import datetime

import requests

# Agregar directorio actual al path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from collector import collect_full_inventory
from config import load_config, save_default_config
from wol import send_magic_packet, check_and_execute_wol

# ---------- Constantes ----------
APP_NAME = "PCInventoryAgent"
APP_VERSION = "1.4.0"
APP_PUBLISHER = "ITDeskManager"
INSTALL_DIR = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), APP_NAME)
TASK_NAME = "PCInventoryAgent_Report"
UNINSTALL_REG_KEY = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\\" + APP_NAME
)

# ---------- Logging ----------
logger = logging.getLogger("InventoryAgent")


def get_exe_path():
    """Retorna la ruta del ejecutable actual."""
    if getattr(sys, 'frozen', False):
        return sys.executable
    return os.path.abspath(__file__)


def is_admin():
    """Verifica si se ejecuta como administrador."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def run_as_admin(args_str=""):
    """Re-lanza el proceso como administrador."""
    exe = get_exe_path()
    try:
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", exe, args_str, None, 1
        )
        return True
    except Exception:
        return False


def setup_logging(log_file="agent.log"):
    """Configura logging a archivo y consola."""
    # Determinar directorio de logs
    if getattr(sys, 'frozen', False):
        log_dir = os.path.dirname(sys.executable)
    else:
        log_dir = os.path.dirname(os.path.abspath(__file__))

    log_path = os.path.join(log_dir, log_file)
    try:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[
                logging.FileHandler(log_path, encoding="utf-8"),
                logging.StreamHandler(sys.stdout),
            ],
        )
    except PermissionError:
        # Si no puede escribir log en Program Files, usar temp
        log_path = os.path.join(os.environ.get("TEMP", "."), log_file)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[
                logging.FileHandler(log_path, encoding="utf-8"),
                logging.StreamHandler(sys.stdout),
            ],
        )


# ---------- Envío de datos ----------
def send_inventory(config):
    """Recopila y envía el inventario al servidor."""
    logger.info("Recopilando inventario del sistema...")

    try:
        inventory = collect_full_inventory()

        # Opcionalmente excluir software/updates para reducir payload
        if not config.get("send_software", True):
            inventory["software"] = []
        if not config.get("send_updates", True):
            inventory["windows_updates"] = []

        url = f"{config['server_url'].rstrip('/')}/api/report"
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": config.get("api_key", ""),
        }

        logger.info(
            f"Enviando inventario a {url} "
            f"(hostname: {inventory['system']['hostname']})"
        )

        response = requests.post(
            url,
            json=inventory,
            headers=headers,
            timeout=30,
            verify=config.get("verify_ssl", True),
        )

        if response.status_code == 200:
            result = response.json()
            logger.info(
                f"Inventario enviado exitosamente. "
                f"ID: {result.get('machine_id', 'N/A')}"
            )
            # Revisar si hay comandos WOL pendientes
            try:
                check_and_execute_wol(config)
            except Exception as e:
                logger.debug(f"WOL check: {e}")
            return True
        else:
            logger.error(
                f"Error del servidor: {response.status_code} - "
                f"{response.text[:200]}"
            )
            return False

    except requests.ConnectionError:
        logger.error(
            f"No se pudo conectar al servidor: "
            f"{config.get('server_url')}"
        )
        return False
    except Exception as e:
        logger.error(f"Error inesperado: {e}")
        return False


def save_local(config):
    """Guarda el inventario localmente como JSON (modo offline)."""
    logger.info("Guardando inventario localmente...")
    try:
        inventory = collect_full_inventory()
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
        out_dir = os.path.join(base_dir, "reports")
        os.makedirs(out_dir, exist_ok=True)
        filename = (
            f"inventory_{inventory['system']['hostname']}_"
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        filepath = os.path.join(out_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(inventory, f, indent=2, ensure_ascii=False, default=str)
        logger.info(f"Inventario guardado: {filepath}")
        return True
    except Exception as e:
        logger.error(f"Error guardando: {e}")
        return False


# ================================================================
#  INSTALADOR / DESINSTALADOR
# ================================================================

def install_agent():
    """
    Instala el agente como programa de Windows:
    1. Copia el .exe a Program Files
    2. Copia config.json si existe
    3. Crea tarea programada cada 30 minutos
    4. Registra en Panel de Control (Programas y características)
    5. Crea script desinstalador
    """
    import platform
    if platform.system() != "Windows":
        print("Esta funcion solo esta disponible en Windows.")
        return False

    if not is_admin():
        print("Se requieren permisos de administrador. Solicitando elevacion...")
        run_as_admin("install")
        return True  # El nuevo proceso elevado hará la instalación

    print()
    print("=" * 52)
    print("  PC Inventory Agent - Instalador v" + APP_VERSION)
    print("  " + APP_PUBLISHER)
    print("=" * 52)
    print()

    exe_source = get_exe_path()

    # --- 1. Crear directorio de instalación ---
    print("[1/5] Creando directorio de instalacion...")
    os.makedirs(INSTALL_DIR, exist_ok=True)
    print(f"  -> {INSTALL_DIR}")

    # --- 2. Copiar archivos ---
    print("[2/5] Copiando archivos...")
    exe_dest = os.path.join(INSTALL_DIR, APP_NAME + ".exe")

    if getattr(sys, 'frozen', False):
        # Es un .exe compilado
        if os.path.normpath(exe_source) != os.path.normpath(exe_dest):
            shutil.copy2(exe_source, exe_dest)
            print(f"  -> {APP_NAME}.exe copiado")
        else:
            print(f"  -> {APP_NAME}.exe ya esta en destino")
    else:
        # Es un .py — copiar todos los .py necesarios
        src_dir = os.path.dirname(os.path.abspath(__file__))
        for fname in os.listdir(src_dir):
            if fname.endswith('.py') or fname == 'config.json':
                shutil.copy2(os.path.join(src_dir, fname),
                             os.path.join(INSTALL_DIR, fname))
                print(f"  -> {fname} copiado")

    # Copiar config.json si existe junto al instalador
    src_dir = os.path.dirname(exe_source)
    config_src = os.path.join(src_dir, "config.json")
    config_dest = os.path.join(INSTALL_DIR, "config.json")
    if os.path.exists(config_src) and not os.path.exists(config_dest):
        shutil.copy2(config_src, config_dest)
        print("  -> config.json copiado")
    elif not os.path.exists(config_dest):
        # Crear config.json por defecto
        from config import DEFAULT_CONFIG
        with open(config_dest, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
        print("  -> config.json creado (editar con datos del servidor)")

    # --- 3. Crear tarea programada cada 30 minutos ---
    print("[3/5] Creando tarea programada (cada 30 minutos)...")

    # Eliminar tarea anterior si existe
    subprocess.run(
        f'schtasks /delete /tn "{TASK_NAME}" /f',
        shell=True, capture_output=True
    )

    # Crear nueva tarea que se ejecuta cada 30 minutos
    cmd = (
        f'schtasks /create /tn "{TASK_NAME}" '
        f'/tr "\\"{exe_dest}\\"" '
        f'/sc minute /mo 30 '
        f'/ru SYSTEM /rl HIGHEST /f'
    )
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

    if result.returncode == 0:
        print("  -> Tarea creada: cada 30 minutos como SYSTEM")
    else:
        print(f"  -> Error creando tarea: {result.stderr.strip()}")
        # Intentar sin /ru SYSTEM para usuarios sin dominio
        cmd_alt = (
            f'schtasks /create /tn "{TASK_NAME}" '
            f'/tr "\\"{exe_dest}\\"" '
            f'/sc minute /mo 30 /rl HIGHEST /f'
        )
        result2 = subprocess.run(cmd_alt, shell=True, capture_output=True, text=True)
        if result2.returncode == 0:
            print("  -> Tarea creada: cada 30 minutos (usuario actual)")
        else:
            print("  -> FALLO: No se pudo crear la tarea programada")

    # --- 4. Registrar en Panel de Control ---
    print("[4/5] Registrando en Panel de Control...")

    try:
        import winreg

        uninstall_cmd = f'"{exe_dest}" uninstall'
        install_date = datetime.now().strftime("%Y%m%d")

        key = winreg.CreateKeyEx(
            winreg.HKEY_LOCAL_MACHINE,
            UNINSTALL_REG_KEY,
            0,
            winreg.KEY_WRITE
        )

        entries = {
            "DisplayName": f"PC Inventory Agent v{APP_VERSION}",
            "DisplayVersion": APP_VERSION,
            "Publisher": APP_PUBLISHER,
            "InstallLocation": INSTALL_DIR,
            "UninstallString": uninstall_cmd,
            "QuietUninstallString": f'"{exe_dest}" uninstall --quiet',
            "DisplayIcon": f"{exe_dest},0",
            "InstallDate": install_date,
            "URLInfoAbout": "https://github.com/jecamilox-jpg/pc-inventory-agent",
            "NoModify": 1,
            "NoRepair": 1,
            "EstimatedSize": _get_folder_size_kb(INSTALL_DIR),
        }

        for name, value in entries.items():
            if isinstance(value, int):
                winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)
            else:
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(value))

        winreg.CloseKey(key)
        print("  -> Registrado en Programas y caracteristicas")

    except Exception as e:
        print(f"  -> Error en registro: {e}")

    # --- 5. Crear desinstalador .bat auxiliar ---
    print("[5/5] Creando desinstalador auxiliar...")
    uninstall_bat = os.path.join(INSTALL_DIR, "uninstall.bat")
    with open(uninstall_bat, "w", encoding="utf-8") as f:
        f.write(f'@echo off\n')
        f.write(f'echo Desinstalando PC Inventory Agent...\n')
        f.write(f'schtasks /delete /tn "{TASK_NAME}" /f >nul 2>&1\n')
        f.write(f'reg delete "HKLM\\{UNINSTALL_REG_KEY}" /f >nul 2>&1\n')
        f.write(f'timeout /t 2 /nobreak >nul\n')
        f.write(f'rmdir /s /q "{INSTALL_DIR}"\n')
        f.write(f'echo Desinstalacion completada.\n')
        f.write(f'pause\n')
    print("  -> uninstall.bat creado")

    # --- Ejecutar primera vez ---
    print()
    print("=" * 52)
    print("  INSTALACION COMPLETADA")
    print("=" * 52)
    print()
    print(f"  Directorio: {INSTALL_DIR}")
    print(f"  Tarea: {TASK_NAME} (cada 30 min)")
    print(f"  Desinstalar: Panel de Control > Programas")
    print()
    print("  Ejecutando primer reporte ahora...")
    print()

    # Primer reporte inmediato
    try:
        config = load_config()
        setup_logging(config.get("log_file", "agent.log"))
        send_inventory(config)
    except Exception as e:
        print(f"  Primer reporte fallo: {e}")
        print("  Se reintentara en el proximo ciclo (30 min)")

    print()
    input("  Presione ENTER para cerrar...")
    return True


def uninstall_agent(quiet=False):
    """
    Desinstala el agente:
    1. Elimina la tarea programada
    2. Elimina el registro de Panel de Control
    3. Programa eliminación de archivos
    """
    import platform
    if platform.system() != "Windows":
        print("Esta funcion solo esta disponible en Windows.")
        return False

    if not is_admin():
        args = "uninstall --quiet" if quiet else "uninstall"
        run_as_admin(args)
        return True

    if not quiet:
        print()
        print("=" * 52)
        print("  PC Inventory Agent - Desinstalador")
        print("=" * 52)
        print()

    # 1. Eliminar tarea programada
    if not quiet:
        print("[1/3] Eliminando tarea programada...")
    result = subprocess.run(
        f'schtasks /delete /tn "{TASK_NAME}" /f',
        shell=True, capture_output=True, text=True
    )
    if not quiet:
        if result.returncode == 0:
            print("  -> Tarea eliminada")
        else:
            print("  -> Tarea no encontrada (ya eliminada)")

    # 2. Eliminar registro
    if not quiet:
        print("[2/3] Eliminando registro de Panel de Control...")
    try:
        import winreg
        winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_REG_KEY)
        if not quiet:
            print("  -> Registro eliminado")
    except FileNotFoundError:
        if not quiet:
            print("  -> Registro no encontrado (ya eliminado)")
    except Exception as e:
        if not quiet:
            print(f"  -> Error: {e}")

    # 3. Programar eliminación de archivos (no puede borrarse a sí mismo)
    if not quiet:
        print("[3/3] Programando eliminacion de archivos...")

    # Crear un .bat temporal que espere y borre la carpeta
    temp_dir = os.environ.get("TEMP", r"C:\Windows\Temp")
    cleanup_bat = os.path.join(temp_dir, "pcia_cleanup.bat")
    with open(cleanup_bat, "w") as f:
        f.write('@echo off\n')
        f.write('timeout /t 3 /nobreak >nul\n')
        f.write(f'rmdir /s /q "{INSTALL_DIR}" 2>nul\n')
        f.write(f'del /f /q "%~f0"\n')  # Auto-eliminar el .bat

    # Lanzar el cleanup en segundo plano
    subprocess.Popen(
        f'cmd /c "{cleanup_bat}"',
        shell=True,
        creationflags=0x08000000  # CREATE_NO_WINDOW
    )

    if not quiet:
        print("  -> Archivos se eliminaran en unos segundos")
        print()
        print("=" * 52)
        print("  DESINSTALACION COMPLETADA")
        print("=" * 52)
        print()
        print("  PC Inventory Agent ha sido desinstalado.")
        print()
        input("  Presione ENTER para cerrar...")

    return True


def _get_folder_size_kb(path):
    """Calcula tamaño de carpeta en KB para el registro."""
    total = 0
    try:
        for dirpath, dirnames, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if os.path.isfile(fp):
                    total += os.path.getsize(fp)
    except Exception:
        total = 0
    return max(total // 1024, 1)


# ---------- CLI ----------
def main():
    # Si se ejecuta sin argumentos desde Program Files, es la tarea programada
    # -> ejecutar reporte directo
    if len(sys.argv) == 1:
        config = load_config()
        setup_logging(config.get("log_file", "agent.log"))
        logger.info("=== PC Inventory Agent v" + APP_VERSION + " ===")
        success = send_inventory(config)
        if not success:
            save_local(config)
        return

    # Comandos simples (sin --)
    command = sys.argv[1].lower().strip("-")

    if command == "install":
        install_agent()
        return

    if command == "uninstall":
        quiet = "--quiet" in sys.argv or "-q" in sys.argv
        uninstall_agent(quiet=quiet)
        return

    # Argumentos con -- para compatibilidad hacia atrás
    parser = argparse.ArgumentParser(
        description="PC Inventory Agent v" + APP_VERSION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Comandos principales:
  install              Instalar como programa de Windows
  uninstall            Desinstalar completamente

Opciones:
  --once               Recopilar y enviar una sola vez
  --local              Solo guardar inventario localmente
  --print              Imprimir inventario en consola (JSON)
  --init-config        Crear archivo config.json
  --wol MAC            Enviar Wake-on-LAN

Ejemplos:
  PCInventoryAgent.exe install      Instalar (Panel de Control + tarea 30min)
  PCInventoryAgent.exe uninstall    Desinstalar completamente
  PCInventoryAgent.exe              Enviar reporte (lo hace la tarea cada 30min)
  PCInventoryAgent.exe --once       Enviar reporte manual una sola vez
"""
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Recopilar y enviar una sola vez"
    )
    parser.add_argument(
        "--local", action="store_true",
        help="Solo guardar inventario localmente (sin enviar)"
    )
    parser.add_argument(
        "--print", action="store_true", dest="print_json",
        help="Imprimir inventario en consola (JSON)"
    )
    parser.add_argument(
        "--init-config", action="store_true",
        help="Crear archivo config.json con valores por defecto"
    )
    parser.add_argument(
        "--wol", type=str, metavar="MAC",
        help="Enviar Wake-on-LAN a una MAC address (ej: AA:BB:CC:DD:EE:FF)"
    )
    parser.add_argument(
        "--daemon", action="store_true",
        help="Ejecutar en modo daemon (loop continuo, uso interno)"
    )

    args = parser.parse_args()
    config = load_config()
    setup_logging(config.get("log_file", "agent.log"))

    if args.wol:
        print(f"Enviando Wake-on-LAN a {args.wol}...")
        if send_magic_packet(args.wol):
            print("Magic packet enviado.")
        else:
            print("Error enviando magic packet.")
    elif args.init_config:
        save_default_config()
    elif args.print_json:
        data = collect_full_inventory()
        print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    elif args.local:
        save_local(config)
    elif args.once:
        send_inventory(config)
    elif args.daemon:
        interval = config.get("interval_seconds", 1800)
        logger.info(f"Agente iniciado en modo daemon. Intervalo: {interval}s")
        while True:
            try:
                success = send_inventory(config)
                if not success:
                    save_local(config)
            except Exception as e:
                logger.error(f"Error en ciclo: {e}")
            time.sleep(interval)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
