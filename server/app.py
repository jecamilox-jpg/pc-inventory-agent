"""
PC Inventory Server
Servidor Flask que recibe reportes de los agentes y muestra un dashboard.
Incluye autenticación, rate limiting y headers de seguridad.
"""

import os
import sys
import json
import sqlite3
import hashlib
import secrets
import time
import logging
from datetime import datetime, timedelta
from functools import wraps
from collections import defaultdict

from flask import (
    Flask, request, jsonify, render_template, g, redirect, url_for,
    flash, session, abort
)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.permanent_session_lifetime = timedelta(hours=8)

# ---------- Configuración ----------
_data_dir = os.environ.get("DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(_data_dir, "inventory.db")
API_KEY = os.environ.get("INVENTORY_API_KEY", "change-me-on-first-run")
DASHBOARD_PORT = int(os.environ.get("PORT", 8086))
ALLOWED_NETWORKS = os.environ.get("ALLOWED_NETWORKS", "").split(",")  # ej: "192.168.1.0/24,10.0.0.0/8"

# ---------- Logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(_data_dir, "server.log"), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("InventoryServer")

# ---------- Rate Limiting (in-memory) ----------
_rate_limits = defaultdict(list)  # ip -> [timestamps]
RATE_LIMIT_WINDOW = 60   # segundos
RATE_LIMIT_MAX_API = 30  # máx requests API por ventana
RATE_LIMIT_MAX_LOGIN = 5  # máx intentos login por ventana


# ========== Rate Limiting ==========
def _check_rate_limit(key, max_requests):
    """Retorna True si el request está dentro del límite."""
    now = time.time()
    # Limpiar entradas viejas
    _rate_limits[key] = [t for t in _rate_limits[key] if now - t < RATE_LIMIT_WINDOW]
    if len(_rate_limits[key]) >= max_requests:
        return False
    _rate_limits[key].append(now)
    return True


# ========== Security Headers ==========
@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    # CSP: solo permite recursos del mismo origen + Google Fonts
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com https://cdn.jsdelivr.net; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'"
    )
    return response


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

        CREATE TABLE IF NOT EXISTS wol_commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_machine_id TEXT NOT NULL,
            target_mac TEXT NOT NULL,
            target_hostname TEXT,
            requested_at TEXT,
            requested_by TEXT DEFAULT 'dashboard',
            status TEXT DEFAULT 'pending',
            picked_by_machine TEXT,
            picked_at TEXT,
            result TEXT,
            completed_at TEXT,
            FOREIGN KEY (target_machine_id) REFERENCES machines(id)
        );

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT,
            role TEXT DEFAULT 'admin',
            is_active INTEGER DEFAULT 1,
            created_at TEXT,
            last_login TEXT
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            user TEXT,
            ip TEXT,
            action TEXT NOT NULL,
            detail TEXT
        );

        CREATE TABLE IF NOT EXISTS login_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip TEXT NOT NULL,
            username TEXT,
            success INTEGER DEFAULT 0,
            attempted_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS peripherals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine_id TEXT NOT NULL,
            peripheral_type TEXT NOT NULL,
            name TEXT,
            manufacturer TEXT,
            serial TEXT,
            connection_type TEXT,
            detail TEXT,
            FOREIGN KEY (machine_id) REFERENCES machines(id)
        );

        CREATE TABLE IF NOT EXISTS vulnerabilities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine_id TEXT NOT NULL,
            vuln_id TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            severity TEXT NOT NULL,
            category TEXT,
            remediation TEXT,
            detected_at TEXT,
            FOREIGN KEY (machine_id) REFERENCES machines(id)
        );

        CREATE INDEX IF NOT EXISTS idx_vulns_machine
            ON vulnerabilities(machine_id);
        CREATE INDEX IF NOT EXISTS idx_vulns_severity
            ON vulnerabilities(severity);
        CREATE INDEX IF NOT EXISTS idx_peripherals_machine
            ON peripherals(machine_id);
        CREATE INDEX IF NOT EXISTS idx_peripherals_type
            ON peripherals(peripheral_type);
        CREATE INDEX IF NOT EXISTS idx_machines_hostname
            ON machines(hostname);
        CREATE INDEX IF NOT EXISTS idx_software_machine
            ON software(machine_id);
        CREATE INDEX IF NOT EXISTS idx_disks_machine
            ON disks(machine_id);
        CREATE INDEX IF NOT EXISTS idx_wol_status
            ON wol_commands(status);
        CREATE INDEX IF NOT EXISTS idx_audit_timestamp
            ON audit_log(timestamp);
        CREATE INDEX IF NOT EXISTS idx_login_attempts_ip
            ON login_attempts(ip, attempted_at);
    """)

    # Crear usuario admin por defecto si no existe ninguno
    existing = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if existing == 0:
        default_user = os.environ.get("ADMIN_USER", "admin")
        default_pass = os.environ.get("ADMIN_PASSWORD", "")
        if not default_pass:
            default_pass = secrets.token_urlsafe(12)
            logger.info("=" * 60)
            logger.info("  PRIMER INICIO - CREDENCIALES DE ACCESO")
            logger.info(f"  Usuario: {default_user}")
            logger.info(f"  Contraseña: {default_pass}")
            logger.info("  ¡CAMBIA ESTA CONTRASEÑA DESPUÉS DEL PRIMER LOGIN!")
            logger.info("=" * 60)

        db.execute(
            "INSERT INTO users (username, password_hash, display_name, role, created_at) "
            "VALUES (?, ?, ?, 'superadmin', ?)",
            (default_user, generate_password_hash(default_pass), "Administrador",
             datetime.now().isoformat())
        )
        db.commit()

    db.close()


# ========== Audit Log ==========
def audit_log(action, detail="", user=None):
    """Registra una acción en el log de auditoría."""
    try:
        db = get_db()
        db.execute(
            "INSERT INTO audit_log (timestamp, user, ip, action, detail) VALUES (?, ?, ?, ?, ?)",
            (datetime.now().isoformat(), user or session.get("username", "system"),
             request.remote_addr, action, detail[:500])
        )
        db.commit()
    except Exception:
        pass  # No fallar por auditoría


# ========== Auth: API Key (agentes) ==========
def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get("X-API-Key", "")
        if not key or key != API_KEY:
            logger.warning(f"API key inválida desde {request.remote_addr}")
            return jsonify({"error": "API key inválida"}), 401
        # Rate limit para API
        if not _check_rate_limit(f"api:{request.remote_addr}", RATE_LIMIT_MAX_API):
            return jsonify({"error": "Rate limit excedido"}), 429
        return f(*args, **kwargs)
    return decorated


# ========== Auth: Dashboard (sesión) ==========
def require_login(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"error": "No autenticado"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def _is_ip_blocked(ip):
    """Verifica si una IP está bloqueada por muchos intentos fallidos."""
    try:
        db_conn = sqlite3.connect(DB_PATH)
        count = db_conn.execute(
            "SELECT COUNT(*) FROM login_attempts WHERE ip = ? AND success = 0 "
            "AND attempted_at > datetime('now', '-15 minutes')",
            (ip,)
        ).fetchone()[0]
        db_conn.close()
        return count >= 10  # Bloquear después de 10 intentos fallidos en 15 min
    except Exception:
        return False


# ========== Login / Logout ==========
@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("dashboard"))

    error = None
    if request.method == "POST":
        ip = request.remote_addr
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        # Rate limit login
        if not _check_rate_limit(f"login:{ip}", RATE_LIMIT_MAX_LOGIN):
            error = "Demasiados intentos. Espera 1 minuto."
            logger.warning(f"Login rate limit excedido: {ip}")
            return render_template("login.html", error=error), 429

        # IP bloqueada
        if _is_ip_blocked(ip):
            error = "IP bloqueada temporalmente por múltiples intentos fallidos."
            logger.warning(f"IP bloqueada: {ip}")
            return render_template("login.html", error=error), 403

        db = get_db()
        user = db.execute(
            "SELECT * FROM users WHERE username = ? AND is_active = 1",
            (username,)
        ).fetchone()

        if user and check_password_hash(user["password_hash"], password):
            session.permanent = True
            session["logged_in"] = True
            session["username"] = user["username"]
            session["display_name"] = user["display_name"] or user["username"]
            session["role"] = user["role"]
            session["user_id"] = user["id"]

            # Actualizar último login
            db.execute(
                "UPDATE users SET last_login = ? WHERE id = ?",
                (datetime.now().isoformat(), user["id"])
            )
            # Registrar intento exitoso
            db.execute(
                "INSERT INTO login_attempts (ip, username, success, attempted_at) VALUES (?, ?, 1, ?)",
                (ip, username, datetime.now().isoformat())
            )
            db.commit()

            audit_log("login", f"Login exitoso: {username}")
            logger.info(f"Login exitoso: {username} desde {ip}")
            return redirect(url_for("dashboard"))
        else:
            # Registrar intento fallido
            db.execute(
                "INSERT INTO login_attempts (ip, username, success, attempted_at) VALUES (?, ?, 0, ?)",
                (ip, username, datetime.now().isoformat())
            )
            db.commit()
            error = "Usuario o contraseña incorrectos."
            logger.warning(f"Login fallido: '{username}' desde {ip}")

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    username = session.get("username", "?")
    audit_log("logout", f"Logout: {username}")
    session.clear()
    return redirect(url_for("login"))


@app.route("/change-password", methods=["POST"])
@require_login
def change_password():
    current = request.form.get("current_password", "")
    new_pass = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")

    if not new_pass or len(new_pass) < 8:
        flash("La contraseña debe tener al menos 8 caracteres.", "error")
        return redirect(url_for("settings_page"))

    if new_pass != confirm:
        flash("Las contraseñas no coinciden.", "error")
        return redirect(url_for("settings_page"))

    db = get_db()
    user = db.execute(
        "SELECT * FROM users WHERE id = ?", (session["user_id"],)
    ).fetchone()

    if not check_password_hash(user["password_hash"], current):
        flash("Contraseña actual incorrecta.", "error")
        return redirect(url_for("settings_page"))

    db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (generate_password_hash(new_pass), session["user_id"])
    )
    db.commit()
    audit_log("change_password", "Contraseña cambiada")
    flash("Contraseña actualizada exitosamente.", "success")
    return redirect(url_for("settings_page"))


@app.route("/settings")
@require_login
def settings_page():
    return render_template("settings.html",
                           username=session.get("username"),
                           display_name=session.get("display_name"))


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

    # Actualizar periféricos
    db.execute("DELETE FROM peripherals WHERE machine_id = ?", (machine_id,))
    periph = data.get("peripherals", {})

    for mon in periph.get("monitors", []):
        db.execute("""
            INSERT INTO peripherals (machine_id, peripheral_type, name,
                manufacturer, serial, connection_type, detail)
            VALUES (?, 'monitor', ?, ?, ?, 'Video', ?)
        """, (machine_id, mon.get("name"), mon.get("manufacturer"),
              mon.get("serial"), mon.get("resolution", "")))

    for prn in periph.get("printers", []):
        conn = "Red" if prn.get("is_network") else "Local"
        detail_parts = []
        if prn.get("driver"):
            detail_parts.append(f"Driver: {prn['driver']}")
        if prn.get("port"):
            detail_parts.append(f"Puerto: {prn['port']}")
        if prn.get("is_default"):
            detail_parts.append("Predeterminada")
        db.execute("""
            INSERT INTO peripherals (machine_id, peripheral_type, name,
                manufacturer, serial, connection_type, detail)
            VALUES (?, 'printer', ?, ?, ?, ?, ?)
        """, (machine_id, prn.get("name"), "", "", conn,
              "; ".join(detail_parts)))

    for kbd in periph.get("keyboards", []):
        db.execute("""
            INSERT INTO peripherals (machine_id, peripheral_type, name,
                manufacturer, serial, connection_type, detail)
            VALUES (?, 'keyboard', ?, ?, ?, ?, ?)
        """, (machine_id, kbd.get("name"), "", "",
              kbd.get("type", ""), kbd.get("layout", "")))

    for mouse in periph.get("mice", []):
        detail_parts = []
        if mouse.get("pointing_type"):
            detail_parts.append(mouse["pointing_type"])
        if mouse.get("buttons"):
            detail_parts.append(f"{mouse['buttons']} botones")
        db.execute("""
            INSERT INTO peripherals (machine_id, peripheral_type, name,
                manufacturer, serial, connection_type, detail)
            VALUES (?, 'mouse', ?, ?, ?, ?, ?)
        """, (machine_id, mouse.get("name"), mouse.get("manufacturer", ""),
              "", mouse.get("type", ""), "; ".join(detail_parts)))

    for usb in periph.get("usb_devices", []):
        db.execute("""
            INSERT INTO peripherals (machine_id, peripheral_type, name,
                manufacturer, serial, connection_type, detail)
            VALUES (?, 'usb', ?, ?, ?, 'USB', ?)
        """, (machine_id, usb.get("name"), usb.get("manufacturer", ""),
              "", usb.get("class", "")))

    # Actualizar vulnerabilidades
    db.execute("DELETE FROM vulnerabilities WHERE machine_id = ?", (machine_id,))
    for vuln in data.get("vulnerabilities", []):
        db.execute("""
            INSERT INTO vulnerabilities (machine_id, vuln_id, title,
                description, severity, category, remediation, detected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            machine_id, vuln.get("vuln_id"), vuln.get("title"),
            vuln.get("description"), vuln.get("severity"),
            vuln.get("category"), vuln.get("remediation"), now
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
@require_login
def api_machines():
    """Lista todas las máquinas registradas."""
    db = get_db()
    # is_online se calcula dinámicamente: offline si last_seen > 10 minutos
    rows = db.execute("""
        SELECT id, hostname, manufacturer, model, serial_number,
               os_edition, cpu_name, ram_total_gb, logged_user,
               last_seen,
               CASE WHEN last_seen >= datetime('now', '-10 minutes')
                    THEN 1 ELSE 0 END as is_online,
               chassis_type
        FROM machines ORDER BY hostname
    """).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/machines/<machine_id>", methods=["GET"])
@require_login
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
    result["peripherals"] = [
        dict(r) for r in db.execute(
            "SELECT * FROM peripherals WHERE machine_id = ? ORDER BY peripheral_type",
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
@require_login
def api_delete_machine(machine_id):
    """Elimina una máquina y todos sus datos."""
    db = get_db()
    db.execute("DELETE FROM software WHERE machine_id = ?", (machine_id,))
    db.execute("DELETE FROM disks WHERE machine_id = ?", (machine_id,))
    db.execute("DELETE FROM ram_modules WHERE machine_id = ?", (machine_id,))
    db.execute(
        "DELETE FROM network_adapters WHERE machine_id = ?", (machine_id,)
    )
    db.execute("DELETE FROM peripherals WHERE machine_id = ?", (machine_id,))
    db.execute("DELETE FROM vulnerabilities WHERE machine_id = ?", (machine_id,))
    db.execute("DELETE FROM report_log WHERE machine_id = ?", (machine_id,))
    hostname = db.execute("SELECT hostname FROM machines WHERE id = ?", (machine_id,)).fetchone()
    db.execute("DELETE FROM machines WHERE id = ?", (machine_id,))
    db.commit()
    audit_log("delete_machine", f"Eliminado: {hostname['hostname'] if hostname else machine_id}")
    return jsonify({"status": "deleted", "machine_id": machine_id})


@app.route("/api/vulnerabilities", methods=["GET"])
@require_login
def api_vulnerabilities():
    """Lista todas las vulnerabilidades detectadas."""
    db = get_db()
    rows = db.execute("""
        SELECT v.id, v.machine_id, m.hostname, v.vuln_id, v.title,
               v.description, v.severity, v.category, v.remediation,
               v.detected_at
        FROM vulnerabilities v
        JOIN machines m ON v.machine_id = m.id
        ORDER BY
            CASE v.severity
                WHEN 'critical' THEN 1
                WHEN 'high' THEN 2
                WHEN 'medium' THEN 3
                WHEN 'low' THEN 4
            END,
            v.detected_at DESC
    """).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/vulnerabilities/summary", methods=["GET"])
@require_login
def api_vulnerabilities_summary():
    """Resumen de vulnerabilidades por severidad."""
    db = get_db()
    counts = db.execute("""
        SELECT severity, COUNT(*) as count
        FROM vulnerabilities GROUP BY severity
    """).fetchall()
    summary = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for row in counts:
        summary[row["severity"]] = row["count"]

    total_machines = db.execute("SELECT COUNT(*) FROM machines").fetchone()[0]
    machines_with_vulns = db.execute("""
        SELECT COUNT(DISTINCT machine_id) FROM vulnerabilities
    """).fetchone()[0]
    machines_clean = total_machines - machines_with_vulns

    # Top vulnerabilidades más frecuentes
    top_vulns = db.execute("""
        SELECT vuln_id, title, severity, COUNT(*) as affected_count
        FROM vulnerabilities
        GROUP BY vuln_id
        ORDER BY
            CASE severity
                WHEN 'critical' THEN 1
                WHEN 'high' THEN 2
                WHEN 'medium' THEN 3
                WHEN 'low' THEN 4
            END,
            affected_count DESC
        LIMIT 10
    """).fetchall()

    return jsonify({
        "counts": summary,
        "total": sum(summary.values()),
        "machines_scanned": total_machines,
        "machines_with_vulns": machines_with_vulns,
        "machines_clean": machines_clean,
        "top_vulns": [dict(r) for r in top_vulns],
    })


@app.route("/api/stats", methods=["GET"])
@require_login
def api_stats():
    """Estadísticas generales del inventario."""
    db = get_db()
    total = db.execute("SELECT COUNT(*) FROM machines").fetchone()[0]
    online = db.execute(
        "SELECT COUNT(*) FROM machines WHERE last_seen >= datetime('now', '-10 minutes')"
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


# ========== Wake-on-LAN ==========
@app.route("/api/wol/<machine_id>", methods=["POST"])
@require_login
def api_wol_request(machine_id):
    """Solicita encender un equipo via Wake-on-LAN."""
    db = get_db()
    machine = db.execute(
        "SELECT id, hostname FROM machines WHERE id = ?", (machine_id,)
    ).fetchone()
    if not machine:
        return jsonify({"error": "Máquina no encontrada"}), 404

    # Buscar MAC principal (adaptador activo con IP)
    adapter = db.execute("""
        SELECT mac FROM network_adapters
        WHERE machine_id = ? AND mac != '' AND mac IS NOT NULL
            AND ipv4 != '' AND ipv4 IS NOT NULL
        ORDER BY is_up DESC, speed_mbps DESC
        LIMIT 1
    """, (machine_id,)).fetchone()

    if not adapter or not adapter["mac"]:
        # Fallback: cualquier MAC que no sea vacía
        adapter = db.execute("""
            SELECT mac FROM network_adapters
            WHERE machine_id = ? AND mac != '' AND mac IS NOT NULL
                AND mac != '00:00:00:00:00:00'
            LIMIT 1
        """, (machine_id,)).fetchone()

    if not adapter or not adapter["mac"]:
        return jsonify({"error": "No se encontró MAC address para este equipo"}), 400

    mac = adapter["mac"]
    now = datetime.now().isoformat()

    # Verificar que no haya un comando pendiente reciente (< 2 min)
    recent = db.execute("""
        SELECT id FROM wol_commands
        WHERE target_machine_id = ? AND status = 'pending'
            AND requested_at > datetime('now', '-2 minutes')
    """, (machine_id,)).fetchone()

    if recent:
        return jsonify({
            "status": "already_pending",
            "message": "Ya hay un comando WOL pendiente para este equipo",
        })

    db.execute("""
        INSERT INTO wol_commands (target_machine_id, target_mac,
            target_hostname, requested_at, requested_by, status)
        VALUES (?, ?, ?, ?, ?, 'pending')
    """, (machine_id, mac, machine["hostname"], now,
          session.get("username", "dashboard")))
    db.commit()
    audit_log("wol_request", f"WOL solicitado: {machine['hostname']} ({mac})")

    return jsonify({
        "status": "queued",
        "machine_id": machine_id,
        "hostname": machine["hostname"],
        "target_mac": mac,
        "message": f"Comando WOL encolado. Un agente en la red local lo ejecutará.",
    })


@app.route("/api/wol/pending", methods=["GET"])
@require_api_key
def api_wol_pending():
    """Retorna comandos WOL pendientes para que un agente los ejecute."""
    db = get_db()
    commands = db.execute("""
        SELECT id, target_machine_id, target_mac, target_hostname,
               requested_at
        FROM wol_commands
        WHERE status = 'pending'
        ORDER BY requested_at ASC
        LIMIT 10
    """).fetchall()

    return jsonify([dict(c) for c in commands])


@app.route("/api/wol/<int:wol_id>/complete", methods=["POST"])
@require_api_key
def api_wol_complete(wol_id):
    """El agente reporta que ejecutó un comando WOL."""
    data = request.get_json() or {}
    db = get_db()

    now = datetime.now().isoformat()
    picked_by = data.get("picked_by", "unknown")
    result = data.get("result", "sent")

    db.execute("""
        UPDATE wol_commands
        SET status = 'completed', picked_by_machine = ?,
            picked_at = ?, result = ?, completed_at = ?
        WHERE id = ?
    """, (picked_by, now, result, now, wol_id))
    db.commit()

    return jsonify({"status": "ok", "wol_id": wol_id})


@app.route("/api/wol/history", methods=["GET"])
@require_login
def api_wol_history():
    """Historial de comandos WOL."""
    db = get_db()
    commands = db.execute("""
        SELECT id, target_machine_id, target_mac, target_hostname,
               requested_at, status, picked_by_machine, result, completed_at
        FROM wol_commands
        ORDER BY requested_at DESC
        LIMIT 50
    """).fetchall()
    return jsonify([dict(c) for c in commands])


# ========== Audit API ==========
@app.route("/api/audit", methods=["GET"])
@require_login
def api_audit():
    """Retorna las últimas acciones del log de auditoría."""
    db = get_db()
    limit = min(int(request.args.get("limit", 100)), 500)
    rows = db.execute(
        "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/users", methods=["GET"])
@require_login
def api_users():
    """Lista usuarios (solo superadmin)."""
    if session.get("role") != "superadmin":
        return jsonify({"error": "Sin permisos"}), 403
    db = get_db()
    rows = db.execute(
        "SELECT id, username, display_name, role, is_active, created_at, last_login "
        "FROM users ORDER BY username"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/users", methods=["POST"])
@require_login
def api_create_user():
    """Crear un nuevo usuario (solo superadmin)."""
    if session.get("role") != "superadmin":
        return jsonify({"error": "Sin permisos"}), 403

    data = request.get_json()
    username = data.get("username", "").strip().lower()
    password = data.get("password", "")
    display_name = data.get("display_name", username)

    if not username or not password:
        return jsonify({"error": "Username y password requeridos"}), 400
    if len(password) < 8:
        return jsonify({"error": "Contraseña mínimo 8 caracteres"}), 400

    db = get_db()
    existing = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if existing:
        return jsonify({"error": "Usuario ya existe"}), 409

    db.execute(
        "INSERT INTO users (username, password_hash, display_name, role, created_at) "
        "VALUES (?, ?, ?, 'admin', ?)",
        (username, generate_password_hash(password), display_name,
         datetime.now().isoformat())
    )
    db.commit()
    audit_log("create_user", f"Usuario creado: {username}")
    return jsonify({"status": "ok", "username": username})


@app.route("/api/users/<int:user_id>", methods=["DELETE"])
@require_login
def api_delete_user(user_id):
    """Desactivar usuario (solo superadmin)."""
    if session.get("role") != "superadmin":
        return jsonify({"error": "Sin permisos"}), 403
    if user_id == session.get("user_id"):
        return jsonify({"error": "No puedes eliminarte a ti mismo"}), 400

    db = get_db()
    db.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user_id,))
    db.commit()
    audit_log("delete_user", f"Usuario desactivado: ID {user_id}")
    return jsonify({"status": "ok"})


# ========== Software Catalog API ==========
@app.route("/api/software/catalog", methods=["GET"])
@require_login
def api_software_catalog():
    """Catálogo de software agregado por todas las máquinas."""
    db = get_db()
    search = request.args.get("search", "").strip()
    page = max(int(request.args.get("page", 1)), 1)
    per_page = min(int(request.args.get("per_page", 50)), 200)
    offset = (page - 1) * per_page

    base_where = ""
    params = []
    if search:
        base_where = "WHERE s.name LIKE ?"
        params.append(f"%{search}%")

    # Total count
    total = db.execute(
        f"SELECT COUNT(DISTINCT s.name) FROM software s {base_where}", params
    ).fetchone()[0]

    # Aggregated list
    rows = db.execute(f"""
        SELECT s.name,
               s.publisher,
               COUNT(DISTINCT s.machine_id) as machine_count,
               GROUP_CONCAT(DISTINCT s.version) as versions
        FROM software s
        {base_where}
        GROUP BY s.name
        ORDER BY machine_count DESC, s.name
        LIMIT ? OFFSET ?
    """, params + [per_page, offset]).fetchall()

    return jsonify({
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
    })


@app.route("/api/software/top", methods=["GET"])
@require_login
def api_software_top():
    """Top software más instalado."""
    db = get_db()
    limit = min(int(request.args.get("limit", 15)), 50)
    rows = db.execute("""
        SELECT name, publisher, COUNT(DISTINCT machine_id) as machine_count
        FROM software
        GROUP BY name
        ORDER BY machine_count DESC
        LIMIT ?
    """, (limit,)).fetchall()
    return jsonify([dict(r) for r in rows])


# ========== Network Overview API ==========
@app.route("/api/network/overview", methods=["GET"])
@require_login
def api_network_overview():
    """Resumen de red: adaptadores, IPs, subredes."""
    db = get_db()

    # All adapters with machine info
    adapters = db.execute("""
        SELECT n.*, m.hostname, m.is_online
        FROM network_adapters n
        JOIN machines m ON n.machine_id = m.id
        WHERE n.ipv4 IS NOT NULL AND n.ipv4 != ''
        ORDER BY m.hostname
    """).fetchall()

    # Subnet summary
    subnets = {}
    for a in adapters:
        ip = a["ipv4"]
        if ip:
            parts = ip.split(".")
            if len(parts) == 4:
                subnet = f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
                if subnet not in subnets:
                    subnets[subnet] = {"subnet": subnet, "count": 0, "machines": []}
                subnets[subnet]["count"] += 1
                subnets[subnet]["machines"].append(a["hostname"])

    return jsonify({
        "adapters": [dict(a) for a in adapters],
        "subnets": list(subnets.values()),
        "total_adapters": len(adapters),
    })


# ========== Peripherals API ==========
@app.route("/api/peripherals", methods=["GET"])
@require_login
def api_peripherals():
    """Lista periféricos, con filtro opcional por máquina o tipo."""
    db = get_db()
    machine_id = request.args.get("machine_id", "")
    ptype = request.args.get("type", "")
    search = request.args.get("search", "").strip()
    page = max(int(request.args.get("page", 1)), 1)
    per_page = min(int(request.args.get("per_page", 50)), 200)
    offset = (page - 1) * per_page

    where_parts = []
    params = []

    if machine_id:
        where_parts.append("p.machine_id = ?")
        params.append(machine_id)
    if ptype:
        where_parts.append("p.peripheral_type = ?")
        params.append(ptype)
    if search:
        where_parts.append("(p.name LIKE ? OR p.manufacturer LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    total = db.execute(
        f"SELECT COUNT(*) FROM peripherals p {where_clause}", params
    ).fetchone()[0]

    rows = db.execute(f"""
        SELECT p.*, m.hostname
        FROM peripherals p
        JOIN machines m ON p.machine_id = m.id
        {where_clause}
        ORDER BY p.peripheral_type, m.hostname
        LIMIT ? OFFSET ?
    """, params + [per_page, offset]).fetchall()

    return jsonify({
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
    })


@app.route("/api/peripherals/summary", methods=["GET"])
@require_login
def api_peripherals_summary():
    """Resumen de periféricos por tipo y estadísticas."""
    db = get_db()

    # Conteo por tipo
    by_type = db.execute("""
        SELECT peripheral_type as type, COUNT(*) as count
        FROM peripherals
        GROUP BY peripheral_type
        ORDER BY count DESC
    """).fetchall()

    # Top fabricantes de monitores
    top_monitor_mfr = db.execute("""
        SELECT COALESCE(NULLIF(manufacturer, ''), 'Desconocido') as name,
               COUNT(*) as count
        FROM peripherals
        WHERE peripheral_type = 'monitor' AND manufacturer != ''
        GROUP BY manufacturer
        ORDER BY count DESC
        LIMIT 10
    """).fetchall()

    # Top impresoras
    top_printers = db.execute("""
        SELECT name, COUNT(*) as count
        FROM peripherals
        WHERE peripheral_type = 'printer'
        GROUP BY name
        ORDER BY count DESC
        LIMIT 10
    """).fetchall()

    # Tipos de conexión (teclados + mouse)
    conn_types = db.execute("""
        SELECT connection_type as type, COUNT(*) as count
        FROM peripherals
        WHERE peripheral_type IN ('keyboard', 'mouse')
          AND connection_type != ''
        GROUP BY connection_type
        ORDER BY count DESC
    """).fetchall()

    # Total
    total = db.execute("SELECT COUNT(*) FROM peripherals").fetchone()[0]
    total_machines_with = db.execute(
        "SELECT COUNT(DISTINCT machine_id) FROM peripherals"
    ).fetchone()[0]

    return jsonify({
        "total": total,
        "total_machines": total_machines_with,
        "by_type": [dict(r) for r in by_type],
        "top_monitor_manufacturers": [dict(r) for r in top_monitor_mfr],
        "top_printers": [dict(r) for r in top_printers],
        "connection_types": [dict(r) for r in conn_types],
    })


# ========== Alerts API ==========
@app.route("/api/alerts", methods=["GET"])
@require_login
def api_alerts():
    """Alertas del sistema: disco bajo, equipos offline, reportes viejos."""
    db = get_db()
    alerts = []

    # Equipos offline (no reportan hace > 10 minutos)
    offline = db.execute("""
        SELECT id, hostname, last_seen
        FROM machines
        WHERE last_seen < datetime('now', '-10 minutes')
           OR last_seen IS NULL
        ORDER BY last_seen ASC
    """).fetchall()
    for m in offline:
        alerts.append({
            "type": "offline",
            "severity": "warning",
            "icon": "bi-wifi-off",
            "title": f"{m['hostname']} sin conexión",
            "detail": f"Último reporte: {m['last_seen'] or 'Nunca'}",
            "machine_id": m["id"],
        })

    # Discos con uso > 90%
    low_disk = db.execute("""
        SELECT d.machine_id, d.mountpoint, d.usage_percent,
               d.total_gb, d.free_gb, m.hostname
        FROM disks d
        JOIN machines m ON d.machine_id = m.id
        WHERE d.disk_type = 'partition'
          AND d.usage_percent > 90
        ORDER BY d.usage_percent DESC
    """).fetchall()
    for d in low_disk:
        alerts.append({
            "type": "disk",
            "severity": "danger" if d["usage_percent"] > 95 else "warning",
            "icon": "bi-hdd",
            "title": f"{d['hostname']} — disco {d['mountpoint']} al {d['usage_percent']:.0f}%",
            "detail": f"{d['free_gb']:.1f} GB libres de {d['total_gb']:.1f} GB",
            "machine_id": d["machine_id"],
        })

    # RAM alta (> 90%)
    high_ram = db.execute("""
        SELECT id, hostname, ram_usage_percent, ram_total_gb
        FROM machines
        WHERE ram_usage_percent > 90
        ORDER BY ram_usage_percent DESC
    """).fetchall()
    for m in high_ram:
        alerts.append({
            "type": "ram",
            "severity": "warning",
            "icon": "bi-memory",
            "title": f"{m['hostname']} — RAM al {m['ram_usage_percent']:.0f}%",
            "detail": f"Total: {m['ram_total_gb']:.1f} GB",
            "machine_id": m["id"],
        })

    # CPU alta (> 90%)
    high_cpu = db.execute("""
        SELECT id, hostname, cpu_usage_percent
        FROM machines
        WHERE cpu_usage_percent > 90
        ORDER BY cpu_usage_percent DESC
    """).fetchall()
    for m in high_cpu:
        alerts.append({
            "type": "cpu",
            "severity": "warning",
            "icon": "bi-cpu",
            "title": f"{m['hostname']} — CPU al {m['cpu_usage_percent']:.0f}%",
            "detail": "Uso elevado del procesador",
            "machine_id": m["id"],
        })

    # Sort: danger first, then warning
    severity_order = {"danger": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: severity_order.get(a["severity"], 9))

    return jsonify({
        "alerts": alerts,
        "total": len(alerts),
        "critical": sum(1 for a in alerts if a["severity"] == "danger"),
        "warnings": sum(1 for a in alerts if a["severity"] == "warning"),
    })


# ========== Chart Data API ==========
@app.route("/api/stats/charts", methods=["GET"])
@require_login
def api_stats_charts():
    """Datos formateados para gráficos Chart.js."""
    db = get_db()

    # OS Distribution
    os_dist = db.execute("""
        SELECT COALESCE(os_edition, 'Desconocido') as label, COUNT(*) as value
        FROM machines GROUP BY os_edition ORDER BY value DESC
    """).fetchall()

    # Online vs Offline (dinámico: 10 min sin reportar = offline)
    online = db.execute(
        "SELECT COUNT(*) FROM machines WHERE last_seen >= datetime('now', '-10 minutes')"
    ).fetchone()[0]
    offline = db.execute(
        "SELECT COUNT(*) FROM machines WHERE last_seen < datetime('now', '-10 minutes') OR last_seen IS NULL"
    ).fetchone()[0]

    # Manufacturer distribution
    mfr_dist = db.execute("""
        SELECT COALESCE(manufacturer, 'Desconocido') as label, COUNT(*) as value
        FROM machines GROUP BY manufacturer ORDER BY value DESC LIMIT 10
    """).fetchall()

    # Chassis type distribution
    chassis_dist = db.execute("""
        SELECT COALESCE(chassis_type, 'Desconocido') as label, COUNT(*) as value
        FROM machines GROUP BY chassis_type ORDER BY value DESC
    """).fetchall()

    # RAM distribution (ranges)
    ram_ranges = db.execute("""
        SELECT
            CASE
                WHEN ram_total_gb <= 4 THEN '0-4 GB'
                WHEN ram_total_gb <= 8 THEN '4-8 GB'
                WHEN ram_total_gb <= 16 THEN '8-16 GB'
                WHEN ram_total_gb <= 32 THEN '16-32 GB'
                ELSE '32+ GB'
            END as label,
            COUNT(*) as value
        FROM machines
        GROUP BY label
        ORDER BY MIN(ram_total_gb)
    """).fetchall()

    # Disk usage average per machine
    disk_usage = db.execute("""
        SELECT m.hostname as label,
               COALESCE(AVG(d.usage_percent), 0) as value
        FROM machines m
        LEFT JOIN disks d ON d.machine_id = m.id AND d.disk_type = 'partition'
        GROUP BY m.id
        ORDER BY value DESC
        LIMIT 15
    """).fetchall()

    # CPU usage per machine
    cpu_usage = db.execute("""
        SELECT hostname as label, COALESCE(cpu_usage_percent, 0) as value
        FROM machines
        ORDER BY cpu_usage_percent DESC
        LIMIT 15
    """).fetchall()

    # Reports per day (last 30 days)
    reports_timeline = db.execute("""
        SELECT DATE(reported_at) as label, COUNT(*) as value
        FROM report_log
        WHERE reported_at > datetime('now', '-30 days')
        GROUP BY DATE(reported_at)
        ORDER BY label
    """).fetchall()

    return jsonify({
        "os_distribution": [dict(r) for r in os_dist],
        "online_offline": {"online": online, "offline": offline},
        "manufacturer_distribution": [dict(r) for r in mfr_dist],
        "chassis_distribution": [dict(r) for r in chassis_dist],
        "ram_distribution": [dict(r) for r in ram_ranges],
        "disk_usage": [dict(r) for r in disk_usage],
        "cpu_usage": [dict(r) for r in cpu_usage],
        "reports_timeline": [dict(r) for r in reports_timeline],
    })


# ========== Enhanced Machine Detail API ==========
@app.route("/api/machines/<machine_id>/history", methods=["GET"])
@require_login
def api_machine_history(machine_id):
    """Historial de reportes de una máquina."""
    db = get_db()
    rows = db.execute("""
        SELECT reported_at, agent_version, success
        FROM report_log
        WHERE machine_id = ?
        ORDER BY reported_at DESC
        LIMIT 50
    """, (machine_id,)).fetchall()
    return jsonify([dict(r) for r in rows])


# ========== Users Management API (enhanced) ==========
@app.route("/api/users/<int:user_id>/toggle", methods=["POST"])
@require_login
def api_toggle_user(user_id):
    """Activar/desactivar usuario (solo superadmin)."""
    if session.get("role") != "superadmin":
        return jsonify({"error": "Sin permisos"}), 403
    if user_id == session.get("user_id"):
        return jsonify({"error": "No puedes desactivarte a ti mismo"}), 400

    db = get_db()
    user = db.execute("SELECT is_active, username FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        return jsonify({"error": "Usuario no encontrado"}), 404

    new_status = 0 if user["is_active"] else 1
    db.execute("UPDATE users SET is_active = ? WHERE id = ?", (new_status, user_id))
    db.commit()
    action = "activado" if new_status else "desactivado"
    audit_log("toggle_user", f"Usuario {action}: {user['username']}")
    return jsonify({"status": "ok", "is_active": new_status})


# ========== Dashboard ==========
@app.route("/")
@require_login
def dashboard():
    return render_template("dashboard.html",
                           username=session.get("display_name", "Admin"),
                           role=session.get("role", "admin"))


@app.route("/machine/<machine_id>")
@require_login
def machine_detail_page(machine_id):
    return render_template("dashboard.html", machine_id=machine_id,
                           username=session.get("display_name", "Admin"),
                           role=session.get("role", "admin"))


# ========== Init ==========
with app.app_context():
    init_db()


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"

    print("=" * 60)
    print("  PC Inventory Server - ITDeskManager")
    print(f"  Puerto: {DASHBOARD_PORT}")
    print(f"  Base de datos: {DB_PATH}")
    print(f"  Debug: {debug}")
    print(f"  URL: http://0.0.0.0:{DASHBOARD_PORT}")
    print("=" * 60)

    app.run(host="0.0.0.0", port=DASHBOARD_PORT, debug=debug)
