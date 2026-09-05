"""
auth_logger.py - Kullanıcı Giriş ve Güvenlik Log Yöneticisi
Sisteme giriş yapan, hatalı şifre deneyen veya çıkış yapan kullanıcıların
tarih, saat, IP ve cihaz bilgilerini SQLite veritabanında güvenle saklar.
"""
import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'login_logs.db')

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_log_db():
    try:
        with get_db_connection() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS login_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    username TEXT NOT NULL,
                    name TEXT,
                    role TEXT,
                    ip TEXT,
                    device TEXT,
                    browser TEXT,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    details TEXT
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_login_logs_id ON login_logs(id DESC)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_login_logs_user ON login_logs(username)')
    except Exception as e:
        print(f"Log DB başlatma hatası: {e}")

def parse_user_agent(ua_string):
    """User-Agent metninden cihaz ve tarayıcıyı tespit eder."""
    if not ua_string:
        return ("💻 Masaüstü", "Web Tarayıcı")
    
    ua = ua_string.lower()
    
    # Cihaz / İşletim Sistemi
    if "iphone" in ua or "ipad" in ua:
        device = "📱 iOS (Apple)"
    elif "android" in ua:
        device = "📱 Android"
    elif "windows" in ua:
        device = "💻 Windows"
    elif "macintosh" in ua or "mac os" in ua:
        device = "💻 macOS"
    elif "linux" in ua:
        device = "💻 Linux"
    else:
        device = "💻 Masaüstü/Mobil"
        
    # Tarayıcı
    if "edg/" in ua or "edge/" in ua:
        browser = "Microsoft Edge"
    elif "opr/" in ua or "opera" in ua:
        browser = "Opera"
    elif "chrome/" in ua and "safari" in ua:
        browser = "Google Chrome"
    elif "safari/" in ua and "chrome/" not in ua:
        browser = "Safari"
    elif "firefox/" in ua:
        browser = "Mozilla Firefox"
    else:
        browser = "Web Tarayıcı"
        
    return (device, browser)

def log_login_event(username, name=None, role=None, ip=None, user_agent=None, action='LOGIN', status='SUCCESS', details=''):
    """Giriş/çıkış veya deneme olayını kaydeder."""
    try:
        init_log_db()
        device, browser = parse_user_agent(user_agent)
        now_str = datetime.now().strftime('%d.%m.%Y %H:%M:%S')
        
        with get_db_connection() as conn:
            conn.execute('''
                INSERT INTO login_logs (timestamp, username, name, role, ip, device, browser, action, status, details)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                now_str,
                username or 'Bilinmiyor',
                name or username or '-',
                role or '-',
                ip or '127.0.0.1',
                device,
                browser,
                action,
                status,
                details
            ))
            # Maksimum 1000 kayıt tut (Eski kayıtları otomatik temizle)
            conn.execute('''
                DELETE FROM login_logs WHERE id NOT IN (
                    SELECT id FROM login_logs ORDER BY id DESC LIMIT 1000
                )
            ''')
    except Exception as e:
        print(f"Log yazma hatası: {e}")

def get_login_logs(limit=100, search=None, action_filter=None):
    """Kayıtlı giriş loglarını döner."""
    try:
        init_log_db()
        query = "SELECT * FROM login_logs"
        conditions = []
        params = []
        
        if search:
            search_param = f"%{search.strip()}%"
            conditions.append("(username LIKE ? OR name LIKE ? OR ip LIKE ? OR details LIKE ?)")
            params.extend([search_param, search_param, search_param, search_param])
            
        if action_filter and action_filter != 'ALL':
            conditions.append("action = ?")
            params.append(action_filter)
            
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
            
        query += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        
        with get_db_connection() as conn:
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        print(f"Log okuma hatası: {e}")
        return []

def clear_login_logs():
    """Tüm giriş loglarını siler."""
    try:
        init_log_db()
        with get_db_connection() as conn:
            conn.execute("DELETE FROM login_logs")
        return True
    except Exception as e:
        print(f"Log silme hatası: {e}")
        return False
