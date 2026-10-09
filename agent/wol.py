"""
Wake-on-LAN Module
Envía magic packets para encender equipos remotamente.
También actúa como relay: consulta al servidor por comandos WOL pendientes.
"""

import socket
import struct
import logging
import time

import requests

logger = logging.getLogger("InventoryAgent")


def send_magic_packet(mac_address, broadcast="255.255.255.255", port=9):
    """
    Envía un magic packet (Wake-on-LAN) a la MAC address especificada.

    El magic packet es:
    - 6 bytes de 0xFF
    - Seguido de la MAC address repetida 16 veces
    """
    # Limpiar y validar MAC
    mac = mac_address.replace(":", "").replace("-", "").replace(".", "")
    if len(mac) != 12:
        raise ValueError(f"MAC address inválida: {mac_address}")

    # Construir magic packet
    mac_bytes = bytes.fromhex(mac)
    magic = b'\xff' * 6 + mac_bytes * 16

    # Enviar por UDP broadcast
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        sock.sendto(magic, (broadcast, port))
        logger.info(f"✓ Magic packet enviado a {mac_address} ({broadcast}:{port})")
        return True
    except Exception as e:
        logger.error(f"✗ Error enviando magic packet: {e}")
        return False
    finally:
        sock.close()


def check_and_execute_wol(config):
    """
    Consulta al servidor por comandos WOL pendientes y los ejecuta.
    Esta función se llama cada vez que el agente reporta inventario.
    """
    server_url = config.get("server_url", "").rstrip("/")
    api_key = config.get("api_key", "")

    if not server_url:
        return

    try:
        # Consultar comandos pendientes
        response = requests.get(
            f"{server_url}/api/wol/pending",
            headers={"X-API-Key": api_key},
            timeout=10,
            verify=config.get("verify_ssl", True),
        )

        if response.status_code != 200:
            return

        commands = response.json()
        if not commands:
            return

        hostname = socket.gethostname()
        logger.info(f"WOL: {len(commands)} comando(s) pendiente(s)")

        for cmd in commands:
            target_mac = cmd.get("target_mac", "")
            target_host = cmd.get("target_hostname", "")
            wol_id = cmd.get("id")

            if not target_mac or not wol_id:
                continue

            logger.info(
                f"WOL: Enviando magic packet a {target_host} "
                f"({target_mac})"
            )

            # Enviar a broadcast estándar y también a subnets comunes
            success = send_magic_packet(target_mac)

            # También intentar con broadcast de subred local
            try:
                local_broadcast = _get_local_broadcast()
                if local_broadcast and local_broadcast != "255.255.255.255":
                    send_magic_packet(target_mac, broadcast=local_broadcast)
            except Exception:
                pass

            # Reportar resultado al servidor
            result = "sent" if success else "failed"
            try:
                requests.post(
                    f"{server_url}/api/wol/{wol_id}/complete",
                    json={
                        "picked_by": hostname,
                        "result": result,
                    },
                    headers={"X-API-Key": api_key},
                    timeout=10,
                    verify=config.get("verify_ssl", True),
                )
            except Exception as e:
                logger.error(f"WOL: Error reportando resultado: {e}")

    except requests.ConnectionError:
        pass  # Servidor no disponible, silenciar
    except Exception as e:
        logger.error(f"WOL: Error verificando comandos: {e}")


def _get_local_broadcast():
    """Intenta obtener la dirección de broadcast de la red local."""
    try:
        import psutil
        for iface, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family == socket.AF_INET and addr.broadcast:
                    # Ignorar loopback
                    if not addr.address.startswith("127."):
                        return addr.broadcast
    except Exception:
        pass
    return "255.255.255.255"
