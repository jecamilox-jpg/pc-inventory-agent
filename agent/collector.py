"""
PC Inventory Collector
Recopila información completa del hardware y software del equipo.
Compatible con Windows (usa WMI y PowerShell).
"""

import platform
import socket
import subprocess
import json
import uuid
import os
import sys
import ctypes
from datetime import datetime

try:
    import psutil
except ImportError:
    psutil = None

try:
    import wmi as wmi_module
except ImportError:
    wmi_module = None


def _run_powershell(command):
    """Ejecuta un comando PowerShell y retorna la salida."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True, text=True, timeout=30
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _run_wmic(command):
    """Ejecuta un comando WMIC y retorna la salida."""
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=15, shell=True
        )
        return result.stdout.strip()
    except Exception:
        return ""


def get_system_info():
    """Información básica del sistema operativo."""
    info = {
        "hostname": socket.gethostname(),
        "fqdn": socket.getfqdn(),
        "os_name": platform.system(),
        "os_version": platform.version(),
        "os_release": platform.release(),
        "os_edition": "",
        "os_build": "",
        "os_architecture": platform.machine(),
        "platform": platform.platform(),
        "boot_time": "",
    }

    if platform.system() == "Windows":
        edition = _run_powershell(
            "(Get-CimInstance Win32_OperatingSystem).Caption"
        )
        if edition:
            info["os_edition"] = edition

        build = _run_powershell(
            "(Get-CimInstance Win32_OperatingSystem).BuildNumber"
        )
        if build:
            info["os_build"] = build

    if psutil:
        import datetime as dt
        boot = dt.datetime.fromtimestamp(psutil.boot_time())
        info["boot_time"] = boot.isoformat()

    return info


def get_hardware_ids():
    """Modelo, serial, fabricante, BIOS."""
    info = {
        "manufacturer": "",
        "model": "",
        "serial_number": "",
        "bios_version": "",
        "bios_vendor": "",
        "system_uuid": "",
        "chassis_type": "",
    }

    if platform.system() == "Windows":
        # Intentar con WMI Python
        if wmi_module:
            try:
                w = wmi_module.WMI()
                for cs in w.Win32_ComputerSystem():
                    info["manufacturer"] = cs.Manufacturer or ""
                    info["model"] = cs.Model or ""
                for bios in w.Win32_BIOS():
                    info["serial_number"] = bios.SerialNumber or ""
                    info["bios_version"] = bios.SMBIOSBIOSVersion or ""
                    info["bios_vendor"] = bios.Manufacturer or ""
                for csproduct in w.Win32_ComputerSystemProduct():
                    info["system_uuid"] = csproduct.UUID or ""
                for enclosure in w.Win32_SystemEnclosure():
                    chassis_types = enclosure.ChassisTypes
                    if chassis_types:
                        info["chassis_type"] = _chassis_type_name(chassis_types[0])
                return info
            except Exception:
                pass

        # Fallback con PowerShell
        info["manufacturer"] = _run_powershell(
            "(Get-CimInstance Win32_ComputerSystem).Manufacturer"
        )
        info["model"] = _run_powershell(
            "(Get-CimInstance Win32_ComputerSystem).Model"
        )
        info["serial_number"] = _run_powershell(
            "(Get-CimInstance Win32_BIOS).SerialNumber"
        )
        info["bios_version"] = _run_powershell(
            "(Get-CimInstance Win32_BIOS).SMBIOSBIOSVersion"
        )
        info["system_uuid"] = _run_powershell(
            "(Get-CimInstance Win32_ComputerSystemProduct).UUID"
        )

    return info


def _chassis_type_name(code):
    """Convierte código de chasis a nombre legible."""
    types = {
        1: "Other", 2: "Unknown", 3: "Desktop", 4: "Low Profile Desktop",
        5: "Pizza Box", 6: "Mini Tower", 7: "Tower", 8: "Portable",
        9: "Laptop", 10: "Notebook", 11: "Hand Held", 12: "Docking Station",
        13: "All in One", 14: "Sub Notebook", 15: "Space-Saving",
        16: "Lunch Box", 17: "Main Server Chassis", 18: "Expansion Chassis",
        19: "SubChassis", 20: "Bus Expansion Chassis",
        21: "Peripheral Chassis", 22: "RAID Chassis", 23: "Rack Mount Chassis",
        24: "Sealed-case PC", 30: "Tablet", 31: "Convertible",
        32: "Detachable",
    }
    return types.get(code, f"Unknown ({code})")


def get_cpu_info():
    """Información del procesador."""
    info = {
        "name": "",
        "cores_physical": 0,
        "cores_logical": 0,
        "max_speed_mhz": 0,
        "current_speed_mhz": 0,
        "architecture": platform.processor(),
        "usage_percent": 0,
    }

    if platform.system() == "Windows":
        cpu_name = _run_powershell(
            "(Get-CimInstance Win32_Processor).Name"
        )
        if cpu_name:
            info["name"] = cpu_name

        max_speed = _run_powershell(
            "(Get-CimInstance Win32_Processor).MaxClockSpeed"
        )
        if max_speed and max_speed.isdigit():
            info["max_speed_mhz"] = int(max_speed)

    if psutil:
        info["cores_physical"] = psutil.cpu_count(logical=False) or 0
        info["cores_logical"] = psutil.cpu_count(logical=True) or 0
        info["usage_percent"] = psutil.cpu_percent(interval=1)

    return info


def get_memory_info():
    """Información de la memoria RAM."""
    info = {
        "total_gb": 0,
        "available_gb": 0,
        "used_gb": 0,
        "usage_percent": 0,
        "modules": [],
    }

    if psutil:
        mem = psutil.virtual_memory()
        info["total_gb"] = round(mem.total / (1024 ** 3), 2)
        info["available_gb"] = round(mem.available / (1024 ** 3), 2)
        info["used_gb"] = round(mem.used / (1024 ** 3), 2)
        info["usage_percent"] = mem.percent

    if platform.system() == "Windows":
        # Detalle de módulos RAM
        ram_json = _run_powershell(
            "Get-CimInstance Win32_PhysicalMemory | "
            "Select-Object Manufacturer,PartNumber,Speed,Capacity,"
            "DeviceLocator,MemoryType,FormFactor | ConvertTo-Json"
        )
        if ram_json:
            try:
                modules = json.loads(ram_json)
                if isinstance(modules, dict):
                    modules = [modules]
                for mod in modules:
                    info["modules"].append({
                        "manufacturer": mod.get("Manufacturer", "").strip(),
                        "part_number": mod.get("PartNumber", "").strip(),
                        "speed_mhz": mod.get("Speed", 0),
                        "capacity_gb": round(
                            (mod.get("Capacity", 0) or 0) / (1024 ** 3), 2
                        ),
                        "slot": mod.get("DeviceLocator", ""),
                    })
            except json.JSONDecodeError:
                pass

    return info


def get_disk_info():
    """Información de discos físicos y particiones."""
    disks = {
        "physical_drives": [],
        "partitions": [],
    }

    if platform.system() == "Windows":
        # Discos físicos
        disk_json = _run_powershell(
            "Get-CimInstance Win32_DiskDrive | "
            "Select-Object Model,SerialNumber,Size,InterfaceType,MediaType,"
            "FirmwareRevision,Partitions | ConvertTo-Json"
        )
        if disk_json:
            try:
                drives = json.loads(disk_json)
                if isinstance(drives, dict):
                    drives = [drives]
                for d in drives:
                    disks["physical_drives"].append({
                        "model": (d.get("Model") or "").strip(),
                        "serial": (d.get("SerialNumber") or "").strip(),
                        "size_gb": round(
                            (d.get("Size", 0) or 0) / (1024 ** 3), 2
                        ),
                        "interface": d.get("InterfaceType", ""),
                        "media_type": d.get("MediaType", ""),
                        "firmware": d.get("FirmwareRevision", ""),
                        "partitions": d.get("Partitions", 0),
                    })
            except json.JSONDecodeError:
                pass

    if psutil:
        for part in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(part.mountpoint)
                disks["partitions"].append({
                    "device": part.device,
                    "mountpoint": part.mountpoint,
                    "fstype": part.fstype,
                    "total_gb": round(usage.total / (1024 ** 3), 2),
                    "used_gb": round(usage.used / (1024 ** 3), 2),
                    "free_gb": round(usage.free / (1024 ** 3), 2),
                    "usage_percent": usage.percent,
                })
            except PermissionError:
                pass

    return disks


def get_network_info():
    """Información de adaptadores de red."""
    adapters = []

    if psutil:
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()

        for iface, addr_list in addrs.items():
            adapter = {
                "name": iface,
                "is_up": False,
                "speed_mbps": 0,
                "ipv4": "",
                "ipv6": "",
                "mac": "",
            }
            if iface in stats:
                adapter["is_up"] = stats[iface].isup
                adapter["speed_mbps"] = stats[iface].speed

            for addr in addr_list:
                if addr.family == socket.AF_INET:
                    adapter["ipv4"] = addr.address
                elif addr.family == socket.AF_INET6:
                    adapter["ipv6"] = addr.address
                elif addr.family == psutil.AF_LINK:
                    adapter["mac"] = addr.address

            adapters.append(adapter)

    return adapters


def get_gpu_info():
    """Información de tarjetas gráficas."""
    gpus = []

    if platform.system() == "Windows":
        gpu_json = _run_powershell(
            "Get-CimInstance Win32_VideoController | "
            "Select-Object Name,AdapterRAM,DriverVersion,"
            "VideoProcessor,CurrentHorizontalResolution,"
            "CurrentVerticalResolution | ConvertTo-Json"
        )
        if gpu_json:
            try:
                items = json.loads(gpu_json)
                if isinstance(items, dict):
                    items = [items]
                for g in items:
                    gpus.append({
                        "name": g.get("Name", ""),
                        "vram_mb": round(
                            (g.get("AdapterRAM", 0) or 0) / (1024 ** 2), 0
                        ),
                        "driver_version": g.get("DriverVersion", ""),
                        "resolution": (
                            f"{g.get('CurrentHorizontalResolution', '')}x"
                            f"{g.get('CurrentVerticalResolution', '')}"
                        ),
                    })
            except json.JSONDecodeError:
                pass

    return gpus


def get_installed_software():
    """Lista de software instalado."""
    software = []

    if platform.system() == "Windows":
        ps_cmd = (
            "Get-ItemProperty "
            "'HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
            "'HKLM:\\Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*' "
            "| Where-Object { $_.DisplayName } "
            "| Select-Object DisplayName,DisplayVersion,Publisher,InstallDate "
            "| Sort-Object DisplayName "
            "| ConvertTo-Json -Compress"
        )
        sw_json = _run_powershell(ps_cmd)
        if sw_json:
            try:
                items = json.loads(sw_json)
                if isinstance(items, dict):
                    items = [items]
                for s in items:
                    software.append({
                        "name": s.get("DisplayName", ""),
                        "version": s.get("DisplayVersion", ""),
                        "publisher": s.get("Publisher", ""),
                        "install_date": s.get("InstallDate", ""),
                    })
            except json.JSONDecodeError:
                pass

    return software


def get_windows_updates():
    """Últimas actualizaciones de Windows instaladas."""
    updates = []

    if platform.system() == "Windows":
        upd_json = _run_powershell(
            "Get-HotFix | Select-Object HotFixID,Description,InstalledOn "
            "| Sort-Object InstalledOn -Descending "
            "| Select-Object -First 20 | ConvertTo-Json"
        )
        if upd_json:
            try:
                items = json.loads(upd_json)
                if isinstance(items, dict):
                    items = [items]
                for u in items:
                    installed = u.get("InstalledOn", "")
                    if isinstance(installed, dict):
                        installed = installed.get("DateTime", "")
                    updates.append({
                        "kb": u.get("HotFixID", ""),
                        "description": u.get("Description", ""),
                        "installed_on": str(installed),
                    })
            except json.JSONDecodeError:
                pass

    return updates


def get_logged_user():
    """Usuario actualmente logueado."""
    try:
        if platform.system() == "Windows":
            user = _run_powershell(
                "[System.Security.Principal.WindowsIdentity]"
                "::GetCurrent().Name"
            )
            if user:
                return user
        return os.getlogin()
    except Exception:
        return ""


def is_admin():
    """Verifica si se ejecuta con privilegios de administrador."""
    try:
        if platform.system() == "Windows":
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        pass
    return False


def collect_full_inventory():
    """Recopila toda la información y la retorna como diccionario."""
    inventory = {
        "agent_version": "1.0.0",
        "collected_at": datetime.now().isoformat(),
        "agent_id": str(uuid.getnode()),
        "is_admin": is_admin(),
        "logged_user": get_logged_user(),
        "system": get_system_info(),
        "hardware": get_hardware_ids(),
        "cpu": get_cpu_info(),
        "memory": get_memory_info(),
        "disks": get_disk_info(),
        "network": get_network_info(),
        "gpu": get_gpu_info(),
        "software": get_installed_software(),
        "windows_updates": get_windows_updates(),
    }

    return inventory


if __name__ == "__main__":
    data = collect_full_inventory()
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
