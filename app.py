import json
import os
import time
from datetime import timedelta
from functools import wraps
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import create_engine, text
from db_manager import load_db_config, save_db_config, build_connection_uri, get_engine

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'finans_muhasebe_secret_key_2025_secure_xyz')

# Güvenlik Ayarları: Sayfa kapanınca oturum silinir, maksimum 5 dakika işlem yapılmazsa kilitlenir
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=5)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

INACTIVITY_TIMEOUT_SECONDS = 300  # 5 Dakika (300 saniye)
USERS_FILE = os.path.join(os.path.dirname(__file__), 'users.json')

def load_users():
    if not os.path.exists(USERS_FILE):
        default_users = [
            {
                "username": "yokus",
                "password_hash": generate_password_hash("hasret7903"),
                "name": "Ali Yokuş (Yönetici)",
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

import secrets

@app.context_processor
def inject_user():
    is_fresh = session.pop('login_fresh', False)
    return {
        'current_user': session.get('user'),
        'session_tab_token': session.get('tab_token', ''),
        'is_fresh_login': is_fresh
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

    # 5 Dakika Hareketsizlik / Zaman Aşımı Kontrolü
    now = time.time()
    last_active = session.get('last_active')
    if last_active and (now - last_active) > INACTIVITY_TIMEOUT_SECONDS:
        session.clear()
        return redirect(url_for('login', timeout=1))

    session['last_active'] = now

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user' in session:
        return redirect(url_for('index'))
    
    error = None
    if request.args.get('timeout'):
        error = 'Güvenlik gereği oturumunuz 5 dakika işlem yapılmadığı veya sekme kapatıldığı için sonlandırıldı. Lütfen tekrar şifre girin.'

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        user = authenticate_user(username, password)
        if user:
            # Tarayıcı veya sekme kapandığında oturumun silinmesi için permanent=False
            session.permanent = False
            session['user'] = {
                'username': user.get('username'),
                'name': user.get('name', username),
                'role': user.get('role', 'user')
            }
            session['tab_token'] = secrets.token_hex(16)
            session['login_fresh'] = True
            session['last_active'] = time.time()
            next_url = request.args.get('next') or url_for('index')
            return redirect(next_url)
        else:
            error = 'Kullanıcı adı veya şifre hatalı. Lütfen tekrar deneyin.'

    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.clear()
    timeout = request.args.get('timeout')
    if timeout:
        return redirect(url_for('login', timeout=1))
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
    users_raw = load_users()
    # Şifre hash'lerini frontend'e göndermemek için temiz liste oluştur
    users = [
        {
            'username': u.get('username'),
            'name': u.get('name', u.get('username')),
            'role': u.get('role', 'user')
        }
        for u in users_raw
    ]
    return render_template('parametreler.html', aktif_sayfa='parametreler', connections=connections, users=users)

@app.route('/api/users/add', methods=['POST'])
def add_user_api():
    if 'user' not in session or session['user'].get('role') != 'admin':
        return jsonify({'success': False, 'message': 'Bu işlem için yönetici (admin) yetkisi gereklidir.'}), 403

    data = request.get_json() or {}
    username = data.get('username', '').strip().lower()
    name = data.get('name', '').strip() or username
    password = data.get('password', '')
    role = data.get('role', 'user')

    if not username or not password:
        return jsonify({'success': False, 'message': 'Kullanıcı adı ve şifre zorunludur.'}), 400

    if len(password) < 4:
        return jsonify({'success': False, 'message': 'Şifre en az 4 karakter olmalıdır.'}), 400

    users = load_users()
    for u in users:
        if u.get('username', '').lower() == username:
            return jsonify({'success': False, 'message': f"'{username}' kullanıcı adı zaten kullanımda."}), 400

    users.append({
        'username': username,
        'name': name,
        'password_hash': generate_password_hash(password),
        'role': role
    })

    if save_users(users):
        return jsonify({'success': True, 'message': f"'{username}' kullanıcısı başarıyla eklendi."})
    return jsonify({'success': False, 'message': 'Kullanıcı kaydedilemedi.'}), 500

@app.route('/api/users/reset-password', methods=['POST'])
def admin_reset_password_api():
    if 'user' not in session or session['user'].get('role') != 'admin':
        return jsonify({'success': False, 'message': 'Bu işlem için yönetici yetkisi gereklidir.'}), 403

    data = request.get_json() or {}
    target_username = data.get('username', '').strip()
    new_password = data.get('new_password', '')

    if not target_username or not new_password:
        return jsonify({'success': False, 'message': 'Kullanıcı ve yeni şifre zorunludur.'}), 400

    if len(new_password) < 4:
        return jsonify({'success': False, 'message': 'Yeni şifre en az 4 karakter olmalıdır.'}), 400

    users = load_users()
    user_found = False
    for u in users:
        if u.get('username') == target_username:
            u['password_hash'] = generate_password_hash(new_password)
            user_found = True
            break

    if user_found and save_users(users):
        return jsonify({'success': True, 'message': f"'{target_username}' kullanıcısının şifresi güncellendi."})
    return jsonify({'success': False, 'message': 'Kullanıcı bulunamadı.'}), 404

@app.route('/api/users/delete', methods=['POST'])
def delete_user_api():
    if 'user' not in session or session['user'].get('role') != 'admin':
        return jsonify({'success': False, 'message': 'Bu işlem için yönetici yetkisi gereklidir.'}), 403

    data = request.get_json() or {}
    target_username = data.get('username', '').strip()

    if not target_username:
        return jsonify({'success': False, 'message': 'Kullanıcı adı belirtilmedi.'}), 400

    if target_username == session['user']['username']:
        return jsonify({'success': False, 'message': 'Kendinizi silemezsiniz.'}), 400

    users = load_users()
    initial_len = len(users)
    users = [u for u in users if u.get('username') != target_username]

    if len(users) == initial_len:
        return jsonify({'success': False, 'message': 'Kullanıcı bulunamadı.'}), 404

    if save_users(users):
        return jsonify({'success': True, 'message': f"'{target_username}' kullanıcısı silindi."})
    return jsonify({'success': False, 'message': 'Kullanıcı silinemedi.'}), 500

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

        conn_uri = build_connection_uri(conn_data)
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
