"""
PC Inventory Collector
Recopila información completa del hardware y software del equipo.
Compatible con Windows (WMI/PowerShell), macOS (system_profiler) y Linux.
"""

import platform
import socket
import subprocess
import json
import uuid
import os
import sys
from datetime import datetime

try:
    import psutil
except ImportError:
    psutil = None

# WMI solo en Windows
wmi_module = None
if platform.system() == "Windows":
    try:
        import wmi as wmi_module
    except ImportError:
        pass

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"


# ========== Helpers de subproceso ==========
def _run_cmd(command, timeout=30, shell=False):
    """Ejecuta un comando y retorna la salida."""
    try:
        result = subprocess.run(
            command, capture_output=True, text=True,
            timeout=timeout, shell=shell
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _run_powershell(command):
    """Ejecuta un comando PowerShell y retorna la salida."""
    return _run_cmd(
        ["powershell", "-NoProfile", "-Command", command], timeout=30
    )


def _run_wmic(command):
    """Ejecuta un comando WMIC y retorna la salida."""
    return _run_cmd(command, timeout=15, shell=True)


def _run_system_profiler(data_type):
    """Ejecuta system_profiler en macOS y retorna JSON."""
    try:
        output = _run_cmd(
            ["system_profiler", data_type, "-json"], timeout=30
        )
        if output:
            return json.loads(output)
    except (json.JSONDecodeError, Exception):
        pass
    return {}


# ========== System Info ==========
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

    if IS_WINDOWS:
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

    elif IS_MAC:
        # macOS version info
        sw_vers = _run_cmd(["sw_vers", "-productVersion"])
        build = _run_cmd(["sw_vers", "-buildVersion"])
        product = _run_cmd(["sw_vers", "-productName"])
        info["os_edition"] = f"{product} {sw_vers}" if product else f"macOS {sw_vers}"
        info["os_build"] = build
        info["os_version"] = sw_vers

    elif IS_LINUX:
        # Linux distro info
        try:
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        info["os_edition"] = line.split("=", 1)[1].strip().strip('"')
                    elif line.startswith("VERSION_ID="):
                        info["os_build"] = line.split("=", 1)[1].strip().strip('"')
        except FileNotFoundError:
            pass

    if psutil:
        import datetime as dt
        boot = dt.datetime.fromtimestamp(psutil.boot_time())
        info["boot_time"] = boot.isoformat()

    return info


# ========== Hardware IDs ==========
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

    if IS_WINDOWS:
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
                        info["chassis_type"] = _chassis_type_name(
                            chassis_types[0]
                        )
                return info
            except Exception:
                pass

        # Fallback PowerShell
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

    elif IS_MAC:
        sp = _run_system_profiler("SPHardwareDataType")
        hw_items = sp.get("SPHardwareDataType", [])
        if hw_items:
            hw = hw_items[0]
            info["model"] = hw.get("machine_model", "")
            info["serial_number"] = hw.get("serial_number", "")
            info["system_uuid"] = hw.get("platform_UUID", "")
            # Apple Silicon vs Intel
            chip = hw.get("chip_type", "")
            if chip:
                info["manufacturer"] = "Apple"
                info["model"] = f"{hw.get('machine_name', '')} ({chip})"
            else:
                info["manufacturer"] = "Apple"
            info["chassis_type"] = _detect_mac_chassis()

    elif IS_LINUX:
        # Leer de DMI/SMBIOS
        dmi_paths = {
            "manufacturer": "/sys/class/dmi/id/sys_vendor",
            "model": "/sys/class/dmi/id/product_name",
            "serial_number": "/sys/class/dmi/id/product_serial",
            "bios_version": "/sys/class/dmi/id/bios_version",
            "bios_vendor": "/sys/class/dmi/id/bios_vendor",
            "system_uuid": "/sys/class/dmi/id/product_uuid",
            "chassis_type": "/sys/class/dmi/id/chassis_type",
        }
        for key, path in dmi_paths.items():
            try:
                with open(path) as f:
                    val = f.read().strip()
                    if key == "chassis_type" and val.isdigit():
                        val = _chassis_type_name(int(val))
                    info[key] = val
            except (FileNotFoundError, PermissionError):
                pass

    return info


def _detect_mac_chassis():
    """Detecta tipo de chasis en Mac."""
    model = _run_cmd(["sysctl", "-n", "hw.model"])
    model_lower = model.lower() if model else ""
    if "book" in model_lower:
        return "Laptop"
    elif "imac" in model_lower:
        return "All in One"
    elif "mini" in model_lower:
        return "Desktop"
    elif "pro" in model_lower and "book" not in model_lower:
        return "Desktop"
    return "Desktop"


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


# ========== CPU ==========
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

    if IS_WINDOWS:
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

    elif IS_MAC:
        # CPU name
        brand = _run_cmd(["sysctl", "-n", "machdep.cpu.brand_string"])
        if brand:
            info["name"] = brand
        else:
            # Apple Silicon
            chip = _run_cmd(
                ["sysctl", "-n", "machdep.cpu.brand"]
            )
            if not chip:
                sp = _run_system_profiler("SPHardwareDataType")
                hw_items = sp.get("SPHardwareDataType", [])
                if hw_items:
                    chip = hw_items[0].get("chip_type", "Apple Silicon")
            info["name"] = chip or "Apple Silicon"

    elif IS_LINUX:
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        info["name"] = line.split(":", 1)[1].strip()
                        break
        except FileNotFoundError:
            pass

    if psutil:
        info["cores_physical"] = psutil.cpu_count(logical=False) or 0
        info["cores_logical"] = psutil.cpu_count(logical=True) or 0
        info["usage_percent"] = psutil.cpu_percent(interval=1)

    return info


# ========== Memory ==========
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

    if IS_WINDOWS:
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

    elif IS_MAC:
        sp = _run_system_profiler("SPMemoryDataType")
        mem_items = sp.get("SPMemoryDataType", [])
        for bank in mem_items:
            # macOS puede tener items directos o sub-items
            items = bank.get("_items", [bank])
            for mod in items:
                size_str = mod.get("dimm_size", "0")
                # Parsear "8 GB" -> 8.0
                try:
                    size_gb = float(size_str.split()[0])
                except (ValueError, IndexError):
                    size_gb = 0
                speed_str = mod.get("dimm_speed", "0")
                try:
                    speed = int(speed_str.split()[0])
                except (ValueError, IndexError):
                    speed = 0
                info["modules"].append({
                    "manufacturer": mod.get("dimm_manufacturer", ""),
                    "part_number": mod.get("dimm_part_number", ""),
                    "speed_mhz": speed,
                    "capacity_gb": size_gb,
                    "slot": mod.get("_name", ""),
                })

    return info


# ========== Disks ==========
def get_disk_info():
    """Información de discos físicos y particiones."""
    disks = {
        "physical_drives": [],
        "partitions": [],
    }

    if IS_WINDOWS:
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

    elif IS_MAC:
        sp = _run_system_profiler("SPStorageDataType")
        storage_items = sp.get("SPStorageDataType", [])
        for vol in storage_items:
            size_bytes = vol.get("size_in_bytes", 0)
            free_bytes = vol.get("free_space_in_bytes", 0)
            total_gb = round(size_bytes / (1024 ** 3), 2) if size_bytes else 0
            free_gb = round(free_bytes / (1024 ** 3), 2) if free_bytes else 0
            disks["physical_drives"].append({
                "model": vol.get("physical_drive", {}).get(
                    "device_name", vol.get("_name", "")
                ),
                "serial": "",
                "size_gb": total_gb,
                "interface": vol.get("physical_drive", {}).get(
                    "protocol", ""
                ),
                "media_type": vol.get("physical_drive", {}).get(
                    "medium_type", ""
                ),
            })

    elif IS_LINUX:
        # lsblk para discos físicos
        lsblk_out = _run_cmd(
            ["lsblk", "-Jbo", "NAME,SIZE,TYPE,MODEL,SERIAL,TRAN"],
            timeout=10
        )
        if lsblk_out:
            try:
                blk = json.loads(lsblk_out)
                for dev in blk.get("blockdevices", []):
                    if dev.get("type") == "disk":
                        disks["physical_drives"].append({
                            "model": dev.get("model", "").strip()
                                if dev.get("model") else "",
                            "serial": dev.get("serial", "").strip()
                                if dev.get("serial") else "",
                            "size_gb": round(
                                int(dev.get("size", 0)) / (1024 ** 3), 2
                            ),
                            "interface": dev.get("tran", "") or "",
                            "media_type": "",
                        })
            except (json.JSONDecodeError, ValueError):
                pass

    # Particiones (multiplataforma con psutil)
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
            except (PermissionError, OSError):
                pass

    return disks


# ========== Network ==========
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


# ========== GPU ==========
def get_gpu_info():
    """Información de tarjetas gráficas."""
    gpus = []

    if IS_WINDOWS:
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

    elif IS_MAC:
        sp = _run_system_profiler("SPDisplaysDataType")
        display_items = sp.get("SPDisplaysDataType", [])
        for gpu in display_items:
            vram_str = gpu.get("sppci_vram", gpu.get("sppci_vram_shared", ""))
            try:
                vram_mb = int(vram_str.split()[0]) if vram_str else 0
            except (ValueError, IndexError):
                vram_mb = 0
            # Resolution from connected displays
            resolution = ""
            ndrvs = gpu.get("spdisplays_ndrvs", [])
            if ndrvs:
                res = ndrvs[0].get("_spdisplays_resolution", "")
                resolution = res
            gpus.append({
                "name": gpu.get("sppci_model", gpu.get("_name", "")),
                "vram_mb": vram_mb,
                "driver_version": "",
                "resolution": resolution,
            })

    elif IS_LINUX:
        # Intentar con lspci
        lspci = _run_cmd(["lspci"], timeout=10)
        if lspci:
            for line in lspci.split("\n"):
                if "VGA" in line or "3D" in line or "Display" in line:
                    # Extraer nombre después del ": "
                    parts = line.split(": ", 1)
                    name = parts[1] if len(parts) > 1 else line
                    gpus.append({
                        "name": name.strip(),
                        "vram_mb": 0,
                        "driver_version": "",
                        "resolution": "",
                    })

    return gpus


# ========== Software ==========
def get_installed_software():
    """Lista de software instalado."""
    software = []

    if IS_WINDOWS:
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

    elif IS_MAC:
        sp = _run_system_profiler("SPApplicationsDataType")
        app_items = sp.get("SPApplicationsDataType", [])
        for app in app_items:
            software.append({
                "name": app.get("_name", ""),
                "version": app.get("version", ""),
                "publisher": app.get("obtained_from", ""),
                "install_date": app.get("lastModified", ""),
            })
        # Sort by name
        software.sort(key=lambda x: x.get("name", "").lower())

    elif IS_LINUX:
        # dpkg para Debian/Ubuntu
        dpkg_out = _run_cmd(
            ["dpkg-query", "-W", "-f",
             '${Package}\\t${Version}\\t${Maintainer}\\n'],
            timeout=30
        )
        if dpkg_out:
            for line in dpkg_out.split("\n"):
                parts = line.split("\t")
                if len(parts) >= 2:
                    software.append({
                        "name": parts[0],
                        "version": parts[1],
                        "publisher": parts[2] if len(parts) > 2 else "",
                        "install_date": "",
                    })
        else:
            # rpm para RHEL/CentOS/Fedora
            rpm_out = _run_cmd(
                ["rpm", "-qa", "--queryformat",
                 "%{NAME}\\t%{VERSION}-%{RELEASE}\\t%{VENDOR}\\n"],
                timeout=30
            )
            if rpm_out:
                for line in rpm_out.split("\n"):
                    parts = line.split("\t")
                    if len(parts) >= 2:
                        software.append({
                            "name": parts[0],
                            "version": parts[1],
                            "publisher": parts[2]
                                if len(parts) > 2 else "",
                            "install_date": "",
                        })

    return software


# ========== Windows Updates ==========
def get_windows_updates():
    """Últimas actualizaciones de Windows instaladas."""
    updates = []

    if not IS_WINDOWS:
        return updates

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


# ========== User / Admin ==========
def get_logged_user():
    """Usuario actualmente logueado."""
    try:
        if IS_WINDOWS:
            user = _run_powershell(
                "[System.Security.Principal.WindowsIdentity]"
                "::GetCurrent().Name"
            )
            if user:
                return user
        return os.getlogin()
    except Exception:
        return os.environ.get("USER", os.environ.get("USERNAME", ""))


def is_admin():
    """Verifica si se ejecuta con privilegios de administrador/root."""
    try:
        if IS_WINDOWS:
            import ctypes
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        else:
            return os.geteuid() == 0
    except Exception:
        return False


# ========== Collect All ==========
def collect_full_inventory():
    """Recopila toda la información y la retorna como diccionario."""
    inventory = {
        "agent_version": "1.1.0",
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
