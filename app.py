import json
import os
import time
import secrets
import requests as http_requests
from datetime import timedelta
import io
import pandas as pd
from functools import wraps
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash, Response
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import create_engine, text
from db_manager import load_db_config, save_db_config, build_connection_uri, get_engine

# ============================================================
# HASSAS VERİLER VE ORTAM DEĞİŞKENLERİ ZORUNLULUĞU
# ============================================================
# Kod içine asla varsayılan gizli anahtar yazılmaz.
SECRET_KEY = os.environ.get('SECRET_KEY')
if not SECRET_KEY:
    if os.environ.get('RENDER') or os.environ.get('PORT'):
        raise RuntimeError("KRİTİK GÜVENLİK HATASI: Canlı sunucuda 'SECRET_KEY' ortam değişkeni zorunludur! Lütfen Render Environment sekmesinden SECRET_KEY tanımlayın.")
    else:
        SECRET_KEY = secrets.token_hex(32)
        print("BİLGİ: 'SECRET_KEY' ortam değişkeni tanımlanmadığı için lokal ortamda dinamik güvenli anahtar üretildi.")

BRIDGE_URL = os.environ.get('BRIDGE_URL', '').rstrip('/')
BRIDGE_KEY = os.environ.get('BRIDGE_API_KEY', '')
USE_BRIDGE = bool(BRIDGE_URL)  # Eğer BRIDGE_URL varsa SQL bridge'i kullan

if USE_BRIDGE and not BRIDGE_KEY:
    raise RuntimeError("KRİTİK GÜVENLİK HATASI: Köprü modu (BRIDGE_URL) aktifken 'BRIDGE_API_KEY' ortam değişkeni zorunludur!")

app = Flask(__name__)
app.secret_key = SECRET_KEY

# Güvenlik Ayarları (HTTPS Zorunluluğu, XSS ve CSRF Koruması)
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=5)
app.config['SESSION_COOKIE_SECURE'] = True      # HTTPS Zorunluluğu: Çerezler sadece şifreli HTTPS üzerinden iletilir
app.config['SESSION_COOKIE_HTTPONLY'] = True    # XSS Koruması: JavaScript çerezlere erişemez
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'    # CSRF Koruması

# ============================================================
# İSTEK SINIRLANDIRMASI (RATE LIMITING) - Giriş Sayfası Koruması
# ============================================================
_LOGIN_ATTEMPTS = {}  # { ip: [timestamp, ...] }
_LOGIN_LOCKOUTS = {}  # { ip: lockout_expiry_timestamp }

MAX_LOGIN_ATTEMPTS = 5       # 5 dakika içinde izin verilen maksimum başarısız deneme
LOGIN_WINDOW_SECONDS = 300   # 5 dakika (300 saniye)
LOCKOUT_DURATION = 900       # Kilitlenme süresi: 15 dakika (900 saniye)

def get_client_ip():
    """İstemcinin gerçek IP adresini döner."""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr or '127.0.0.1'

def is_ip_rate_limited(ip):
    """IP kilitli mi kontrol eder. Kalan kilit saniyesini döner, kilitli değilse 0."""
    now = time.time()
    if ip in _LOGIN_LOCKOUTS:
        expiry = _LOGIN_LOCKOUTS[ip]
        if now < expiry:
            return int(expiry - now)
        else:
            del _LOGIN_LOCKOUTS[ip]
            _LOGIN_ATTEMPTS[ip] = []
            
    attempts = [t for t in _LOGIN_ATTEMPTS.get(ip, []) if now - t < LOGIN_WINDOW_SECONDS]
    _LOGIN_ATTEMPTS[ip] = attempts
    
    if len(attempts) >= MAX_LOGIN_ATTEMPTS:
        _LOGIN_LOCKOUTS[ip] = now + LOCKOUT_DURATION
        return LOCKOUT_DURATION
    return 0

def record_failed_login(ip):
    """Başarısız giriş denemesini kaydeder."""
    now = time.time()
    if ip not in _LOGIN_ATTEMPTS:
        _LOGIN_ATTEMPTS[ip] = []
    _LOGIN_ATTEMPTS[ip].append(now)
    if len(_LOGIN_ATTEMPTS[ip]) >= MAX_LOGIN_ATTEMPTS:
        _LOGIN_LOCKOUTS[ip] = now + LOCKOUT_DURATION

def clear_login_attempts(ip):
    """Başarılı girişte IP kısıtını sıfırlar."""
    _LOGIN_ATTEMPTS.pop(ip, None)
    _LOGIN_LOCKOUTS.pop(ip, None)

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

ALL_SYSTEM_PAGES = [
    {'key': 'index', 'title': 'Ana Sayfa', 'icon': '🏠', 'url': '/'},
    {'key': 'muhasebe', 'title': 'Muhasebe', 'icon': '📑', 'url': '/muhasebe'},
    {'key': 'cari_ekstre', 'title': 'Cari Hesap Ekstresi', 'icon': '📑', 'url': '/finans/cari-ekstre'},
    {'key': 'cari_kapama', 'title': 'Cari Hesap Kapama', 'icon': '🔄', 'url': '/finans/cari-kapama'},
    {'key': 'nakit_akis', 'title': 'Nakit Akış Paneli', 'icon': '💵', 'url': '/finans/nakit-akis'},
    {'key': 'finans', 'title': 'Finans Genel Görünüm', 'icon': '📊', 'url': '/finans'},
    {'key': 'raporlar', 'title': 'Raporlar', 'icon': '📈', 'url': '/raporlar'},
    {'key': 'parametreler', 'title': 'Parametreler & Ayarlar', 'icon': '⚙️', 'url': '/parametreler'}
]

def user_has_permission(page_key):
    u = session.get('user')
    if not u:
        return False
    if u.get('role') == 'admin':
        return True
    allowed = u.get('allowed_pages')
    if allowed is None:
        return page_key != 'parametreler'
    return page_key in allowed or '*' in allowed

def permission_required(page_key):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not user_has_permission(page_key):
                if request.is_json or request.path.startswith('/api/') or request.path.startswith('/finans/api/'):
                    return jsonify({'success': False, 'message': 'Bu işlem için yetkiniz bulunmamaktadır.'}), 403
                flash('Bu sayfaya erişim yetkiniz bulunmamaktadır.', 'error')
                return render_template('unauthorized.html', page_key=page_key, aktif_sayfa='unauthorized'), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator

@app.context_processor
def inject_user():
    is_fresh = session.pop('login_fresh', False)
    return {
        'current_user': session.get('user'),
        'session_tab_token': session.get('tab_token', ''),
        'is_fresh_login': is_fresh,
        'has_permission': user_has_permission,
        'ALL_SYSTEM_PAGES': ALL_SYSTEM_PAGES
    }

@app.before_request
def require_login():
    # İzin verilen açık rotalar ve köprü istekleri (Köprü istekleri kendi API anahtarıyla doğrulanır)
    if request.path.startswith('/bridge/'):
        return None

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
    
    client_ip = get_client_ip()
    remaining_lock = is_ip_rate_limited(client_ip)
    
    error = None
    if remaining_lock > 0:
        minutes = (remaining_lock // 60) + 1
        error = f'Güvenlik uyarısı: Çok fazla hatalı giriş denemesi yapıldı. Bot ve saldırı koruması nedeniyle erişiminiz {minutes} dakika kısıtlandı.'
        return render_template('login.html', error=error), 429

    if request.args.get('timeout'):
        error = 'Güvenlik gereği oturumunuz 5 dakika işlem yapılmadığı veya sekme kapatıldığı için sonlandırıldı. Lütfen tekrar şifre girin.'

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        user = authenticate_user(username, password)
        if user:
            clear_login_attempts(client_ip)  # Başarılı girişte sıfırla
            # Tarayıcı veya sekme kapandığında oturumun silinmesi için permanent=False
            session.permanent = False
            session['user'] = {
                'username': user.get('username'),
                'name': user.get('name', username),
                'role': user.get('role', 'user'),
                'allowed_pages': user.get('allowed_pages', ['index', 'muhasebe', 'cari_ekstre', 'cari_kapama', 'nakit_akis', 'finans', 'raporlar']) if user.get('role') != 'admin' else ['*']
            }
            session['tab_token'] = secrets.token_hex(16)
            session['login_fresh'] = True
            session['last_active'] = time.time()
            next_url = request.args.get('next') or url_for('index')
            return redirect(next_url)
        else:
            record_failed_login(client_ip)
            rem = is_ip_rate_limited(client_ip)
            if rem > 0:
                minutes = (rem // 60) + 1
                error = f'Çok fazla hatalı giriş denemesi yapıldı! Hesabınız geçici olarak {minutes} dakika kilitlendi.'
                return render_template('login.html', error=error), 429
            else:
                attempts_left = MAX_LOGIN_ATTEMPTS - len(_LOGIN_ATTEMPTS.get(client_ip, []))
                error = f'Kullanıcı adı veya şifre hatalı. (Kalan deneme hakkı: {attempts_left})'

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
@permission_required('muhasebe')
def muhasebe():
    return render_template('muhasebe.html', aktif_sayfa='muhasebe')

@app.route('/finans')
@permission_required('finans')
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


@app.template_filter('format_currency')
def format_currency_filter(value):
    if value is None:
        return "-"
    try:
        val = round(float(value), 2)
        if abs(val) == 0:
            val = 0.0
        formatted = f"{val:,.2f}"
        return formatted.replace(',', '_').replace('.', ',').replace('_', '.')
    except (ValueError, TypeError):
        return str(value)

import gunluk_nakit_akis

@app.template_filter('friendly_bank')
def friendly_bank_filter(value):
    return gunluk_nakit_akis.get_friendly_bank_name(value)

@app.route('/finans/cari-ekstre')
@permission_required('cari_ekstre')
def cari_ekstre():
    return render_template('cari_ekstre.html', aktif_sayfa='cari_ekstre')

@app.route('/finans/cari-kapama')
@permission_required('cari_kapama')
def cari_kapama():
    return render_template('cari_kapama.html', aktif_sayfa='cari_kapama')

@app.route('/finans/nakit-akis', methods=['GET', 'POST'], endpoint='nakit_akis')
@app.route('/dashboard', methods=['GET', 'POST'], endpoint='dashboard')
@permission_required('nakit_akis')
def nakit_akis():
    return gunluk_nakit_akis.dashboard()

# Nakit Akış Alt İşlemleri (gunluk_nakit_akis modülü üzerinden doğrudan çalışır)
@app.route('/export_eski_sablon', methods=['GET'], endpoint='export_eski_sablon_route')
@permission_required('nakit_akis')
def export_eski_sablon_route():
    return gunluk_nakit_akis.export_eski_sablon_route()

@app.route('/save_custom_payments', methods=['POST'], endpoint='save_custom_payments_route')
@permission_required('nakit_akis')
def save_custom_payments_route():
    return gunluk_nakit_akis.save_custom_payments_route()

@app.route('/toggle_payment_paid/<int:idx>', methods=['POST', 'GET'], endpoint='toggle_payment_paid_route')
@permission_required('nakit_akis')
def toggle_payment_paid_route(idx):
    return gunluk_nakit_akis.toggle_payment_paid_route(idx)

@app.route('/save_custom_payments_json', methods=['POST'], endpoint='save_custom_payments_json_route')
@permission_required('nakit_akis')
def save_custom_payments_json_route():
    return gunluk_nakit_akis.save_custom_payments_json_route()

@app.route('/toggle_credit_card_payment/<int:idx>', methods=['POST', 'GET'], endpoint='toggle_credit_card_payment_route')
@permission_required('nakit_akis')
def toggle_credit_card_payment_route(idx):
    return gunluk_nakit_akis.toggle_credit_card_payment_route(idx)

@app.route('/delete_custom_payment/<int:idx>', methods=['POST', 'GET'], endpoint='delete_custom_payment_route')
@permission_required('nakit_akis')
def delete_custom_payment_route(idx):
    return gunluk_nakit_akis.delete_custom_payment_route(idx)

@app.route('/delete_multiple_payments', methods=['POST'], endpoint='delete_multiple_payments_route')
@permission_required('nakit_akis')
def delete_multiple_payments_route():
    return gunluk_nakit_akis.delete_multiple_payments_route()

@app.route('/toggle_auto_clean', methods=['GET', 'POST'], endpoint='toggle_auto_clean_route')
@permission_required('nakit_akis')
def toggle_auto_clean_route():
    return gunluk_nakit_akis.toggle_auto_clean_route()

@app.route('/clear_past_payments', methods=['GET', 'POST'], endpoint='clear_past_payments_route')
@permission_required('nakit_akis')
def clear_past_payments_route():
    return gunluk_nakit_akis.clear_past_payments_route()

@app.route('/clear_all_payments', methods=['POST'], endpoint='clear_all_payments_route')
@permission_required('nakit_akis')
def clear_all_payments_route():
    return gunluk_nakit_akis.clear_all_payments_route()

@app.route('/save_budget', methods=['POST'], endpoint='save_budget_route')
@permission_required('nakit_akis')
def save_budget_route():
    return gunluk_nakit_akis.save_budget_route()

@app.route('/save_starting_cash', methods=['POST'], endpoint='save_starting_cash_route')
@permission_required('nakit_akis')
def save_starting_cash_route():
    return gunluk_nakit_akis.save_starting_cash_route()

@app.route('/save_assets', methods=['POST'], endpoint='save_assets_route')
@permission_required('nakit_akis')
def save_assets_route():
    return gunluk_nakit_akis.save_assets_route()

@app.route('/api/get_categories', methods=['GET'], endpoint='api_get_categories')
@permission_required('nakit_akis')
def api_get_categories():
    return gunluk_nakit_akis.api_get_categories()

@app.route('/api/add_category', methods=['POST'], endpoint='api_add_category')
@permission_required('nakit_akis')
def api_add_category():
    return gunluk_nakit_akis.api_add_category()

@app.route('/api/delete_category/<int:cat_id>', methods=['POST', 'DELETE'], endpoint='api_delete_category')
@permission_required('nakit_akis')
def api_delete_category(cat_id):
    return gunluk_nakit_akis.api_delete_category(cat_id)

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


def detect_cari_currency(cari_name="", cari_code=""):
    text_upper = f"{cari_code} {cari_name}".upper()
    if 'USD' in text_upper or 'DOLAR' in text_upper:
        return 'USD'
    elif 'EUR' in text_upper or 'EURO' in text_upper:
        return 'EUR'
    elif 'GBP' in text_upper or 'STERLİN' in text_upper or 'STERLIN' in text_upper:
        return 'GBP'
    elif 'TL' in text_upper or 'TRY' in text_upper:
        return 'TL'
    return 'TL'

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
                't_bakiye': '0,00',
                'currency': 'TL'
            })

        t_borc = float(df['BORC'].sum())
        t_alacak = float(df['ALACAK'].sum())
        t_bakiye = t_borc - t_alacak

        # Döviz cinsini tespit et (Cari ünvanından veya kodundan)
        sample_name = ""
        for _, r in df.iterrows():
            u = str(r.get('CARI_UNVAN', '') or '')
            if u and u != 'Önceki Dönemden Devir':
                sample_name = u
                break
        if not sample_name:
            sample_name = cari

        curr_code = detect_cari_currency(sample_name, cari)

        html = '<table class="ekstre-table"><thead><tr>'
        html += '<th style="width: 100px;">Tarih</th>'
        html += '<th style="width: 80px;">Özel Kod</th>'
        html += '<th style="width: 220px;">Cari Ünvan</th>'
        html += '<th style="width: 130px;">İşlem Türü</th>'
        html += '<th style="width: 100px;">Fiş No</th>'
        html += '<th style="width: auto;">Açıklama</th>'
        html += f'<th class="text-right" style="width: 110px;">Borç ({curr_code})</th>'
        html += f'<th class="text-right" style="width: 110px;">Alacak ({curr_code})</th>'
        html += f'<th class="text-right" style="width: 110px;">Bakiye ({curr_code})</th></tr></thead><tbody>'

        for _, row in df.iterrows():
            is_devir = row['CARI_UNVAN'] == 'Önceki Dönemden Devir'
            tarih_str = row['TARIH'].strftime('%d.%m.%Y') if row['TARIH'] is not None and not pd.isna(row['TARIH']) else 'DEVİR'
            row_class = ' class="devir-row"' if is_devir else ''
            
            bakiye_val = float(row['BAKIYE'])
            bakiye_color = '#60a5fa' if bakiye_val == 0 else ('#f87171' if bakiye_val > 0 else '#34d399')

            cari_unvan_raw = str(row["CARI_UNVAN"] or "-")
            cari_unvan_esc = cari_unvan_raw.replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
            
            islem_turu_raw = str(row["ISLEM_TURU"] or "-")
            islem_turu_esc = islem_turu_raw.replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
            
            fis_no_raw = str(row["FIS_NO"] or "-")
            fis_no_esc = fis_no_raw.replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
            
            aciklama_raw = str(row["ACIKLAMA"] or "-")
            aciklama_esc = aciklama_raw.replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
            
            ozel_kod_raw = str(row["OZEL_KOD"] or "-")
            ozel_kod_esc = ozel_kod_raw.replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')

            html += f'<tr{row_class}>'
            html += f'<td title="{tarih_str}"><strong style="color: #93c5fd;">{tarih_str}</strong></td>'
            html += f'<td class="cell-clickable" data-title="Özel Kod" data-text="{ozel_kod_esc}" title="{ozel_kod_esc}">{ozel_kod_esc}</td>'
            html += f'<td class="cell-clickable" data-title="Cari Ünvan" data-text="{cari_unvan_esc}" title="{cari_unvan_esc}"><span style="font-weight: 500;">{cari_unvan_esc}</span></td>'
            html += f'<td class="cell-clickable" data-title="İşlem Türü" data-text="{islem_turu_esc}" title="{islem_turu_esc}"><span class="badge-islem">{islem_turu_esc}</span></td>'
            html += f'<td class="cell-clickable" data-title="Fiş No" data-text="{fis_no_esc}" title="{fis_no_esc}"><code style="color: #cbd5e1;">{fis_no_esc}</code></td>'
            html += f'<td class="cell-clickable" data-title="Açıklama" data-text="{aciklama_esc}" title="{aciklama_esc}" style="color: var(--text-muted);">{aciklama_esc}</td>'
            html += f'<td class="text-right" style="color: #fca5a5;">{format_currency(row["BORC"])}</td>'
            html += f'<td class="text-right" style="color: #86efac;">{format_currency(row["ALACAK"])}</td>'
            html += f'<td class="text-right" style="font-weight: 700; color: {bakiye_color};">{format_currency(bakiye_val)}</td>'
            html += '</tr>'

        html += '</tbody></table>'

        return jsonify({
            'html': html,
            't_borc': format_currency(t_borc),
            't_alacak': format_currency(t_alacak),
            't_bakiye': format_currency(t_bakiye),
            'currency': curr_code
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
@permission_required('raporlar')
def raporlar():
    return render_template('raporlar.html', aktif_sayfa='raporlar')

@app.route('/parametreler')
@permission_required('parametreler')
def parametreler():
    connections = load_db_config()
    users_raw = load_users()
    # Şifre hash'lerini frontend'e göndermemek için temiz liste oluştur
    users = [
        {
            'username': u.get('username'),
            'name': u.get('name', u.get('username')),
            'role': u.get('role', 'user'),
            'allowed_pages': u.get('allowed_pages', ['index', 'muhasebe', 'cari_ekstre', 'cari_kapama', 'nakit_akis', 'finans', 'raporlar']) if u.get('role') != 'admin' else ['*']
        }
        for u in users_raw
    ]
    return render_template('parametreler.html', aktif_sayfa='parametreler', connections=connections, users=users, system_pages=ALL_SYSTEM_PAGES)

@app.route('/api/users/add', methods=['POST'])
def add_user_api():
    if 'user' not in session or session['user'].get('role') != 'admin':
        return jsonify({'success': False, 'message': 'Bu işlem için yönetici (admin) yetkisi gereklidir.'}), 403

    data = request.get_json() or {}
    username = data.get('username', '').strip().lower()
    name = data.get('name', '').strip() or username
    password = data.get('password', '')
    role = data.get('role', 'user')
    allowed_pages = data.get('allowed_pages', [])

    if not username or not password:
        return jsonify({'success': False, 'message': 'Kullanıcı adı ve şifre zorunludur.'}), 400

    if len(password) < 8:
        return jsonify({'success': False, 'message': 'Güçlü Parola Politikası: Şifre en az 8 karakter olmalıdır.'}), 400

    users = load_users()
    for u in users:
        if u.get('username', '').lower() == username:
            return jsonify({'success': False, 'message': f"'{username}' kullanıcı adı zaten kullanımda."}), 400

    if role == 'admin':
        allowed_pages = ['*']
    elif not allowed_pages:
        allowed_pages = ['index', 'muhasebe', 'cari_ekstre', 'cari_kapama', 'finans', 'raporlar']

    users.append({
        'username': username,
        'name': name,
        'password_hash': generate_password_hash(password),
        'role': role,
        'allowed_pages': allowed_pages
    })

    if save_users(users):
        return jsonify({'success': True, 'message': f"'{username}' kullanıcısı başarıyla eklendi."})
    return jsonify({'success': False, 'message': 'Kullanıcı kaydedilemedi.'}), 500

@app.route('/api/users/edit-permissions', methods=['POST'])
def edit_user_permissions_api():
    if 'user' not in session or session['user'].get('role') != 'admin':
        return jsonify({'success': False, 'message': 'Bu işlem için yönetici yetkisi gereklidir.'}), 403

    data = request.get_json() or {}
    target_username = data.get('username', '').strip().lower()
    name = data.get('name', '').strip()
    role = data.get('role', 'user')
    allowed_pages = data.get('allowed_pages', [])

    if not target_username:
        return jsonify({'success': False, 'message': 'Kullanıcı adı belirtilmedi.'}), 400

    users = load_users()
    user_found = False
    for u in users:
        if u.get('username', '').lower() == target_username:
            if name:
                u['name'] = name
            u['role'] = role
            u['allowed_pages'] = ['*'] if role == 'admin' else allowed_pages
            user_found = True
            
            # Eğer kendi oturumumuz güncelleniyorsa session'ı da güncelle
            if target_username == session['user']['username'].lower():
                session['user']['name'] = u['name']
                session['user']['role'] = role
                session['user']['allowed_pages'] = u['allowed_pages']
            break

    if user_found and save_users(users):
        return jsonify({'success': True, 'message': f"'{target_username}' kullanıcısının yetkileri güncellendi."})
    return jsonify({'success': False, 'message': 'Kullanıcı bulunamadı.'}), 404

@app.route('/api/users/reset-password', methods=['POST'])
def admin_reset_password_api():
    if 'user' not in session or session['user'].get('role') != 'admin':
        return jsonify({'success': False, 'message': 'Bu işlem için yönetici yetkisi gereklidir.'}), 403

    data = request.get_json() or {}
    target_username = data.get('username', '').strip()
    new_password = data.get('new_password', '')

    if not target_username or not new_password:
        return jsonify({'success': False, 'message': 'Kullanıcı ve yeni şifre zorunludur.'}), 400

    if len(new_password) < 8:
        return jsonify({'success': False, 'message': 'Güçlü Parola Politikası: Yeni şifre en az 8 karakter olmalıdır.'}), 400

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

    if len(new_password) < 8:
        return jsonify({'success': False, 'message': 'Güçlü Parola Politikası: Yeni şifre en az 8 karakter olmalıdır.'}), 400

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

def perform_sql_test(conn_data):
    try:
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

@app.route('/api/db-connections/test', methods=['POST'])
def test_connection_api():
    conn_data = request.get_json()
    if not conn_data:
        return jsonify({'success': False, 'message': 'Bağlantı bilgisi bulunamadı.'}), 400
    
    # Canlı ortamda (Render) BRIDGE_URL tanımlıysa testi lokal köprü üzerinden yap
    if USE_BRIDGE:
        try:
            resp = http_requests.post(
                f"{BRIDGE_URL}/bridge/test-connection",
                json=conn_data,
                headers={'X-Bridge-Key': BRIDGE_KEY},
                timeout=15
            )
            return jsonify(resp.json())
        except Exception as e:
            return jsonify({
                'success': False,
                'message': f"Lokal SQL Köprüsüne (Bridge) ulaşılamadı: {str(e)}. Bilgisayarınızda baslat.bat dosyasının açık olduğundan ve Render'daki BRIDGE_URL adresinin güncel olduğundan emin olun."
            })
    
    # Eğer Render ortamındaysa ve BRIDGE_URL tanımlı DEĞİLSE
    if os.environ.get('RENDER') or os.environ.get('DYNO'):
        return jsonify({
            'success': False,
            'message': "Canlı bulut sunucusunda (Render) yerel ağdaki 'UFUK-SERVER' SQL sunucusuna doğrudan erişilemez. Lütfen Render Dashboard -> Environment bölümünden 'BRIDGE_URL' ve 'BRIDGE_API_KEY' tanımlayın ve yerel bilgisayarınızda baslat.bat dosyasını çalıştırın."
        })

    # Lokal ortamda doğrudan test et
    return perform_sql_test(conn_data)


# =============================================================
# SQL KÖPRÜ ENDPOINTLERİ (Lokal bilgisayarda çalışır, Render buna bağlanır)
# Bu endpointler sadece doğru API key ile çalışır
# =============================================================

@app.route('/bridge/health')
def bridge_health():
    return jsonify({'status': 'ok', 'service': 'SQL Bridge'})

@app.route('/bridge/test-connection', methods=['POST'])
def bridge_test_connection():
    key = request.headers.get('X-Bridge-Key') or request.args.get('key', '')
    if key != BRIDGE_KEY:
        return jsonify({'error': 'Yetkisiz erişim'}), 401
    conn_data = request.get_json() or {}
    return perform_sql_test(conn_data)

@app.route('/bridge/cariler')
def bridge_cariler():
    key = request.headers.get('X-Bridge-Key') or request.args.get('key', '')
    if key != BRIDGE_KEY:
        return jsonify({'error': 'Yetkisiz'}), 401
    year = request.args.get('year', '2026')
    try:
        engine = get_engine(1)
        cards_q, _, _ = get_cari_queries(year)
        with engine.connect() as conn:
            cariler = pd.read_sql(text(cards_q), conn).to_dict(orient='records')
        return jsonify(cariler)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/bridge/cari-ekstre')
def bridge_cari_ekstre():
    key = request.headers.get('X-Bridge-Key') or request.args.get('key', '')
    if key != BRIDGE_KEY:
        return jsonify({'error': 'Yetkisiz'}), 401
    year = request.args.get('year', '2026')
    date_val = request.args.get('date', f'{year}-01-01')
    cari = request.args.get('cari', '').strip()
    if not cari:
        return jsonify({'error': 'Cari kodu eksik'}), 400
    try:
        engine = get_engine(1)
        _, base_q, devir_q = get_cari_queries(year)
        params = {'cari_code': cari, 'date': date_val}
        with engine.connect() as conn:
            devir_res = conn.execute(text(devir_q), params).fetchone()
            devir = float(devir_res[0] or 0) if devir_res and devir_res[0] is not None else 0.0
            df = pd.read_sql(text(base_q), conn, params=params)
        if 'TARIH' in df.columns:
            df['TARIH'] = df['TARIH'].astype(str)
        return jsonify({'rows': df.to_dict(orient='records'), 'devir': devir})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/bridge-status')
def api_bridge_status():
    """Render tarafından bridge erişimini test eder."""
    import os as _os
    bridge_url = _os.environ.get('BRIDGE_URL', '').rstrip('/')
    bridge_key = _os.environ.get('BRIDGE_API_KEY', '')
    
    status = {
        'bridge_url_configured': bool(bridge_url),
        'bridge_url': bridge_url or '(ayarlanmamış)',
        'use_bridge': bool(bridge_url),
    }
    
    if bridge_url:
        # Health check
        try:
            r = http_requests.get(
                f"{bridge_url}/bridge/health",
                headers={'ngrok-skip-browser-warning': 'true', 'User-Agent': 'NexlogBridgeClient/1.0'},
                timeout=10
            )
            status['health_check'] = r.json() if r.status_code == 200 else f"HTTP {r.status_code}"
        except Exception as e:
            status['health_check'] = f"BAĞLANAMADI: {str(e)}"
        
        # Debug check
        try:
            r2 = http_requests.get(
                f"{bridge_url}/bridge/nakit/debug",
                headers={
                    'X-Bridge-Key': bridge_key,
                    'ngrok-skip-browser-warning': 'true',
                    'User-Agent': 'NexlogBridgeClient/1.0'
                },
                timeout=30
            )
            status['nakit_debug'] = r2.json() if r2.status_code == 200 else f"HTTP {r2.status_code}"
        except Exception as e:
            status['nakit_debug'] = f"BAĞLANAMADI: {str(e)}"
    
    return jsonify(status)

# ============================================================
# NAKİT AKIŞ KÖPRÜ ENDPOINTLERİ (app.py port 5000 üzerinden de çalışır)
# ============================================================

Firma_Bridge = "226"
Donem_Bridge = "01"

def df_to_json_safe_app(df):
    if df.empty:
        return []
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].astype(str)
    return df.where(pd.notnull(df), None).to_dict(orient='records')

@app.route('/bridge/nakit/own-checks')
def bridge_nakit_own_checks():
    key = request.headers.get('X-Bridge-Key') or request.args.get('key', '')
    if key != BRIDGE_KEY:
        return jsonify({'error': 'Yetkisiz erişim'}), 401
    target_date = request.args.get('date', pd.Timestamp.now().strftime('%Y-%m-%d'))
    limit_date = request.args.get('limit_date', 'true').lower() == 'true'
    
    date_filter = f"AND CONVERT(VARCHAR, CSC.DUEDATE, 23) <= '{target_date}'" if limit_date else ""
    query = f"""
    SELECT 
        (SELECT TOP 1 CL.DEFINITION_ 
         FROM LG_{Firma_Bridge}_CLCARD CL WITH(NOLOCK)
         JOIN LG_{Firma_Bridge}_{Donem_Bridge}_CSTRANS CS WITH(NOLOCK) ON CL.LOGICALREF = CS.CARDREF 
         WHERE CS.CSREF = CSC.LOGICALREF ORDER BY CS.LOGICALREF ASC) AS [CH_UNVANI], 
        CSC.BANKNAME AS [BANKA], 
        CONVERT(VARCHAR(10), CSC.DUEDATE, 23) AS [VADE], 
        CSC.AMOUNT AS [TUTAR],
        CSC.TRCURR AS [DOVIZ_TIPI]
    FROM LG_{Firma_Bridge}_{Donem_Bridge}_CSCARD CSC WITH(NOLOCK)
    WHERE CSC.DOC IN (3,4) AND CSC.CURRSTAT NOT IN (8, 6) {date_filter}
    ORDER BY CSC.DUEDATE ASC
    """
    try:
        engine = get_engine(1)
        with engine.connect() as conn:
            df = pd.read_sql(text(query), conn)
        return jsonify({'rows': df_to_json_safe_app(df)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/bridge/nakit/customer-checks')
def bridge_nakit_customer_checks():
    key = request.headers.get('X-Bridge-Key') or request.args.get('key', '')
    if key != BRIDGE_KEY:
        return jsonify({'error': 'Yetkisiz erişim'}), 401
    target_date = request.args.get('date', pd.Timestamp.now().strftime('%Y-%m-%d'))
    limit_date = request.args.get('limit_date', 'true').lower() == 'true'
    
    date_filter = f"AND CONVERT(VARCHAR, CSC.DUEDATE, 23) <= '{target_date}'" if limit_date else ""
    query = f"""
    SELECT 
        (SELECT TOP 1 CL.DEFINITION_ FROM LG_{Firma_Bridge}_CLCARD CL WITH(NOLOCK)
         JOIN LG_{Firma_Bridge}_{Donem_Bridge}_CSTRANS CS WITH(NOLOCK) ON CL.LOGICALREF = CS.CARDREF 
         WHERE CS.CSREF = CSC.LOGICALREF AND CS.TRCODE = 1) AS [CH_UNVANI], 
        CSC.BANKNAME AS [BANKA], 
        (SELECT TOP 1 BA.DEFINITION_ FROM LG_{Firma_Bridge}_{Donem_Bridge}_CSTRANS CST WITH(NOLOCK)
         JOIN LG_{Firma_Bridge}_BANKACC BA WITH(NOLOCK) ON BA.LOGICALREF = CST.CARDREF
         WHERE CST.CSREF = CSC.LOGICALREF AND CST.STATUS = CSC.CURRSTAT AND CST.CARDMD = 7
         ORDER BY CST.LOGICALREF DESC) AS [OUR_BANK],
        CSC.NEWSERINO AS [CEK_NO],
        CONVERT(VARCHAR(10), CSC.DUEDATE, 23) AS [VADE], 
        CSC.AMOUNT AS [TUTAR],
        CSC.TRCURR AS [DOVIZ_TIPI],
        CSC.CURRSTAT AS [DURUM_KODU],
        CASE CSC.CURRSTAT
            WHEN 1 THEN 'Portföyde' WHEN 2 THEN 'Ciro Edildi'
            WHEN 3 THEN 'Teminata Verildi' WHEN 4 THEN 'Tahsile Verildi'
            WHEN 5 THEN 'Protestolu Tahsile Verildi' WHEN 6 THEN 'İade Edildi'
            WHEN 7 THEN 'Protesto Edildi' WHEN 8 THEN 'Tahsil Edildi'
            WHEN 9 THEN 'Kendi Çekimiz' WHEN 10 THEN 'Borç Senedimiz'
            WHEN 11 THEN 'Karşılıksız' WHEN 12 THEN 'Tahsil Edilemiyor'
            ELSE 'Bilinmeyen'
        END AS [DURUM]
    FROM LG_{Firma_Bridge}_{Donem_Bridge}_CSCARD CSC WITH(NOLOCK)
    WHERE CSC.DOC = 1 AND CSC.CURRSTAT NOT IN (2, 6, 8) {date_filter}
    ORDER BY CSC.DUEDATE ASC
    """
    try:
        engine = get_engine(1)
        with engine.connect() as conn:
            df = pd.read_sql(text(query), conn)
        return jsonify({'rows': df_to_json_safe_app(df)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/bridge/nakit/credits')
def bridge_nakit_credits():
    key = request.headers.get('X-Bridge-Key') or request.args.get('key', '')
    if key != BRIDGE_KEY:
        return jsonify({'error': 'Yetkisiz erişim'}), 401
    target_date = request.args.get('date', pd.Timestamp.now().strftime('%Y-%m-%d'))
    limit_date = request.args.get('limit_date', 'true').lower() == 'true'
    
    date_filter = f"WHERE CONVERT(VARCHAR, P.DUEDATE, 23) <= '{target_date}'" if limit_date else ""
    query = f"""
    SELECT C.NAME_ AS [BANKA_KREDI],
        CONVERT(VARCHAR(10), P.DUEDATE, 23) AS [VADE],
        MAX(P.TOTAL) AS [ANAPARA], MAX(P.INTTOTAL) AS [FAIZ],
        MAX(P.TOTAL + P.INTTOTAL) AS [TUTAR], MAX(C.TRCURR) AS [DOVIZ_TIPI]
    FROM LG_{Firma_Bridge}_BNCREPAYTR P WITH(NOLOCK)
    LEFT JOIN LG_{Firma_Bridge}_BNCREDITCARD C WITH(NOLOCK) ON C.LOGICALREF = P.CREDITREF
    {date_filter}
    GROUP BY C.CODE, C.NAME_, P.DUEDATE
    HAVING MAX(P.TRANSTYPE) <> 1
    ORDER BY P.DUEDATE ASC
    """
    try:
        engine = get_engine(1)
        with engine.connect() as conn:
            df = pd.read_sql(text(query), conn)
        return jsonify({'rows': df_to_json_safe_app(df)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/bridge/nakit/credit-cards')
def bridge_nakit_credit_cards():
    key = request.headers.get('X-Bridge-Key') or request.args.get('key', '')
    if key != BRIDGE_KEY:
        return jsonify({'error': 'Yetkisiz erişim'}), 401
    target_date = request.args.get('date', pd.Timestamp.now().strftime('%Y-%m-%d'))
    limit_date = request.args.get('limit_date', 'true').lower() == 'true'
    
    date_filter = f"AND CONVERT(VARCHAR, KSV.MAX_DATE, 23) <= '{target_date}'" if limit_date else ""
    query = f"""
    WITH KartSonVade AS (
        SELECT B.LOGICALREF AS BNACCREF, MAX(ISNULL(P.DATE_, F.DATE_)) AS MAX_DATE
        FROM LG_{Firma_Bridge}_{Donem_Bridge}_BNFLINE F WITH(NOLOCK)
        LEFT JOIN LG_{Firma_Bridge}_{Donem_Bridge}_PAYTRANS P WITH(NOLOCK) ON P.LOGICALREF = F.SOURCEFREF
        LEFT JOIN LG_{Firma_Bridge}_BANKACC B WITH(NOLOCK) ON B.LOGICALREF = F.BNACCREF
        WHERE B.CODE LIKE '50.%' AND F.CANCELLED = 0
        GROUP BY B.LOGICALREF
    )
    SELECT B.DEFINITION_ AS [KARTI_ADI],
        CONVERT(VARCHAR(10), KSV.MAX_DATE, 23) AS [VADE],
        SUM(CASE WHEN F.SIGN = 0 THEN F.AMOUNT ELSE -F.AMOUNT END) AS [TUTAR]
    FROM LG_{Firma_Bridge}_{Donem_Bridge}_BNFLINE F WITH(NOLOCK)
    JOIN KartSonVade KSV ON KSV.BNACCREF = F.BNACCREF
    LEFT JOIN LG_{Firma_Bridge}_BANKACC B WITH(NOLOCK) ON B.LOGICALREF = F.BNACCREF
    WHERE F.CANCELLED = 0 {date_filter}
    GROUP BY B.DEFINITION_, KSV.MAX_DATE
    HAVING SUM(CASE WHEN F.SIGN = 0 THEN F.AMOUNT ELSE -F.AMOUNT END) > 0.01
    ORDER BY KSV.MAX_DATE ASC
    """
    try:
        engine = get_engine(1)
        with engine.connect() as conn:
            df = pd.read_sql(text(query), conn)
        return jsonify({'rows': df_to_json_safe_app(df)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/bridge/nakit/bank-balances')
def bridge_nakit_bank_balances():
    key = request.headers.get('X-Bridge-Key') or request.args.get('key', '')
    if key != BRIDGE_KEY:
        return jsonify({'error': 'Yetkisiz erişim'}), 401
    query = f"""
    SELECT BC.DEFINITION_ AS BANKA_ADI, BA.DEFINITION_ AS HESAP_ADI,
        BA.IBAN AS IBAN, BA.CURRENCY AS DOVIZ_TIPI,
        CASE WHEN BA.CURRENCY = 0 THEN ROUND(ISNULL(SUM(CASE BN.SIGN WHEN 0 THEN BN.AMOUNT ELSE -BN.AMOUNT END), 0), 2)
             ELSE ROUND(ISNULL(SUM(CASE BN.SIGN WHEN 0 THEN BN.TRNET ELSE -BN.TRNET END), 0), 2) END AS BAKIYE
    FROM LG_{Firma_Bridge}_BANKACC BA WITH(NOLOCK)
    INNER JOIN LG_{Firma_Bridge}_BNCARD BC WITH(NOLOCK) ON BC.LOGICALREF = BA.BANKREF
    LEFT JOIN LG_{Firma_Bridge}_{Donem_Bridge}_BNFLINE BN WITH(NOLOCK) ON BN.BNACCREF = BA.LOGICALREF AND BN.TRANSTYPE = 1 
    WHERE BA.ACTIVE = 0 AND BA.CARDTYPE IN (1, 3) 
    GROUP BY BA.CODE, BA.DEFINITION_, BC.CODE, BC.DEFINITION_, BA.CARDTYPE, BA.CURRENCY, BA.IBAN
    HAVING ROUND(ISNULL(SUM(CASE BN.SIGN WHEN 0 THEN BN.AMOUNT ELSE -BN.AMOUNT END), 0), 2) <> 0
        OR ROUND(ISNULL(SUM(CASE WHEN BA.CURRENCY = 0 THEN 0 ELSE (CASE BN.SIGN WHEN 0 THEN BN.TRNET ELSE -BN.TRNET END) END), 0), 2) <> 0
    ORDER BY BA.CODE
    """
    try:
        engine = get_engine(1)
        with engine.connect() as conn:
            df = pd.read_sql(text(query), conn)
        return jsonify({'rows': df_to_json_safe_app(df)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/bridge/nakit/kasa-balances')
def bridge_nakit_kasa_balances():
    key = request.headers.get('X-Bridge-Key') or request.args.get('key', '')
    if key != BRIDGE_KEY:
        return jsonify({'error': 'Yetkisiz erişim'}), 401
    target_date = request.args.get('date', '')
    date_filter = f"AND CONVERT(VARCHAR, KS.DATE_, 23) <= '{target_date}'" if target_date else ""
    query = f"""
    SELECT K.CCURRENCY AS DOVIZ_TIPI,
        CASE WHEN K.CCURRENCY = 0 THEN ROUND(ISNULL(SUM(CASE WHEN KS.SIGN = 0 THEN KS.AMOUNT ELSE -KS.AMOUNT END), 0), 2)
             ELSE ROUND(ISNULL(SUM(CASE WHEN KS.SIGN = 0 THEN KS.TRNET ELSE -KS.TRNET END), 0), 2) END AS BAKIYE
    FROM LG_{Firma_Bridge}_KSCARD K WITH(NOLOCK)
    LEFT JOIN LG_{Firma_Bridge}_{Donem_Bridge}_KSLINES KS WITH(NOLOCK) ON KS.CARDREF = K.LOGICALREF AND KS.CANCELLED = 0 {date_filter}
    WHERE K.ACTIVE = 0 AND (K.CODE LIKE '100.01.%' OR K.NAME LIKE '%MERKEZ%')
    GROUP BY K.CCURRENCY
    """
    try:
        engine = get_engine(1)
        with engine.connect() as conn:
            df = pd.read_sql(text(query), conn)
        return jsonify({'rows': df_to_json_safe_app(df)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/bridge/nakit/receivables')
def bridge_nakit_receivables():
    key = request.headers.get('X-Bridge-Key') or request.args.get('key', '')
    if key != BRIDGE_KEY:
        return jsonify({'error': 'Yetkisiz erişim'}), 401
    query = f"""
    SELECT
        SUM(CASE WHEN CL.CODE LIKE '120.01.%' THEN (CASE WHEN CLF.SIGN = 0 THEN CLF.AMOUNT ELSE -CLF.AMOUNT END) ELSE 0 END) AS BAKIYE_TL,
        SUM(CASE WHEN CL.CODE LIKE '120.05.%' AND CLF.TRCURR = 1 THEN (CASE WHEN CLF.SIGN = 0 THEN CLF.TRNET ELSE -CLF.TRNET END) ELSE 0 END) AS BAKIYE_USD,
        SUM(CASE WHEN CL.CODE LIKE '120.05.%' AND CLF.TRCURR = 20 THEN (CASE WHEN CLF.SIGN = 0 THEN CLF.TRNET ELSE -CLF.TRNET END) ELSE 0 END) AS BAKIYE_EUR
    FROM LG_{Firma_Bridge}_CLCARD CL WITH(NOLOCK)
    INNER JOIN LG_{Firma_Bridge}_{Donem_Bridge}_CLFLINE CLF WITH(NOLOCK) ON CLF.CLIENTREF = CL.LOGICALREF
    WHERE CLF.CANCELLED = 0 AND CL.ACTIVE = 0 AND (CL.CODE LIKE '120.01.%' OR CL.CODE LIKE '120.05.%')
    """
    try:
        engine = get_engine(1)
        with engine.connect() as conn:
            df = pd.read_sql(text(query), conn)
        if not df.empty:
            return jsonify({'TL': float(df.iloc[0]['BAKIYE_TL'] or 0.0), 'USD': float(df.iloc[0]['BAKIYE_USD'] or 0.0), 'EUR': float(df.iloc[0]['BAKIYE_EUR'] or 0.0)})
        return jsonify({'TL': 0.0, 'USD': 0.0, 'EUR': 0.0})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/bridge/nakit/payables')
def bridge_nakit_payables():
    key = request.headers.get('X-Bridge-Key') or request.args.get('key', '')
    if key != BRIDGE_KEY:
        return jsonify({'error': 'Yetkisiz erişim'}), 401
    query = f"""
    SELECT
        SUM(CASE WHEN CL.CODE LIKE '320.01.%' THEN (CASE WHEN CLF.SIGN = 1 THEN CLF.AMOUNT ELSE -CLF.AMOUNT END) ELSE 0 END) AS AKARYAKIT_TL,
        SUM(CASE WHEN CL.CODE LIKE '320.02.%' THEN (CASE WHEN CLF.SIGN = 1 THEN CLF.AMOUNT ELSE -CLF.AMOUNT END) ELSE 0 END) AS SIGORTA_TL,
        SUM(CASE WHEN (CL.CODE LIKE '320.03.%' OR CL.CODE LIKE '320.04.%') THEN (CASE WHEN CLF.SIGN = 1 THEN CLF.AMOUNT ELSE -CLF.AMOUNT END) ELSE 0 END) AS SANAYI_TL,
        SUM(CASE WHEN (CL.CODE LIKE '320.05.%' OR CL.CODE LIKE '320.06.%') THEN (CASE WHEN CLF.SIGN = 1 THEN CLF.AMOUNT ELSE -CLF.AMOUNT END) ELSE 0 END) AS NAVLUN_TL,
        SUM(CASE WHEN (CL.CODE LIKE '320.01.%') AND CLF.TRCURR = 1 THEN (CASE WHEN CLF.SIGN = 1 THEN CLF.TRNET ELSE -CLF.TRNET END) ELSE 0 END) AS AKARYAKIT_USD,
        SUM(CASE WHEN (CL.CODE LIKE '320.02.%') AND CLF.TRCURR = 1 THEN (CASE WHEN CLF.SIGN = 1 THEN CLF.TRNET ELSE -CLF.TRNET END) ELSE 0 END) AS SIGORTA_USD,
        SUM(CASE WHEN (CL.CODE LIKE '320.03.%' OR CL.CODE LIKE '320.04.%') AND CLF.TRCURR = 1 THEN (CASE WHEN CLF.SIGN = 1 THEN CLF.TRNET ELSE -CLF.TRNET END) ELSE 0 END) AS SANAYI_USD,
        SUM(CASE WHEN (CL.CODE LIKE '320.05.%' OR CL.CODE LIKE '320.06.%') AND CLF.TRCURR = 1 THEN (CASE WHEN CLF.SIGN = 1 THEN CLF.TRNET ELSE -CLF.TRNET END) ELSE 0 END) AS NAVLUN_USD,
        SUM(CASE WHEN (CL.CODE LIKE '320.01.%') AND CLF.TRCURR = 20 THEN (CASE WHEN CLF.SIGN = 1 THEN CLF.TRNET ELSE -CLF.TRNET END) ELSE 0 END) AS AKARYAKIT_EUR,
        SUM(CASE WHEN (CL.CODE LIKE '320.02.%') AND CLF.TRCURR = 20 THEN (CASE WHEN CLF.SIGN = 1 THEN CLF.TRNET ELSE -CLF.TRNET END) ELSE 0 END) AS SIGORTA_EUR,
        SUM(CASE WHEN (CL.CODE LIKE '320.03.%' OR CL.CODE LIKE '320.04.%') AND CLF.TRCURR = 20 THEN (CASE WHEN CLF.SIGN = 1 THEN CLF.TRNET ELSE -CLF.TRNET END) ELSE 0 END) AS SANAYI_EUR,
        SUM(CASE WHEN (CL.CODE LIKE '320.05.%' OR CL.CODE LIKE '320.06.%') AND CLF.TRCURR = 20 THEN (CASE WHEN CLF.SIGN = 1 THEN CLF.TRNET ELSE -CLF.TRNET END) ELSE 0 END) AS NAVLUN_EUR
    FROM LG_{Firma_Bridge}_CLCARD CL WITH(NOLOCK)
    INNER JOIN LG_{Firma_Bridge}_{Donem_Bridge}_CLFLINE CLF WITH(NOLOCK) ON CLF.CLIENTREF = CL.LOGICALREF
    WHERE CLF.CANCELLED = 0 AND CL.ACTIVE = 0 AND (
        CL.CODE LIKE '320.01.%' OR CL.CODE LIKE '320.02.%' OR CL.CODE LIKE '320.03.%' OR
        CL.CODE LIKE '320.04.%' OR CL.CODE LIKE '320.05.%' OR CL.CODE LIKE '320.06.%')
    """
    empty_val = {'TL': 0.0, 'USD': 0.0, 'EUR': 0.0}
    try:
        engine = get_engine(1)
        with engine.connect() as conn:
            df = pd.read_sql(text(query), conn)
        if not df.empty:
            r = df.iloc[0]
            return jsonify({
                'akaryakit': {'TL': float(r['AKARYAKIT_TL'] or 0.0), 'USD': float(r['AKARYAKIT_USD'] or 0.0), 'EUR': float(r['AKARYAKIT_EUR'] or 0.0)},
                'sigorta':   {'TL': float(r['SIGORTA_TL'] or 0.0),   'USD': float(r['SIGORTA_USD'] or 0.0),   'EUR': float(r['SIGORTA_EUR'] or 0.0)},
                'sanayi':    {'TL': float(r['SANAYI_TL'] or 0.0),    'USD': float(r['SANAYI_USD'] or 0.0),    'EUR': float(r['SANAYI_EUR'] or 0.0)},
                'navlun':    {'TL': float(r['NAVLUN_TL'] or 0.0),    'USD': float(r['NAVLUN_USD'] or 0.0),    'EUR': float(r['NAVLUN_EUR'] or 0.0)}
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'akaryakit': empty_val, 'sigorta': empty_val, 'sanayi': empty_val, 'navlun': empty_val})

@app.route('/bridge/nakit/virman')
def bridge_nakit_virman():
    key = request.headers.get('X-Bridge-Key') or request.args.get('key', '')
    if key != BRIDGE_KEY:
        return jsonify({'error': 'Yetkisiz erişim'}), 401
    target_date = request.args.get('date', pd.Timestamp.now().strftime('%Y-%m-%d'))
    query = f"""
    SELECT BNFLINE.LOGICALREF, BNFLINE.SOURCEFREF, BNFICHE.FICHENO,
        CONVERT(VARCHAR(10), BNFLINE.DATE_, 23) AS TARIH, BNFLINE.SIGN,
        BNFLINE.AMOUNT AS TL_TUTAR, BNFLINE.TRNET AS DOVIZLI_TUTAR,
        CASE BANKACC.CURRENCY WHEN 0 THEN 'TL' WHEN 1 THEN 'USD' WHEN 20 THEN 'EUR' ELSE ISNULL(L_CURRENCYLIST.CURCODE, '') END AS HESAP_DOVIZI_RAPOR,
        BANKACC.DEFINITION_ AS HESAP_ACIKLAMASI, BANKACC.CODE AS HESAP_KODU, BANKACC.ACCOUNTNO AS HESAP_NO,
        BNFLINE.LINEEXP AS SATIR_ACIKLAMASI,
        ISNULL(BNFICHE.GENEXP1, '') +' '+ ISNULL(BNFICHE.GENEXP2, '') AS FIS_ACIKLAMASI
    FROM LG_{Firma_Bridge}_{Donem_Bridge}_BNFLINE BNFLINE WITH(NOLOCK)
    LEFT JOIN LG_{Firma_Bridge}_{Donem_Bridge}_BNFICHE BNFICHE WITH(NOLOCK) ON BNFICHE.LOGICALREF = BNFLINE.SOURCEFREF
    LEFT JOIN LG_{Firma_Bridge}_BANKACC BANKACC WITH(NOLOCK) ON BANKACC.LOGICALREF = BNFLINE.BNACCREF
    LEFT OUTER JOIN L_CURRENCYLIST WITH(NOLOCK) ON L_CURRENCYLIST.CURTYPE = BANKACC.CURRENCY
    WHERE BNFLINE.MODULENR = 7 AND BNFLINE.TRCODE = 2
      AND NOT (BANKACC.CODE LIKE '50.%' OR BANKACC.CARDTYPE IN (5, 6))
      AND CONVERT(VARCHAR, BNFLINE.DATE_, 23) = '{target_date}'
    ORDER BY BNFLINE.SOURCEFREF, BNFLINE.SIGN
    """
    try:
        engine = get_engine(1)
        with engine.connect() as conn:
            df = pd.read_sql(text(query), conn)
        return jsonify({'rows': df_to_json_safe_app(df)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/bridge/nakit/incoming')
def bridge_nakit_incoming():
    key = request.headers.get('X-Bridge-Key') or request.args.get('key', '')
    if key != BRIDGE_KEY:
        return jsonify({'error': 'Yetkisiz erişim'}), 401
    target_date = request.args.get('date', pd.Timestamp.now().strftime('%Y-%m-%d'))
    filter_virman = request.args.get('filter_virman', 'false').lower() == 'true'
    
    virman_bank_filter = "AND BNFLINE.TRCODE NOT IN (2)" if filter_virman else ""
    virman_kasa_filter = "AND K.TRCODE NOT IN (61, 62, 63)" if filter_virman else ""
    query = f"""
    SELECT DISTINCT BNFLINE.LOGICALREF,
        CASE BANKACC.CARDTYPE WHEN 1 THEN 'Banka Ticari' WHEN 2 THEN 'Banka Kredi'
            WHEN 3 THEN 'Banka Dövizli Ticari' WHEN 4 THEN 'Banka Dövizli Kredi'
            WHEN 5 THEN 'Banka Kredi Kartı' WHEN 6 THEN 'Banka Dövizli Kredi Kartı' ELSE 'Banka' END AS HESAP_TURU_RAPOR,
        BANKACC.CODE AS HESAP_KODU, BANKACC.DEFINITION_ AS HESAP_ACIKLAMASI,
        BANKACC.ACCOUNTNO AS HESAP_NO,
        CASE BANKACC.CURRENCY WHEN 0 THEN 'TL' WHEN 1 THEN 'USD' WHEN 20 THEN 'EUR' ELSE ISNULL(L_CURRENCYLIST.CURCODE, '') END AS HESAP_DOVIZI_RAPOR,
        BNFICHE.FICHENO, CONVERT(VARCHAR(10), BNFLINE.DATE_, 23) AS TARIH,
        BNFLINE.LINEEXP AS SATIR_ACIKLAMASI,
        ISNULL(BNFICHE.GENEXP1, '') +' '+ ISNULL(BNFICHE.GENEXP2, '') +' '+ ISNULL(BNFICHE.GENEXP3, '') +' '+ ISNULL(BNFICHE.GENEXP4, '') AS FIS_ACIKLAMASI,
        ISNULL((CASE BNFLINE.SIGN WHEN 0 THEN BNFLINE.TRNET ELSE BNFLINE.TRNET * (-1) END),0) AS DOVIZLI_TUTAR,
        ISNULL((CASE BNFLINE.SIGN WHEN 0 THEN BNFLINE.AMOUNT ELSE BNFLINE.AMOUNT * (-1) END),0) AS TL_TUTAR,
        CL.CODE AS CARI_KOD, CL.DEFINITION_ AS CARI_UNVAN,
        CASE WHEN BANKACC.CODE LIKE '50.%' OR BANKACC.CARDTYPE IN (5, 6) THEN
                CASE BNFLINE.TRCODE WHEN 1 THEN 'Kredi Kartı Harcaması' WHEN 2 THEN 'Kredi Kartı Ödemesi' ELSE 'Kredi Kartı İşlemi' END
             ELSE ISNULL([dbo].[fn_trcode] ('Bnfiche', BNFLINE.TRCODE, '', ''), 'Banka İşlemi') END AS FIS_TURU
    FROM LG_{Firma_Bridge}_{Donem_Bridge}_BNFLINE BNFLINE WITH(NOLOCK)
    LEFT OUTER JOIN LG_{Firma_Bridge}_{Donem_Bridge}_BNFICHE BNFICHE WITH(NOLOCK) ON BNFICHE.LOGICALREF=BNFLINE.SOURCEFREF AND BNFLINE.MODULENR=7 AND BNFLINE.TRCODE=BNFICHE.TRCODE
    LEFT OUTER JOIN LG_{Firma_Bridge}_BANKACC BANKACC WITH(NOLOCK) ON BNFLINE.BNACCREF=BANKACC.LOGICALREF
    LEFT JOIN LG_{Firma_Bridge}_CLCARD CL WITH(NOLOCK) ON BNFLINE.CLIENTREF=CL.LOGICALREF
    LEFT OUTER JOIN L_CURRENCYLIST WITH(NOLOCK) ON L_CURRENCYLIST.CURTYPE=BANKACC.CURRENCY
    WHERE BNFLINE.SIGN = 0 AND NOT (BANKACC.CODE LIKE '50.%' OR BANKACC.CARDTYPE IN (5, 6))
      {virman_bank_filter} AND CONVERT(VARCHAR, BNFLINE.DATE_, 23) = '{target_date}'
    UNION ALL
    SELECT K.LOGICALREF, 'Kasa' AS HESAP_TURU_RAPOR, KS.CODE AS HESAP_KODU, KS.NAME AS HESAP_ACIKLAMASI,
        'Kasa' AS HESAP_NO,
        CASE K.TRCURR WHEN 0 THEN 'TL' WHEN 1 THEN 'USD' WHEN 20 THEN 'EUR' ELSE ISNULL(L_CURRENCYLIST.CURCODE, '') END AS HESAP_DOVIZI_RAPOR,
        K.FICHENO, CONVERT(VARCHAR(10), K.DATE_, 23) AS TARIH, K.LINEEXP AS SATIR_ACIKLAMASI, '' AS FIS_ACIKLAMASI,
        ISNULL(K.TRNET, 0) AS DOVIZLI_TUTAR, ISNULL(K.AMOUNT, 0) AS TL_TUTAR,
        CL.CODE AS CARI_KOD, ISNULL(NULLIF(CL.DEFINITION_, ''), ISNULL(NULLIF(K.CUSTTITLE, ''), '')) AS CARI_UNVAN,
        [dbo].[fn_trcode] ('Kslines', K.TRCODE, '', '') AS FIS_TURU
    FROM LG_{Firma_Bridge}_{Donem_Bridge}_KSLINES K WITH(NOLOCK)
    LEFT JOIN LG_{Firma_Bridge}_KSCARD KS WITH(NOLOCK) ON KS.LOGICALREF = K.CARDREF
    LEFT JOIN LG_{Firma_Bridge}_CLCARD CL WITH(NOLOCK) ON K.VCARDREF = CL.LOGICALREF
    LEFT OUTER JOIN L_CURRENCYLIST WITH(NOLOCK) ON L_CURRENCYLIST.CURTYPE = K.TRCURR
    WHERE K.SIGN = 0 AND K.CANCELLED = 0 {virman_kasa_filter} AND CONVERT(VARCHAR, K.DATE_, 23) = '{target_date}'
    ORDER BY LOGICALREF
    """
    try:
        engine = get_engine(1)
        with engine.connect() as conn:
            df = pd.read_sql(text(query), conn)
        return jsonify({'rows': df_to_json_safe_app(df)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/bridge/nakit/outgoing')
def bridge_nakit_outgoing():
    key = request.headers.get('X-Bridge-Key') or request.args.get('key', '')
    if key != BRIDGE_KEY:
        return jsonify({'error': 'Yetkisiz erişim'}), 401
    target_date = request.args.get('date', pd.Timestamp.now().strftime('%Y-%m-%d'))
    filter_virman = request.args.get('filter_virman', 'false').lower() == 'true'
    
    virman_bank_filter = "AND BNFLINE.TRCODE NOT IN (2)" if filter_virman else ""
    virman_kasa_filter = "AND K.TRCODE NOT IN (61, 62, 63)" if filter_virman else ""
    query = f"""
    SELECT DISTINCT BNFLINE.LOGICALREF,
        CASE BANKACC.CARDTYPE WHEN 1 THEN 'Banka Ticari' WHEN 2 THEN 'Banka Kredi'
            WHEN 3 THEN 'Banka Dövizli Ticari' WHEN 4 THEN 'Banka Dövizli Kredi'
            WHEN 5 THEN 'Banka Kredi Kartı' WHEN 6 THEN 'Banka Dövizli Kredi Kartı' ELSE 'Banka' END AS HESAP_TURU_RAPOR,
        BANKACC.CODE AS HESAP_KODU, BANKACC.DEFINITION_ AS HESAP_ACIKLAMASI,
        BANKACC.ACCOUNTNO AS HESAP_NO,
        CASE BANKACC.CURRENCY WHEN 0 THEN 'TL' WHEN 1 THEN 'USD' WHEN 20 THEN 'EUR' ELSE ISNULL(L_CURRENCYLIST.CURCODE, '') END AS HESAP_DOVIZI_RAPOR,
        BNFICHE.FICHENO, CONVERT(VARCHAR(10), BNFLINE.DATE_, 23) AS TARIH,
        BNFLINE.LINEEXP AS SATIR_ACIKLAMASI,
        ISNULL(BNFICHE.GENEXP1, '') +' '+ ISNULL(BNFICHE.GENEXP2, '') +' '+ ISNULL(BNFICHE.GENEXP3, '') +' '+ ISNULL(BNFICHE.GENEXP4, '') AS FIS_ACIKLAMASI,
        ISNULL((CASE BNFLINE.SIGN WHEN 0 THEN BNFLINE.TRNET ELSE BNFLINE.TRNET * (-1) END),0) AS DOVIZLI_TUTAR,
        ISNULL((CASE BNFLINE.SIGN WHEN 0 THEN BNFLINE.AMOUNT ELSE BNFLINE.AMOUNT * (-1) END),0) AS TL_TUTAR,
        CL.CODE AS CARI_KOD, CL.DEFINITION_ AS CARI_UNVAN,
        CASE WHEN BANKACC.CODE LIKE '50.%' OR BANKACC.CARDTYPE IN (5, 6) THEN
                CASE BNFLINE.TRCODE WHEN 1 THEN 'Kredi Kartı Harcaması' WHEN 2 THEN 'Kredi Kartı Ödemesi' ELSE 'Kredi Kartı İşlemi' END
             ELSE ISNULL([dbo].[fn_trcode] ('Bnfiche', BNFLINE.TRCODE, '', ''), 'Banka İşlemi') END AS FIS_TURU
    FROM LG_{Firma_Bridge}_{Donem_Bridge}_BNFLINE BNFLINE WITH(NOLOCK)
    LEFT OUTER JOIN LG_{Firma_Bridge}_{Donem_Bridge}_BNFICHE BNFICHE WITH(NOLOCK) ON BNFICHE.LOGICALREF=BNFLINE.SOURCEFREF AND BNFLINE.MODULENR=7 AND BNFLINE.TRCODE=BNFICHE.TRCODE
    LEFT OUTER JOIN LG_{Firma_Bridge}_BANKACC BANKACC WITH(NOLOCK) ON BNFLINE.BNACCREF=BANKACC.LOGICALREF
    LEFT JOIN LG_{Firma_Bridge}_CLCARD CL WITH(NOLOCK) ON BNFLINE.CLIENTREF=CL.LOGICALREF
    LEFT OUTER JOIN L_CURRENCYLIST WITH(NOLOCK) ON L_CURRENCYLIST.CURTYPE=BANKACC.CURRENCY
    WHERE BNFLINE.SIGN = 1 {virman_bank_filter} AND CONVERT(VARCHAR, BNFLINE.DATE_, 23) = '{target_date}'
    UNION ALL
    SELECT K.LOGICALREF, 'Kasa' AS HESAP_TURU_RAPOR, KS.CODE AS HESAP_KODU, KS.NAME AS HESAP_ACIKLAMASI,
        'Kasa' AS HESAP_NO,
        CASE K.TRCURR WHEN 0 THEN 'TL' WHEN 1 THEN 'USD' WHEN 20 THEN 'EUR' ELSE ISNULL(L_CURRENCYLIST.CURCODE, '') END AS HESAP_DOVIZI_RAPOR,
        K.FICHENO, CONVERT(VARCHAR(10), K.DATE_, 23) AS TARIH, K.LINEEXP AS SATIR_ACIKLAMASI, '' AS FIS_ACIKLAMASI,
        ISNULL(K.TRNET, 0) * (-1) AS DOVIZLI_TUTAR, ISNULL(K.AMOUNT, 0) * (-1) AS TL_TUTAR,
        CL.CODE AS CARI_KOD, ISNULL(NULLIF(CL.DEFINITION_, ''), ISNULL(NULLIF(K.CUSTTITLE, ''), '')) AS CARI_UNVAN,
        [dbo].[fn_trcode] ('Kslines', K.TRCODE, '', '') AS FIS_TURU
    FROM LG_{Firma_Bridge}_{Donem_Bridge}_KSLINES K WITH(NOLOCK)
    LEFT JOIN LG_{Firma_Bridge}_KSCARD KS WITH(NOLOCK) ON KS.LOGICALREF = K.CARDREF
    LEFT JOIN LG_{Firma_Bridge}_CLCARD CL WITH(NOLOCK) ON K.VCARDREF = CL.LOGICALREF
    LEFT OUTER JOIN L_CURRENCYLIST WITH(NOLOCK) ON L_CURRENCYLIST.CURTYPE = K.TRCURR
    WHERE K.SIGN = 1 AND K.CANCELLED = 0 {virman_kasa_filter} AND CONVERT(VARCHAR, K.DATE_, 23) = '{target_date}'
    ORDER BY LOGICALREF
    """
    try:
        engine = get_engine(1)
        with engine.connect() as conn:
            df = pd.read_sql(text(query), conn)
        return jsonify({'rows': df_to_json_safe_app(df)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/bridge/nakit/unbilled')
def bridge_nakit_unbilled():
    key = request.headers.get('X-Bridge-Key') or request.args.get('key', '')
    if key != BRIDGE_KEY:
        return jsonify({'error': 'Yetkisiz erişim'}), 401
    query = """
    WITH LineTotals AS (
        SELECT SHK.KapAdedi * SHK.KDVsizBirimFiyat AS NetAmount,
            CASE WHEN KDVDurumu = 'M' THEN 0.0 ELSE SHK.KDVOrani END AS KdvOrani,
            CASE WHEN SHK.KDVOrani = 16 AND KDVDurumu <> 'M' THEN 0.20 ELSE 0.0 END AS TevkifatOrani,
            PB.PBAdi AS DovizTipi
        FROM LojistikERP_UFUK.dbo.DY_STOK_HAREKETLERI SHK 
        INNER JOIN LojistikERP_UFUK.dbo.DY_FATURALAR FT ON FT.FaturaKodu = SHK.FaturaKodu 
        INNER JOIN LojistikERP_UFUK.dbo.CH_CARI_HESAPLAR CH ON CH.CariHesapKodu = FT.CariHesapKodu 
        INNER JOIN LojistikERP_UFUK.dbo.DY_STOKLAR ST ON ST.StokKodu = SHK.StokKodu 
        LEFT JOIN LojistikERP_UFUK.dbo.V_LO_SEVK SVK ON SVK.SevkKodu = SHK.SevkKodu 
        LEFT JOIN LojistikERP_UFUK.dbo.V_LO_OPR OPR ON OPR.OprKodu = SVK.OprKodu 
        INNER JOIN LojistikERP_UFUK.dbo.CH_PARA_BIRIMLERI PB ON PB.PBKodu = SHK.PB 
        INNER JOIN LojistikERP_UFUK.dbo.CH_ANLIK_DOVIZ_KURLARI AD ON AD.PB = SHK.PB AND AD.StokHareketKodu = SHK.StokHareketKodu 
        WHERE FT.FaturaTipiKodu = 2 AND CH.CariHesapKodu NOT LIKE '53590' 
          AND FT.ResmiMi = '1' AND (FT.FaturaNo IS NULL OR FT.FaturaNo = '') AND FT.FaturaTarihi >= '2026-01-01'
    ),
    LineCalculations AS (
        SELECT DovizTipi, NetAmount + (NetAmount * KdvOrani / 100.0) - (NetAmount * KdvOrani / 100.0 * TevkifatOrani) AS LineTotal
        FROM LineTotals
    )
    SELECT DovizTipi, SUM(LineTotal) AS ToplamTutar FROM LineCalculations GROUP BY DovizTipi
    """
    totals = {'TL': 0.0, 'USD': 0.0, 'EUR': 0.0}
    try:
        engine = get_engine(1)
        with engine.connect() as conn:
            df = pd.read_sql(text(query), conn)
        if not df.empty:
            for _, r in df.iterrows():
                doviz = r['DovizTipi']
                if doviz in totals:
                    totals[doviz] = float(r['ToplamTutar'] or 0.0)
        return jsonify(totals)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/bridge/nakit/debug')
def bridge_nakit_debug():
    key = request.headers.get('X-Bridge-Key') or request.args.get('key', '')
    if key != BRIDGE_KEY:
        return jsonify({'error': 'Yetkisiz erişim'}), 401
    
    results = {}
    today = pd.Timestamp.now().strftime('%Y-%m-%d')
    
    tests = {
        'own_checks':       f"SELECT COUNT(*) FROM LG_{Firma_Bridge}_{Donem_Bridge}_CSCARD WHERE DOC IN (3,4) AND CURRSTAT NOT IN (8,6)",
        'customer_checks':  f"SELECT COUNT(*) FROM LG_{Firma_Bridge}_{Donem_Bridge}_CSCARD WHERE DOC = 1 AND CURRSTAT NOT IN (2,6,8)",
        'credits':          f"SELECT COUNT(*) FROM LG_{Firma_Bridge}_BNCREPAYTR",
        'bank_balances':    f"SELECT COUNT(*) FROM LG_{Firma_Bridge}_BANKACC WHERE ACTIVE=0 AND CARDTYPE IN (1,3)",
        'kasa_balances':    f"SELECT COUNT(*) FROM LG_{Firma_Bridge}_KSCARD WHERE ACTIVE=0",
        'receivables':      f"SELECT COUNT(*) FROM LG_{Firma_Bridge}_{Donem_Bridge}_CLFLINE WHERE CANCELLED=0",
        'incoming_bank':    f"SELECT COUNT(*) FROM LG_{Firma_Bridge}_{Donem_Bridge}_BNFLINE WHERE SIGN=0 AND CONVERT(VARCHAR,DATE_,23)='{today}'",
        'fn_trcode_exists': "SELECT COUNT(*) FROM sys.objects WHERE type='FN' AND name='fn_trcode'",
        'db_name':          "SELECT DB_NAME()",
    }
    
    try:
        engine = get_engine(1)
        with engine.connect() as conn:
            for test_name, query in tests.items():
                try:
                    result = conn.execute(text(query)).fetchone()
                    results[test_name] = str(result[0]) if result else 'NULL'
                except Exception as e:
                    results[test_name] = f'HATA: {str(e)}'
    except Exception as e:
        return jsonify({'connection_error': str(e)}), 500
    
    return jsonify({'firma': Firma_Bridge, 'donem': Donem_Bridge, 'test_date': today, 'results': results})

if __name__ == '__main__':
    app.run(debug=True)
