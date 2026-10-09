"""
PC Inventory Server
Servidor Flask que recibe reportes de los agentes y muestra un dashboard.
"""

import os
import json
import sqlite3
import hashlib
from datetime import datetime
from functools import wraps

from flask import (
    Flask, request, jsonify, render_template, g, redirect, url_for, flash
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "inventory-secret-change-me")

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inventory.db")
API_KEY = os.environ.get("INVENTORY_API_KEY", "change-me-on-first-run")


# ========== Database ==========
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS machines (
            id TEXT PRIMARY KEY,
            hostname TEXT NOT NULL,
            fqdn TEXT,
            os_edition TEXT,
            os_version TEXT,
            os_build TEXT,
            os_architecture TEXT,
            manufacturer TEXT,
            model TEXT,
            serial_number TEXT,
            bios_version TEXT,
            system_uuid TEXT,
            chassis_type TEXT,
            cpu_name TEXT,
            cpu_cores_physical INTEGER,
            cpu_cores_logical INTEGER,
            cpu_max_speed_mhz INTEGER,
            ram_total_gb REAL,
            ram_usage_percent REAL,
            cpu_usage_percent REAL,
            gpu_info TEXT,
            logged_user TEXT,
            agent_version TEXT,
            is_admin INTEGER DEFAULT 0,
            first_seen TEXT,
            last_seen TEXT,
            is_online INTEGER DEFAULT 1,
            raw_data TEXT
        );

        CREATE TABLE IF NOT EXISTS disks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine_id TEXT NOT NULL,
            disk_type TEXT DEFAULT 'physical',
            model TEXT,
            serial TEXT,
            size_gb REAL,
            interface TEXT,
            media_type TEXT,
            mountpoint TEXT,
            fstype TEXT,
            total_gb REAL,
            used_gb REAL,
            free_gb REAL,
            usage_percent REAL,
            FOREIGN KEY (machine_id) REFERENCES machines(id)
        );

        CREATE TABLE IF NOT EXISTS ram_modules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine_id TEXT NOT NULL,
            manufacturer TEXT,
            part_number TEXT,
            speed_mhz INTEGER,
            capacity_gb REAL,
            slot TEXT,
            FOREIGN KEY (machine_id) REFERENCES machines(id)
        );

        CREATE TABLE IF NOT EXISTS network_adapters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine_id TEXT NOT NULL,
            name TEXT,
            is_up INTEGER,
            speed_mbps INTEGER,
            ipv4 TEXT,
            ipv6 TEXT,
            mac TEXT,
            FOREIGN KEY (machine_id) REFERENCES machines(id)
        );

        CREATE TABLE IF NOT EXISTS software (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine_id TEXT NOT NULL,
            name TEXT,
            version TEXT,
            publisher TEXT,
            install_date TEXT,
            FOREIGN KEY (machine_id) REFERENCES machines(id)
        );

        CREATE TABLE IF NOT EXISTS report_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine_id TEXT NOT NULL,
            reported_at TEXT,
            agent_version TEXT,
            success INTEGER DEFAULT 1
        );

        CREATE INDEX IF NOT EXISTS idx_machines_hostname
            ON machines(hostname);
        CREATE INDEX IF NOT EXISTS idx_software_machine
            ON software(machine_id);
        CREATE INDEX IF NOT EXISTS idx_disks_machine
            ON disks(machine_id);
    """)
    db.close()


# ========== Auth ==========
def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get("X-API-Key", "")
        if key != API_KEY:
            return jsonify({"error": "API key inválida"}), 401
        return f(*args, **kwargs)
    return decorated


# ========== Helpers ==========
def generate_machine_id(data):
    """Genera un ID único basado en UUID del sistema o MAC + hostname."""
    system_uuid = data.get("hardware", {}).get("system_uuid", "")
    hostname = data.get("system", {}).get("hostname", "")

    if system_uuid and system_uuid not in ("", "None", "To be filled"):
        seed = system_uuid
    else:
        seed = f"{data.get('agent_id', '')}-{hostname}"

    return hashlib.sha256(seed.encode()).hexdigest()[:16]


# ========== API ==========
@app.route("/api/report", methods=["POST"])
@require_api_key
def api_report():
    """Recibe un reporte de inventario del agente."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No se recibieron datos"}), 400

    db = get_db()
    machine_id = generate_machine_id(data)
    now = datetime.now().isoformat()

    sys_info = data.get("system", {})
    hw = data.get("hardware", {})
    cpu = data.get("cpu", {})
    mem = data.get("memory", {})

    # GPU como JSON string
    gpu_info = json.dumps(data.get("gpu", []), ensure_ascii=False)

    # Upsert máquina
    existing = db.execute(
        "SELECT id FROM machines WHERE id = ?", (machine_id,)
    ).fetchone()

    if existing:
        db.execute("""
            UPDATE machines SET
                hostname=?, fqdn=?, os_edition=?, os_version=?, os_build=?,
                os_architecture=?, manufacturer=?, model=?, serial_number=?,
                bios_version=?, system_uuid=?, chassis_type=?,
                cpu_name=?, cpu_cores_physical=?, cpu_cores_logical=?,
                cpu_max_speed_mhz=?, ram_total_gb=?, ram_usage_percent=?,
                cpu_usage_percent=?, gpu_info=?, logged_user=?,
                agent_version=?, is_admin=?, last_seen=?, is_online=1,
                raw_data=?
            WHERE id=?
        """, (
            sys_info.get("hostname"), sys_info.get("fqdn"),
            sys_info.get("os_edition"), sys_info.get("os_version"),
            sys_info.get("os_build"), sys_info.get("os_architecture"),
            hw.get("manufacturer"), hw.get("model"),
            hw.get("serial_number"), hw.get("bios_version"),
            hw.get("system_uuid"), hw.get("chassis_type"),
            cpu.get("name"), cpu.get("cores_physical"),
            cpu.get("cores_logical"), cpu.get("max_speed_mhz"),
            mem.get("total_gb"), mem.get("usage_percent"),
            cpu.get("usage_percent"), gpu_info,
            data.get("logged_user"), data.get("agent_version"),
            1 if data.get("is_admin") else 0, now,
            json.dumps(data, ensure_ascii=False, default=str),
            machine_id
        ))
    else:
        db.execute("""
            INSERT INTO machines (
                id, hostname, fqdn, os_edition, os_version, os_build,
                os_architecture, manufacturer, model, serial_number,
                bios_version, system_uuid, chassis_type,
                cpu_name, cpu_cores_physical, cpu_cores_logical,
                cpu_max_speed_mhz, ram_total_gb, ram_usage_percent,
                cpu_usage_percent, gpu_info, logged_user,
                agent_version, is_admin, first_seen, last_seen,
                is_online, raw_data
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)
        """, (
            machine_id,
            sys_info.get("hostname"), sys_info.get("fqdn"),
            sys_info.get("os_edition"), sys_info.get("os_version"),
            sys_info.get("os_build"), sys_info.get("os_architecture"),
            hw.get("manufacturer"), hw.get("model"),
            hw.get("serial_number"), hw.get("bios_version"),
            hw.get("system_uuid"), hw.get("chassis_type"),
            cpu.get("name"), cpu.get("cores_physical"),
            cpu.get("cores_logical"), cpu.get("max_speed_mhz"),
            mem.get("total_gb"), mem.get("usage_percent"),
            cpu.get("usage_percent"), gpu_info,
            data.get("logged_user"), data.get("agent_version"),
            1 if data.get("is_admin") else 0, now, now,
            json.dumps(data, ensure_ascii=False, default=str),
        ))

    # Actualizar discos
    db.execute("DELETE FROM disks WHERE machine_id = ?", (machine_id,))
    disks = data.get("disks", {})
    for d in disks.get("physical_drives", []):
        db.execute("""
            INSERT INTO disks (machine_id, disk_type, model, serial,
                size_gb, interface, media_type)
            VALUES (?, 'physical', ?, ?, ?, ?, ?)
        """, (
            machine_id, d.get("model"), d.get("serial"),
            d.get("size_gb"), d.get("interface"), d.get("media_type")
        ))
    for p in disks.get("partitions", []):
        db.execute("""
            INSERT INTO disks (machine_id, disk_type, mountpoint, fstype,
                total_gb, used_gb, free_gb, usage_percent)
            VALUES (?, 'partition', ?, ?, ?, ?, ?, ?)
        """, (
            machine_id, p.get("mountpoint"), p.get("fstype"),
            p.get("total_gb"), p.get("used_gb"), p.get("free_gb"),
            p.get("usage_percent")
        ))

    # Actualizar módulos RAM
    db.execute("DELETE FROM ram_modules WHERE machine_id = ?", (machine_id,))
    for mod in mem.get("modules", []):
        db.execute("""
            INSERT INTO ram_modules (machine_id, manufacturer, part_number,
                speed_mhz, capacity_gb, slot)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            machine_id, mod.get("manufacturer"), mod.get("part_number"),
            mod.get("speed_mhz"), mod.get("capacity_gb"), mod.get("slot")
        ))

    # Actualizar red
    db.execute(
        "DELETE FROM network_adapters WHERE machine_id = ?", (machine_id,)
    )
    for adapter in data.get("network", []):
        db.execute("""
            INSERT INTO network_adapters (machine_id, name, is_up,
                speed_mbps, ipv4, ipv6, mac)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            machine_id, adapter.get("name"),
            1 if adapter.get("is_up") else 0,
            adapter.get("speed_mbps"), adapter.get("ipv4"),
            adapter.get("ipv6"), adapter.get("mac")
        ))

    # Actualizar software
    db.execute("DELETE FROM software WHERE machine_id = ?", (machine_id,))
    for sw in data.get("software", []):
        db.execute("""
            INSERT INTO software (machine_id, name, version, publisher,
                install_date)
            VALUES (?, ?, ?, ?, ?)
        """, (
            machine_id, sw.get("name"), sw.get("version"),
            sw.get("publisher"), sw.get("install_date")
        ))

    # Log
    db.execute("""
        INSERT INTO report_log (machine_id, reported_at, agent_version)
        VALUES (?, ?, ?)
    """, (machine_id, now, data.get("agent_version")))

    db.commit()

    return jsonify({
        "status": "ok",
        "machine_id": machine_id,
        "hostname": sys_info.get("hostname"),
        "received_at": now,
    })


@app.route("/api/machines", methods=["GET"])
def api_machines():
    """Lista todas las máquinas registradas."""
    db = get_db()
    rows = db.execute("""
        SELECT id, hostname, manufacturer, model, serial_number,
               os_edition, cpu_name, ram_total_gb, logged_user,
               last_seen, is_online, chassis_type
        FROM machines ORDER BY hostname
    """).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/machines/<machine_id>", methods=["GET"])
def api_machine_detail(machine_id):
    """Detalle completo de una máquina."""
    db = get_db()
    machine = db.execute(
        "SELECT * FROM machines WHERE id = ?", (machine_id,)
    ).fetchone()
    if not machine:
        return jsonify({"error": "Máquina no encontrada"}), 404

    result = dict(machine)

    # Agregar relaciones
    result["disks"] = [
        dict(r) for r in db.execute(
            "SELECT * FROM disks WHERE machine_id = ?", (machine_id,)
        ).fetchall()
    ]
    result["ram_modules"] = [
        dict(r) for r in db.execute(
            "SELECT * FROM ram_modules WHERE machine_id = ?", (machine_id,)
        ).fetchall()
    ]
    result["network_adapters"] = [
        dict(r) for r in db.execute(
            "SELECT * FROM network_adapters WHERE machine_id = ?",
            (machine_id,)
        ).fetchall()
    ]
    result["software"] = [
        dict(r) for r in db.execute(
            "SELECT * FROM software WHERE machine_id = ? ORDER BY name",
            (machine_id,)
        ).fetchall()
    ]

    # Parsear GPU
    try:
        result["gpu"] = json.loads(result.get("gpu_info", "[]"))
    except Exception:
        result["gpu"] = []

    # Raw data no se envía en la API (muy pesado)
    result.pop("raw_data", None)

    return jsonify(result)


@app.route("/api/machines/<machine_id>", methods=["DELETE"])
def api_delete_machine(machine_id):
    """Elimina una máquina y todos sus datos."""
    db = get_db()
    db.execute("DELETE FROM software WHERE machine_id = ?", (machine_id,))
    db.execute("DELETE FROM disks WHERE machine_id = ?", (machine_id,))
    db.execute("DELETE FROM ram_modules WHERE machine_id = ?", (machine_id,))
    db.execute(
        "DELETE FROM network_adapters WHERE machine_id = ?", (machine_id,)
    )
    db.execute("DELETE FROM report_log WHERE machine_id = ?", (machine_id,))
    db.execute("DELETE FROM machines WHERE id = ?", (machine_id,))
    db.commit()
    return jsonify({"status": "deleted", "machine_id": machine_id})


@app.route("/api/stats", methods=["GET"])
def api_stats():
    """Estadísticas generales del inventario."""
    db = get_db()
    total = db.execute("SELECT COUNT(*) FROM machines").fetchone()[0]
    online = db.execute(
        "SELECT COUNT(*) FROM machines WHERE is_online = 1"
    ).fetchone()[0]
    total_sw = db.execute(
        "SELECT COUNT(DISTINCT name) FROM software"
    ).fetchone()[0]
    total_ram = db.execute(
        "SELECT COALESCE(SUM(ram_total_gb), 0) FROM machines"
    ).fetchone()[0]

    # Por fabricante
    by_manufacturer = [
        dict(r) for r in db.execute("""
            SELECT COALESCE(manufacturer, 'Desconocido') as name,
                   COUNT(*) as count
            FROM machines GROUP BY manufacturer ORDER BY count DESC
        """).fetchall()
    ]

    # Por OS
    by_os = [
        dict(r) for r in db.execute("""
            SELECT COALESCE(os_edition, 'Desconocido') as name,
                   COUNT(*) as count
            FROM machines GROUP BY os_edition ORDER BY count DESC
        """).fetchall()
    ]

    return jsonify({
        "total_machines": total,
        "online_machines": online,
        "total_unique_software": total_sw,
        "total_ram_gb": round(total_ram, 2),
        "by_manufacturer": by_manufacturer,
        "by_os": by_os,
    })


# ========== Dashboard ==========
@app.route("/")
def dashboard():
    return render_template("dashboard.html")


@app.route("/machine/<machine_id>")
def machine_detail_page(machine_id):
    return render_template("dashboard.html", machine_id=machine_id)


# ========== Init ==========
with app.app_context():
    init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
