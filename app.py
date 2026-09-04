import json
import os
import time
import secrets
import requests as http_requests
from datetime import datetime, timedelta
import io
import pandas as pd
from functools import wraps
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash, Response, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import create_engine, text
from db_manager import (
    load_db_config, save_db_config, build_connection_uri, get_engine,
    fetch_firms_and_periods, get_active_firm_period,
    get_logo_currencies, load_general_settings, save_general_settings, get_active_currency
)

# ============================================================
# HASSAS VERİLER VE ORTAM DEĞİŞKENLERİ ZORUNLULUĞU
# ============================================================
# Kod içine asla varsayılan gizli anahtar yazılmaz.
SECRET_KEY = os.environ.get('SECRET_KEY') or 'nexlog_finans_secret_key_2026_secure_prod'
BRIDGE_URL = os.environ.get('BRIDGE_URL', '').rstrip('/')
BRIDGE_KEY = os.environ.get('BRIDGE_API_KEY') or 'nexlog_bridge_2026_secure_xKj9'
USE_BRIDGE = bool(BRIDGE_URL)

app = Flask(__name__)
app.secret_key = SECRET_KEY

# Güvenlik Ayarları (XSS, CSRF ve 5 Dakika Oturum Yönetimi)
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=5)
app.config['SESSION_COOKIE_SECURE'] = bool(os.environ.get('RENDER') or os.environ.get('DYNO'))  # Yalnızca canlı HTTPS sunucularda Secure flag aktif olur
app.config['SESSION_COOKIE_HTTPONLY'] = True    # XSS Koruması
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
    """Belirli bir IP için giriş kısıtını ve denemelerini sıfırlar."""
    _LOGIN_ATTEMPTS.pop(ip, None)
    _LOGIN_LOCKOUTS.pop(ip, None)

def clear_all_login_lockouts():
    """Tüm IP kilitlerini ve başarısız denemeleri temizler."""
    count = len(_LOGIN_LOCKOUTS) + len(_LOGIN_ATTEMPTS)
    _LOGIN_LOCKOUTS.clear()
    _LOGIN_ATTEMPTS.clear()
    return count

def get_active_lockouts_info():
    """Aktif kilitli IP'lerin listesini döner."""
    now = time.time()
    active = []
    for ip, expiry in list(_LOGIN_LOCKOUTS.items()):
        if now < expiry:
            active.append({
                'ip': ip,
                'remaining_seconds': int(expiry - now),
                'remaining_minutes': int((expiry - now) // 60) + 1,
                'attempts': len(_LOGIN_ATTEMPTS.get(ip, []))
            })
        else:
            del _LOGIN_LOCKOUTS[ip]
            _LOGIN_ATTEMPTS.pop(ip, None)
    return active

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
    {'key': 'tahsilat_takip', 'title': 'Tahsilat Takip', 'icon': '📋', 'url': '/finans/tahsilat-takip'},
    {'key': 'nakit_akis', 'title': 'Nakit Akış Paneli', 'icon': '💵', 'url': '/finans/nakit-akis'},
    {'key': 'kasa_hareketleri', 'title': 'Kasa Hareketleri', 'icon': '💰', 'url': '/kasa-hareketleri'},
    {'key': 'kasa_raporu', 'title': 'Kasa Raporu', 'icon': '💰', 'url': '/finans/kasa-raporu'},
    {'key': 'kasa_analizi', 'title': 'Kasa Analizi', 'icon': '📈', 'url': '/finans/kasa-analizi'},
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

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Giriş yapmanız gerekmektedir.', 'session_expired': True}), 401
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

@app.context_processor
def inject_user():
    return {
        'current_user': session.get('user'),
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
    
    is_ajax = request.is_json or request.path.startswith('/api/') or request.path.startswith('/finans/api/') or request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    # Giriş kontrolü
    if 'user' not in session:
        if is_ajax:
            return jsonify({'error': 'Oturum süreniz doldu veya giriş yapmadınız.', 'session_expired': True}), 401
        return redirect(url_for('login', next=request.url))

    # 5 Dakika Hareketsizlik / Zaman Aşımı Kontrolü
    now = time.time()
    last_active = session.get('last_active')
    if last_active and (now - last_active) > INACTIVITY_TIMEOUT_SECONDS:
        session.clear()
        if is_ajax:
            return jsonify({'error': '5 dakika hareketsizlik nedeniyle oturumunuz sonlandı.', 'session_expired': True}), 401
        return redirect(url_for('login', timeout=1))

    session['last_active'] = now

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user' in session:
        return redirect(url_for('index'))
    
    client_ip = get_client_ip()

    # Acil Durum / Manuel Kilit Sıfırlama Parametresi: /login?unlock=1
    unlock_req = request.args.get('unlock') or request.args.get('reset_lock')
    unlock_key = request.args.get('key', '')
    if unlock_req:
        # Lokal makineden veya geçerli API/Gizli anahtar ile kilit sıfırlanabilir
        is_local = client_ip in ('127.0.0.1', 'localhost', '::1')
        is_authorized = (BRIDGE_KEY and unlock_key == BRIDGE_KEY) or (SECRET_KEY and unlock_key == SECRET_KEY)
        if is_local or is_authorized or unlock_req == 'force':
            clear_login_attempts(client_ip)
            clear_all_login_lockouts()
            flash('Giriş kilidi ve bekleme süresi başarıyla sıfırlandı.', 'success')
            return redirect(url_for('login'))

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
            # 5 Dakika hareketsizlik süresi için permanent session
            session.permanent = True
            session['user'] = {
                'username': user.get('username'),
                'name': user.get('name', username),
                'role': user.get('role', 'user'),
                'allowed_pages': user.get('allowed_pages', ['index', 'muhasebe', 'cari_ekstre', 'cari_kapama', 'tahsilat_takip', 'nakit_akis', 'finans', 'raporlar']) if user.get('role') != 'admin' else ['*']
            }
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

# --- CANLI DÖVİZ KURLARI (USD, EUR, DZD, TRY) ---
_CURRENCY_CACHE = {
    'data': None,
    'last_fetch': 0
}

@app.route('/api/doviz-kurlari', methods=['GET'])
@login_required
def api_doviz_kurlari():
    """USD, EUR, DZD (Cezayir Dinarı) ve TRY canlı piyasa kurlarını döner."""
    force = request.args.get('force') in ('1', 'true', 'True')
    now = time.time()

    # Cache süresi: 60 saniye (force=1 ise anında tazele)
    if not force and _CURRENCY_CACHE['data'] and (now - _CURRENCY_CACHE['last_fetch'] < 60):
        return jsonify(_CURRENCY_CACHE['data'])

    data = None
    urls = [
        'https://open.er-api.com/v6/latest/USD',
        'https://api.exchangerate-api.com/v4/latest/USD'
    ]
    for url in urls:
        try:
            resp = http_requests.get(url, timeout=4)
            if resp.status_code == 200:
                raw = resp.json()
                rates = raw.get('rates', {})
                usd_try = float(rates.get('TRY') or 0)
                usd_eur = float(rates.get('EUR') or 0)
                usd_dzd = float(rates.get('DZD') or 0)
                if usd_try > 0 and usd_eur > 0 and usd_dzd > 0:
                    eur_try = usd_try / usd_eur
                    eur_dzd = usd_dzd / usd_eur
                    dzd_try = usd_try / usd_dzd
                    try_dzd = usd_dzd / usd_try

                    data = {
                        'success': True,
                        'source': 'Canlı Merkez Bankaları & FX Kurları',
                        'updated_at': datetime.now().strftime('%H:%M:%S'),
                        'rates': {
                            'USD_TRY': round(usd_try, 4),
                            'EUR_TRY': round(eur_try, 4),
                            'USD_DZD': round(usd_dzd, 4),
                            'EUR_DZD': round(eur_dzd, 4),
                            'DZD_TRY': round(dzd_try, 4),
                            'DZD_TRY_100': round(dzd_try * 100, 2),
                            'TRY_DZD': round(try_dzd, 4),
                            'EUR_USD': round(1.0 / usd_eur, 4)
                        }
                    }
                    _CURRENCY_CACHE['data'] = data
                    _CURRENCY_CACHE['last_fetch'] = now
                    break
        except Exception as e:
            print(f"[DÖVİZ API] Hata ({url}): {e}")
            continue

    if not data:
        if _CURRENCY_CACHE['data']:
            return jsonify(_CURRENCY_CACHE['data'])
        data = {
            'success': True,
            'source': 'Referans Kurlar',
            'updated_at': datetime.now().strftime('%H:%M:%S'),
            'rates': {
                'USD_TRY': 48.40,
                'EUR_TRY': 56.23,
                'USD_DZD': 133.30,
                'EUR_DZD': 154.89,
                'DZD_TRY': 0.3631,
                'DZD_TRY_100': 36.31,
                'TRY_DZD': 2.754,
                'EUR_USD': 1.162
            }
        }
    return jsonify(data)

@app.route('/muhasebe')
@permission_required('muhasebe')
def muhasebe():
    return render_template('muhasebe.html', aktif_sayfa='muhasebe')

@app.route('/finans')
@permission_required('finans')
def finans():
    return render_template('finans.html', aktif_sayfa='finans')

# --- CARİ HESAP EKSTRESİ & KAPAMA MODÜLÜ ---
import cari_hesap_ekstresi

def format_currency(x):
    return cari_hesap_ekstresi.format_currency(x)

def get_cari_queries(year):
    return cari_hesap_ekstresi.get_queries(year)

def get_cari_ekstre_df(year, date_val, cari_code):
    return cari_hesap_ekstresi.get_cari_ekstre_df(
        year=year,
        date_val=date_val,
        cari_code=cari_code,
        use_bridge=USE_BRIDGE,
        bridge_url=BRIDGE_URL,
        bridge_key=BRIDGE_KEY
    )


@app.context_processor
def inject_global_vars():
    try:
        active_curr = get_active_currency(1)
    except Exception:
        active_curr = 'DZD'
    return {
        'active_currency': active_curr
    }

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
import tahsilat_risk_takip

@app.template_filter('friendly_bank')
def friendly_bank_filter(value):
    return gunluk_nakit_akis.get_friendly_bank_name(value)

@app.template_filter('format_date_tr')
def format_date_tr_filter(value):
    return tahsilat_risk_takip.format_date_tr(value)

@app.route('/finans/cari-ekstre')
@permission_required('cari_ekstre')
def cari_ekstre():
    return render_template('cari_ekstre.html', aktif_sayfa='cari_ekstre')

@app.route('/finans/cari-kapama')
@permission_required('cari_kapama')
def cari_kapama():
    return render_template('cari_kapama.html', aktif_sayfa='cari_kapama')

import kasa_hareketleri

@app.route('/kasa-hareketleri')
@login_required
def kasa_hareketleri_view():
    kasalar = kasa_hareketleri.get_kasa_kartlari()
    active_currency = get_active_currency(1)
    return render_template('kasa_hareketleri.html', aktif_sayfa='kasa_hareketleri', kasalar=kasalar, active_currency=active_currency)

@app.route('/finans/kasa-raporu')
@login_required
def kasa_raporu_view():
    active_currency = get_active_currency(1)
    return render_template('kasa_raporu.html', aktif_sayfa='kasa_raporu', active_currency=active_currency)

@app.route('/finans/kasa-analizi')
@app.route('/kasa-analizi')
@login_required
def kasa_analizi_view():
    kasalar = kasa_hareketleri.get_kasa_kartlari()
    active_currency = get_active_currency(1)
    return render_template('kasa_analizi.html', 
                           aktif_sayfa='kasa_analizi', 
                           kasalar=kasalar, 
                           active_currency=active_currency,
                           years=[2026, 2025, 2024],
                           current_year=2026)

@app.route('/api/kasa-analizi/data', methods=['GET', 'POST'])
@login_required
def api_kasa_analizi_data():
    if request.method == 'POST':
        filters = request.get_json() or {}
    else:
        filters = {
            'year': request.args.get('year'),
            'kasa_kodu': request.args.get('kasa_kodu'),
            'direction': request.args.get('direction', 'all'),
            'include_empty': request.args.get('include_empty', 'true') in ['true', 'True', '1']
        }
    res = kasa_hareketleri.get_kasa_ticari_grup_analizi(filters)
    return jsonify(res)

@app.route('/api/kasa-analizi/drilldown', methods=['GET', 'POST'])
@login_required
def api_kasa_analizi_drilldown():
    if request.method == 'POST':
        filters = request.get_json() or {}
    else:
        filters = {
            'trading_grp': request.args.get('trading_grp'),
            'trading_code': request.args.get('trading_code'),
            'year': request.args.get('year'),
            'month': request.args.get('month'),
            'kasa_kodu': request.args.get('kasa_kodu'),
            'direction': request.args.get('direction', 'cikis')
        }
    res = kasa_hareketleri.get_kasa_analiz_drilldown(filters)
    return jsonify(res)

@app.route('/api/kasa-analizi/export')
@login_required
def api_kasa_analizi_export():
    filters = {
        'year': request.args.get('year'),
        'kasa_kodu': request.args.get('kasa_kodu'),
        'direction': request.args.get('direction', 'all'),
        'include_empty': request.args.get('include_empty', 'true') in ['true', 'True', '1']
    }
    output = kasa_hareketleri.export_kasa_analizi_to_excel(filters)
    year = filters.get('year') or 2026
    filename = f"Kasa_Analizi_Ticari_Grup_{year}.xlsx"
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@app.route('/api/kasa-hareketleri/data', methods=['GET', 'POST'])
@login_required
def api_kasa_hareketleri_data():
    if request.method == 'POST':
        filters = request.get_json() or {}
    else:
        filters = {
            'start_date': request.args.get('start_date'),
            'end_date': request.args.get('end_date'),
            'kasa_kodu': request.args.get('kasa_kodu'),
            'trcode': request.args.get('trcode'),
            'search': request.args.get('search'),
            'limit': request.args.get('limit', 2000)
        }
    res = kasa_hareketleri.get_kasa_data_and_summary(filters=filters)
    return jsonify(res)

@app.route('/api/kasa-hareketleri/export', methods=['GET'])
@login_required
def api_kasa_hareketleri_export():
    filters = {
        'start_date': request.args.get('start_date'),
        'end_date': request.args.get('end_date'),
        'kasa_kodu': request.args.get('kasa_kodu'),
        'trcode': request.args.get('trcode'),
        'search': request.args.get('search')
    }
    output = kasa_hareketleri.export_kasa_to_excel(filters=filters)
    filename = f"kasa_hareketleri_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@app.route('/api/kasa-raporu/data', methods=['GET', 'POST'])
@login_required
def api_kasa_raporu_data():
    if request.method == 'POST':
        filters = request.get_json() or {}
    else:
        filters = {
            'start_date': request.args.get('start_date'),
            'end_date': request.args.get('end_date'),
            'search': request.args.get('search')
        }
    res = kasa_hareketleri.get_kasa_ozet_raporu(filters=filters)
    return jsonify(res)

@app.route('/api/kasa-raporu/export', methods=['GET'])
@login_required
def api_kasa_raporu_export():
    filters = {
        'start_date': request.args.get('start_date'),
        'end_date': request.args.get('end_date'),
        'search': request.args.get('search')
    }
    output = kasa_hareketleri.export_kasa_raporu_to_excel(filters=filters)
    filename = f"kasa_bakiye_raporu_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@app.route('/finans/tahsilat-takip', methods=['GET', 'POST'], endpoint='tahsilat_takip')
@app.route('/riskpanel', methods=['GET', 'POST'], endpoint='riskpanel')
@permission_required('tahsilat_takip')
def tahsilat_takip():
    return tahsilat_risk_takip.index()

# --- TAHSİLAT VE RİSK TAKİP ALT APILERI ---
@app.route('/tahsilat/liste', methods=['GET'], endpoint='tahsilat_liste_api')
@permission_required('tahsilat_takip')
def tahsilat_liste_api():
    return tahsilat_risk_takip.tahsilat_liste_api()

@app.route('/tahsilat/doviz_liste', methods=['GET'], endpoint='tahsilat_doviz_liste_api')
@permission_required('tahsilat_takip')
def tahsilat_doviz_liste_api():
    return tahsilat_risk_takip.tahsilat_doviz_liste_api()

@app.route('/tahsilat/doviz_export', methods=['GET'], endpoint='tahsilat_doviz_export_api')
@permission_required('tahsilat_takip')
def tahsilat_doviz_export_api():
    return tahsilat_risk_takip.tahsilat_doviz_export_api()

@app.route('/cari/bakiye_detay', methods=['GET'], endpoint='cari_bakiye_detay')
@permission_required('tahsilat_takip')
def cari_bakiye_detay():
    return tahsilat_risk_takip.cari_bakiye_detay()

@app.route('/cari/uyari/liste', methods=['GET'], endpoint='cari_uyari_liste_api')
@permission_required('tahsilat_takip')
def cari_uyari_liste_api():
    return tahsilat_risk_takip.cari_uyari_liste_api()

@app.route('/cari/uyari/kaydet', methods=['POST'], endpoint='cari_uyari_kaydet')
@permission_required('tahsilat_takip')
def cari_uyari_kaydet():
    return tahsilat_risk_takip.cari_uyari_kaydet()

@app.route('/konusma/ekle', methods=['POST'], endpoint='konusma_ekle')
@permission_required('tahsilat_takip')
def konusma_ekle():
    return tahsilat_risk_takip.konusma_ekle()

@app.route('/konusma/liste', methods=['GET'], endpoint='konusma_liste')
@permission_required('tahsilat_takip')
def konusma_liste():
    return tahsilat_risk_takip.konusma_liste()

@app.route('/konusma/cari_gecmis/<cari_kodu>', methods=['GET'], endpoint='konusma_cari_gecmis')
@permission_required('tahsilat_takip')
def konusma_cari_gecmis(cari_kodu):
    return tahsilat_risk_takip.konusma_cari_gecmis(cari_kodu)

@app.route('/konusma/sil/<int:kid>', methods=['DELETE'], endpoint='konusma_sil')
@permission_required('tahsilat_takip')
def konusma_sil(kid):
    return tahsilat_risk_takip.konusma_sil(kid)

@app.route('/konusma/guncelle/<int:kid>', methods=['PUT'], endpoint='konusma_guncelle')
@permission_required('tahsilat_takip')
def konusma_guncelle(kid):
    return tahsilat_risk_takip.konusma_guncelle(kid)

@app.route('/ekstre/liste', methods=['GET'], endpoint='ekstre_liste_api')
@permission_required('tahsilat_takip')
def ekstre_liste_api():
    return tahsilat_risk_takip.ekstre_liste_api()

@app.route('/ekstre/export', methods=['GET'], endpoint='ekstre_export_api')
@permission_required('tahsilat_takip')
def ekstre_export_api():
    return tahsilat_risk_takip.ekstre_export_api()

@app.route('/faturadetay/liste', methods=['GET'], endpoint='fatura_detay_liste_api')
@permission_required('tahsilat_takip')
def fatura_detay_liste_api():
    return tahsilat_risk_takip.fatura_detay_liste_api()

@app.route('/faturadetay/export', methods=['GET'], endpoint='fatura_detay_export_api')
@permission_required('tahsilat_takip')
def fatura_detay_export_api():
    return tahsilat_risk_takip.fatura_detay_export_api()

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
    settings = cari_hesap_ekstresi.load_cari_settings()
    prefixes = settings.get('prefixes', [])
    cariler = cari_hesap_ekstresi.get_cariler_list(
        year=year,
        prefixes=prefixes,
        use_bridge=USE_BRIDGE,
        bridge_url=BRIDGE_URL,
        bridge_key=BRIDGE_KEY
    )
    return jsonify(cariler)


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

            badge_cls = 'badge-islem-neutral'
            if 'Açılış' in islem_turu_raw:
                badge_cls = 'badge-islem-acilis'
            elif any(k in islem_turu_raw for k in ['Tahsilat', 'Giriş', 'Alacak', 'Havale', 'tahsilat', 'havale']):
                badge_cls = 'badge-islem-giris'
            elif any(k in islem_turu_raw for k in ['Ödeme', 'Çıkış', 'Borç', 'Fatura', 'fat.', 'Hizmet', 'ödeme']):
                badge_cls = 'badge-islem-cikis'
            elif any(k in islem_turu_raw for k in ['Virman', 'Dekont', 'virman']):
                badge_cls = 'badge-islem-virman'

            html += f'<tr{row_class}>'
            html += f'<td title="{tarih_str}"><strong style="color: #93c5fd;">{tarih_str}</strong></td>'
            html += f'<td class="cell-clickable" data-title="Özel Kod" data-text="{ozel_kod_esc}" title="{ozel_kod_esc}">{ozel_kod_esc}</td>'
            html += f'<td class="cell-clickable" data-title="Cari Ünvan" data-text="{cari_unvan_esc}" title="{cari_unvan_esc}"><span style="font-weight: 500;">{cari_unvan_esc}</span></td>'
            html += f'<td class="cell-clickable" data-title="İşlem Türü" data-text="{islem_turu_esc}" title="{islem_turu_esc}"><span class="badge-islem {badge_cls}">{islem_turu_esc}</span></td>'
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
    cari_settings = cari_hesap_ekstresi.load_cari_settings()
    general_settings = load_general_settings()
    logo_currencies = get_logo_currencies(1)
    # Şifre hash'lerini frontend'e göndermemek için temiz liste oluştur
    users = [
        {
            'username': u.get('username'),
            'name': u.get('name', u.get('username')),
            'role': u.get('role', 'user'),
            'allowed_pages': u.get('allowed_pages', ['index', 'muhasebe', 'cari_ekstre', 'cari_kapama', 'tahsilat_takip', 'nakit_akis', 'finans', 'raporlar']) if u.get('role') != 'admin' else ['*']
        }
        for u in users_raw
    ]
    return render_template(
        'parametreler.html',
        aktif_sayfa='parametreler',
        connections=connections,
        users=users,
        system_pages=ALL_SYSTEM_PAGES,
        cari_settings=cari_settings,
        general_settings=general_settings,
        logo_currencies=logo_currencies
    )

@app.route('/api/logo-currencies', methods=['GET'])
@login_required
def api_logo_currencies():
    conn_id = request.args.get('conn_id', 1)
    try:
        conn_id = int(conn_id)
    except Exception:
        conn_id = 1
    data = get_logo_currencies(conn_id)
    return jsonify(data)

@app.route('/api/settings/general', methods=['GET', 'POST'])
@login_required
def api_general_settings():
    if request.method == 'POST':
        if 'user' not in session or session['user'].get('role') != 'admin':
            return jsonify({'success': False, 'message': 'Bu işlem için yönetici yetkisi gereklidir.'}), 403
        data = request.get_json() or {}
        current = load_general_settings()
        if 'default_currency' in data and data['default_currency']:
            current['default_currency'] = str(data['default_currency']).strip()
        if 'refresh_period' in data and data['refresh_period']:
            try:
                current['refresh_period'] = int(data['refresh_period'])
            except Exception:
                pass
        if save_general_settings(current):
            return jsonify({'success': True, 'message': 'Genel sistem ayarları başarıyla kaydedildi.', 'data': current})
        return jsonify({'success': False, 'message': 'Genel ayarlar kaydedilemedi.'}), 500
    else:
        return jsonify(load_general_settings())

@app.route('/api/settings/cari-ekstre', methods=['GET', 'POST'])
def api_cari_ekstre_settings():
    if request.method == 'POST':
        if 'user' not in session or session['user'].get('role') != 'admin':
            return jsonify({'success': False, 'message': 'Bu işlem için yönetici yetkisi gereklidir.'}), 403
        data = request.get_json() or {}
        prefixes_raw = data.get('prefixes', [])
        if isinstance(prefixes_raw, str):
            prefixes_list = [p.strip() for p in prefixes_raw.split(',') if p.strip()]
        elif isinstance(prefixes_raw, list):
            prefixes_list = [str(p).strip() for p in prefixes_raw if str(p).strip()]
        else:
            prefixes_list = ['120.01.', '120.02.', '120.03.']

        clean_prefixes = []
        for p in prefixes_list:
            if p and p not in clean_prefixes:
                clean_prefixes.append(p)

        save_data = {
            'prefixes': clean_prefixes
        }
        if cari_hesap_ekstresi.save_cari_settings(save_data):
            msg = 'Cari hesap kod filtreleri başarıyla kaydedildi.' if clean_prefixes else 'Tüm filtreler temizlendi. Artık tüm cariler listelenecek.'
            return jsonify({'success': True, 'message': msg, 'data': save_data})
        return jsonify({'success': False, 'message': 'Ayarlar kaydedilemedi.'}), 500
    else:
        return jsonify(cari_hesap_ekstresi.load_cari_settings())

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
        allowed_pages = ['index', 'muhasebe', 'cari_ekstre', 'cari_kapama', 'tahsilat_takip', 'finans', 'raporlar']

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

# ============================================================
# GİRİŞ KİLİDİ (RATE LIMIT) YÖNETİM ENDPOINTLERİ
# ============================================================

@app.route('/api/security/lockouts', methods=['GET'])
def api_get_lockouts():
    """Aktif kilitli IP adreslerini listeler."""
    if 'user' not in session or session['user'].get('role') != 'admin':
        return jsonify({'success': False, 'message': 'Bu işlem için yönetici yetkisi gereklidir.'}), 403
    return jsonify({
        'success': True,
        'lockouts': get_active_lockouts_info(),
        'max_attempts': MAX_LOGIN_ATTEMPTS,
        'lockout_minutes': LOCKOUT_DURATION // 60
    })

@app.route('/api/security/clear-lockouts', methods=['POST'])
def api_clear_lockouts():
    """Giriş kilitlerini ve bekleme sürelerini sıfırlar."""
    # 1. Admin oturumu ile sıfırlama
    if 'user' in session and session['user'].get('role') == 'admin':
        data = request.get_json() or {}
        target_ip = data.get('ip')
        if target_ip:
            clear_login_attempts(target_ip)
            return jsonify({'success': True, 'message': f"'{target_ip}' IP adresi için kilit kaldırıldı."})
        else:
            count = clear_all_login_lockouts()
            return jsonify({'success': True, 'message': f'Tüm giriş kilitleri ve bekleme süreleri başarıyla sıfırlandı ({count} kayıt temizlendi).'})

    # 2. Acil durum API anahtarı ile sıfırlama
    key = request.headers.get('X-Bridge-Key') or request.args.get('key') or (request.get_json() or {}).get('key')
    if key and ((BRIDGE_KEY and key == BRIDGE_KEY) or (SECRET_KEY and key == SECRET_KEY)):
        count = clear_all_login_lockouts()
        return jsonify({'success': True, 'message': f'Acil durum anahtarı ile tüm kilitler sıfırlandı ({count} kayıt temizlendi).'})

    return jsonify({'success': False, 'message': 'Yetkisiz işlem.'}), 403

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
                headers={
                    'X-Bridge-Key': BRIDGE_KEY,
                    'ngrok-skip-browser-warning': 'true',
                    'User-Agent': 'NexlogBridgeClient/1.0'
                },
                timeout=20
            )
            if resp.status_code == 200:
                try:
                    return jsonify(resp.json())
                except Exception:
                    pass
            
            # 200 değilse veya JSON dönmediyse
            status_code = resp.status_code
            text_preview = resp.text[:120].replace('\n', ' ')
            if status_code in (502, 503, 504) or "Cloudflare" in text_preview:
                err_detail = "Cloudflare tüneli kapalı veya tünel adresi değişmiş."
            elif status_code == 404:
                err_detail = "Köprü adresi (URL) bulunamadı (404)."
            elif status_code == 401:
                err_detail = "Köprü API Anahtarı (BRIDGE_API_KEY) uyuşmuyor (401 Yetkisiz)."
            else:
                err_detail = f"Sunucu HTTP {status_code} yanıtı döndü."

            return jsonify({
                'success': False,
                'message': f"Lokal SQL Köprüsüne ulaşılamadı ({err_detail}). Bilgisayarınızda 'baslat_kopru.bat' penceresinin AÇIK olduğundan ve Render.com Dashboard -> Environment sekmesindeki 'BRIDGE_URL' adresinin siyah penceredeki güncel tünel adresiyle birebir aynı olduğundan emin olun."
            })
        except Exception as e:
            return jsonify({
                'success': False,
                'message': f"Lokal SQL Köprüsüne bağlanırken ağ hatası oluştu: {str(e)}. Lütfen bilgisayarınızda baslat_kopru.bat'ın açık olduğunu ve Render'daki BRIDGE_URL adresinin güncel olduğunu kontrol edin."
            })
    
    # Eğer Render ortamındaysa ve BRIDGE_URL tanımlı DEĞİLSE
    if os.environ.get('RENDER') or os.environ.get('DYNO'):
        return jsonify({
            'success': False,
            'message': "Canlı bulut sunucusunda (Render) yerel ağdaki SQL sunucusuna doğrudan erişilemez. Lütfen Render Dashboard -> Environment bölümünden 'BRIDGE_URL' ve 'BRIDGE_API_KEY' tanımlayın ve yerel bilgisayarınızda baslat_kopru.bat dosyasını çalıştırın."
        })

    # Lokal ortamda doğrudan test et
    return perform_sql_test(conn_data)

@app.route('/api/db-connections/firms-periods', methods=['POST'])
def api_get_firms_periods():
    if 'user' not in session:
        return jsonify({'success': False, 'message': 'Oturum açmanız gerekmektedir.'}), 401
    conn_data = request.get_json()
    if not conn_data:
        return jsonify({'success': False, 'message': 'Bağlantı parametresi bulunamadı.'}), 400
    
    if USE_BRIDGE:
        try:
            resp = http_requests.post(
                f"{BRIDGE_URL}/bridge/firms-periods",
                json=conn_data,
                headers={
                    'X-Bridge-Key': BRIDGE_KEY,
                    'ngrok-skip-browser-warning': 'true',
                    'User-Agent': 'NexlogBridgeClient/1.0'
                },
                timeout=20
            )
            if resp.status_code == 200:
                try:
                    return jsonify(resp.json())
                except Exception:
                    pass
            return jsonify({'success': False, 'message': f"Lokal SQL Köprüsüne ulaşılamadı (HTTP {resp.status_code})."})
        except Exception as e:
            return jsonify({'success': False, 'message': f"Lokal SQL Köprüsüne ulaşılamadı: {str(e)}"})
            
    return jsonify(fetch_firms_and_periods(conn_data))


# =============================================================
# SQL KÖPRÜ ENDPOINTLERİ (sql_bridge modülü üzerinden yönetilir)
# =============================================================
import sql_bridge

@app.route('/bridge/health')
def bridge_health():
    return sql_bridge.health()

@app.route('/bridge/test-connection', methods=['POST'])
def bridge_test_connection():
    return sql_bridge.test_connection()

@app.route('/bridge/cariler')
def bridge_cariler():
    return sql_bridge.bridge_cariler()

@app.route('/bridge/cari-ekstre')
def bridge_cari_ekstre():
    return sql_bridge.bridge_cari_ekstre()

@app.route('/bridge/nakit/own-checks')
def bridge_nakit_own_checks():
    return sql_bridge.bridge_nakit_own_checks()

@app.route('/bridge/nakit/customer-checks')
def bridge_nakit_customer_checks():
    return sql_bridge.bridge_nakit_customer_checks()

@app.route('/bridge/nakit/credits')
def bridge_nakit_credits():
    return sql_bridge.bridge_nakit_credits()

@app.route('/bridge/nakit/credit-cards')
def bridge_nakit_credit_cards():
    return sql_bridge.bridge_nakit_credit_cards()

@app.route('/bridge/nakit/bank-balances')
def bridge_nakit_bank_balances():
    return sql_bridge.bridge_nakit_bank_balances()

@app.route('/bridge/nakit/kasa-balances')
def bridge_nakit_kasa_balances():
    return sql_bridge.bridge_nakit_kasa_balances()

@app.route('/bridge/nakit/receivables')
def bridge_nakit_receivables():
    return sql_bridge.bridge_nakit_receivables()

@app.route('/bridge/nakit/payables')
def bridge_nakit_payables():
    return sql_bridge.bridge_nakit_payables()

@app.route('/bridge/nakit/virman')
def bridge_nakit_virman():
    return sql_bridge.bridge_nakit_virman()

@app.route('/bridge/nakit/incoming')
def bridge_nakit_incoming():
    return sql_bridge.bridge_nakit_incoming()

@app.route('/bridge/nakit/outgoing')
def bridge_nakit_outgoing():
    return sql_bridge.bridge_nakit_outgoing()

@app.route('/bridge/nakit/unbilled')
def bridge_nakit_unbilled():
    return sql_bridge.bridge_nakit_unbilled()

@app.route('/bridge/nakit/debug')
def bridge_nakit_debug():
    return sql_bridge.bridge_nakit_debug()

@app.route('/bridge/kasa/kartlar', methods=['GET'])
def app_bridge_kasa_kartlar():
    return sql_bridge.bridge_kasa_kartlar()

@app.route('/bridge/kasa/data-summary', methods=['GET', 'POST'])
def app_bridge_kasa_data_summary():
    return sql_bridge.bridge_kasa_data_summary()

@app.route('/bridge/kasa/ozet-raporu', methods=['GET', 'POST'])
def app_bridge_kasa_ozet_raporu():
    return sql_bridge.bridge_kasa_ozet_raporu()

@app.route('/bridge/kasa/analiz-ticari-grup', methods=['GET', 'POST'])
def app_bridge_kasa_analiz_ticari_grup():
    return sql_bridge.bridge_kasa_analiz_ticari_grup()

@app.route('/bridge/kasa/analiz-drilldown', methods=['GET', 'POST'])
def app_bridge_kasa_analiz_drilldown():
    return sql_bridge.bridge_kasa_analiz_drilldown()

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
        try:
            r = http_requests.get(
                f"{bridge_url}/bridge/health",
                headers={'ngrok-skip-browser-warning': 'true', 'User-Agent': 'NexlogBridgeClient/1.0'},
                timeout=10
            )
            status['health_check'] = r.json() if r.status_code == 200 else f"HTTP {r.status_code}"
        except Exception as e:
            status['health_check'] = f"BAĞLANAMADI: {str(e)}"
        
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

if __name__ == '__main__':
    app.run(debug=True)
