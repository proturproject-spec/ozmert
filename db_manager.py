import json
import os
import urllib.parse
from sqlalchemy import create_engine, text

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
    # SQLAlchemy'nin pyodbc dialect'i odbc_connect parametresini hem make_url hem de create_connect_args
    # fonksiyonunda iki kez unquote_plus işleminden geçirir. Bu yüzden şifrelerdeki '+' gibi karakterlerin
    # boşluğa dönüşmemesi ve güvenli aktarılması için çift URL encode uygulanır.
    encoded_params = urllib.parse.quote_plus(urllib.parse.quote_plus(connection_str))
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

def get_active_firm_period(conn_id_or_name=1):
    """
    Aktif veritabanı yapılandırmasından seçili firma ve dönem kodlarını döner.
    Örnek: {'firm_nr': '225', 'period_nr': '01', 'card_prefix': 'LG_225_', 'line_prefix': 'LG_225_01_'}
    """
    cfg = get_connection_config(conn_id_or_name) or {}
    raw_firm = str(cfg.get('firm_nr', '225')).strip()
    raw_period = str(cfg.get('period_nr', '01')).strip()
    
    firm_nr = f"{int(raw_firm):03d}" if raw_firm.isdigit() else raw_firm
    period_nr = f"{int(raw_period):02d}" if raw_period.isdigit() else raw_period
    
    return {
        'firm_nr': firm_nr,
        'period_nr': period_nr,
        'card_prefix': f"LG_{firm_nr}_",
        'line_prefix': f"LG_{firm_nr}_{period_nr}_"
    }

def fetch_firms_and_periods(conn_data):
    """
    Verilen bağlantı bilgisiyle veritabanına bağlanıp Logo Tiger
    L_CAPIFIRM ve L_CAPIPERIOD tablolarından firma ve dönemleri çeker.
    """
    try:
        uri = build_connection_uri(conn_data)
        timeout = int(conn_data.get('timeout', 5))
        engine = create_engine(uri, connect_args={"timeout": timeout})
        
        with engine.connect() as conn:
            # 1. Firmalar
            try:
                firms_res = conn.execute(text("SELECT NR, NAME, TITLE FROM L_CAPIFIRM ORDER BY NR")).fetchall()
            except Exception as e:
                return {
                    'success': False,
                    'message': f"Logo Tiger firma tablosu (L_CAPIFIRM) okunamadı: {str(e)}. Firma ve dönemi manuel girebilirsiniz."
                }
            
            # 2. Dönemler
            periods_by_firm = {}
            try:
                periods_res = conn.execute(text("SELECT FIRMNR, NR, BEGDATE, ENDDATE FROM L_CAPIPERIOD ORDER BY FIRMNR, NR")).fetchall()
                for p in periods_res:
                    fnr = f"{int(p[0]):03d}" if str(p[0]).isdigit() else str(p[0])
                    p_nr = f"{int(p[1]):02d}" if str(p[1]).isdigit() else str(p[1])
                    beg = p[2].strftime('%Y-%m-%d') if hasattr(p[2], 'strftime') and p[2] else str(p[2] or '')[:10]
                    end = p[3].strftime('%Y-%m-%d') if hasattr(p[3], 'strftime') and p[3] else str(p[3] or '')[:10]
                    periods_by_firm.setdefault(fnr, []).append({
                        'nr': p_nr,
                        'begdate': beg,
                        'enddate': end
                    })
            except Exception:
                pass

            firms_list = []
            for f in firms_res:
                fnr = f"{int(f[0]):03d}" if str(f[0]).isdigit() else str(f[0])
                name = str(f[1] or '').strip()
                title = str(f[2] or '').strip()
                firms_list.append({
                    'nr': fnr,
                    'name': name,
                    'title': title,
                    'periods': periods_by_firm.get(fnr, [])
                })

            return {
                'success': True,
                'firms': firms_list,
                'count': len(firms_list)
            }
    except Exception as e:
        return {
            'success': False,
            'message': f"Bağlantı hatası: {str(e)}"
        }

GENERAL_SETTINGS_FILE = os.path.join(os.path.dirname(__file__), 'general_settings.json')

def load_general_settings():
    """Genel sistem ayarlarını (para birimi, yenileme periyodu vb.) yükler."""
    if not os.path.exists(GENERAL_SETTINGS_FILE):
        return {'default_currency': 'DZD', 'refresh_period': 5}
    try:
        with open(GENERAL_SETTINGS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {'default_currency': 'DZD', 'refresh_period': 5}
    except Exception as e:
        print(f"general_settings.json okuma hatası: {e}")
        return {'default_currency': 'DZD', 'refresh_period': 5}

def save_general_settings(settings):
    """Genel sistem ayarlarını general_settings.json dosyasına kaydeder."""
    try:
        with open(GENERAL_SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"general_settings.json kaydetme hatası: {e}")
        return False

def get_logo_currencies(conn_id_or_name=1):
    """
    Logo Tiger veritabanından (L_CAPIFIRM ve L_CURRENCYLIST)
    aktif firmanın yerel para birimini, raporlama dövizini ve kullanılan dövizleri çeker.
    Örn: Cezayir firması (225) için yerel para birimi DZD (Cezayir Dinarı)'dır.
    """
    try:
        fp = get_active_firm_period(conn_id_or_name)
        raw_firm = fp.get('firm_nr', '225')
        firm_nr = int(raw_firm) if str(raw_firm).isdigit() else 225
        period_nr = fp.get('period_nr', '01')
        engine = get_engine(conn_id_or_name)

        with engine.connect() as conn:
            # 1. Firma Yerel Para Birimi (LOCALCTYP) ve Raporlama Dövizi (FIRMREPCURR)
            local_ctyp = 81  # Cezayir Dinarı varsayılan
            rep_curr = 1     # USD
            try:
                firm_row = conn.execute(
                    text("SELECT LOCALCTYP, FIRMREPCURR FROM L_CAPIFIRM WHERE NR = :nr"),
                    {'nr': firm_nr}
                ).fetchone()
                if firm_row:
                    if firm_row[0] is not None:
                        local_ctyp = int(firm_row[0])
                    if firm_row[1] is not None:
                        rep_curr = int(firm_row[1])
            except Exception as e:
                print(f"L_CAPIFIRM okuma uyarısı: {e}")

            # 2. L_CURRENCYLIST'ten firmanın tüm para birimlerini al
            curr_rows = conn.execute(
                text("""
                    SELECT CURTYPE, LTRIM(RTRIM(CURCODE)) AS CURCODE, 
                           LTRIM(RTRIM(CURNAME)) AS CURNAME, 
                           LTRIM(RTRIM(CURSYMBOL)) AS CURSYMBOL,
                           CURINUSE
                    FROM L_CURRENCYLIST WITH(NOLOCK)
                    WHERE FIRMNR = :fnr
                    ORDER BY 
                        (CASE WHEN CURTYPE = :loc THEN 0 WHEN CURTYPE = :rep THEN 1 ELSE 2 END),
                        CURCODE
                """),
                {'fnr': firm_nr, 'loc': local_ctyp, 'rep': rep_curr}
            ).fetchall()

            # 3. KSLINES ve CLFLINE'da fiilen işlem görmüş döviz türlerini tespit et
            used_types = {0, local_ctyp, rep_curr}
            for tbl in [f"LG_{firm_nr:03d}_{period_nr}_KSLINES", f"LG_{firm_nr:03d}_{period_nr}_CLFLINE"]:
                try:
                    res = conn.execute(text(f"SELECT DISTINCT TRCURR FROM {tbl} WITH(NOLOCK)")).fetchall()
                    for r in res:
                        if r[0] is not None:
                            used_types.add(int(r[0]))
                except Exception:
                    pass

            currencies = []
            local_info = None
            rep_info = None

            for r in curr_rows:
                ctype = int(r[0])
                ccode = str(r[1] or '').strip()
                cname = str(r[2] or '').strip()
                csym = str(r[3] or '').strip()

                if not csym:
                    if ccode == 'DZD':
                        csym = 'DZD'
                    elif ccode == 'USD':
                        csym = '$'
                    elif ccode == 'EUR':
                        csym = '€'
                    elif ccode in ('TRY', 'TL'):
                        csym = '₺'
                    elif ccode == 'GBP':
                        csym = '£'
                    else:
                        csym = ccode

                item = {
                    'curtype': ctype,
                    'curcode': ccode,
                    'curname': cname,
                    'cursymbol': csym,
                    'is_local': (ctype == local_ctyp),
                    'is_rep': (ctype == rep_curr),
                    'is_used': (ctype in used_types) or (ctype == local_ctyp)
                }

                if ctype == local_ctyp:
                    local_info = item
                if ctype == rep_curr:
                    rep_info = item

                currencies.append(item)

            if not local_info:
                local_info = {'curtype': 81, 'curcode': 'DZD', 'curname': 'Cezayir Dinarı', 'cursymbol': 'DZD', 'is_local': True, 'is_rep': False, 'is_used': True}

            return {
                'success': True,
                'firm_nr': firm_nr,
                'local_currency': local_info,
                'reporting_currency': rep_info,
                'used_currencies': [c for c in currencies if c['is_used']],
                'all_currencies': currencies
            }
    except Exception as e:
        print(f"get_logo_currencies hatası: {e}")
        fallback_local = {'curtype': 81, 'curcode': 'DZD', 'curname': 'Cezayir Dinarı', 'cursymbol': 'DZD', 'is_local': True, 'is_rep': False, 'is_used': True}
        return {
            'success': False,
            'firm_nr': 225,
            'local_currency': fallback_local,
            'reporting_currency': {'curtype': 1, 'curcode': 'USD', 'curname': 'ABD Doları', 'cursymbol': '$', 'is_local': False, 'is_rep': True, 'is_used': True},
            'used_currencies': [
                fallback_local,
                {'curtype': 1, 'curcode': 'USD', 'curname': 'ABD Doları', 'cursymbol': '$', 'is_local': False, 'is_rep': True, 'is_used': True},
                {'curtype': 20, 'curcode': 'EUR', 'curname': 'Euro', 'cursymbol': '€', 'is_local': False, 'is_rep': False, 'is_used': True}
            ],
            'all_currencies': []
        }

def get_active_currency(conn_id_or_name=1):
    """Sistemde aktif kullanılan para birimi kodunu döner (Örn: 'DZD')."""
    settings = load_general_settings()
    custom_curr = settings.get('default_currency')
    if custom_curr:
        return custom_curr
    logo_data = get_logo_currencies(conn_id_or_name)
    local_info = logo_data.get('local_currency') or {}
    return local_info.get('curcode', 'DZD')
