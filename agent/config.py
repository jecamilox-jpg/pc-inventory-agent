"""
Configuración del agente de inventario.
Se puede sobreescribir con un archivo config.json en el mismo directorio.
"""

import os
import json

# Valores por defecto
DEFAULT_CONFIG = {
    "server_url": "http://localhost:5050",
    "api_key": "change-me-on-first-run",
    "interval_seconds": 300,      # cada 5 minutos
    "send_software": True,
    "send_updates": True,
    "log_file": "agent.log",
    "verify_ssl": True,
}


def load_config():
    """Carga configuración desde config.json, con defaults."""
    config = DEFAULT_CONFIG.copy()
    config_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "config.json"
    )
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                user_config = json.load(f)
                config.update(user_config)
        except Exception as e:
            print(f"[WARN] Error leyendo config.json: {e}")

    # Variables de entorno sobreescriben
    if os.environ.get("INVENTORY_SERVER_URL"):
        config["server_url"] = os.environ["INVENTORY_SERVER_URL"]
    if os.environ.get("INVENTORY_API_KEY"):
        config["api_key"] = os.environ["INVENTORY_API_KEY"]
    if os.environ.get("INVENTORY_INTERVAL"):
        config["interval_seconds"] = int(os.environ["INVENTORY_INTERVAL"])

    return config


def save_default_config():
    """Genera un config.json de ejemplo."""
    config_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "config.json"
    )
    if not os.path.exists(config_path):
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
        print(f"[OK] Archivo de configuración creado: {config_path}")
    else:
        print(f"[INFO] config.json ya existe en: {config_path}")
