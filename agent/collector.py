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


# ========== Peripherals ==========
def get_peripherals():
    """Recopila periféricos: monitores, impresoras, teclados, mouse, USB."""
    peripherals = {
        "monitors": [],
        "printers": [],
        "keyboards": [],
        "mice": [],
        "usb_devices": [],
    }

    if IS_WINDOWS:
        # --- Monitores ---
        try:
            # WmiMonitorID da serial/manufacturer reales vía PowerShell
            ps_monitors = _run_powershell("""
                try {
                    $monitors = Get-CimInstance -Namespace root\\wmi -ClassName WmiMonitorID -ErrorAction Stop
                    $results = @()
                    foreach ($m in $monitors) {
                        $name = if ($m.UserFriendlyName) { ($m.UserFriendlyName | Where-Object {$_ -ne 0} | ForEach-Object { [char]$_ }) -join '' } else { '' }
                        $serial = if ($m.SerialNumberID) { ($m.SerialNumberID | Where-Object {$_ -ne 0} | ForEach-Object { [char]$_ }) -join '' } else { '' }
                        $mfr = if ($m.ManufacturerName) { ($m.ManufacturerName | Where-Object {$_ -ne 0} | ForEach-Object { [char]$_ }) -join '' } else { '' }
                        $results += @{
                            name = $name
                            serial = $serial
                            manufacturer = $mfr
                            instance = $m.InstanceName
                        }
                    }
                    $results | ConvertTo-Json -Compress
                } catch { '[]' }
            """)
            try:
                mon_list = json.loads(ps_monitors) if ps_monitors else []
                if isinstance(mon_list, dict):
                    mon_list = [mon_list]
            except Exception:
                mon_list = []

            # Complementar con Win32_DesktopMonitor para resolución
            ps_desktop_mon = _run_powershell(
                "Get-CimInstance Win32_DesktopMonitor | Select-Object Name, "
                "ScreenWidth, ScreenHeight, PNPDeviceID | ConvertTo-Json -Compress"
            )
            try:
                desk_mons = json.loads(ps_desktop_mon) if ps_desktop_mon else []
                if isinstance(desk_mons, dict):
                    desk_mons = [desk_mons]
            except Exception:
                desk_mons = []

            # Fusionar datos
            for i, m in enumerate(mon_list):
                entry = {
                    "name": m.get("name", "").strip() or "Monitor",
                    "serial": m.get("serial", "").strip(),
                    "manufacturer": m.get("manufacturer", "").strip(),
                    "resolution": "",
                }
                if i < len(desk_mons):
                    dm = desk_mons[i]
                    w = dm.get("ScreenWidth")
                    h = dm.get("ScreenHeight")
                    if w and h:
                        entry["resolution"] = f"{w}x{h}"
                    if not entry["name"] or entry["name"] == "Monitor":
                        entry["name"] = dm.get("Name", "Monitor") or "Monitor"
                peripherals["monitors"].append(entry)

            # Si WmiMonitorID no devolvió nada, usar Win32_DesktopMonitor solo
            if not mon_list and desk_mons:
                for dm in desk_mons:
                    w = dm.get("ScreenWidth")
                    h = dm.get("ScreenHeight")
                    peripherals["monitors"].append({
                        "name": dm.get("Name", "Monitor") or "Monitor",
                        "serial": "",
                        "manufacturer": "",
                        "resolution": f"{w}x{h}" if w and h else "",
                    })
        except Exception:
            pass

        # --- Impresoras ---
        try:
            ps_printers = _run_powershell(
                "Get-CimInstance Win32_Printer | Select-Object Name, DriverName, "
                "PortName, DeviceID, Local, Network, Default | ConvertTo-Json -Compress"
            )
            try:
                prn_list = json.loads(ps_printers) if ps_printers else []
                if isinstance(prn_list, dict):
                    prn_list = [prn_list]
            except Exception:
                prn_list = []

            for p in prn_list:
                peripherals["printers"].append({
                    "name": p.get("Name", ""),
                    "driver": p.get("DriverName", ""),
                    "port": p.get("PortName", ""),
                    "is_local": bool(p.get("Local", False)),
                    "is_network": bool(p.get("Network", False)),
                    "is_default": bool(p.get("Default", False)),
                })
        except Exception:
            pass

        # --- Teclados ---
        try:
            ps_kbd = _run_powershell(
                "Get-CimInstance Win32_Keyboard | Select-Object Name, Description, "
                "DeviceID, Layout, PNPDeviceID, Status | ConvertTo-Json -Compress"
            )
            try:
                kbd_list = json.loads(ps_kbd) if ps_kbd else []
                if isinstance(kbd_list, dict):
                    kbd_list = [kbd_list]
            except Exception:
                kbd_list = []

            for k in kbd_list:
                pnp = k.get("PNPDeviceID", "")
                conn_type = "USB" if "USB" in pnp.upper() else ("PS/2" if "PS2" in pnp.upper() else "Otro")
                peripherals["keyboards"].append({
                    "name": k.get("Name", "") or k.get("Description", ""),
                    "type": conn_type,
                    "layout": k.get("Layout", ""),
                    "device_id": k.get("DeviceID", ""),
                    "status": k.get("Status", ""),
                })
        except Exception:
            pass

        # --- Mouse ---
        try:
            ps_mouse = _run_powershell(
                "Get-CimInstance Win32_PointingDevice | Select-Object Name, Description, "
                "Manufacturer, DeviceID, PNPDeviceID, PointingType, Status, "
                "NumberOfButtons | ConvertTo-Json -Compress"
            )
            try:
                mouse_list = json.loads(ps_mouse) if ps_mouse else []
                if isinstance(mouse_list, dict):
                    mouse_list = [mouse_list]
            except Exception:
                mouse_list = []

            pointing_types = {1: "Otro", 2: "Desconocido", 3: "Mouse", 4: "Trackball",
                              5: "Track Point", 6: "Glide Point", 7: "Touch Pad",
                              8: "Touch Screen", 9: "Mouse óptico"}
            for m in mouse_list:
                pnp = m.get("PNPDeviceID", "")
                conn_type = "USB" if "USB" in pnp.upper() else ("PS/2" if "PS2" in pnp.upper() else ("Bluetooth" if "BTHLE" in pnp.upper() or "BTH" in pnp.upper() else "Otro"))
                pt = m.get("PointingType")
                peripherals["mice"].append({
                    "name": m.get("Name", "") or m.get("Description", ""),
                    "manufacturer": m.get("Manufacturer", ""),
                    "type": conn_type,
                    "pointing_type": pointing_types.get(pt, "Mouse"),
                    "buttons": m.get("NumberOfButtons", 0),
                    "status": m.get("Status", ""),
                })
        except Exception:
            pass

        # --- Dispositivos USB ---
        try:
            ps_usb = _run_powershell(
                "Get-CimInstance Win32_USBControllerDevice | "
                "ForEach-Object { [wmi]($_.Dependent) } | "
                "Where-Object { $_.PNPClass -ne 'USB' -and $_.PNPClass -ne $null } | "
                "Select-Object Name, Manufacturer, DeviceID, PNPClass, Status -First 50 | "
                "ConvertTo-Json -Compress"
            )
            try:
                usb_list = json.loads(ps_usb) if ps_usb else []
                if isinstance(usb_list, dict):
                    usb_list = [usb_list]
            except Exception:
                usb_list = []

            for u in usb_list:
                name = u.get("Name", "")
                # Filtrar dispositivos genéricos de sistema
                if name and "USB Composite" not in name and "USB Root Hub" not in name:
                    peripherals["usb_devices"].append({
                        "name": name,
                        "manufacturer": u.get("Manufacturer", ""),
                        "device_id": u.get("DeviceID", ""),
                        "class": u.get("PNPClass", ""),
                        "status": u.get("Status", ""),
                    })
        except Exception:
            pass

    elif IS_MAC:
        # --- macOS: system_profiler ---
        try:
            # Monitores
            disp_data = _run_system_profiler("SPDisplaysDataType")
            if disp_data:
                for gpu in disp_data:
                    displays = gpu.get("spdisplays_ndrvs", [])
                    for d in displays:
                        res = d.get("_spdisplays_resolution", "")
                        peripherals["monitors"].append({
                            "name": d.get("_name", "Display"),
                            "serial": d.get("_spdisplays_display-serial-number", ""),
                            "manufacturer": d.get("_spdisplays_display-vendor-id", ""),
                            "resolution": res,
                        })
        except Exception:
            pass

        try:
            # Impresoras
            prn_data = _run_system_profiler("SPPrintersDataType")
            if prn_data:
                for p in prn_data:
                    peripherals["printers"].append({
                        "name": p.get("_name", ""),
                        "driver": p.get("ppd", ""),
                        "port": p.get("uri", ""),
                        "is_local": True,
                        "is_network": "://" in p.get("uri", ""),
                        "is_default": p.get("default", "") == "yes",
                    })
        except Exception:
            pass

        try:
            # USB
            usb_data = _run_system_profiler("SPUSBDataType")
            if usb_data:
                def _parse_usb_mac(items, results):
                    for item in items:
                        name = item.get("_name", "")
                        if name and "hub" not in name.lower():
                            results.append({
                                "name": name,
                                "manufacturer": item.get("manufacturer", ""),
                                "device_id": item.get("vendor_id", ""),
                                "class": "USB",
                                "status": "OK",
                            })
                        # Recurse into children
                        children = item.get("_items", [])
                        if children:
                            _parse_usb_mac(children, results)
                _parse_usb_mac(usb_data, peripherals["usb_devices"])
        except Exception:
            pass

        # Teclado y mouse (genéricos en macOS)
        try:
            hid_output = _run_cmd(
                ["ioreg", "-r", "-c", "IOHIDInterface", "-l"], timeout=10
            )
            if "Keyboard" in hid_output:
                peripherals["keyboards"].append({
                    "name": "Apple Keyboard",
                    "type": "Built-in / Bluetooth",
                    "layout": "",
                    "device_id": "",
                    "status": "OK",
                })
            if "Mouse" in hid_output or "Trackpad" in hid_output:
                peripherals["mice"].append({
                    "name": "Apple Trackpad / Mouse",
                    "manufacturer": "Apple",
                    "type": "Built-in / Bluetooth",
                    "pointing_type": "Touch Pad",
                    "buttons": 2,
                    "status": "OK",
                })
        except Exception:
            pass

    elif IS_LINUX:
        # --- Linux ---
        try:
            # Monitores via xrandr
            xrandr = _run_cmd(["xrandr", "--query"], timeout=10)
            if xrandr:
                for line in xrandr.split("\n"):
                    if " connected" in line:
                        parts = line.split()
                        name = parts[0]
                        res = ""
                        for p in parts:
                            if "x" in p and "+" in p:
                                res = p.split("+")[0]
                                break
                        peripherals["monitors"].append({
                            "name": name,
                            "serial": "",
                            "manufacturer": "",
                            "resolution": res,
                        })
        except Exception:
            pass

        try:
            # Impresoras via lpstat
            lpstat = _run_cmd(["lpstat", "-p", "-d"], timeout=10)
            if lpstat:
                for line in lpstat.split("\n"):
                    if line.startswith("printer"):
                        parts = line.split()
                        if len(parts) >= 2:
                            peripherals["printers"].append({
                                "name": parts[1],
                                "driver": "",
                                "port": "",
                                "is_local": True,
                                "is_network": False,
                                "is_default": False,
                            })
                    if "default destination:" in line.lower():
                        default_name = line.split(":")[-1].strip()
                        for p in peripherals["printers"]:
                            if p["name"] == default_name:
                                p["is_default"] = True
        except Exception:
            pass

        try:
            # USB via lsusb
            lsusb = _run_cmd(["lsusb"], timeout=10)
            if lsusb:
                for line in lsusb.split("\n"):
                    if line and "hub" not in line.lower():
                        # Bus 001 Device 003: ID 046d:c077 Logitech, Inc. M105 Optical Mouse
                        parts = line.split("ID ")
                        if len(parts) == 2:
                            dev_info = parts[1]
                            dev_id = dev_info.split(" ")[0] if " " in dev_info else dev_info
                            dev_name = " ".join(dev_info.split(" ")[1:]).strip() if " " in dev_info else ""
                            if dev_name:
                                peripherals["usb_devices"].append({
                                    "name": dev_name,
                                    "manufacturer": "",
                                    "device_id": dev_id,
                                    "class": "USB",
                                    "status": "OK",
                                })
        except Exception:
            pass

        # Teclado y mouse desde /proc/bus/input/devices
        try:
            if os.path.exists("/proc/bus/input/devices"):
                with open("/proc/bus/input/devices", "r") as f:
                    content = f.read()
                blocks = content.split("\n\n")
                for block in blocks:
                    if "keyboard" in block.lower() and "EV=" in block:
                        name_line = [l for l in block.split("\n") if l.startswith("N:")]
                        if name_line:
                            kbd_name = name_line[0].split('"')[1] if '"' in name_line[0] else "Keyboard"
                            peripherals["keyboards"].append({
                                "name": kbd_name,
                                "type": "USB" if "usb" in block.lower() else "PS/2",
                                "layout": "",
                                "device_id": "",
                                "status": "OK",
                            })
                    if "mouse" in block.lower() and "EV=" in block:
                        name_line = [l for l in block.split("\n") if l.startswith("N:")]
                        if name_line:
                            mouse_name = name_line[0].split('"')[1] if '"' in name_line[0] else "Mouse"
                            peripherals["mice"].append({
                                "name": mouse_name,
                                "manufacturer": "",
                                "type": "USB" if "usb" in block.lower() else "PS/2",
                                "pointing_type": "Mouse",
                                "buttons": 3,
                                "status": "OK",
                            })
        except Exception:
            pass

    return peripherals


# ========== Collect All ==========
def get_vulnerabilities():
    """Escanea vulnerabilidades y configuraciones inseguras del equipo."""
    vulns = []

    if not IS_WINDOWS:
        return vulns

    def _ps(cmd):
        """Ejecuta PowerShell y retorna stdout."""
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", cmd],
                capture_output=True, text=True, timeout=30
            )
            return r.stdout.strip()
        except Exception:
            return ""

    def _add(vuln_id, title, description, severity, category, remediation=""):
        vulns.append({
            "vuln_id": vuln_id,
            "title": title,
            "description": description,
            "severity": severity,  # critical, high, medium, low
            "category": category,
            "remediation": remediation,
        })

    # ── 1. Firewall deshabilitado ──
    try:
        fw = _ps("Get-NetFirewallProfile | Select-Object -Property Name,Enabled | ConvertTo-Json")
        if fw:
            profiles = json.loads(fw)
            if not isinstance(profiles, list):
                profiles = [profiles]
            for p in profiles:
                if not p.get("Enabled", True):
                    _add("FW-001",
                         f"Firewall deshabilitado: perfil {p.get('Name', '?')}",
                         f"El perfil de firewall '{p.get('Name')}' está desactivado, dejando el equipo expuesto a conexiones no autorizadas.",
                         "critical", "firewall",
                         f"Habilitar: Set-NetFirewallProfile -Name {p.get('Name')} -Enabled True")
    except Exception:
        pass

    # ── 2. Antivirus / Windows Defender ──
    try:
        av = _ps("Get-MpComputerStatus | Select-Object AntivirusEnabled,RealTimeProtectionEnabled,AntivirusSignatureAge,AntispywareEnabled | ConvertTo-Json")
        if av:
            avd = json.loads(av)
            if not avd.get("AntivirusEnabled", True):
                _add("AV-001", "Antivirus deshabilitado",
                     "Windows Defender u otro antivirus está desactivado en este equipo.",
                     "critical", "antivirus",
                     "Activar Windows Defender desde Seguridad de Windows")
            if not avd.get("RealTimeProtectionEnabled", True):
                _add("AV-002", "Protección en tiempo real desactivada",
                     "La protección en tiempo real del antivirus no está activa.",
                     "high", "antivirus",
                     "Set-MpPreference -DisableRealtimeMonitoring $false")
            sig_age = avd.get("AntivirusSignatureAge", 0)
            if sig_age and int(sig_age) > 7:
                _add("AV-003", f"Firmas de antivirus desactualizadas ({sig_age} días)",
                     f"Las definiciones de virus tienen {sig_age} días de antigüedad. Se recomienda actualizar al menos semanalmente.",
                     "high" if int(sig_age) > 14 else "medium", "antivirus",
                     "Update-MpSignature")
    except Exception:
        pass

    # ── 3. SMBv1 habilitado (WannaCry, EternalBlue) ──
    try:
        smb1 = _ps("Get-SmbServerConfiguration | Select-Object EnableSMB1Protocol | ConvertTo-Json")
        if smb1:
            smbd = json.loads(smb1)
            if smbd.get("EnableSMB1Protocol", False):
                _add("SMB-001", "SMBv1 habilitado",
                     "El protocolo SMBv1 está activo. Es vulnerable a exploits como WannaCry y EternalBlue.",
                     "critical", "protocol",
                     "Set-SmbServerConfiguration -EnableSMB1Protocol $false -Force")
    except Exception:
        pass

    # ── 4. Cuenta de invitado activa ──
    try:
        guest = _ps("Get-LocalUser -Name Guest | Select-Object Enabled | ConvertTo-Json")
        if guest:
            gd = json.loads(guest)
            if gd.get("Enabled", False):
                _add("ACC-001", "Cuenta de invitado activa",
                     "La cuenta 'Guest' está habilitada, permitiendo acceso anónimo al equipo.",
                     "high", "accounts",
                     "Disable-LocalUser -Name Guest")
    except Exception:
        pass

    # ── 5. Cuentas de administrador sin contraseña ──
    try:
        admins = _ps("""
            $admins = Get-LocalGroupMember -Group 'Administradores' -ErrorAction SilentlyContinue
            if (-not $admins) { $admins = Get-LocalGroupMember -Group 'Administrators' -ErrorAction SilentlyContinue }
            foreach ($a in $admins) {
                $user = Get-LocalUser -Name ($a.Name -split '\\\\')[-1] -ErrorAction SilentlyContinue
                if ($user -and $user.PasswordRequired -eq $false) {
                    Write-Output $user.Name
                }
            }
        """)
        if admins:
            for admin_name in admins.strip().split("\n"):
                admin_name = admin_name.strip()
                if admin_name:
                    _add("ACC-002", f"Admin sin contraseña requerida: {admin_name}",
                         f"La cuenta de administrador '{admin_name}' no tiene contraseña obligatoria.",
                         "critical", "accounts",
                         f"Set-LocalUser -Name {admin_name} -PasswordNeverExpires $false")
    except Exception:
        pass

    # ── 6. RDP habilitado ──
    try:
        rdp = _ps("(Get-ItemProperty 'HKLM:\\System\\CurrentControlSet\\Control\\Terminal Server').fDenyTSConnections")
        if rdp == "0":
            # RDP está habilitado, verificar NLA
            nla = _ps("(Get-ItemProperty 'HKLM:\\System\\CurrentControlSet\\Control\\Terminal Server\\WinStations\\RDP-Tcp').UserAuthentication")
            if nla == "0":
                _add("RDP-001", "RDP habilitado sin NLA",
                     "El escritorio remoto está activo sin autenticación a nivel de red (NLA), haciéndolo vulnerable a ataques de fuerza bruta.",
                     "high", "remote_access",
                     "Habilitar NLA en Propiedades del sistema > Acceso remoto")
            else:
                _add("RDP-002", "RDP habilitado",
                     "El escritorio remoto está habilitado. Verificar que solo usuarios autorizados tengan acceso.",
                     "low", "remote_access",
                     "Revisar usuarios en el grupo 'Remote Desktop Users'")
    except Exception:
        pass

    # ── 7. UAC deshabilitado ──
    try:
        uac = _ps("(Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\System').EnableLUA")
        if uac == "0":
            _add("UAC-001", "UAC deshabilitado",
                 "El Control de Cuentas de Usuario (UAC) está desactivado. Cualquier programa puede ejecutarse con privilegios elevados sin confirmación.",
                 "high", "system",
                 "Habilitar UAC desde Panel de control > Cuentas de usuario")
    except Exception:
        pass

    # ── 8. AutoRun / AutoPlay habilitado ──
    try:
        autorun = _ps("(Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\Explorer' -ErrorAction SilentlyContinue).NoDriveTypeAutoRun")
        if not autorun or autorun == "0":
            _add("AR-001", "AutoRun/AutoPlay habilitado",
                 "La ejecución automática de medios extraíbles está activa, lo que puede permitir ejecución de malware por USB.",
                 "medium", "system",
                 "GPO: Desactivar reproducción automática en todos los drives")
    except Exception:
        pass

    # ── 9. Actualizaciones pendientes (Windows Update) ──
    try:
        pending = _ps("""
            $s = New-Object -ComObject Microsoft.Update.Session
            $searcher = $s.CreateUpdateSearcher()
            $results = $searcher.Search('IsInstalled=0')
            $results.Updates.Count
        """)
        if pending and pending.isdigit() and int(pending) > 0:
            count = int(pending)
            sev = "low"
            if count > 20:
                sev = "high"
            elif count > 10:
                sev = "medium"
            _add("UPD-001", f"{count} actualizaciones de Windows pendientes",
                 f"Hay {count} actualizaciones de Windows sin instalar. Mantener el equipo actualizado reduce riesgos de seguridad.",
                 sev, "updates",
                 "Instalar actualizaciones desde Windows Update")
    except Exception:
        pass

    # ── 10. Puertos peligrosos abiertos ──
    try:
        if psutil:
            dangerous_ports = {
                21: "FTP", 23: "Telnet", 135: "RPC", 139: "NetBIOS",
                445: "SMB", 1433: "SQL Server", 3306: "MySQL",
                5900: "VNC", 5985: "WinRM",
            }
            listening = set()
            for conn in psutil.net_connections(kind='inet'):
                if conn.status == 'LISTEN' and conn.laddr:
                    port = conn.laddr.port
                    if port in dangerous_ports:
                        listening.add(port)

            for port in sorted(listening):
                svc = dangerous_ports[port]
                sev = "medium"
                if port == 23:  # Telnet
                    sev = "high"
                elif port in (21, 5900):  # FTP, VNC
                    sev = "high"
                elif port in (1433, 3306):  # DB
                    sev = "medium"
                _add(f"PORT-{port}", f"Puerto {port} ({svc}) abierto",
                     f"El servicio {svc} está escuchando en el puerto {port}. Si no se utiliza, deshabilitarlo reduce la superficie de ataque.",
                     sev, "ports",
                     f"Detener el servicio asociado o bloquear el puerto {port} en el firewall")
    except Exception:
        pass

    # ── 11. Contraseñas que no expiran (cuentas locales) ──
    try:
        no_expire = _ps("Get-LocalUser | Where-Object { $_.Enabled -and $_.PasswordNeverExpires } | Select-Object Name | ConvertTo-Json")
        if no_expire:
            users = json.loads(no_expire)
            if not isinstance(users, list):
                users = [users]
            active_users = [u.get("Name", "") for u in users if u.get("Name")]
            if len(active_users) > 0:
                names = ", ".join(active_users[:5])
                extra = f" (+{len(active_users)-5} más)" if len(active_users) > 5 else ""
                _add("ACC-003", f"Contraseñas que nunca expiran: {names}{extra}",
                     f"Hay {len(active_users)} cuenta(s) con contraseñas que nunca expiran: {names}{extra}.",
                     "medium", "accounts",
                     "Establecer política de expiración de contraseñas vía GPO o Set-LocalUser")
    except Exception:
        pass

    # ── 12. BitLocker no habilitado ──
    try:
        bl = _ps("(Get-BitLockerVolume -MountPoint C: -ErrorAction SilentlyContinue).ProtectionStatus")
        if bl == "Off" or bl == "0":
            _add("ENC-001", "BitLocker no habilitado en C:",
                 "La unidad del sistema no está cifrada con BitLocker. En caso de robo o pérdida, los datos quedan expuestos.",
                 "medium", "encryption",
                 "Habilitar BitLocker desde Panel de control > Cifrado de unidad BitLocker")
    except Exception:
        pass

    # ── 13. Carpetas compartidas expuestas ──
    try:
        shares = _ps("Get-SmbShare | Where-Object { $_.Name -notmatch '\\$$' -and $_.Name -ne 'print$' -and $_.Name -ne 'IPC$' } | Select-Object Name,Path | ConvertTo-Json")
        if shares:
            share_list = json.loads(shares)
            if not isinstance(share_list, list):
                share_list = [share_list]
            user_shares = [s for s in share_list if s.get("Name")]
            if user_shares:
                names = ", ".join(s["Name"] for s in user_shares[:3])
                _add("SHARE-001", f"Carpetas compartidas: {names}",
                     f"Se encontraron {len(user_shares)} carpeta(s) compartida(s). Verificar permisos de acceso.",
                     "low", "shares",
                     "Revisar permisos en: Administración del equipo > Carpetas compartidas")
    except Exception:
        pass

    # ── 14. Política de contraseñas débil ──
    try:
        min_len = _ps("(net accounts | Select-String 'Longitud').ToString() -replace '.*:\\s*',''")
        if min_len and min_len.isdigit() and int(min_len) < 8:
            _add("POL-001", f"Longitud mínima de contraseña: {min_len} caracteres",
                 f"La política de contraseñas permite contraseñas de solo {min_len} caracteres. Se recomienda mínimo 8.",
                 "medium", "policy",
                 "net accounts /minpwlen:8")
    except Exception:
        pass

    # ── 15. Inicio automático (AutoLogon) ──
    try:
        autologon = _ps("(Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon' -ErrorAction SilentlyContinue).AutoAdminLogon")
        if autologon == "1":
            _add("AUTH-001", "Inicio de sesión automático habilitado",
                 "El equipo inicia sesión automáticamente sin pedir contraseña, exponiendo el acceso físico.",
                 "high", "authentication",
                 "Desactivar AutoAdminLogon en el registro de Windows")
    except Exception:
        pass

    # ── 16. Windows obsoleto (build antiguo) ──
    try:
        build = _ps("(Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion').CurrentBuild")
        if build and build.isdigit():
            build_num = int(build)
            # Windows 10 1507 = 10240, 1607 = 14393, 1809 = 17763, 21H2 = 19044
            # Windows 11 21H2 = 22000
            if build_num < 17763:  # Anterior a 1809
                _add("OS-001", f"Windows con build obsoleto ({build})",
                     "Esta versión de Windows ya no recibe actualizaciones de seguridad. Actualizar al build más reciente.",
                     "critical", "system",
                     "Actualizar Windows a la versión más reciente")
            elif build_num < 19044:  # Anterior a 21H2
                _add("OS-002", f"Windows con build desactualizado ({build})",
                     "Esta versión de Windows puede estar próxima a fin de soporte.",
                     "medium", "system",
                     "Actualizar Windows a la versión más reciente")
    except Exception:
        pass

    return vulns


def collect_full_inventory():
    """Recopila toda la información y la retorna como diccionario."""
    inventory = {
        "agent_version": "1.2.0",
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
        "peripherals": get_peripherals(),
        "vulnerabilities": get_vulnerabilities(),
    }

    return inventory


if __name__ == "__main__":
    data = collect_full_inventory()
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
