# SCIC — Sistema de Captura y preservacion de evidencia digital para trabajo con equipo de colaboradores -ingestigadores- (versión con servidor)

Herramienta de **preservación de evidencia digital de fuentes abiertas** para prácticas de OSINT/SOCMINT. Combina una extensión de Chrome/Edge que captura páginas web con metadatos técnicos y hashes, y un servidor Flask que centraliza los casos, recalcula la integridad, sella con **OpenTimestamps** (blockchain de Bitcoin) y genera el informe PDF del caso.

Proyecto con fines exclusivamente educativos. ¿Necesitás trabajar sin infraestructura? Mirá la versión **[SCIC-local](https://github.com/gonzalocatanzariti/SCIC-local)**.

## Componentes

| Carpeta | Qué es |
|---|---|
| `extension/` | Extensión Chrome MV3 "Captura con metadatos" v2.5 |
| `server/` | Servidor Flask: casos, evidencias, hash, OTS, informe PDF |

### Extensión (v2.5)
- Tres modos de captura: **visible**, **área** y **página completa**.
- Por cada captura crea en Descargas `SCIC_<sitio>_<modo>_<fecha>/` con `captura.jpeg`, `captura.pdf`, `metadatos.txt` y `SHA256.txt`.
- Metadatos: hora de fuente externa (con fallback al reloj local), IP pública del investigador, IP del sitio, URL, MD5, SHA-1 y SHA-256, en UTC y en la zona elegida.
- Dos modos de trabajo: **Solo local** o **Reportar al servidor** (sube la captura al caso asociado a la API Key).

### Servidor
- Flask + SQLite. Login local o contra AD/LDAP (opcional).
- **Casos** con API Key de ingesta propia.
- `/api/upload` valida la API Key (`X-API-Key`), **recalcula el SHA-256 del lado del servidor** y guarda la evidencia.
- Anotación de cada evidencia con editor enriquecido, reordenamiento e **informe PDF del caso**.
- **OpenTimestamps**: sellar, verificar, actualizar (upgrade) y descargar recibo. Calendarios: Alice, Bob y Finney.

## Instalación rápida

### 1. Servidor (Linux, Python 3.10+)
```bash
cd server
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json
nano config.json        # usuario/clave de admin, host y puerto
mkdir -p evidence reports
python app.py           # http://<IP>:5000
```

> ⚠️ **Cambiá `admin_password` antes de usarlo.** Dejá `"debug": false` fuera de un entorno de pruebas. Si vas a exponerlo fuera de la LAN, ponelo detrás de un reverse proxy con HTTPS.

Para dejarlo como servicio, un `systemd` unit mínimo:
```ini
[Unit]
Description=SCIC - Servidor de recoleccion de evidencias
After=network-online.target

[Service]
User=ubuntu
WorkingDirectory=/opt/scic
ExecStart=/opt/scic/venv/bin/python app.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

### 2. Extensión
1. Abrí `chrome://extensions` (o `edge://extensions`) y activá **Modo de desarrollador**.
2. **Cargar descomprimida** → elegí la carpeta `extension/`.
3. En el popup: modo **Reportar al servidor**, URL `http://<IP>:5000` (sin barra final) y la **API Key** del caso.

## Flujo de trabajo
1. Crear caso en el dashboard → copiar su API Key.
2. Configurar la extensión con URL + API Key.
3. Capturar (visible / área / completa).
4. Revisar y anotar en el dashboard.
5. Sellar con OpenTimestamps; verificar más tarde (la confirmación en Bitcoin tarda unas horas).
6. Generar el informe PDF del caso.

## Configuración (`server/config.json`)

| Clave | Descripción |
|---|---|
| `listen_host` / `listen_port` | Interfaz y puerto (por defecto `0.0.0.0:5000`) |
| `admin_username` / `admin_password` | Cuenta local inicial |
| `ad_enabled`, `ad_server`, `ad_domain`, `ad_base_dn` | Login contra Active Directory (opcional) |
| `timezone_offset` | Offset horario para informes (Argentina: `-3`) |
| `dashboard_origins` | Orígenes CORS permitidos para el dashboard |
| `network_reports_*` | Copia opcional de informes a un recurso SMB |
| `debug` | Modo debug de Flask. **Siempre `false` en uso real** |

## Consideraciones
- La herramienta **preserva** lo que se ve públicamente; no evade controles de acceso. Usala dentro del marco legal y con cuentas de investigación, nunca personales.
- El sello OpenTimestamps prueba que el hash existía en una fecha; no prueba por sí solo la autenticidad del contenido capturado.
- Proyecto educativo: revisalo y endurecelo antes de usarlo en un entorno productivo.

## Librerías de terceros incluidas
[crypto-js](https://github.com/brix/crypto-js) (MIT), [pdf-lib](https://github.com/Hopding/pdf-lib) (MIT), [Quill](https://github.com/slab/quill) (BSD-3), [SortableJS](https://github.com/SortableJS/Sortable) (MIT), [Tailwind CSS](https://github.com/tailwindlabs/tailwindcss) (MIT).

## Licencia
[GPL-3.0](LICENSE) © 2026 Gonzalo Catanzariti
