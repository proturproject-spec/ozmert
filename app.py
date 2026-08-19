import json
import os
import time
import requests as http_requests
from datetime import timedelta
import io
import pandas as pd
from functools import wraps
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash, Response
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import create_engine, text
from db_manager import load_db_config, save_db_config, build_connection_uri, get_engine

# Render ortamında: BRIDGE_URL env değişkeni ile yerel SQL köprüsüne bağlan
# Örnek: BRIDGE_URL=https://xxxx.ngrok-free.app
BRIDGE_URL = os.environ.get('BRIDGE_URL', '').rstrip('/')
BRIDGE_KEY = os.environ.get('BRIDGE_API_KEY', 'nexlog_bridge_2026_secure_xKj9')
USE_BRIDGE = bool(BRIDGE_URL)  # Eğer BRIDGE_URL varsa SQL bridge'i kullan

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

# --- CARİ HESAP EKSTRESİ & KAPAMA MODÜLÜ ---
DB_YEAR_PREFIX_MAP = {
    "2025": "225",
    "2026": "226"
}

def format_currency(x):
    if pd.isna(x) or x is None:
        return "0,00"
    return "{:,.2f}".format(float(x)).replace(",", "X").replace(".", ",").replace("X", ".")

def get_cari_queries(year):
    p = DB_YEAR_PREFIX_MAP.get(str(year), "226")
    cards_query = f"SELECT CODE, DEFINITION_ FROM LG_{p}_CLCARD WHERE CODE LIKE '120.%' ORDER BY DEFINITION_"
    
    base_query = f"""
    SELECT
        CLF.DATE_ AS [TARIH],
        REPLACE(LTRIM(RTRIM(C.SPECODE2)), '  ', ' ') AS [OZEL_KOD],
        C.CODE + ' / ' + C.DEFINITION_ AS [CARI_UNVAN],
        CASE WHEN CLF.TRCODE = 1 THEN 'Nakit tahsilat'
             WHEN CLF.TRCODE = 2 THEN 'Nakit ödeme'
             WHEN CLF.TRCODE = 3 THEN 'Borç dekontu'
             WHEN CLF.TRCODE = 4 THEN 'Alacak dekontu'
             WHEN CLF.TRCODE = 5 THEN 'Virman İşlemi'
             WHEN CLF.TRCODE = 6 THEN 'Kur farkı işlemi'
             WHEN CLF.TRCODE = 12 THEN 'Özel işlem'
             WHEN CLF.TRCODE = 14 THEN 'Açılış'
             WHEN CLF.TRCODE = 20 THEN 'Gelen havaleler'
             WHEN CLF.TRCODE = 21 THEN 'Gönderilen havaleler'
             WHEN CLF.TRCODE = 31 THEN 'Mal alım fat.'
             WHEN CLF.TRCODE = 36 THEN 'Alım iade fat.'
             WHEN CLF.TRCODE = 37 THEN 'Perakende satış fat.'
             WHEN CLF.TRCODE = 38 THEN 'Toptan satış fat.'
             WHEN CLF.TRCODE = 39 THEN 'Verilen hizmet faturası'
             WHEN CLF.TRCODE IN (61,62) THEN 'Çek/Senet Girişi'
             WHEN CLF.TRCODE IN (63,64) THEN 'Çek/Senet Çıkışı'
             ELSE 'Diğer' END AS [ISLEM_TURU],
        CLF.TRANNO AS [FIS_NO],
        CAST(CLF.LINEEXP AS VARCHAR(MAX)) AS [ACIKLAMA],
        CASE WHEN CLF.SIGN = 0 THEN CLF.TRNET ELSE 0 END AS [BORC],
        CASE WHEN CLF.SIGN = 1 THEN CLF.TRNET ELSE 0 END AS [ALACAK]
    FROM LG_{p}_01_CLFLINE CLF
    LEFT JOIN LG_{p}_CLCARD C ON C.LOGICALREF = CLF.CLIENTREF
    WHERE CLF.CANCELLED = 0 
    AND LTRIM(RTRIM(C.CODE)) = LTRIM(RTRIM(:cari_code)) 
    AND CAST(CLF.DATE_ AS DATE) >= :date
    ORDER BY CLF.DATE_, CLF.FTIME, CLF.LOGICALREF
    """

    devir_query = f"""
    SELECT 
        SUM(CASE WHEN CLF.SIGN = 0 THEN CLF.TRNET ELSE 0 END) - 
        SUM(CASE WHEN CLF.SIGN = 1 THEN CLF.TRNET ELSE 0 END) as DEVIR
    FROM LG_{p}_01_CLFLINE CLF
    LEFT JOIN LG_{p}_CLCARD C ON C.LOGICALREF = CLF.CLIENTREF
    WHERE CLF.CANCELLED = 0 
    AND LTRIM(RTRIM(C.CODE)) = LTRIM(RTRIM(:cari_code)) 
    AND CAST(CLF.DATE_ AS DATE) < :date
    """
    return cards_query, base_query, devir_query

def get_cari_ekstre_df(year, date_val, cari_code):
    if not cari_code:
        return pd.DataFrame()

    # Render ortamında SQL köprüsünden veri çek
    if USE_BRIDGE:
        try:
            resp = http_requests.get(
                f"{BRIDGE_URL}/bridge/cari-ekstre",
                params={'year': year, 'date': date_val, 'cari': cari_code},
                headers={'X-Bridge-Key': BRIDGE_KEY},
                timeout=30
            )
            data = resp.json()
            if 'error' in data:
                return pd.DataFrame()
            rows = data.get('rows', [])
            devir_tutari = float(data.get('devir', 0))
            df = pd.DataFrame(rows)
            if not df.empty and 'TARIH' in df.columns:
                df['TARIH'] = pd.to_datetime(df['TARIH'], errors='coerce')
        except Exception as e:
            print(f"Bridge hatası: {e}")
            return pd.DataFrame()
    else:
        # Lokal SQL bağlantısı
        engine = get_engine(1)
        _, base_q, devir_q = get_cari_queries(year)
        with engine.connect() as conn:
            params = {"date": date_val, "cari_code": cari_code.strip()}
            devir_res = conn.execute(text(devir_q), params).fetchone()
            devir_tutari = float(devir_res[0] or 0) if devir_res and devir_res[0] is not None else 0.0
            df = pd.read_sql(text(base_q), conn, params=params)

    devir_row = pd.DataFrame([{
        'TARIH': None,
        'OZEL_KOD': '',
        'CARI_UNVAN': 'Önceki Dönemden Devir',
        'ISLEM_TURU': 'Açılış/Devir',
        'FIS_NO': '---',
        'ACIKLAMA': 'Önceki Dönem Bakiye Devri',
        'BORC': devir_tutari if devir_tutari > 0 else 0.0,
        'ALACAK': abs(devir_tutari) if devir_tutari < 0 else 0.0
    }])

    df = pd.concat([devir_row, df], ignore_index=True)
    df['BORC'] = pd.to_numeric(df['BORC'], errors='coerce').fillna(0.0)
    df['ALACAK'] = pd.to_numeric(df['ALACAK'], errors='coerce').fillna(0.0)
    df['BAKIYE'] = (df['BORC'] - df['ALACAK']).cumsum()
    return df


@app.route('/finans/cari-ekstre')
def cari_ekstre():
    return render_template('cari_ekstre.html', aktif_sayfa='cari_ekstre')

@app.route('/finans/cari-kapama')
def cari_kapama():
    return render_template('cari_kapama.html', aktif_sayfa='cari_kapama')

@app.route('/finans/api/cariler')
def api_cariler():
    year = request.args.get('year', '2026')
    try:
        # Render ortamında köprüden çek
        if USE_BRIDGE:
            resp = http_requests.get(
                f"{BRIDGE_URL}/bridge/cariler",
                params={'year': year},
                headers={'X-Bridge-Key': BRIDGE_KEY},
                timeout=15
            )
            return jsonify(resp.json())
        else:
            engine = get_engine(1)
            cards_q, _, _ = get_cari_queries(year)
            with engine.connect() as conn:
                cariler = pd.read_sql(text(cards_q), conn).to_dict(orient='records')
            return jsonify(cariler)
    except Exception as e:
        print(f"Cari kartlar getirme hatası: {e}")
        return jsonify([])


@app.route('/finans/api/cari-ekstre-data')
def api_cari_ekstre_data():
    year = request.args.get('year', '2026')
    date_val = request.args.get('date', f'{year}-01-01')
    cari = request.args.get('cari', '').strip()

    if not cari:
        return jsonify({'error': 'Cari hesap seçilmedi.'}), 400

    try:
        df = get_cari_ekstre_df(year, date_val, cari)
        if df.empty:
            return jsonify({
                'html': '<div style="padding: 4rem 2rem; text-align: center; color: var(--text-muted);">Bu cari karta ait hareket bulunamadı.</div>',
                't_borc': '0,00',
                't_alacak': '0,00',
                't_bakiye': '0,00'
            })

        t_borc = float(df['BORC'].sum())
        t_alacak = float(df['ALACAK'].sum())
        t_bakiye = t_borc - t_alacak

        html = '<table class="ekstre-table"><thead><tr>'
        html += '<th>Tarih</th><th>Özel Kod</th><th>Cari Ünvan</th>'
        html += '<th>İşlem Türü</th><th>Fiş No</th><th>Açıklama</th>'
        html += '<th class="text-right">Borç</th><th class="text-right">Alacak</th><th class="text-right">Bakiye</th></tr></thead><tbody>'

        for _, row in df.iterrows():
            is_devir = row['CARI_UNVAN'] == 'Önceki Dönemden Devir'
            tarih_str = row['TARIH'].strftime('%d.%m.%Y') if row['TARIH'] is not None and not pd.isna(row['TARIH']) else 'DEVİR'
            row_class = ' class="devir-row"' if is_devir else ''
            
            bakiye_val = float(row['BAKIYE'])
            bakiye_color = '#60a5fa' if bakiye_val == 0 else ('#f87171' if bakiye_val > 0 else '#34d399')

            html += f'<tr{row_class}>'
            html += f'<td><strong style="color: #93c5fd;">{tarih_str}</strong></td>'
            html += f'<td>{row["OZEL_KOD"] or "-"}</td>'
            html += f'<td><span style="font-weight: 500;">{row["CARI_UNVAN"]}</span></td>'
            html += f'<td><span class="badge-islem">{row["ISLEM_TURU"]}</span></td>'
            html += f'<td><code style="color: #cbd5e1;">{row["FIS_NO"] or "-"}</code></td>'
            html += f'<td style="max-width: 350px; word-break: break-word; color: var(--text-muted);">{row["ACIKLAMA"] or "-"}</td>'
            html += f'<td class="text-right" style="color: #fca5a5;">{format_currency(row["BORC"])}</td>'
            html += f'<td class="text-right" style="color: #86efac;">{format_currency(row["ALACAK"])}</td>'
            html += f'<td class="text-right" style="font-weight: 700; color: {bakiye_color};">{format_currency(bakiye_val)}</td>'
            html += '</tr>'

        html += '</tbody></table>'

        return jsonify({
            'html': html,
            't_borc': format_currency(t_borc),
            't_alacak': format_currency(t_alacak),
            't_bakiye': format_currency(t_bakiye)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/finans/cari-ekstre/export')
def export_cari_ekstre():
    year = request.args.get('year', '2026')
    date_val = request.args.get('date', f'{year}-01-01')
    cari = request.args.get('cari', '').strip()

    if not cari:
        return redirect(url_for('cari_ekstre'))

    try:
        df = get_cari_ekstre_df(year, date_val, cari)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Cari_Ekstre')
        output.seek(0)

        filename = f"Cari_Ekstre_{cari.replace('/', '_').replace(' ', '_')}_{year}.xlsx"
        return Response(
            output.read(),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as e:
        return f"Excel dışa aktarma hatası: {str(e)}", 500

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
