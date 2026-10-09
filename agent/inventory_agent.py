"""
PC Inventory Agent
Agente que recopila información del PC y la envía periódicamente al servidor.
Puede ejecutarse como servicio de Windows o como tarea programada.
"""

import sys
import os
import time
import json
import logging
import argparse
import threading
from datetime import datetime

import requests

# Agregar directorio actual al path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from collector import collect_full_inventory
from config import load_config, save_default_config

# ---------- Logging ----------
logger = logging.getLogger("InventoryAgent")


def setup_logging(log_file="agent.log"):
    """Configura logging a archivo y consola."""
    log_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), log_file
    )
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
                f"✓ Inventario enviado exitosamente. "
                f"ID: {result.get('machine_id', 'N/A')}"
            )
            return True
        else:
            logger.error(
                f"✗ Error del servidor: {response.status_code} - "
                f"{response.text[:200]}"
            )
            return False

    except requests.ConnectionError:
        logger.error(
            f"✗ No se pudo conectar al servidor: "
            f"{config.get('server_url')}"
        )
        return False
    except Exception as e:
        logger.error(f"✗ Error inesperado: {e}")
        return False


def save_local(config):
    """Guarda el inventario localmente como JSON (modo offline)."""
    logger.info("Guardando inventario localmente...")
    try:
        inventory = collect_full_inventory()
        out_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "reports"
        )
        os.makedirs(out_dir, exist_ok=True)
        filename = (
            f"inventory_{inventory['system']['hostname']}_"
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        filepath = os.path.join(out_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(inventory, f, indent=2, ensure_ascii=False, default=str)
        logger.info(f"✓ Inventario guardado: {filepath}")
        return True
    except Exception as e:
        logger.error(f"✗ Error guardando: {e}")
        return False


# ---------- Loop principal ----------
def run_agent_loop(config):
    """Ejecuta el agente en loop continuo."""
    interval = config.get("interval_seconds", 300)
    logger.info(
        f"Agente iniciado. Intervalo: {interval}s "
        f"| Servidor: {config.get('server_url')}"
    )
    logger.info("-" * 50)

    while True:
        try:
            success = send_inventory(config)
            if not success:
                # Guardar localmente si falla el envío
                save_local(config)
        except Exception as e:
            logger.error(f"Error en ciclo del agente: {e}")

        logger.info(f"Próximo reporte en {interval} segundos...")
        time.sleep(interval)


# ---------- Windows Service ----------
def install_as_task():
    """Registra el agente como tarea programada de Windows."""
    import platform
    if platform.system() != "Windows":
        print("Esta función solo está disponible en Windows.")
        return

    script_path = os.path.abspath(__file__)
    python_path = sys.executable
    task_name = "PCInventoryAgent"

    cmd = (
        f'schtasks /create /tn "{task_name}" '
        f'/tr "\\\"{python_path}\\\" \\\"{script_path}\\\" --daemon" '
        f'/sc onstart /ru SYSTEM /rl HIGHEST /f'
    )

    print(f"Registrando tarea programada: {task_name}")
    result = os.system(cmd)
    if result == 0:
        print(f"✓ Tarea '{task_name}' creada exitosamente.")
        print(f"  Se ejecutará al iniciar el sistema como SYSTEM.")
    else:
        print(f"✗ Error creando la tarea. Ejecutar como Administrador.")


def uninstall_task():
    """Elimina la tarea programada."""
    task_name = "PCInventoryAgent"
    result = os.system(f'schtasks /delete /tn "{task_name}" /f')
    if result == 0:
        print(f"✓ Tarea '{task_name}' eliminada.")
    else:
        print(f"✗ Error eliminando la tarea.")


# ---------- CLI ----------
def main():
    parser = argparse.ArgumentParser(
        description="PC Inventory Agent - Recopila y envía inventario del PC"
    )
    parser.add_argument(
        "--daemon", action="store_true",
        help="Ejecutar en modo daemon (loop continuo)"
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
        "--install", action="store_true",
        help="Instalar como tarea programada de Windows"
    )
    parser.add_argument(
        "--uninstall", action="store_true",
        help="Desinstalar tarea programada"
    )
    parser.add_argument(
        "--init-config", action="store_true",
        help="Crear archivo config.json con valores por defecto"
    )

    args = parser.parse_args()
    config = load_config()
    setup_logging(config.get("log_file", "agent.log"))

    if args.init_config:
        save_default_config()
    elif args.install:
        install_as_task()
    elif args.uninstall:
        uninstall_task()
    elif args.print_json:
        data = collect_full_inventory()
        print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    elif args.local:
        save_local(config)
    elif args.once:
        send_inventory(config)
    elif args.daemon:
        run_agent_loop(config)
    else:
        parser.print_help()
        print("\nEjemplos:")
        print("  python inventory_agent.py --once        # Enviar una vez")
        print("  python inventory_agent.py --daemon      # Modo continuo")
        print("  python inventory_agent.py --local       # Solo guardar local")
        print("  python inventory_agent.py --print       # Ver JSON en consola")
        print("  python inventory_agent.py --install     # Tarea programada")
        print("  python inventory_agent.py --init-config # Crear config.json")


if __name__ == "__main__":
    main()
