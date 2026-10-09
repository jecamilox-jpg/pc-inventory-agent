# 🖥️ PC Inventory Agent

Sistema de inventario de PCs con arquitectura **agente-servidor**. El agente recopila información completa del hardware y software de cada equipo Windows y la envía periódicamente a un servidor Flask central.

## Arquitectura

```
┌─────────────┐     HTTP/JSON     ┌─────────────────┐
│  PC Agent   │ ───────────────►  │  Flask Server    │
│  (Windows)  │   POST /api/report│  + Dashboard     │
│  psutil/WMI │                   │  SQLite          │
└─────────────┘                   └─────────────────┘
      ×N equipos                    1 servidor central
```

## Qué recopila el agente

| Categoría | Datos |
|-----------|-------|
| **Sistema** | Hostname, FQDN, OS edición/build, arquitectura, boot time |
| **Hardware** | Fabricante, modelo, serial, BIOS, UUID, tipo chasis |
| **CPU** | Nombre, núcleos, velocidad, uso % |
| **RAM** | Total, usado, módulos (fabricante, velocidad, slot) |
| **Discos** | Físicos (modelo, serial, tamaño, interfaz) + particiones (uso) |
| **Red** | Adaptadores, IPs, MAC, velocidad, estado |
| **GPU** | Nombre, VRAM, driver, resolución |
| **Software** | Lista completa de programas instalados |
| **Updates** | Últimas 20 actualizaciones de Windows |

## Instalación rápida

### 1. Servidor

```bash
cd server
pip install -r requirements.txt
python app.py
```

El servidor arranca en `http://localhost:5050`.

Variables de entorno opcionales:
- `PORT` — Puerto (default: 5050)
- `INVENTORY_API_KEY` — API key para autenticar agentes
- `SECRET_KEY` — Clave secreta de Flask
- `FLASK_DEBUG` — "1" para modo debug

### 2. Agente (en cada PC Windows)

```bash
cd agent
pip install -r requirements.txt

# Generar config
python inventory_agent.py --init-config

# Editar config.json con URL del servidor y API key
notepad config.json

# Probar
python inventory_agent.py --print   # Ver inventario en consola
python inventory_agent.py --once    # Enviar una vez

# Modo daemon (loop continuo)
python inventory_agent.py --daemon

# Instalar como tarea programada de Windows (como Admin)
python inventory_agent.py --install
```

### Configuración del agente (`config.json`)

```json
{
  "server_url": "http://tu-servidor:5050",
  "api_key": "change-me-on-first-run",
  "interval_seconds": 300,
  "send_software": true,
  "send_updates": true,
  "log_file": "agent.log",
  "verify_ssl": true
}
```

También se puede configurar con variables de entorno:
- `INVENTORY_SERVER_URL`
- `INVENTORY_API_KEY`
- `INVENTORY_INTERVAL`

## API REST

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| POST | `/api/report` | Recibir reporte de agente (requiere API key) |
| GET | `/api/machines` | Listar todas las máquinas |
| GET | `/api/machines/<id>` | Detalle de una máquina |
| DELETE | `/api/machines/<id>` | Eliminar máquina |
| GET | `/api/stats` | Estadísticas generales |

## Stack

- **Agente**: Python 3.8+ / psutil / WMI / requests
- **Servidor**: Flask / SQLite / Jinja2
- **Dashboard**: Vanilla JS, CSS Grid, dark theme

## Licencia

MIT
