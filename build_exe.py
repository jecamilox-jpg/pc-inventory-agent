"""
Build script para generar el ejecutable .exe del agente.
Requiere PyInstaller: pip install pyinstaller

Uso:
    python build_exe.py

Genera: dist/PCInventoryAgent.exe (standalone, no requiere Python instalado)
"""

import subprocess
import sys
import os


def main():
    # Verificar PyInstaller
    try:
        import PyInstaller
    except ImportError:
        print("[!] Instalando PyInstaller...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "pyinstaller"]
        )

    agent_script = os.path.join("agent", "inventory_agent.py")

    if not os.path.exists(agent_script):
        print(f"[ERROR] No se encontró {agent_script}")
        sys.exit(1)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", "PCInventoryAgent",
        "--icon", "NONE",
        "--add-data", f"agent/config.py{os.pathsep}.",
        "--add-data", f"agent/collector.py{os.pathsep}.",
        "--hidden-import", "psutil",
        "--hidden-import", "requests",
        "--hidden-import", "wmi",
        "--hidden-import", "win32com",
        "--hidden-import", "win32api",
        "--clean",
        "--noconfirm",
        agent_script,
    ]

    print("=" * 50)
    print("  Generando PCInventoryAgent.exe")
    print("=" * 50)
    print()

    result = subprocess.run(cmd)

    if result.returncode == 0:
        exe_path = os.path.join("dist", "PCInventoryAgent.exe")
        if os.path.exists(exe_path):
            size_mb = os.path.getsize(exe_path) / (1024 * 1024)
            print()
            print("=" * 50)
            print(f"  [OK] Ejecutable generado: {exe_path}")
            print(f"  Tamaño: {size_mb:.1f} MB")
            print("=" * 50)
            print()
            print("Uso:")
            print(f"  {exe_path} --once          # Enviar una vez")
            print(f"  {exe_path} --print         # Ver JSON")
            print(f"  {exe_path} --init-config   # Crear config.json")
        else:
            print("[ERROR] El exe no se generó correctamente.")
    else:
        print("[ERROR] Falló la compilación.")
        sys.exit(1)


if __name__ == "__main__":
    main()
