import json
import os
import time
import urllib.parse
from functools import wraps
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import create_engine, text

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'finans_muhasebe_secret_key_2025_secure_xyz')

CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'db_config.json')
USERS_FILE = os.path.join(os.path.dirname(__file__), 'users.json')

DEFAULT_CONNECTIONS = [
    {
        "id": 1,
        "name": "1. SQL Bağlantısı (Ana Veritabanı)",
        "driver": "ODBC Driver 17 for SQL Server",
        "server": "UFUK-SERVER",
        "port": "1433",
        "database": "UFUK2025",
        "username": "MDT_REPORT",
        "password": "MDT_REPORT",
        "trusted_connection": False,
        "trust_server_certificate": True,
        "timeout": 5,
        "is_active": True
    },
    {
        "id": 2,
        "name": "2. SQL Bağlantısı (Nexlog Veritabanı)",
        "driver": "ODBC Driver 17 for SQL Server",
        "server": "UFUK-SERVER",
        "port": "1433",
        "database": "NEXLOG",
        "username": "MDT_REPORT",
        "password": "MDT_REPORT",
        "trusted_connection": False,
        "trust_server_certificate": True,
        "timeout": 5,
        "is_active": True
    },
    {
        "id": 3,
        "name": "3. SQL Bağlantısı (Rapor / Arşiv DB)",
        "driver": "ODBC Driver 17 for SQL Server",
        "server": "UFUK-SERVER",
        "port": "1433",
        "database": "RAPORDB",
        "username": "sa",
        "password": "",
        "trusted_connection": False,
        "trust_server_certificate": True,
        "timeout": 5,
        "is_active": False
    }
]

def load_users():
    if not os.path.exists(USERS_FILE):
        default_users = [
            {
                "username": "admin",
                "password_hash": generate_password_hash("admin123"),
                "name": "Yönetici (Admin)",
                "role": "admin"
            }
        ]
        save_users(default_users)
        return default_users
    try:
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []

def save_users(users):
    try:
        with open(USERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"Kullanıcı kaydetme hatası: {e}")
        return False

def authenticate_user(username, password):
    users = load_users()
    for user in users:
        if user.get('username') == username:
            if check_password_hash(user.get('password_hash', ''), password):
                return user
    return None

def load_db_config():
    if not os.path.exists(CONFIG_FILE):
        save_db_config(DEFAULT_CONNECTIONS)
        return DEFAULT_CONNECTIONS
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, list) and len(data) >= 3:
                return data
            return DEFAULT_CONNECTIONS
    except Exception:
        return DEFAULT_CONNECTIONS

def save_db_config(connections):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(connections, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"Config kaydetme hatası: {e}")
        return False

@app.context_processor
def inject_user():
    return {
        'current_user': session.get('user')
    }

@app.before_request
def require_login():
    # İzin verilen açık rotalar
    allowed_endpoints = ['login', 'static']
    if request.endpoint and (request.endpoint in allowed_endpoints or request.endpoint.startswith('static')):
        return None
    
    # Giriş kontrolü
    if 'user' not in session:
        return redirect(url_for('login', next=request.url))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user' in session:
        return redirect(url_for('index'))
    
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remember = request.form.get('remember')

        user = authenticate_user(username, password)
        if user:
            session.permanent = bool(remember)
            session['user'] = {
                'username': user.get('username'),
                'name': user.get('name', username),
                'role': user.get('role', 'user')
            }
            next_url = request.args.get('next') or url_for('index')
            return redirect(next_url)
        else:
            error = 'Kullanıcı adı veya şifre hatalı. Lütfen tekrar deneyin.'

    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
def index():
    return render_template('index.html', aktif_sayfa='index')

@app.route('/muhasebe')
def muhasebe():
    return render_template('muhasebe.html', aktif_sayfa='muhasebe')

@app.route('/finans')
def finans():
    return render_template('finans.html', aktif_sayfa='finans')

@app.route('/raporlar')
def raporlar():
    return render_template('raporlar.html', aktif_sayfa='raporlar')

@app.route('/parametreler')
def parametreler():
    connections = load_db_config()
    return render_template('parametreler.html', aktif_sayfa='parametreler', connections=connections)

@app.route('/api/change-password', methods=['POST'])
def change_password():
    if 'user' not in session:
        return jsonify({'success': False, 'message': 'Oturum bulunamadı.'}), 401
    
    data = request.get_json() or {}
    old_password = data.get('old_password', '')
    new_password = data.get('new_password', '')

    if not old_password or not new_password:
        return jsonify({'success': False, 'message': 'Eski ve yeni şifre alanları zorunludur.'}), 400

    if len(new_password) < 4:
        return jsonify({'success': False, 'message': 'Yeni şifre en az 4 karakter olmalıdır.'}), 400

    current_username = session['user']['username']
    users = load_users()
    user_found = False

    for user in users:
        if user.get('username') == current_username:
            if not check_password_hash(user.get('password_hash', ''), old_password):
                return jsonify({'success': False, 'message': 'Mevcut şifreniz hatalı.'}), 400
            user['password_hash'] = generate_password_hash(new_password)
            user_found = True
            break

    if user_found and save_users(users):
        return jsonify({'success': True, 'message': 'Şifreniz başarıyla değiştirildi.'})
    return jsonify({'success': False, 'message': 'Şifre güncellenemedi.'}), 500

@app.route('/api/db-connections/save', methods=['POST'])
def save_connections_api():
    try:
        data = request.get_json()
        if not data or 'connections' not in data:
            return jsonify({'success': False, 'message': 'Geçersiz veri gönderildi.'}), 400
        
        connections = data['connections']
        if save_db_config(connections):
            return jsonify({'success': True, 'message': 'Bağlantı parametreleri başarıyla kaydedildi.'})
        else:
            return jsonify({'success': False, 'message': 'Dosyaya yazılamadı.'}), 500
    except Exception as e:
        return jsonify({'success': False, 'message': f'Hata oluştu: {str(e)}'}), 500

@app.route('/api/db-connections/test', methods=['POST'])
def test_connection_api():
    try:
        conn_data = request.get_json()
        if not conn_data:
            return jsonify({'success': False, 'message': 'Bağlantı bilgisi bulunamadı.'}), 400
        
        driver = conn_data.get('driver', 'SQL Server')
        server = conn_data.get('server', '').strip()
        port = conn_data.get('port', '').strip()
        database = conn_data.get('database', '').strip()
        username = conn_data.get('username', '').strip()
        password = conn_data.get('password', '')
        trusted_conn = conn_data.get('trusted_connection', False)
        trust_cert = conn_data.get('trust_server_certificate', True)
        timeout = int(conn_data.get('timeout', 5))

        if not server:
            return jsonify({'success': False, 'message': 'Sunucu (Server) adresi boş bırakılamaz.'}), 400
        if not database:
            return jsonify({'success': False, 'message': 'Veritabanı (Database) adı boş bırakılamaz.'}), 400

        server_part = f"{server},{port}" if port and port != "1433" else server

        params_parts = [
            f"DRIVER={{{driver}}}",
            f"SERVER={server_part}",
            f"DATABASE={database}"
        ]

        if trusted_conn:
            params_parts.append("Trusted_Connection=yes")
        else:
            if not username:
                return jsonify({'success': False, 'message': 'Kullanıcı adı boş bırakılamaz.'}), 400
            params_parts.append(f"UID={username}")
            params_parts.append(f"PWD={password}")

        if trust_cert:
            params_parts.append("TrustServerCertificate=yes")

        connection_str = ";".join(params_parts) + ";"
        encoded_params = urllib.parse.quote_plus(connection_str)
        conn_uri = f"mssql+pyodbc:///?odbc_connect={encoded_params}"

        start_time = time.time()
        engine = create_engine(conn_uri, connect_args={"timeout": timeout})
        
        with engine.connect() as conn:
            result = conn.execute(text("SELECT @@VERSION AS ver, DB_NAME() AS dbname")).fetchone()
            version_info = result[0].split('\n')[0] if result and result[0] else 'Bilinmiyor'
            current_db = result[1] if result and len(result) > 1 else database

        elapsed_ms = round((time.time() - start_time) * 1000)
        return jsonify({
            'success': True,
            'message': f"Bağlantı başarılı! ({elapsed_ms} ms)",
            'version': version_info,
            'database': current_db,
            'latency_ms': elapsed_ms
        })
    except Exception as e:
        err_msg = str(e)
        if "[SQL Server]" in err_msg:
            err_msg = err_msg.split("[SQL Server]")[-1].split("[SQLState")[0].strip()
        elif "Login failed" in err_msg:
            err_msg = "Kullanıcı adı veya şifre hatalı."
        elif "Cannot open database" in err_msg:
            err_msg = "Belirtilen veritabanı bulunamadı veya erişim yetkisi yok."
        elif "Server is not found" in err_msg or "error: 08001" in err_msg:
            err_msg = "Sunucuya erişilemedi. Sunucu adı ve ağ bağlantınızı kontrol edin."

        return jsonify({
            'success': False,
            'message': f"Bağlantı hatası: {err_msg}"
        })

if __name__ == '__main__':
    app.run(debug=True)
