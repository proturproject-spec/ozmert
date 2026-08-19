import json
import os
import urllib.parse
from sqlalchemy import create_engine

CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'db_config.json')

def load_db_config():
    """db_config.json dosyasından SQL bağlantı parametrelerini yükler."""
    if not os.path.exists(CONFIG_FILE):
        return []
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        print(f"db_config.json okuma hatası: {e}")
        return []

def save_db_config(connections):
    """SQL bağlantı parametrelerini db_config.json dosyasına kaydeder."""
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(connections, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"db_config.json kaydetme hatası: {e}")
        return False

def get_connection_config(conn_id_or_name=1):
    """Belirtilen ID veya isme göre bağlantı ayarını döner."""
    configs = load_db_config()
    for cfg in configs:
        if cfg.get('id') == conn_id_or_name or cfg.get('name') == conn_id_or_name:
            return cfg
    if isinstance(conn_id_or_name, int) and 0 < conn_id_or_name <= len(configs):
        return configs[conn_id_or_name - 1]
    return None

def build_connection_uri(conn_data):
    """JSON konfigürasyon nesnesini güvenli SQLAlchemy URI'sine dönüştürür."""
    if not conn_data:
        raise ValueError("Bağlantı parametreleri bulunamadı.")
    
    driver = conn_data.get('driver', 'ODBC Driver 17 for SQL Server')
    server = conn_data.get('server', '').strip()
    port = conn_data.get('port', '').strip()
    database = conn_data.get('database', '').strip()
    username = conn_data.get('username', '').strip()
    password = conn_data.get('password', '')
    trusted_conn = conn_data.get('trusted_connection', False)
    trust_cert = conn_data.get('trust_server_certificate', True)

    server_part = f"{server},{port}" if port and str(port).strip() != "1433" else server

    params_parts = [
        f"DRIVER={{{driver}}}",
        f"SERVER={server_part}",
        f"DATABASE={database}"
    ]

    if trusted_conn:
        params_parts.append("Trusted_Connection=yes")
    else:
        if username:
            params_parts.append(f"UID={username}")
        if password:
            params_parts.append(f"PWD={password}")

    if trust_cert:
        params_parts.append("TrustServerCertificate=yes")

    connection_str = ";".join(params_parts) + ";"
    encoded_params = urllib.parse.quote_plus(connection_str)
    return f"mssql+pyodbc:///?odbc_connect={encoded_params}"

def get_engine(conn_id_or_name=1, timeout=10, pool_pre_ping=True):
    """
    Belirtilen bağlantı için db_config.json'dan okuyarak SQLAlchemy Engine nesnesi üretir.
    Kodlar ve sorgular içinde şifre/sunucu bilgisi tutulmaz, tamamen JSON dosyasından yönetilir.
    """
    cfg = get_connection_config(conn_id_or_name)
    if not cfg:
        raise ValueError(f"'{conn_id_or_name}' kimlikli SQL bağlantı ayarı db_config.json içerisinde bulunamadı.")
    
    uri = build_connection_uri(cfg)
    conn_timeout = int(cfg.get('timeout', timeout))
    return create_engine(
        uri,
        pool_pre_ping=pool_pre_ping,
        connect_args={"timeout": conn_timeout}
    )
