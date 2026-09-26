from flask import Flask, render_template, request, jsonify, send_file, send_from_directory, session, redirect, url_for
from flask_cors import CORS
from datetime import datetime, timedelta, timezone as dt_timezone
import json
import os
import base64
import hashlib
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, PageBreak, Table, TableStyle, KeepTogether
from reportlab.lib.units import inch, mm
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY, TA_RIGHT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from PIL import Image as PILImage
import secrets
from functools import wraps
import sqlite3
from contextlib import contextmanager
import re
from html.parser import HTMLParser
import html as html_lib
import shutil
import subprocess
import socket
from urllib.parse import urlparse
import ots_service
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(
    __name__,
    template_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates'),
    static_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def load_secret_key():
    secret_file = os.path.join(BASE_DIR, '.flask_secret')
    if os.path.exists(secret_file):
        with open(secret_file, 'r', encoding='utf-8') as handle:
            key = handle.read().strip()
            if key:
                return key
    key = secrets.token_hex(32)
    with open(secret_file, 'w', encoding='utf-8') as handle:
        handle.write(key)
    return key

@app.after_request
def allow_extension_cors(response):
    origin = request.headers.get('Origin', '')
    if origin.startswith('chrome-extension://'):
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Accept, X-API-Key'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    return response

app.secret_key = load_secret_key()
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

def load_runtime_config():
    defaults = {
        'ad_enabled': False,
        'ad_server': '',
        'ad_domain': '',
        'ad_base_dn': '',
        'network_reports_server': '',
        'network_reports_share': '',
        'network_reports_subfolder': '',
        'timezone_offset': -3,
        'dashboard_origins': [],
        'listen_host': '0.0.0.0',
        'listen_port': 5000,
        'admin_username': 'admin',
        'admin_password': 'admin123'
    }
    config_path = os.path.join(BASE_DIR, 'config.json')
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as handle:
                defaults.update(json.load(handle))
        except Exception as exc:
            print(f'No se pudo leer config.json: {exc}')
    else:
        with open(config_path, 'w', encoding='utf-8') as handle:
            json.dump(defaults, handle, indent=2)
    return defaults

RUNTIME = load_runtime_config()

cors_origins = ['chrome-extension://*']
for origin in RUNTIME.get('dashboard_origins') or []:
    origin = str(origin).strip().rstrip('/')
    if origin and origin not in cors_origins:
        cors_origins.append(origin)

CORS(app,
     supports_credentials=True,
     origins=cors_origins,
     allow_headers=["Content-Type", "Accept", "X-API-Key"],
     methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"]
)

AD_ENABLED = bool(RUNTIME.get('ad_enabled', False))
AD_SERVER = RUNTIME.get('ad_server', '') or ''
AD_DOMAIN = RUNTIME.get('ad_domain', '') or ''
AD_BASE_DN = RUNTIME.get('ad_base_dn', '') or ''

UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
EVIDENCE_FOLDER = os.path.join(BASE_DIR, 'evidence')
REPORTS_FOLDER = os.path.join(BASE_DIR, 'reports')
DB_FILE = os.path.join(BASE_DIR, 'evidence_system.db')

NETWORK_REPORTS_SERVER = RUNTIME.get('network_reports_server', '') or ''
NETWORK_REPORTS_SHARE = RUNTIME.get('network_reports_share', '') or ''
NETWORK_REPORTS_SUBFOLDER = RUNTIME.get('network_reports_subfolder', '') or ''

for folder in [UPLOAD_FOLDER, EVIDENCE_FOLDER, REPORTS_FOLDER]:
    os.makedirs(folder, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['EVIDENCE_FOLDER'] = EVIDENCE_FOLDER
app.config['REPORTS_FOLDER'] = REPORTS_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

# Zona horaria por defecto (se puede cambiar por caso en el dashboard)
TIMEZONE_OFFSET = int(RUNTIME.get('timezone_offset', -3))

FONT_CANDIDATES = {
    'Arial': (r'C:\Windows\Fonts\arial.ttf', r'C:\Windows\Fonts\arialbd.ttf'),
    'Calibri': (r'C:\Windows\Fonts\calibri.ttf', r'C:\Windows\Fonts\calibrib.ttf'),
    'Times New Roman': (r'C:\Windows\Fonts\times.ttf', r'C:\Windows\Fonts\timesbd.ttf'),
    'Georgia': (r'C:\Windows\Fonts\georgia.ttf', r'C:\Windows\Fonts\georgiab.ttf'),
    'Verdana': (r'C:\Windows\Fonts\verdana.ttf', r'C:\Windows\Fonts\verdanab.ttf'),
}
QUILL_FONT_TO_FAMILY = {
    'arial': 'Arial',
    'sans-serif': 'Arial',
    'calibri': 'Calibri',
    'times-new-roman': 'Times New Roman',
    'serif': 'Times New Roman',
    'georgia': 'Georgia',
    'verdana': 'Verdana',
}
REGISTERED_FONTS = {}

def _font_id(family):
    return 'Report' + re.sub(r'[^A-Za-z0-9]', '', family)

def register_report_fonts():
    if REGISTERED_FONTS:
        return REGISTERED_FONTS
    for family, (regular, bold) in FONT_CANDIDATES.items():
        if not os.path.exists(regular):
            continue
        family_id = _font_id(family)
        pdfmetrics.registerFont(TTFont(family_id, regular))
        bold_id = family_id
        if os.path.exists(bold):
            bold_id = family_id + 'Bold'
            pdfmetrics.registerFont(TTFont(bold_id, bold))
        try:
            pdfmetrics.registerFontFamily(family_id, normal=family_id, bold=bold_id, italic=family_id, boldItalic=bold_id)
        except Exception:
            pass
        REGISTERED_FONTS[family] = {'family': family_id, 'regular': family_id, 'bold': bold_id}
    if not REGISTERED_FONTS:
        REGISTERED_FONTS['Helvetica'] = {'family': 'Helvetica', 'regular': 'Helvetica', 'bold': 'Helvetica-Bold'}
    return REGISTERED_FONTS

def resolve_font_pair(family):
    fonts = register_report_fonts()
    if family in fonts:
        return fonts[family]
    key = (family or '').lower().replace(' ', '-')
    mapped = QUILL_FONT_TO_FAMILY.get(key)
    if mapped and mapped in fonts:
        return fonts[mapped]
    return next(iter(fonts.values()))

def pdf_plain(value):
    return html_lib.escape(str(value or ''), quote=True)

def _xor_bytes(data, key, nonce):
    return bytes(data[i] ^ key[i % len(key)] ^ nonce[i % len(nonce)] for i in range(len(data)))

def encrypt_session_secret(plain):
    if not plain:
        return None
    key = hashlib.sha256(app.secret_key.encode('utf-8')).digest()
    nonce = os.urandom(16)
    packed = nonce + _xor_bytes(plain.encode('utf-8'), key, nonce)
    return base64.urlsafe_b64encode(packed).decode('ascii')

def decrypt_session_secret(token):
    if not token:
        return None
    packed = base64.urlsafe_b64decode(token.encode('ascii'))
    nonce, data = packed[:16], packed[16:]
    key = hashlib.sha256(app.secret_key.encode('utf-8')).digest()
    return _xor_bytes(data, key, nonce).decode('utf-8')

# ==================== BASE DE DATOS ====================

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

USERNAME_RE = re.compile(r'^[A-Za-z0-9._-]{3,64}$')

def hash_password(password):
    return generate_password_hash(str(password or ''))

def verify_local_password(password_hash, password):
    if not password_hash or not password:
        return False
    try:
        return check_password_hash(password_hash, password)
    except Exception:
        return False

def provision_user_api_key(c, username):
    api_key = secrets.token_hex(16)
    now = get_local_time().isoformat()
    c.execute('''
        INSERT INTO api_keys (api_key, username, name, created_at, is_active)
        VALUES (?, ?, ?, ?, 1)
    ''', (api_key, username, 'API Key Principal', now))
    c.execute('''
        INSERT INTO api_key_users (api_key, username, created_at)
        VALUES (?, ?, ?)
    ''', (api_key, username, now))
    return api_key

def ensure_local_admin(c, conn):
    admin_name = str(RUNTIME.get('admin_username') or 'admin').strip() or 'admin'
    initial_password = str(RUNTIME.get('admin_password') or 'admin123')
    now = get_local_time().isoformat()
    c.execute('SELECT username, password_hash, is_admin FROM users WHERE username = ?', (admin_name,))
    row = c.fetchone()
    if not row:
        c.execute('''
            INSERT INTO users (username, ad_user, is_admin, password_hash, created_at, last_login)
            VALUES (?, 0, 1, ?, ?, ?)
        ''', (admin_name, hash_password(initial_password), now, now))
        api_key = provision_user_api_key(c, admin_name)
        conn.commit()
        print(f'Usuario administrador "{admin_name}" creado en SQLite.')
        print('Cambie admin_password en config.json despues del primer ingreso.')
        print(f'API Key inicial de {admin_name}: {api_key}')
        return
    updates = []
    params = []
    if not row['is_admin']:
        updates.append('is_admin = 1')
    if not row['password_hash']:
        updates.append('password_hash = ?')
        params.append(hash_password(initial_password))
    if updates:
        params.append(admin_name)
        c.execute(f"UPDATE users SET {', '.join(updates)} WHERE username = ?", params)
        conn.commit()

def init_db():
    with get_db() as conn:
        c = conn.cursor()
        
        # Tabla de usuarios
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                ad_user BOOLEAN DEFAULT 0,
                is_admin INTEGER DEFAULT 0,
                password_hash TEXT,
                created_at TEXT,
                last_login TEXT
            )
        ''')
        
        # Tabla de API Keys
        c.execute('''
            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                api_key TEXT UNIQUE NOT NULL,
                username TEXT NOT NULL,
                name TEXT,
                created_at TEXT,
                last_used TEXT,
                is_active BOOLEAN DEFAULT 1,
                FOREIGN KEY (username) REFERENCES users(username)
            )
        ''')
        
        # Tabla de usuarios con acceso a cada API key (para compartir)
        c.execute('''
            CREATE TABLE IF NOT EXISTS api_key_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                api_key TEXT NOT NULL,
                username TEXT NOT NULL,
                created_at TEXT,
                UNIQUE(api_key, username),
                FOREIGN KEY (api_key) REFERENCES api_keys(api_key),
                FOREIGN KEY (username) REFERENCES users(username)
            )
        ''')
        
        # Tabla de sesiones
        c.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                title TEXT,
                created_at TEXT,
                last_updated TEXT,
                is_archived BOOLEAN DEFAULT 0,
                metadata TEXT,
                FOREIGN KEY (username) REFERENCES users(username)
            )
        ''')
        
        # Tabla de evidencias
        c.execute('''
            CREATE TABLE IF NOT EXISTS evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                timestamp TEXT,
                url TEXT,
                user_ip TEXT,
                server_ip TEXT,
                md5 TEXT,
                sha1 TEXT,
                capture_mode TEXT,
                filename TEXT,
                annotation TEXT,
                evidence_order INTEGER,
                created_at TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            )
        ''')
        
        # Tabla de reportes generados
        c.execute('''
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                filename TEXT,
                title TEXT,
                created_at TEXT,
                created_by TEXT,
                num_evidences INTEGER,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            )
        ''')
        
        conn.commit()

        for column, definition in (
            ('is_admin', 'INTEGER DEFAULT 0'),
            ('password_hash', 'TEXT'),
        ):
            try:
                c.execute(f'ALTER TABLE users ADD COLUMN {column} {definition}')
                conn.commit()
            except sqlite3.OperationalError:
                pass

        ensure_local_admin(c, conn)

        for column, definition in (
            ('sha256', 'TEXT'),
            ('ots_filename', 'TEXT'),
            ('ots_status', 'TEXT'),
            ('download_folder', 'TEXT'),
            ('ots_stamped_at', 'TEXT'),
            ('ots_verified_at', 'TEXT'),
            ('time_source', 'TEXT'),
        ):
            try:
                c.execute(f'ALTER TABLE evidence ADD COLUMN {column} {definition}')
                conn.commit()
            except sqlite3.OperationalError:
                pass
        try:
            c.execute('ALTER TABLE api_keys ADD COLUMN session_id TEXT')
            conn.commit()
        except sqlite3.OperationalError:
            pass

# ==================== UTILIDADES ====================

def capture_mode_label(mode):
    if mode == 'area':
        return 'Area seleccionada'
    if mode == 'full':
        return 'Pagina completa'
    return 'Area visible'

def looks_like_ip(value):
    text = str(value or '').strip()
    if not text or text.upper() == 'N/A':
        return False
    if re.match(r'^(\d{1,3}\.){3}\d{1,3}$', text):
        return True
    return ':' in text and any(ch.isdigit() for ch in text)

def resolve_site_ip(url):
    try:
        host = urlparse(url).hostname
        if not host:
            return 'N/A'
        if looks_like_ip(host):
            return host
        return socket.gethostbyname(host)
    except Exception:
        return 'N/A'

def sanitize_download_folder(value):
    if not value:
        return None
    name = os.path.basename(str(value).replace('\\', '/').strip().rstrip('/'))
    if not name or name in ('.', '..'):
        return None
    return name[:180]

def copy_to_network_folder(local_filepath, filename, username, password=None):
    """Copiar archivo a carpeta de red compartida organizado por fecha usando credenciales del usuario"""
    try:
        # Obtener fecha actual para crear carpeta
        date_folder = get_local_time().strftime('%Y-%m-%d')
        
        print(f"DEBUG: Intentando copiar {filename} a carpeta de red para usuario {username}")
        
        # Crear ruta completa: \\server\share\reportes\YYYY-MM-DD\
        if os.name == 'nt':  # Windows
            network_base = f'\\\\{NETWORK_REPORTS_SERVER}\\{NETWORK_REPORTS_SHARE}\\{NETWORK_REPORTS_SUBFOLDER}'
            network_path = os.path.join(network_base, date_folder)
            # Crear carpeta si no existe
            try:
                os.makedirs(network_path, exist_ok=True)
                # Copiar archivo
                dest_path = os.path.join(network_path, filename)
                shutil.copy2(local_filepath, dest_path)
                print(f"✅ Reporte copiado a red (Windows): {dest_path}")
            except PermissionError as e:
                print(f"⚠️ Error de permisos al copiar a red (Windows): {e}")
                raise
        else:  # Linux - usar smbclient o montar con cifs
            # Usar la configuración de servidor y share
            server = NETWORK_REPORTS_SERVER
            share = NETWORK_REPORTS_SHARE
            subfolder = NETWORK_REPORTS_SUBFOLDER
            
            # La ruta completa dentro del share será: reportes/YYYY-MM-DD/
            full_path = f"{subfolder}/{date_folder}"
            
            print(f"DEBUG: Servidor={server}, Share={share}, Ruta completa={full_path}")
            
            # Verificar si smbclient está disponible
            result = subprocess.run(
                ['which', 'smbclient'],
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                print("DEBUG: Usando smbclient para copiar archivo")
                # Usar smbclient con credenciales del usuario
                # Si no hay password, intentar sin autenticación (guest)
                if password:
                    # Crear carpetas si no existen (primero reportes, luego la fecha)
                    mkdir_result1 = subprocess.run([
                        'smbclient', f'//{server}/{share}',
                        '-U', f'{username}%{password}',
                        '-c', f'mkdir {subfolder}'
                    ], capture_output=True, text=True, check=False)
                    
                    mkdir_result2 = subprocess.run([
                        'smbclient', f'//{server}/{share}',
                        '-U', f'{username}%{password}',
                        '-c', f'mkdir {full_path}'
                    ], capture_output=True, text=True, check=False)
                    
                    if mkdir_result2.returncode != 0 and 'NT_STATUS_OBJECT_NAME_COLLISION' not in mkdir_result2.stderr:
                        print(f"⚠️ Advertencia al crear carpeta: {mkdir_result2.stderr}")
                    
                    # Copiar archivo
                    put_result = subprocess.run([
                        'smbclient', f'//{server}/{share}',
                        '-U', f'{username}%{password}',
                        '-c', f'put {local_filepath} {full_path}/{filename}'
                    ], capture_output=True, text=True, check=True)
                    
                    if put_result.returncode == 0:
                        print(f"✅ Reporte copiado a red usando smbclient con credenciales")
                    else:
                        print(f"⚠️ Error al copiar: {put_result.stderr}")
                        raise Exception(f"Error al copiar archivo: {put_result.stderr}")
                else:
                    # Intentar sin password (puede funcionar si el usuario tiene sesión activa)
                    print("DEBUG: Intentando sin password (sesión activa)")
                    mkdir_result1 = subprocess.run([
                        'smbclient', f'//{server}/{share}',
                        '-U', username,
                        '-N',
                        '-c', f'mkdir {subfolder}'
                    ], capture_output=True, text=True, check=False)
                    
                    mkdir_result2 = subprocess.run([
                        'smbclient', f'//{server}/{share}',
                        '-U', username,
                        '-N',
                        '-c', f'mkdir {full_path}'
                    ], capture_output=True, text=True, check=False)
                    
                    put_result = subprocess.run([
                        'smbclient', f'//{server}/{share}',
                        '-U', username,
                        '-N',
                        '-c', f'put {local_filepath} {full_path}/{filename}'
                    ], capture_output=True, text=True, check=True)
                    
                    if put_result.returncode == 0:
                        print(f"✅ Reporte copiado a red usando smbclient sin password")
                    else:
                        print(f"⚠️ Error al copiar sin password: {put_result.stderr}")
                        raise Exception(f"Error al copiar archivo sin password: {put_result.stderr}")
            else:
                print("⚠️ smbclient no está disponible, intentando montar con cifs")
                # Si no hay smbclient, intentar montar con cifs
                mount_point = tempfile.mkdtemp()
                try:
                    mount_options = f'username={username},uid={os.getuid()},gid={os.getgid()},file_mode=0777,dir_mode=0777'
                    if password:
                        mount_options += f',password={password}'
                    else:
                        mount_options += ',guest'
                    
                    # Montar el share
                    mount_path = f'//{server}/{share}'
                    mount_result = subprocess.run([
                        'mount', '-t', 'cifs',
                        mount_path,
                        mount_point,
                        '-o', mount_options
                    ], capture_output=True, text=True, check=True)
                    
                    # Crear estructura de carpetas: reportes/YYYY-MM-DD
                    subfolder_path = os.path.join(mount_point, subfolder)
                    date_path = os.path.join(subfolder_path, date_folder)
                    os.makedirs(date_path, exist_ok=True)
                    
                    # Copiar archivo
                    dest_path = os.path.join(date_path, filename)
                    shutil.copy2(local_filepath, dest_path)
                    print(f"✅ Reporte copiado a red usando cifs mount: {dest_path}")
                    
                    # Desmontar
                    subprocess.run(['umount', mount_point], check=True, capture_output=True)
                except subprocess.CalledProcessError as e:
                    print(f"⚠️ Error al montar carpeta compartida: {e}")
                    if hasattr(e, 'stderr') and e.stderr:
                        print(f"   Detalles: {e.stderr}")
                    raise
                finally:
                    try:
                        os.rmdir(mount_point)
                    except:
                        pass
    except Exception as e:
        print(f"❌ Error en copy_to_network_folder: {e}")
        import traceback
        traceback.print_exc()
        # No lanzar excepción, solo loguear para no interrumpir el flujo principal

def get_local_time():
    utc_now = datetime.utcnow()
    local_time = utc_now + timedelta(hours=TIMEZONE_OFFSET)
    return local_time

def offset_label(hours):
    hours = int(hours)
    sign = '+' if hours >= 0 else '-'
    return f'UTC{sign}{abs(hours)}'

def session_tz_offset(metadata=None):
    try:
        if metadata is not None and metadata.get('timezone_offset') is not None:
            return int(metadata.get('timezone_offset'))
    except (TypeError, ValueError):
        pass
    return TIMEZONE_OFFSET

def parse_to_utc(dt_string):
    if dt_string is None:
        return datetime.utcnow()
    if isinstance(dt_string, datetime):
        dt = dt_string
        if dt.tzinfo:
            return dt.astimezone(dt_timezone.utc).replace(tzinfo=None)
        return dt
    raw = str(dt_string).strip()
    has_tz = raw.endswith('Z') or raw.endswith('z') or '+' in raw[10:]
    normalized = raw.replace('Z', '+00:00').replace('z', '+00:00')
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.utcnow()
    if dt.tzinfo:
        return dt.astimezone(dt_timezone.utc).replace(tzinfo=None)
    if has_tz:
        return dt
    return dt - timedelta(hours=TIMEZONE_OFFSET)

def format_dual_datetime(dt_string, offset=None):
    offset = session_tz_offset({'timezone_offset': offset}) if offset is not None else TIMEZONE_OFFSET
    utc = parse_to_utc(dt_string)
    local = utc + timedelta(hours=offset)
    return (
        f"{utc.strftime('%Y-%m-%d %H:%M:%S')} UTC  |  "
        f"{local.strftime('%Y-%m-%d %H:%M:%S')} {offset_label(offset)}"
    )

def format_now_dual(offset=None):
    now_utc = datetime.utcnow().replace(tzinfo=dt_timezone.utc).isoformat().replace('+00:00', 'Z')
    return format_dual_datetime(now_utc, offset if offset is not None else TIMEZONE_OFFSET)

def tz_offset_for_evidence(item):
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT metadata FROM sessions WHERE session_id = ?', (item.get('session_id'),))
        row = c.fetchone()
        meta = json.loads(row['metadata']) if row and row['metadata'] else {}
    return session_tz_offset(meta)

def format_datetime(dt_string, offset=None):
    return format_dual_datetime(dt_string, offset)

NAMED_COLORS = {
    'black': '#000000', 'white': '#ffffff', 'red': '#e60000', 'blue': '#0066cc',
    'green': '#008a00', 'orange': '#f90', 'purple': '#9933ff', 'yellow': '#ff0',
    'gray': '#808080', 'grey': '#808080', 'navy': '#001f3f', 'maroon': '#800000',
    'teal': '#008080', 'aqua': '#00ffff', 'lime': '#00ff00', 'fuchsia': '#ff00ff',
    'silver': '#c0c0c0', 'olive': '#808000',
}

def _rgb_to_hex(color_str):
    """Convertir color css a #rrggbb para ReportLab."""
    if not color_str:
        return None
    raw = color_str.strip().lower().replace("'", '').replace('"', '')
    if raw in ('inherit', 'currentcolor', 'transparent', 'initial'):
        return None
    if raw in NAMED_COLORS:
        raw = NAMED_COLORS[raw]
    m = re.search(r'rgba?\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)', raw)
    if m:
        return '#{:02x}{:02x}{:02x}'.format(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    hex_m = re.match(r'#?([0-9a-f]{3}|[0-9a-f]{6})$', raw)
    if hex_m:
        val = hex_m.group(1)
        if len(val) == 3:
            val = ''.join(ch * 2 for ch in val)
        return '#' + val
    return None

def _extract_style_map(attrs):
    styles = {}
    style_m = re.search(r'style=["\']([^"\']*)["\']', attrs or '', re.IGNORECASE)
    if not style_m:
        return styles
    for part in style_m.group(1).split(';'):
        if ':' not in part:
            continue
        key, val = part.split(':', 1)
        styles[key.strip().lower()] = val.strip()
    return styles

def _convert_inline_html(html, default_font=None):
    """Convertir HTML inline de Quill a tags que ReportLab entiende."""
    if not html:
        return ''

    text = html
    text = re.sub(r'<strong\b', '<b', text, flags=re.IGNORECASE)
    text = re.sub(r'</strong>', '</b>', text, flags=re.IGNORECASE)
    text = re.sub(r'<em\b', '<i', text, flags=re.IGNORECASE)
    text = re.sub(r'</em>', '</i>', text, flags=re.IGNORECASE)
    text = re.sub(r'</?s>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<br\s*/?>', '<br/>', text, flags=re.IGNORECASE)

    size_map = {'small': '9', 'large': '14', 'huge': '18'}

    def resolve_family_name(raw):
        raw = (raw or '').strip().strip('"').strip("'").split(',')[0].strip()
        key = raw.lower().replace(' ', '-')
        family = QUILL_FONT_TO_FAMILY.get(key)
        if not family and raw in FONT_CANDIDATES:
            family = raw
        if not family:
            for cand in FONT_CANDIDATES:
                if cand.lower() == raw.lower() or cand.lower().replace(' ', '') == raw.lower().replace(' ', ''):
                    family = cand
                    break
        if not family:
            return default_font
        return resolve_font_pair(family)['family']

    def convert_styled(match):
        tag = match.group(1)
        attrs = match.group(2) or ''
        content = match.group(3)
        styles = _extract_style_map(attrs)
        font_name = None
        size = None
        color = None
        class_m = re.search(r'class=["\']([^"\']*)["\']', attrs)
        if class_m:
            for cls in class_m.group(1).split():
                if cls.startswith('ql-font-'):
                    font_name = resolve_family_name(cls[8:])
                elif cls.startswith('ql-size-'):
                    size = size_map.get(cls[8:])
                elif cls.startswith('ql-color-'):
                    color = _rgb_to_hex(cls[9:].replace('ql-color-', ''))
        if styles.get('font-family'):
            font_name = resolve_family_name(styles.get('font-family'))
        if styles.get('font-size'):
            size_m = re.search(r'(\d+(?:\.\d+)?)', styles.get('font-size'))
            if size_m:
                size = str(max(8, min(22, int(float(size_m.group(1))))))
        if styles.get('color'):
            color = _rgb_to_hex(styles.get('color'))
        color_attr = re.search(r'\bcolor=["\']([^"\']+)["\']', attrs, re.IGNORECASE)
        if color_attr:
            color = _rgb_to_hex(color_attr.group(1)) or color

        keep_open = ''
        keep_close = ''
        tag_l = tag.lower()
        if tag_l in ('b', 'i', 'u'):
            keep_open = f'<{tag_l}>'
            keep_close = f'</{tag_l}>'
        wrapped = content
        font_attrs = []
        if font_name:
            font_attrs.append(f'name="{font_name}"')
        if size:
            font_attrs.append(f'size="{size}"')
        if color:
            font_attrs.append(f'color="{color}"')
        if font_attrs:
            wrapped = f'<font {" ".join(font_attrs)}>{wrapped}</font>'
        return f'{keep_open}{wrapped}{keep_close}'

    for _ in range(20):
        old = text
        text = re.sub(
            r'<(span|font)(\s[^>]*)?>((?:(?!</?(?:span|font)\b).)*)</\1>',
            convert_styled,
            text,
            flags=re.DOTALL | re.IGNORECASE
        )
        if old == text:
            break
    for _ in range(12):
        old = text
        text = re.sub(
            r'<(b|i|u)(\s[^>]*)?>((?:(?!</?(?:b|i|u)\b).)*)</\1>',
            convert_styled,
            text,
            flags=re.DOTALL | re.IGNORECASE
        )
        if old == text:
            break

    text = re.sub(r'</?(?:span|font)[^>]*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<a[^>]*>(.*?)</a>', r'\1', text, flags=re.DOTALL | re.IGNORECASE)
    text = text.replace('&nbsp;', ' ')
    text = re.sub(r'&(?!amp;|lt;|gt;|quot;|nbsp;|#\d+;)', '&amp;', text)
    text = re.sub(r'</?(?:div|section|article|header|footer|nav|aside|main|fontface)[^>]*>', '', text, flags=re.IGNORECASE)
    return text

def _safe_paragraph(text, style):
    """Crear un Paragraph de ReportLab de forma segura"""
    try:
        return Paragraph(text, style)
    except Exception:
        cleaned = re.sub(r'textColor="[^"]*"\s*', '', text)
        try:
            return Paragraph(cleaned, style)
        except Exception:
            plain = re.sub(r'<[^>]+>', '', text)
            plain = plain.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
            try:
                return Paragraph(plain, style)
            except Exception:
                return Paragraph('(error de formato)', style)

def html_to_reportlab_paragraphs(html_content, style):
    """
    Convertir HTML de Quill a una lista de flowables de ReportLab.
    
    Quill genera HTML con esta estructura:
    - <p>texto</p>           → párrafo normal
    - <p><br></p>            → línea vacía
    - <ul><li>item</li></ul> → lista con viñetas
    - <ol><li>item</li></ol> → lista numerada
    
    Inline:
    - <strong>negrita</strong>
    - <em>cursiva</em>
    - <u>subrayado</u>
    - <span style="color: rgb(...)">color</span>
    """
    if not html_content:
        return []
    
    flowables = []
    html = html_content.strip()
    
    # Crear estilo para viñetas (con indentación)
    bullet_style = ParagraphStyle(
        'BulletStyle', parent=style,
        leftIndent=20,
        bulletIndent=6,
    )
    
    # ====== PASO 1: Extraer bloques de nivel superior ======
    # Quill produce secuencias de <p>, <ul>, <ol>
    # Usamos regex para extraer cada bloque
    
    # Patrón para capturar bloques: <p>...</p>, <ul>...</ul>, <ol>...</ol>
    # y cualquier texto suelto entre ellos
    block_pattern = re.compile(
        r'(<(?:p|ul|ol|h[1-6])[^>]*>.*?</(?:p|ul|ol|h[1-6])>)',
        re.DOTALL | re.IGNORECASE
    )
    
    blocks = block_pattern.findall(html)
    
    # Si no se encontraron bloques, tratar todo como un solo párrafo
    if not blocks:
        converted = _convert_inline_html(html, default_font=style.fontName)
        if converted.strip():
            flowables.append(_safe_paragraph(converted, style))
        return flowables
    
    # ====== PASO 2: Procesar cada bloque ======
    ol_counter = 0
    align_i = 0
    
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        
        # --- Párrafo <p>...</p> ---
        p_match = re.match(r'<p([^>]*)>(.*?)</p>', block, re.DOTALL | re.IGNORECASE)
        if p_match:
            p_attrs = p_match.group(1) or ''
            inner = p_match.group(2).strip()
            para_style = style
            p_styles = _extract_style_map(p_attrs)
            p_color = _rgb_to_hex(p_styles.get('color')) if p_styles.get('color') else None
            p_font = None
            class_m = re.search(r'class=["\']([^"\']*)["\']', p_attrs)
            if class_m:
                for cls in class_m.group(1).split():
                    if cls.startswith('ql-font-'):
                        p_font = resolve_font_pair(QUILL_FONT_TO_FAMILY.get(cls[8:], 'Arial'))['family']
                    elif cls.startswith('ql-color-'):
                        p_color = _rgb_to_hex(cls[9:]) or p_color
            extra = {}
            if 'ql-align-center' in p_attrs:
                extra['alignment'] = TA_CENTER
            elif 'ql-align-right' in p_attrs:
                extra['alignment'] = TA_RIGHT
            elif 'ql-align-justify' in p_attrs:
                extra['alignment'] = TA_JUSTIFY
            if p_color:
                extra['textColor'] = colors.HexColor(p_color)
            if p_font:
                extra['fontName'] = p_font
            if extra:
                align_i += 1
                para_style = ParagraphStyle(f'{style.name}-p{align_i}', parent=style, **extra)
            
            # Línea vacía: <p><br></p> o <p><br/></p> o <p></p>
            if not inner or re.match(r'^<br\s*/?>$', inner, re.IGNORECASE):
                flowables.append(Spacer(1, 0.1 * inch))
                continue
            
            # Reemplazar <br> internos con espacios (salto de línea dentro de un párrafo)
            inner = re.sub(r'<br\s*/?>', '<br/>', inner, flags=re.IGNORECASE)
            converted = _convert_inline_html(inner, default_font=style.fontName)
            if converted.strip():
                flowables.append(_safe_paragraph(converted, para_style))
            continue
        
        # --- Lista desordenada <ul>...</ul> ---
        ul_match = re.match(r'<ul[^>]*>(.*?)</ul>', block, re.DOTALL | re.IGNORECASE)
        if ul_match:
            ol_counter = 0
            items = re.findall(r'<li[^>]*>(.*?)</li>', ul_match.group(1), re.DOTALL | re.IGNORECASE)
            for item in items:
                converted = _convert_inline_html(item, default_font=style.fontName)
                if converted.strip():
                    bullet_text = f'• {converted}'
                    flowables.append(_safe_paragraph(bullet_text, bullet_style))
            continue
        
        # --- Lista ordenada <ol>...</ol> ---
        ol_match = re.match(r'<ol[^>]*>(.*?)</ol>', block, re.DOTALL | re.IGNORECASE)
        if ol_match:
            items = re.findall(r'<li[^>]*>(.*?)</li>', ol_match.group(1), re.DOTALL | re.IGNORECASE)
            for item in items:
                ol_counter += 1
                converted = _convert_inline_html(item, default_font=style.fontName)
                if converted.strip():
                    numbered_text = f'{ol_counter}. {converted}'
                    flowables.append(_safe_paragraph(numbered_text, bullet_style))
            continue
        
        # --- Encabezados <h1>...<h6> ---
        h_match = re.match(r'<h([1-6])[^>]*>(.*?)</h\1>', block, re.DOTALL | re.IGNORECASE)
        if h_match:
            level = int(h_match.group(1))
            inner = h_match.group(2).strip()
            converted = _convert_inline_html(inner, default_font=style.fontName)
            if converted.strip():
                heading_size = max(10, 20 - (level * 2))
                h_style = ParagraphStyle(
                    f'H{level}Style{len(flowables)}', parent=style,
                    fontSize=heading_size,
                    fontName=style.fontName,
                    spaceAfter=6,
                    spaceBefore=6,
                )
                flowables.append(_safe_paragraph(f'<b>{converted}</b>', h_style))
            continue
        
        # --- Cualquier otro bloque: tratarlo como texto ---
        converted = _convert_inline_html(block, default_font=style.fontName)
        # Limpiar tags de bloque residuales
        converted = re.sub(r'</?(?:div|section|article|header|footer|nav|aside|main)[^>]*>', '', converted, flags=re.IGNORECASE)
        if converted.strip():
            flowables.append(_safe_paragraph(converted, style))
    
    return flowables

def authenticate_ad(username, password):
    """Autenticar usuario contra el directorio LDAP/NTLM configurado."""
    if not AD_ENABLED or not AD_SERVER or not AD_DOMAIN:
        return False
    
    try:
        from ldap3 import Server, Connection, ALL, NTLM
        server = Server(AD_SERVER, get_info=ALL)
        user_dn = f'{AD_DOMAIN}\\{username}'
        conn = Connection(server, user=user_dn, password=password, authentication=NTLM)
        
        if conn.bind():
            conn.unbind()
            return True
        return False
    except Exception as e:
        print(f"Error de autenticacion LDAP: {e}")
        return False

def authenticate_local(username, password):
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            'SELECT password_hash FROM users WHERE username = ? AND IFNULL(ad_user, 0) = 0',
            (username,)
        )
        row = c.fetchone()
        if not row:
            return False
        return verify_local_password(row['password_hash'], password)

def user_is_admin(username):
    if not username:
        return False
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT is_admin FROM users WHERE username = ?', (username,))
        row = c.fetchone()
        return bool(row and row['is_admin'])

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return jsonify({'success': False, 'error': 'No autenticado'}), 401
        if not user_is_admin(session.get('user')):
            return jsonify({'success': False, 'error': 'Se requiere usuario administrador'}), 403
        return f(*args, **kwargs)
    return decorated_function

def create_or_update_user(username, is_ad=False):
    """Crear o actualizar usuario en la base de datos"""
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT username FROM users WHERE username = ?', (username,))
        user = c.fetchone()
        
        if not user:
            c.execute('''
                INSERT INTO users (username, ad_user, is_admin, password_hash, created_at, last_login)
                VALUES (?, ?, 0, NULL, ?, ?)
            ''', (username, 1 if is_ad else 0, get_local_time().isoformat(), get_local_time().isoformat()))
            
            provision_user_api_key(c, username)
        else:
            c.execute('''
                UPDATE users SET last_login = ? WHERE username = ?
            ''', (get_local_time().isoformat(), username))
        
        conn.commit()

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        session['is_admin'] = user_is_admin(session.get('user'))
        return f(*args, **kwargs)
    return decorated_function

def get_accessible_usernames(username):
    """Obtener todos los usuarios a los que tenemos acceso mediante API keys compartidas"""
    with get_db() as conn:
        c = conn.cursor()
        # Obtener los dueños de las API keys a las que tenemos acceso
        c.execute('''
            SELECT DISTINCT ak.username
            FROM api_key_users aku
            JOIN api_keys ak ON aku.api_key = ak.api_key
            WHERE aku.username = ? AND ak.is_active = 1
        ''', (username,))
        
        usernames = [row['username'] for row in c.fetchall()]
        # Siempre incluir el propio usuario
        if username not in usernames:
            usernames.append(username)
        
        return usernames

def bind_api_key_session(api_key, session_id):
    with get_db() as conn:
        c = conn.cursor()
        c.execute('UPDATE api_keys SET session_id = ? WHERE api_key = ?', (session_id, api_key))
        conn.commit()

def create_ingest_key_for_session(username, session_id, title):
    new_key = secrets.token_hex(16)
    name = f'Ingesta: {title}'[:80]
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            '''
            INSERT INTO api_keys (api_key, username, name, created_at, is_active, session_id)
            VALUES (?, ?, ?, ?, 1, ?)
            ''',
            (new_key, username, name, get_local_time().isoformat(), session_id)
        )
        c.execute(
            '''
            INSERT INTO api_key_users (api_key, username, created_at)
            VALUES (?, ?, ?)
            ''',
            (new_key, username, get_local_time().isoformat())
        )
        conn.commit()
    return new_key

def get_or_create_active_session(username):
    """Obtener o crear sesión activa para el usuario"""
    with get_db() as conn:
        c = conn.cursor()
        
        c.execute('''
            SELECT session_id, title, metadata FROM sessions 
            WHERE username = ? AND is_archived = 0
            ORDER BY last_updated DESC LIMIT 1
        ''', (username,))
        
        result = c.fetchone()
        
        if result:
            session_id = result['session_id']
            metadata = json.loads(result['metadata']) if result['metadata'] else {}
        else:
            session_id = secrets.token_hex(16)
            metadata = {
                'title': f'Reporte de Evidencias - {username}',
                'author': username,
                'asunto': '',
                'fecha': get_local_time().strftime('%Y-%m-%d'),
                'informacion': '',
                'conclusiones': '',
                'timezone_offset': TIMEZONE_OFFSET
            }
            
            c.execute('''
                INSERT INTO sessions (session_id, username, title, created_at, last_updated, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (session_id, username, metadata['title'], get_local_time().isoformat(), 
                  get_local_time().isoformat(), json.dumps(metadata)))
            conn.commit()
        
        return session_id, metadata

def get_session_evidence(session_id):
    """Obtener todas las evidencias de una sesión"""
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''
            SELECT * FROM evidence 
            WHERE session_id = ?
            ORDER BY evidence_order ASC, id ASC
        ''', (session_id,))
        
        rows = c.fetchall()
        return [dict(row) for row in rows]

def user_can_access_session(username, session_id):
    """Verificar si el usuario puede acceder a una sesión (propio o compartido)"""
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT username FROM sessions WHERE session_id = ?', (session_id,))
        result = c.fetchone()
        
        if not result:
            return False, False
        
        session_owner = result['username']
        
        # Si es el dueño, tiene acceso completo
        if session_owner == username:
            return True, True
        
        # Verificar si tiene acceso mediante API key compartida
        accessible_users = get_accessible_usernames(username)
        if session_owner in accessible_users:
            return True, True  # Puede ver y editar con API key compartida
        
        return False, False

# ==================== RUTAS DE AUTENTICACIÓN ====================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template('login.html')
    
    data = request.json if request.is_json else request.form
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    
    authenticated = False
    is_ad_user = False

    if authenticate_local(username, password):
        authenticated = True
        is_ad_user = False
    elif AD_ENABLED and authenticate_ad(username, password):
        authenticated = True
        is_ad_user = True
    
    if authenticated:
        session.permanent = True
        session['user'] = username
        if password:
            session['network_password'] = encrypt_session_secret(password)
        create_or_update_user(username, is_ad_user)
        session['is_admin'] = user_is_admin(username)
        
        if request.is_json:
            return jsonify({'success': True, 'username': username})
        return redirect(url_for('index'))
    
    if request.is_json:
        return jsonify({'success': False, 'error': 'Credenciales inválidas'}), 401
    return render_template('login.html', error='Credenciales inválidas')

@app.route('/logout')
def logout():
    session.pop('is_admin', None)
    session.pop('network_password', None)
    session.pop('user', None)
    return redirect(url_for('login'))

# ==================== RUTAS PRINCIPALES ====================

@app.route('/')
@login_required
def index():
    return render_template('dashboard.html', is_admin=user_is_admin(session.get('user')))

@app.route('/evidence/<filename>')
@login_required
def serve_evidence(filename):
    return send_from_directory(app.config['EVIDENCE_FOLDER'], filename)

@app.route('/reports/<filename>')
@login_required
def serve_report(filename):
    return send_from_directory(app.config['REPORTS_FOLDER'], filename)

@app.route('/api/users', methods=['GET'])
@login_required
@admin_required
def list_users():
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''
            SELECT username, ad_user, is_admin, created_at, last_login,
                   CASE WHEN password_hash IS NOT NULL AND password_hash != '' THEN 1 ELSE 0 END AS has_local_password
            FROM users
            ORDER BY is_admin DESC, username COLLATE NOCASE
        ''')
        users = [dict(row) for row in c.fetchall()]
    return jsonify({'success': True, 'users': users})

@app.route('/api/users', methods=['POST'])
@login_required
@admin_required
def create_local_user():
    data = request.json or {}
    username = str(data.get('username') or '').strip()
    password = str(data.get('password') or '')
    make_admin = bool(data.get('is_admin'))

    if not USERNAME_RE.match(username):
        return jsonify({'success': False, 'error': 'Usuario invalido (3-64 caracteres: letras, numeros, . _ -)'}), 400
    if len(password) < 6:
        return jsonify({'success': False, 'error': 'La contrasena debe tener al menos 6 caracteres'}), 400

    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT username FROM users WHERE username = ?', (username,))
        if c.fetchone():
            return jsonify({'success': False, 'error': 'Ese usuario ya existe'}), 409
        now = get_local_time().isoformat()
        c.execute('''
            INSERT INTO users (username, ad_user, is_admin, password_hash, created_at, last_login)
            VALUES (?, 0, ?, ?, ?, ?)
        ''', (username, 1 if make_admin else 0, hash_password(password), now, None))
        provision_user_api_key(c, username)
        conn.commit()
    return jsonify({'success': True, 'username': username})

@app.route('/api/users/<username>/password', methods=['POST'])
@login_required
@admin_required
def reset_local_user_password(username):
    data = request.json or {}
    password = str(data.get('password') or '')
    if len(password) < 6:
        return jsonify({'success': False, 'error': 'La contrasena debe tener al menos 6 caracteres'}), 400
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT username, ad_user FROM users WHERE username = ?', (username,))
        row = c.fetchone()
        if not row:
            return jsonify({'success': False, 'error': 'Usuario no encontrado'}), 404
        if row['ad_user']:
            return jsonify({'success': False, 'error': 'Ese usuario entra por directorio; no tiene clave local'}), 400
        c.execute('UPDATE users SET password_hash = ? WHERE username = ?', (hash_password(password), username))
        conn.commit()
    return jsonify({'success': True})

# ==================== API ENDPOINTS ====================

@app.route('/api/upload', methods=['POST', 'OPTIONS'])
def upload_evidence():
    if request.method == 'OPTIONS':
        return jsonify({'success': True}), 200
    
    try:
        data = request.json
        api_key = request.headers.get('X-API-Key') or data.get('api_key')
        
        if not api_key:
            return jsonify({'success': False, 'error': 'API Key requerida'}), 401
        
        with get_db() as conn:
            c = conn.cursor()
            c.execute('''
                SELECT ak.username, ak.session_id FROM api_keys ak
                WHERE ak.api_key = ? AND ak.is_active = 1
            ''', (api_key,))
            result = c.fetchone()
            
            if not result:
                return jsonify({'success': False, 'error': 'API Key inválida'}), 401
            
            username = result['username']
            bound_session = result['session_id']
            c.execute('UPDATE api_keys SET last_used = ? WHERE api_key = ?',
                     (get_local_time().isoformat(), api_key))
            conn.commit()
        
        session_id = None
        metadata = {}
        if bound_session:
            with get_db() as conn:
                c = conn.cursor()
                c.execute('SELECT metadata FROM sessions WHERE session_id = ?', (bound_session,))
                sess_row = c.fetchone()
            if sess_row:
                session_id = bound_session
                metadata = json.loads(sess_row['metadata']) if sess_row['metadata'] else {}
        if not session_id:
            session_id, metadata = get_or_create_active_session(username)
            bind_api_key_session(api_key, session_id)
        
        filename = None
        if 'image' in data:
            image_data = data['image'].split(',')[1] if ',' in data['image'] else data['image']
            image_bytes = base64.b64decode(image_data)
            
            timestamp = get_local_time().strftime('%Y%m%d_%H%M%S')
            filename = f'evidence_{username}_{timestamp}_{secrets.token_hex(4)}.jpg'
            filepath = os.path.join(app.config['EVIDENCE_FOLDER'], filename)
            
            with open(filepath, 'wb') as f:
                f.write(image_bytes)

            ots_filename = None
            ots_status = None
            sha256_hex = hashlib.sha256(image_bytes).hexdigest()
        else:
            ots_filename = None
            ots_status = None
            sha256_hex = data.get('sha256')
            filepath = None

        download_folder = sanitize_download_folder(data.get('download_folder'))
        
        timestamp_utc = data.get('timestamp')
        if timestamp_utc:
            try:
                parse_to_utc(timestamp_utc)
                timestamp_stored = str(timestamp_utc)
                if not timestamp_stored.endswith('Z') and '+' not in timestamp_stored[10:]:
                    timestamp_stored = timestamp_stored + 'Z'
            except Exception:
                timestamp_stored = datetime.utcnow().replace(tzinfo=dt_timezone.utc).isoformat().replace('+00:00', 'Z')
        else:
            timestamp_stored = datetime.utcnow().replace(tzinfo=dt_timezone.utc).isoformat().replace('+00:00', 'Z')
        
        user_ip = (data.get('user_ip') or '').strip()
        if not looks_like_ip(user_ip):
            forwarded = (request.headers.get('X-Forwarded-For') or '').split(',')[0].strip()
            user_ip = forwarded if looks_like_ip(forwarded) else (request.remote_addr or 'N/A')
        site_ip = (data.get('server_ip') or '').strip()
        page_url = data.get('url', 'N/A')
        if not looks_like_ip(site_ip):
            site_ip = resolve_site_ip(page_url)
        time_source = data.get('time_source') or 'N/A'

        with get_db() as conn:
            c = conn.cursor()
            c.execute('SELECT MAX(evidence_order) as max_order FROM evidence WHERE session_id = ?',
                     (session_id,))
            result = c.fetchone()
            next_order = (result['max_order'] or -1) + 1
            
            c.execute('''
                INSERT INTO evidence (
                    session_id, timestamp, url, user_ip, server_ip, 
                    md5, sha1, capture_mode, filename, annotation, 
                    evidence_order, created_at, sha256, ots_filename, ots_status, download_folder, time_source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                session_id, timestamp_stored, page_url,
                user_ip, site_ip,
                data.get('md5', 'N/A'), data.get('sha1', 'N/A'),
                data.get('capture_mode', 'visible'), filename, '',
                next_order, get_local_time().isoformat(),
                sha256_hex, ots_filename, ots_status, download_folder, time_source
            ))
            
            evidence_id = c.lastrowid
            c.execute('UPDATE sessions SET last_updated = ? WHERE session_id = ?',
                     (get_local_time().isoformat(), session_id))
            conn.commit()
        
        return jsonify({
            'success': True,
            'id': evidence_id,
            'message': 'Evidencia subida correctamente',
            'username': username,
            'session_id': session_id,
            'sha256': sha256_hex
        })
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 400

def _load_evidence_for_user(evidence_id):
    username = session['user']
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM evidence WHERE id = ?', (evidence_id,))
        row = c.fetchone()
        if not row:
            return None, None, jsonify({'success': False, 'error': 'Evidencia no encontrada'}), 404
        item = dict(row)
    can_access, can_edit = user_can_access_session(username, item['session_id'])
    if not can_access:
        return None, None, jsonify({'success': False, 'error': 'No tienes acceso'}), 403
    return item, can_edit, None, None

@app.route('/api/evidence/<int:evidence_id>/ots', methods=['POST'])
@login_required
def stamp_evidence_ots(evidence_id):
    item, can_edit, err, code = _load_evidence_for_user(evidence_id)
    if err:
        return err, code
    if not can_edit:
        return jsonify({'success': False, 'error': 'No tienes permisos de edicion'}), 403
    if not item.get('filename'):
        return jsonify({'success': False, 'error': 'La evidencia no tiene imagen'}), 400
    filepath = os.path.join(app.config['EVIDENCE_FOLDER'], item['filename'])
    try:
        tz = tz_offset_for_evidence(item)
        stamped_at = format_now_dual(tz)
        result = ots_service.stamp_file(filepath, filepath + '.ots', stamped_at=stamped_at)
        ots_filename = os.path.basename(result['ots_path'])
        receipt_filename = os.path.basename(result.get('receipt_path') or (filepath + '.ots.txt'))
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                '''UPDATE evidence SET sha256 = ?, ots_filename = ?, ots_status = ?, ots_stamped_at = ?
                   WHERE id = ?''',
                (result['sha256'], ots_filename, result['status'], stamped_at, evidence_id)
            )
            conn.commit()
        payload = {
            'success': True,
            'ots_filename': ots_filename,
            'receipt_filename': receipt_filename,
            'sha256': result['sha256'],
            'calendars': result.get('calendars') or [],
            'errors': result.get('errors') or [],
            'status': result.get('status') or 'pending',
            'download_folder': item.get('download_folder'),
            'stamped_at': stamped_at,
            'message': 'Se genero la prueba .ots y un recibo de texto con el SHA256.'
        }
        return jsonify(payload)
    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)}), 500

@app.route('/api/evidence/<int:evidence_id>/ots', methods=['GET'])
@login_required
def download_evidence_ots(evidence_id):
    item, can_edit, err, code = _load_evidence_for_user(evidence_id)
    if err:
        return err, code
    ots_name = item.get('ots_filename')
    if not ots_name:
        return jsonify({'success': False, 'error': 'Todavia no hay prueba .ots'}), 404
    ots_path = os.path.join(app.config['EVIDENCE_FOLDER'], ots_name)
    if not os.path.exists(ots_path):
        return jsonify({'success': False, 'error': 'El archivo .ots no esta en disco'}), 404
    return send_from_directory(app.config['EVIDENCE_FOLDER'], ots_name, as_attachment=True)

@app.route('/api/evidence/<int:evidence_id>/ots/receipt', methods=['GET'])
@login_required
def download_evidence_ots_receipt(evidence_id):
    item, can_edit, err, code = _load_evidence_for_user(evidence_id)
    if err:
        return err, code
    ots_name = item.get('ots_filename')
    if not ots_name:
        return jsonify({'success': False, 'error': 'Todavia no hay recibo. Sella primero.'}), 404
    receipt_name = ots_name + '.txt'
    receipt_path = os.path.join(app.config['EVIDENCE_FOLDER'], receipt_name)
    if not os.path.exists(receipt_path):
        return jsonify({'success': False, 'error': 'No hay recibo de texto'}), 404
    return send_from_directory(app.config['EVIDENCE_FOLDER'], receipt_name, as_attachment=True)

@app.route('/api/evidence/<int:evidence_id>/ots/verify', methods=['POST'])
@login_required
def verify_evidence_ots(evidence_id):
    item, can_edit, err, code = _load_evidence_for_user(evidence_id)
    if err:
        return err, code
    if not item.get('filename') or not item.get('ots_filename'):
        return jsonify({'success': False, 'error': 'Falta imagen o prueba .ots. Sella primero.'}), 400
    filepath = os.path.join(app.config['EVIDENCE_FOLDER'], item['filename'])
    ots_path = os.path.join(app.config['EVIDENCE_FOLDER'], item['ots_filename'])
    try:
        result = ots_service.verify_file(filepath, ots_path)
        tz = tz_offset_for_evidence(item)
        verified_at = format_now_dual(tz)
        stamped_at = item.get('ots_stamped_at') or ''
        ots_service.write_receipt(
            ots_path,
            filepath,
            result.get('sha256') or item.get('sha256') or '',
            result.get('pending_calendars') or [],
            [],
            result.get('status') or 'pending',
            stamped_at=stamped_at or None,
            verified_at=verified_at,
            bitcoin_blocks=result.get('bitcoin_blocks'),
            extra_lines=[result.get('message') or '']
        )
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                'UPDATE evidence SET ots_status = ?, sha256 = ?, ots_verified_at = ? WHERE id = ?',
                (result.get('status'), result.get('sha256') or item.get('sha256'), verified_at, evidence_id)
            )
            conn.commit()
        return jsonify({
            'success': True,
            **result,
            'stamped_at': stamped_at,
            'verified_at': verified_at,
            'download_folder': item.get('download_folder')
        })
    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)}), 500

@app.route('/api/evidence/<int:evidence_id>/ots/upgrade', methods=['POST'])
@login_required
def upgrade_evidence_ots(evidence_id):
    item, can_edit, err, code = _load_evidence_for_user(evidence_id)
    if err:
        return err, code
    if not can_edit:
        return jsonify({'success': False, 'error': 'No tienes permisos de edicion'}), 403
    if not item.get('ots_filename'):
        return jsonify({'success': False, 'error': 'No hay prueba .ots'}), 400
    ots_path = os.path.join(app.config['EVIDENCE_FOLDER'], item['ots_filename'])
    filepath = os.path.join(app.config['EVIDENCE_FOLDER'], item['filename'])
    try:
        ots_service.upgrade_proof(ots_path)
        result = ots_service.verify_file(filepath, ots_path)
        tz = tz_offset_for_evidence(item)
        verified_at = format_now_dual(tz)
        stamped_at = item.get('ots_stamped_at') or ''
        ots_service.write_receipt(
            ots_path,
            filepath,
            result.get('sha256') or item.get('sha256') or '',
            result.get('pending_calendars') or [],
            [],
            result.get('status') or 'pending',
            stamped_at=stamped_at or None,
            verified_at=verified_at,
            bitcoin_blocks=result.get('bitcoin_blocks'),
            extra_lines=[result.get('message') or '']
        )
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                'UPDATE evidence SET ots_status = ?, ots_verified_at = ? WHERE id = ?',
                (result.get('status'), verified_at, evidence_id)
            )
            conn.commit()
        return jsonify({
            'success': True,
            **result,
            'stamped_at': stamped_at,
            'verified_at': verified_at
        })
    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)}), 500

@app.route('/api/evidence', methods=['GET'])
@login_required
def get_evidence():
    username = session['user']
    session_id = request.args.get('session_id')
    
    if not session_id:
        session_id, metadata = get_or_create_active_session(username)
    
    # Verificar acceso
    can_access, can_edit = user_can_access_session(username, session_id)
    if not can_access:
        return jsonify({'success': False, 'error': 'No tienes acceso'}), 403
    
    evidence_list = get_session_evidence(session_id)
    
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT metadata, username FROM sessions WHERE session_id = ?', (session_id,))
        result = c.fetchone()
        metadata = json.loads(result['metadata']) if result and result['metadata'] else {}
        session_owner = result['username'] if result else username
        c.execute(
            '''
            SELECT api_key, name FROM api_keys
            WHERE session_id = ? AND is_active = 1
            ORDER BY last_used DESC, created_at DESC
            ''',
            (session_id,)
        )
        ingest_keys = [dict(row) for row in c.fetchall()]
    
    return jsonify({
        'success': True,
        'items': evidence_list,
        'metadata': metadata,
        'session_id': session_id,
        'can_edit': can_edit,
        'owner': session_owner,
        'ingest_api_key': ingest_keys[0]['api_key'] if ingest_keys else None,
        'ingest_keys': ingest_keys
    })

@app.route('/api/evidence/<int:evidence_id>', methods=['PUT'])
@login_required
def update_evidence(evidence_id):
    try:
        username = session['user']
        
        if not request.is_json:
            return jsonify({'success': False, 'error': 'Content-Type debe ser application/json'}), 400
        
        data = request.json
        if not data:
            return jsonify({'success': False, 'error': 'No se recibieron datos'}), 400
        
        with get_db() as conn:
            c = conn.cursor()
            c.execute('''
                SELECT e.session_id FROM evidence e
                JOIN sessions s ON e.session_id = s.session_id
                WHERE e.id = ?
            ''', (evidence_id,))
            result = c.fetchone()
            
            if not result:
                return jsonify({'success': False, 'error': 'Evidencia no encontrada'}), 404
            
            can_access, can_edit = user_can_access_session(username, result['session_id'])
            if not can_edit:
                return jsonify({'success': False, 'error': 'No tienes permisos de edición'}), 403
            
            annotation = data.get('annotation', '')
            c.execute('UPDATE evidence SET annotation = ? WHERE id = ?',
                     (annotation, evidence_id))
            conn.commit()
        
        return jsonify({'success': True, 'message': 'Evidencia actualizada'})
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/evidence/<int:evidence_id>', methods=['DELETE'])
@login_required
def delete_evidence(evidence_id):
    try:
        username = session['user']
        
        with get_db() as conn:
            c = conn.cursor()
            c.execute('''
                SELECT e.session_id, e.filename FROM evidence e
                JOIN sessions s ON e.session_id = s.session_id
                WHERE e.id = ?
            ''', (evidence_id,))
            result = c.fetchone()
            
            if not result:
                return jsonify({'success': False, 'error': 'Evidencia no encontrada'}), 404
            
            can_access, can_edit = user_can_access_session(username, result['session_id'])
            if not can_edit:
                return jsonify({'success': False, 'error': 'No tienes permisos de edición'}), 403
            
            if result['filename']:
                filepath = os.path.join(app.config['EVIDENCE_FOLDER'], result['filename'])
                if os.path.exists(filepath):
                    os.remove(filepath)
            
            c.execute('DELETE FROM evidence WHERE id = ?', (evidence_id,))
            conn.commit()
        
        return jsonify({'success': True, 'message': 'Evidencia eliminada'})
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/evidence/reorder', methods=['POST'])
@login_required
def reorder_evidence():
    try:
        username = session['user']
        data = request.json
        new_order = data.get('order', [])
        session_id = data.get('session_id')
        
        if not session_id:
            session_id, _ = get_or_create_active_session(username)
        
        can_access, can_edit = user_can_access_session(username, session_id)
        if not can_edit:
            return jsonify({'success': False, 'error': 'No tienes permisos de edición'}), 403
        
        with get_db() as conn:
            c = conn.cursor()
            for idx, evidence_id in enumerate(new_order):
                c.execute('UPDATE evidence SET evidence_order = ? WHERE id = ? AND session_id = ?',
                         (idx, evidence_id, session_id))
            conn.commit()
        
        return jsonify({'success': True, 'message': 'Orden actualizado'})
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/metadata', methods=['PUT'])
@login_required
def update_metadata():
    try:
        username = session['user']
        data = request.json
        session_id = data.get('session_id')
        
        if not session_id:
            session_id, _ = get_or_create_active_session(username)
        
        can_access, can_edit = user_can_access_session(username, session_id)
        if not can_edit:
            return jsonify({'success': False, 'error': 'No tienes permisos de edición'}), 403
        
        with get_db() as conn:
            c = conn.cursor()
            c.execute('SELECT metadata FROM sessions WHERE session_id = ?', (session_id,))
            result = c.fetchone()
            current_metadata = json.loads(result['metadata']) if result and result['metadata'] else {}
            current_metadata.update({
                k: v for k, v in data.items()
                if k in (
                    'title', 'author', 'asunto', 'fecha', 'informacion',
                    'conclusiones', 'notas', 'font_family', 'font_size', 'line_spacing',
                    'timezone_offset'
                )
            })
            if 'timezone_offset' in current_metadata:
                try:
                    current_metadata['timezone_offset'] = int(current_metadata['timezone_offset'])
                except (TypeError, ValueError):
                    current_metadata['timezone_offset'] = TIMEZONE_OFFSET
            
            c.execute('UPDATE sessions SET metadata = ?, title = ? WHERE session_id = ?',
                     (json.dumps(current_metadata), data.get('title', current_metadata.get('title')), session_id))
            conn.commit()
        
        return jsonify({'success': True, 'message': 'Metadata actualizada'})
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/generate-pdf', methods=['POST'])
@login_required
def generate_pdf():
    try:
        username = session['user']
        data = request.json or {}
        session_id = data.get('session_id')
        
        if not session_id:
            session_id, _ = get_or_create_active_session(username)
        
        can_access, can_edit = user_can_access_session(username, session_id)
        if not can_access:
            return jsonify({'success': False, 'error': 'No tienes acceso'}), 403
        
        with get_db() as conn:
            c = conn.cursor()
            c.execute('SELECT metadata, title FROM sessions WHERE session_id = ?', (session_id,))
            result = c.fetchone()
            metadata = json.loads(result['metadata']) if result and result['metadata'] else {}
            title = result['title'] if result else 'Reporte'
        
        evidence_list = get_session_evidence(session_id)

        font_pair = resolve_font_pair(metadata.get('font_family', 'Arial'))
        body_font = font_pair['regular']
        bold_font = font_pair['bold']
        try:
            font_size = max(8, min(16, int(metadata.get('font_size', 11))))
        except (TypeError, ValueError):
            font_size = 11
        try:
            leading = max(font_size + 2, min(24, int(metadata.get('line_spacing', font_size + 3))))
        except (TypeError, ValueError):
            leading = font_size + 3

        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=18 * mm,
            leftMargin=18 * mm,
            topMargin=18 * mm,
            bottomMargin=18 * mm
        )
        story = []
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'ReportTitle', parent=styles['Heading1'], fontName=bold_font,
            fontSize=font_size + 7, leading=font_size + 11,
            textColor=colors.HexColor('#1f2937'), spaceAfter=16, alignment=TA_CENTER
        )
        field_style = ParagraphStyle(
            'ReportField', parent=styles['Normal'], fontName=body_font,
            fontSize=font_size, leading=leading, spaceAfter=6, spaceBefore=4
        )
        justify_style = ParagraphStyle(
            'ReportJustify', parent=styles['Normal'], fontName=body_font,
            fontSize=font_size, leading=leading, alignment=TA_JUSTIFY, spaceAfter=6
        )
        url_style = ParagraphStyle(
            'ReportURL', parent=styles['Normal'], fontName=body_font,
            fontSize=max(8, font_size - 2), leading=max(10, leading - 2), wordWrap='CJK'
        )
        table_style = ParagraphStyle(
            'ReportTableCell', parent=styles['Normal'], fontName=body_font,
            fontSize=max(8, font_size - 1), leading=max(10, leading - 2)
        )

        tz = session_tz_offset(metadata)
        story.append(Paragraph(pdf_plain(metadata.get('title', title)), title_style))
        story.append(Spacer(1, 0.2 * inch))
        story.append(Paragraph(f"<b>Zona horaria del reporte:</b> {pdf_plain(offset_label(tz))}", field_style))
        story.append(Paragraph(f"<b>Generado:</b> {pdf_plain(format_now_dual(tz))}", field_style))
        if metadata.get('author'):
            story.append(Paragraph(f"<b>Autor:</b> {pdf_plain(metadata.get('author'))}", field_style))
        if metadata.get('asunto'):
            story.append(Paragraph(f"<b>Asunto:</b> {pdf_plain(metadata.get('asunto'))}", field_style))
        if metadata.get('fecha'):
            story.append(Paragraph(f"<b>Fecha:</b> {pdf_plain(metadata.get('fecha'))}", field_style))
        if metadata.get('informacion'):
            story.append(Paragraph("<b>Informacion</b>", field_style))
            for para in html_to_reportlab_paragraphs(metadata['informacion'], justify_style):
                story.append(para)

        for idx, item in enumerate(evidence_list, 1):
            story.append(PageBreak())
            timestamp_formatted = format_datetime(item['timestamp'], tz)
            url_para = Paragraph(pdf_plain(item.get('url', 'N/A')), url_style)
            hash_data = [
                [Paragraph('<b>Modo</b>', table_style), Paragraph(pdf_plain(capture_mode_label(item.get('capture_mode'))), table_style)],
                [Paragraph('<b>Fecha de captura</b>', table_style), Paragraph(pdf_plain(timestamp_formatted), table_style)],
                [Paragraph('<b>Zona horaria</b>', table_style), Paragraph(pdf_plain('UTC y ' + offset_label(tz)), table_style)],
                [Paragraph('<b>Fuente de hora</b>', table_style), Paragraph(pdf_plain(item.get('time_source') or 'N/A'), table_style)],
                [Paragraph('<b>URL</b>', table_style), url_para],
                [Paragraph('<b>IP usuario</b>', table_style), Paragraph(pdf_plain(item.get('user_ip') or 'N/A'), table_style)],
                [Paragraph('<b>IP del sitio</b>', table_style), Paragraph(pdf_plain(item.get('server_ip') or 'N/A'), table_style)],
                [Paragraph('<b>MD5</b>', table_style), Paragraph(pdf_plain(item.get('md5', 'N/A')), table_style)],
                [Paragraph('<b>SHA1</b>', table_style), Paragraph(pdf_plain(item.get('sha1', 'N/A')), table_style)],
                [Paragraph('<b>SHA256</b>', table_style), Paragraph(pdf_plain(item.get('sha256') or 'N/A'), table_style)],
            ]
            hash_table = Table(hash_data, colWidths=[1.6 * inch, 4.6 * inch])
            hash_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), body_font),
                ('FONTSIZE', (0, 0), (-1, -1), max(8, font_size - 1)),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                ('LEFTPADDING', (0, 0), (-1, -1), 6),
                ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#d1d5db')),
                ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f3f4f6')),
            ]))
            story.append(hash_table)
            story.append(Spacer(1, 0.15 * inch))

            if item.get('annotation'):
                for para in html_to_reportlab_paragraphs(item['annotation'], justify_style):
                    story.append(para)
                story.append(Spacer(1, 0.12 * inch))

            if item.get('filename'):
                filepath = os.path.join(app.config['EVIDENCE_FOLDER'], item['filename'])
                if os.path.exists(filepath):
                    img = PILImage.open(filepath)
                    img_width, img_height = img.size
                    if img_width > 0 and img_height > 0:
                        max_width = float(doc.width) - 8
                        max_height = float(doc.height) * 0.62
                        ratio = min(max_width / img_width, max_height / img_height, 1)
                        new_width = img_width * ratio
                        new_height = img_height * ratio
                        img_obj = Image(filepath, width=new_width, height=new_height)
                        img_frame = Table(
                            [[img_obj]],
                            colWidths=[new_width + 8],
                            rowHeights=[new_height + 8]
                        )
                        img_frame.setStyle(TableStyle([
                            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                            ('LEFTPADDING', (0, 0), (-1, -1), 4),
                            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                            ('TOPPADDING', (0, 0), (-1, -1), 4),
                            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                            ('BOX', (0, 0), (-1, -1), 1, colors.black),
                        ]))
                        story.append(img_frame)

        if metadata.get('conclusiones'):
            story.append(PageBreak())
            conclusiones_title_style = ParagraphStyle(
                'ConclusionesTitle', parent=styles['Normal'],
                fontSize=font_size + 2, fontName=bold_font,
                textColor=colors.black, spaceAfter=8, spaceBefore=8
            )
            story.append(Paragraph("CONCLUSIONES", conclusiones_title_style))
            for para in html_to_reportlab_paragraphs(metadata['conclusiones'], justify_style):
                story.append(para)

        doc.build(story)
        buffer.seek(0)
        
        timestamp_str = get_local_time().strftime('%Y%m%d_%H%M%S')
        filename = f"reporte_{username}_{timestamp_str}.pdf"
        filepath = os.path.join(app.config['REPORTS_FOLDER'], filename)
        
        with open(filepath, 'wb') as f:
            f.write(buffer.getvalue())
        
        # Copiar a carpeta de red compartida usando credenciales del usuario
        try:
            # Obtener password de la sesión si está disponible
            password = None
            if 'network_password' in session:
                try:
                    password = decrypt_session_secret(session['network_password'])
                except:
                    password = None
                    print("⚠️ No se pudo decodificar password de sesión")
            
            copy_to_network_folder(filepath, filename, username, password)
        except Exception as e:
            print(f"⚠️ Advertencia: No se pudo copiar a carpeta de red: {e}")
            import traceback
            traceback.print_exc()
            # No fallar si no se puede copiar a la red, solo loguear
        
        with get_db() as conn:
            c = conn.cursor()
            c.execute('''
                INSERT INTO reports (session_id, filename, title, created_at, created_by, num_evidences)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (session_id, filename, title, get_local_time().isoformat(), username, len(evidence_list)))
            conn.commit()
        
        buffer.seek(0)
        return send_file(buffer, mimetype='application/pdf', as_attachment=True, download_name=filename)
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/reports', methods=['GET'])
@login_required
def get_reports():
    username = session['user']
    accessible_users = get_accessible_usernames(username)
    
    with get_db() as conn:
        c = conn.cursor()
        placeholders = ','.join('?' * len(accessible_users))
        c.execute(f'''
            SELECT r.*, s.username as owner FROM reports r
            JOIN sessions s ON r.session_id = s.session_id
            WHERE s.username IN ({placeholders})
            ORDER BY r.created_at DESC
        ''', accessible_users)
        
        reports = [dict(row) for row in c.fetchall()]
    
    return jsonify({'success': True, 'reports': reports})

@app.route('/api/reports/<int:report_id>', methods=['DELETE'])
@login_required
def delete_report(report_id):
    """Eliminar un reporte específico"""
    try:
        username = session['user']
        
        with get_db() as conn:
            c = conn.cursor()
            # Verificar que el reporte existe y obtener información
            c.execute('''
                SELECT r.filename, r.session_id, s.username as owner 
                FROM reports r
                JOIN sessions s ON r.session_id = s.session_id
                WHERE r.id = ?
            ''', (report_id,))
            result = c.fetchone()
            
            if not result:
                return jsonify({'success': False, 'error': 'Reporte no encontrado'}), 404
            
            # Verificar permisos (solo el dueño puede eliminar)
            if result['owner'] != username:
                return jsonify({'success': False, 'error': 'No tienes permisos para eliminar este reporte'}), 403
            
            # Eliminar archivo local
            filename = result['filename']
            local_filepath = os.path.join(app.config['REPORTS_FOLDER'], filename)
            if os.path.exists(local_filepath):
                try:
                    os.remove(local_filepath)
                except Exception as e:
                    print(f"Advertencia: No se pudo eliminar archivo local: {e}")
            
            # Eliminar de la base de datos
            c.execute('DELETE FROM reports WHERE id = ?', (report_id,))
            conn.commit()
        
        return jsonify({'success': True, 'message': 'Reporte eliminado correctamente'})
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/sessions/cleanup', methods=['POST'])
@login_required
def cleanup_old_sessions():
    """Eliminar casos/sesiones viejas"""
    try:
        username = session['user']
        data = request.json or {}
        
        # Días de antigüedad para considerar "viejo" (por defecto 90 días)
        days_old = data.get('days', 90)
        
        cutoff_date = (get_local_time() - timedelta(days=days_old)).isoformat()
        
        with get_db() as conn:
            c = conn.cursor()
            
            # Obtener sesiones viejas del usuario
            c.execute('''
                SELECT session_id FROM sessions 
                WHERE username = ? AND created_at < ? AND is_archived = 1
            ''', (username, cutoff_date))
            
            old_sessions = c.fetchall()
            deleted_count = 0
            
            for session_row in old_sessions:
                session_id = session_row['session_id']
                
                # Eliminar evidencias asociadas
                c.execute('SELECT filename FROM evidence WHERE session_id = ?', (session_id,))
                evidence_files = c.fetchall()
                
                for evidence_file in evidence_files:
                    if evidence_file['filename']:
                        filepath = os.path.join(app.config['EVIDENCE_FOLDER'], evidence_file['filename'])
                        if os.path.exists(filepath):
                            try:
                                os.remove(filepath)
                            except Exception as e:
                                print(f"Advertencia: No se pudo eliminar evidencia: {e}")
                
                # Eliminar reportes asociados
                c.execute('SELECT filename FROM reports WHERE session_id = ?', (session_id,))
                report_files = c.fetchall()
                
                for report_file in report_files:
                    if report_file['filename']:
                        filepath = os.path.join(app.config['REPORTS_FOLDER'], report_file['filename'])
                        if os.path.exists(filepath):
                            try:
                                os.remove(filepath)
                            except Exception as e:
                                print(f"Advertencia: No se pudo eliminar reporte: {e}")
                
                # Eliminar registros de la base de datos
                c.execute('DELETE FROM evidence WHERE session_id = ?', (session_id,))
                c.execute('DELETE FROM reports WHERE session_id = ?', (session_id,))
                c.execute('DELETE FROM sessions WHERE session_id = ?', (session_id,))
                deleted_count += 1
            
            conn.commit()
        
        return jsonify({
            'success': True, 
            'message': f'Se eliminaron {deleted_count} casos antiguos',
            'deleted_count': deleted_count
        })
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/clear', methods=['POST'])
@login_required
def clear_all():
    try:
        username = session['user']
        data = request.json or {}
        session_id = data.get('session_id')
        
        if not session_id:
            session_id, _ = get_or_create_active_session(username)
        
        can_access, can_edit = user_can_access_session(username, session_id)
        if not can_edit:
            return jsonify({'success': False, 'error': 'No tienes permisos de edición'}), 403
        
        evidence_list = get_session_evidence(session_id)
        
        for item in evidence_list:
            if item['filename']:
                filepath = os.path.join(app.config['EVIDENCE_FOLDER'], item['filename'])
                if os.path.exists(filepath):
                    os.remove(filepath)
        
        with get_db() as conn:
            c = conn.cursor()
            c.execute('DELETE FROM evidence WHERE session_id = ?', (session_id,))
            conn.commit()
        
        return jsonify({'success': True, 'message': 'Evidencias eliminadas'})
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/session-info', methods=['GET'])
@login_required
def get_session_info():
    username = session['user']
    
    with get_db() as conn:
        c = conn.cursor()
        # Obtener API keys propias
        c.execute('''
            SELECT api_key FROM api_keys 
            WHERE username = ? AND is_active = 1
            ORDER BY created_at DESC LIMIT 1
        ''', (username,))
        result = c.fetchone()
        api_key = result['api_key'] if result else None
    
    session_id, _ = get_or_create_active_session(username)
    
    return jsonify({
        'success': True,
        'username': username,
        'session_id': session_id,
        'api_key': api_key
    })

@app.route('/api/api-keys', methods=['GET'])
@login_required
def get_api_keys():
    username = session['user']
    
    with get_db() as conn:
        c = conn.cursor()
        
        # API keys propias
        c.execute('''
            SELECT ak.id, ak.api_key, ak.name, ak.created_at, ak.last_used, ak.is_active,
                   ak.username as owner, ak.session_id, s.title as case_title
            FROM api_keys ak
            LEFT JOIN sessions s ON s.session_id = ak.session_id
            WHERE ak.username = ?
            ORDER BY ak.created_at DESC
        ''', (username,))
        
        own_keys = [dict(row) for row in c.fetchall()]
        
        # API keys compartidas (a las que tengo acceso)
        c.execute('''
            SELECT ak.id, ak.api_key, ak.name, ak.created_at, ak.last_used, 
                   ak.is_active, ak.username as owner, aku.created_at as shared_at,
                   ak.session_id, s.title as case_title
            FROM api_key_users aku
            JOIN api_keys ak ON aku.api_key = ak.api_key
            LEFT JOIN sessions s ON s.session_id = ak.session_id
            WHERE aku.username = ? AND ak.username != ?
            ORDER BY aku.created_at DESC
        ''', (username, username))
        
        shared_keys = [dict(row) for row in c.fetchall()]
    
    return jsonify({
        'success': True,
        'own_keys': own_keys,
        'shared_keys': shared_keys
    })

@app.route('/api/api-keys', methods=['POST'])
@login_required
def create_api_key():
    try:
        username = session['user']
        data = request.json
        name = data.get('name', 'Nueva API Key')
        existing_key = data.get('api_key')

        # VINCULAR API KEY EXISTENTE
        if existing_key:
            existing_key = existing_key.strip()

            with get_db() as conn:
                c = conn.cursor()

                # Verificar que la key existe y está activa
                c.execute(
                    'SELECT username, name FROM api_keys WHERE api_key = ? AND is_active = 1',
                    (existing_key,)
                )
                result = c.fetchone()

                if not result:
                    return jsonify({
                        'success': False,
                        'error': 'API Key no encontrada o inactiva'
                    }), 404

                original_owner = result[0]

                # Si ya es el dueño, ya tiene acceso
                if original_owner == username:
                    return jsonify({
                        'success': False,
                        'error': 'Ya tienes acceso a esta API Key'
                    }), 400

                # Verificar si ya está vinculada
                c.execute(
                    'SELECT 1 FROM api_key_users WHERE api_key = ? AND username = ?',
                    (existing_key, username)
                )
                if c.fetchone():
                    return jsonify({
                        'success': False,
                        'error': 'Ya tienes acceso a esta API Key'
                    }), 400

                # Crear vínculo
                c.execute(
                    '''
                    INSERT INTO api_key_users (api_key, username, created_at)
                    VALUES (?, ?, ?)
                    ''',
                    (existing_key, username, get_local_time().isoformat())
                )

                conn.commit()

            return jsonify({
                'success': True,
                'api_key': existing_key,
                'message': f'Acceso compartido por {original_owner}'
            })

        # CREAR NUEVA API KEY
        else:
            new_key = secrets.token_hex(16)
            session_id = data.get('session_id')
            if not session_id:
                session_id, _ = get_or_create_active_session(username)

            with get_db() as conn:
                c = conn.cursor()

                c.execute(
                    '''
                    INSERT INTO api_keys (api_key, username, name, created_at, is_active, session_id)
                    VALUES (?, ?, ?, ?, 1, ?)
                    ''',
                    (new_key, username, name, get_local_time().isoformat(), session_id)
                )

                # Vincular automáticamente al creador
                c.execute(
                    '''
                    INSERT INTO api_key_users (api_key, username, created_at)
                    VALUES (?, ?, ?)
                    ''',
                    (new_key, username, get_local_time().isoformat())
                )

                conn.commit()

            return jsonify({
                'success': True,
                'api_key': new_key,
                'message': 'API Key creada'
            })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 400

@app.route('/api/api-keys/<int:key_id>/toggle', methods=['POST'])
@login_required
def toggle_api_key(key_id):
    try:
        username = session['user']
        
        with get_db() as conn:
            c = conn.cursor()
            # Solo el dueño puede activar/desactivar
            c.execute('''
                UPDATE api_keys SET is_active = NOT is_active 
                WHERE id = ? AND username = ?
            ''', (key_id, username))
            
            if c.rowcount == 0:
                return jsonify({'success': False, 'error': 'No tienes permisos'}), 403
            
            conn.commit()
        
        return jsonify({'success': True, 'message': 'Estado actualizado'})
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/api-keys/<int:key_id>', methods=['DELETE'])
@login_required
def delete_api_key(key_id):
    try:
        username = session['user']
        
        with get_db() as conn:
            c = conn.cursor()
            # Solo el dueño puede eliminar
            c.execute('DELETE FROM api_keys WHERE id = ? AND username = ?', (key_id, username))
            
            if c.rowcount == 0:
                return jsonify({'success': False, 'error': 'No tienes permisos'}), 403
            
            conn.commit()
        
        return jsonify({'success': True, 'message': 'API Key eliminada'})
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/api-keys/shared/<api_key>', methods=['DELETE'])
@login_required
def remove_shared_access(api_key):
    """Remover acceso propio a una API key compartida"""
    try:
        username = session['user']
        
        with get_db() as conn:
            c = conn.cursor()
            
            # Verificar que no es el dueño
            c.execute('SELECT username FROM api_keys WHERE api_key = ?', (api_key,))
            result = c.fetchone()
            
            if result and result['username'] == username:
                return jsonify({'success': False, 'error': 'No puedes remover tu propia API key'}), 400
            
            # Remover acceso
            c.execute('DELETE FROM api_key_users WHERE api_key = ? AND username = ?', 
                     (api_key, username))
            conn.commit()
        
        return jsonify({'success': True, 'message': 'Acceso removido'})
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/sessions', methods=['GET'])
@login_required
def get_sessions():
    """Obtener todas las sesiones accesibles"""
    username = session['user']
    accessible_users = get_accessible_usernames(username)
    
    with get_db() as conn:
        c = conn.cursor()
        
        placeholders = ','.join('?' * len(accessible_users))
        c.execute(f'''
            SELECT s.session_id, s.title, s.created_at, s.last_updated, 
                   s.is_archived, s.username as owner, COUNT(e.id) as evidence_count,
                   (SELECT ak.api_key FROM api_keys ak
                    WHERE ak.session_id = s.session_id AND ak.username = s.username AND ak.is_active = 1
                    ORDER BY ak.created_at DESC LIMIT 1) as ingest_api_key
            FROM sessions s
            LEFT JOIN evidence e ON s.session_id = e.session_id
            WHERE s.username IN ({placeholders})
            GROUP BY s.session_id
            ORDER BY s.is_archived ASC, s.last_updated DESC
        ''', accessible_users)
        
        sessions = [dict(row) for row in c.fetchall()]
        
        # Marcar si es propia o compartida
        for sess in sessions:
            sess['is_own'] = (sess['owner'] == username)
            if sess['is_own'] and not sess.get('ingest_api_key'):
                sess['ingest_api_key'] = create_ingest_key_for_session(
                    sess['owner'], sess['session_id'], sess['title'] or 'Caso'
                )
    
    return jsonify({
        'success': True,
        'sessions': sessions
    })

@app.route('/api/sessions/new', methods=['POST'])
@login_required
def create_new_session():
    """Crear una nueva sesión/caso"""
    try:
        username = session['user']
        data = request.json
        title = data.get('title', f'Nuevo Caso - {datetime.now().strftime("%Y-%m-%d")}')
        
        session_id = secrets.token_hex(16)
        metadata = {
            'title': title,
            'author': username,
            'asunto': '',
            'fecha': get_local_time().strftime('%Y-%m-%d'),
            'informacion': '',
            'conclusiones': '',
            'timezone_offset': TIMEZONE_OFFSET
        }
        
        with get_db() as conn:
            c = conn.cursor()
            c.execute('''
                INSERT INTO sessions (session_id, username, title, created_at, last_updated, metadata, is_archived)
                VALUES (?, ?, ?, ?, ?, ?, 0)
            ''', (session_id, username, title, get_local_time().isoformat(), 
                  get_local_time().isoformat(), json.dumps(metadata)))
            conn.commit()

        ingest_api_key = create_ingest_key_for_session(username, session_id, title)
        
        return jsonify({
            'success': True,
            'session_id': session_id,
            'ingest_api_key': ingest_api_key,
            'message': 'Nueva sesión creada'
        })
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/sessions/<session_id>', methods=['GET'])
@login_required
def get_session(session_id):
    """Obtener información de una sesión específica"""
    username = session['user']
    can_access, can_edit = user_can_access_session(username, session_id)
    
    if not can_access:
        return jsonify({'success': False, 'error': 'No tienes acceso a esta sesión'}), 403
    
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''
            SELECT session_id, username, title, created_at, last_updated, 
                   is_archived, metadata
            FROM sessions WHERE session_id = ?
        ''', (session_id,))
        result = c.fetchone()
        
        if not result:
            return jsonify({'success': False, 'error': 'Sesión no encontrada'}), 404
        
        session_data = dict(result)
        session_data['metadata'] = json.loads(session_data['metadata']) if session_data['metadata'] else {}
        
        return jsonify({'success': True, 'session': session_data})

@app.route('/api/sessions/<session_id>/metadata', methods=['GET'])
@login_required
def get_session_metadata(session_id):
    """Obtener metadata de una sesión"""
    username = session['user']
    can_access, can_edit = user_can_access_session(username, session_id)
    
    if not can_access:
        return jsonify({'success': False, 'error': 'No tienes acceso a esta sesión'}), 403
    
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT metadata FROM sessions WHERE session_id = ?', (session_id,))
        result = c.fetchone()
        
        if not result:
            return jsonify({'success': False, 'error': 'Sesión no encontrada'}), 404
        
        metadata = json.loads(result['metadata']) if result['metadata'] else {}
        return jsonify({'success': True, 'metadata': metadata})

@app.route('/api/sessions/<session_id>/activate', methods=['POST'])
@login_required
def activate_session(session_id):
    """Activar una sesión específica (desarchivar)"""
    try:
        username = session['user']
        
        can_access, can_edit = user_can_access_session(username, session_id)
        if not can_edit:
            return jsonify({'success': False, 'error': 'No tienes permisos de edición'}), 403
        
        with get_db() as conn:
            c = conn.cursor()
            c.execute('UPDATE sessions SET is_archived = 0 WHERE session_id = ?', (session_id,))
            conn.commit()
        
        return jsonify({'success': True, 'message': 'Sesión activada'})
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/sessions/<session_id>/archive', methods=['POST'])
@login_required
def archive_session(session_id):
    """Archivar una sesión"""
    try:
        username = session['user']
        
        can_access, can_edit = user_can_access_session(username, session_id)
        if not can_edit:
            return jsonify({'success': False, 'error': 'No tienes permisos de edición'}), 403
        
        with get_db() as conn:
            c = conn.cursor()
            c.execute('UPDATE sessions SET is_archived = 1 WHERE session_id = ?', (session_id,))
            conn.commit()
        
        return jsonify({'success': True, 'message': 'Sesión archivada'})
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/sessions/<session_id>', methods=['DELETE'])
@login_required
def delete_session(session_id):
    """Eliminar completamente una sesión"""
    try:
        username = session['user']
        
        can_access, can_edit = user_can_access_session(username, session_id)
        if not can_edit:
            return jsonify({'success': False, 'error': 'No tienes permisos de edición'}), 403
        
        with get_db() as conn:
            c = conn.cursor()
            
            # Obtener y eliminar archivos de evidencias
            c.execute('SELECT filename FROM evidence WHERE session_id = ?', (session_id,))
            files = c.fetchall()
            for file_row in files:
                if file_row['filename']:
                    filepath = os.path.join(app.config['EVIDENCE_FOLDER'], file_row['filename'])
                    if os.path.exists(filepath):
                        os.remove(filepath)
            
            # Eliminar registros
            c.execute('DELETE FROM evidence WHERE session_id = ?', (session_id,))
            c.execute('DELETE FROM reports WHERE session_id = ?', (session_id,))
            c.execute('DELETE FROM sessions WHERE session_id = ?', (session_id,))
            conn.commit()
        
        return jsonify({'success': True, 'message': 'Sesión eliminada'})
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

if __name__ == '__main__':
    init_db()
    listen_host = RUNTIME.get('listen_host') or '0.0.0.0'
    listen_port = int(RUNTIME.get('listen_port') or 5000)
    print("=" * 60)
    print("SERVIDOR DE EVIDENCIAS INICIADO")
    print("=" * 60)
    print("Complete server/config.json (origenes, directorio LDAP, red).")
    origins = RUNTIME.get('dashboard_origins') or []
    if origins:
        print("Origenes CORS del dashboard:")
        for origin in origins:
            print(f"   {origin}")
    else:
        print("dashboard_origins esta vacio: agregue la URL publica del dashboard en config.json")
    print("=" * 60)
    print("Cuenta administradora local: admin_username / admin_password en config.json (se crea en SQLite al arrancar).")
    print("=" * 60)
    print("Directorio LDAP:")
    print(f"   Estado: {'HABILITADO' if AD_ENABLED else 'DESHABILITADO'}")
    if AD_ENABLED:
        print(f"   Servidor: {AD_SERVER or '(vacio: complete ad_server)'}")
        print(f"   Dominio: {AD_DOMAIN or '(vacio: complete ad_domain)'}")
    print("=" * 60)
    print("Base de datos SQLite: evidence_system.db")
    print(f"Escucha: {listen_host}:{listen_port}")
    print("=" * 60)
    # debug solo si se habilita explícitamente en config.json (nunca en producción)
    app.run(debug=bool(RUNTIME.get('debug', False)), host=listen_host, port=listen_port)