"""
SQL Köprü API Servisi
Bu servis yerel bilgisayarda çalışır, SQL Server'a bağlanır ve
Render'daki ana uygulamaya veri sağlar.
"""
import os
import json
import urllib.parse
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from sqlalchemy import create_engine, text
import pandas as pd

bridge_app = Flask(__name__)

# --- GÜVENLİK: API anahtarı ortam değişkeninden alınır, kod içine yazılmaz ---
BRIDGE_API_KEY = os.environ.get('BRIDGE_API_KEY', '')

# --- SQL BAĞLANTI (db_config.json'dan oku) ---
CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'db_config.json')

Firma = "226"
Donem = "01"

def load_config():
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def get_engine_by_id(conn_id=1):
    configs = load_config()
    cfg = next((c for c in configs if c['id'] == conn_id), None)
    if not cfg:
        raise ValueError(f"Bağlantı ID {conn_id} bulunamadı")
    
    server = cfg['server']
    port = cfg.get('port', '1433')
    database = cfg['database']
    driver = cfg.get('driver', 'ODBC Driver 17 for SQL Server')
    
    server_part = f"{server},{port}" if port and port != "1433" else server
    
    if cfg.get('trusted_connection'):
        odbc = f"DRIVER={{{driver}}};SERVER={server_part};DATABASE={database};Trusted_Connection=yes;"
    else:
        username = cfg['username']
        password = cfg['password']
        odbc = f"DRIVER={{{driver}}};SERVER={server_part};DATABASE={database};UID={username};PWD={password};"
    
    if cfg.get('trust_server_certificate'):
        odbc += "TrustServerCertificate=yes;"
    
    encoded = urllib.parse.quote_plus(odbc)
    return create_engine(f"mssql+pyodbc:///?odbc_connect={encoded}")

def verify_key():
    if not BRIDGE_API_KEY:
        return False
    key = request.headers.get('X-Bridge-Key') or request.args.get('key')
    return bool(key and key == BRIDGE_API_KEY)

def df_to_json_safe(df):
    """DataFrame'i JSON-safe dict listesine çevirir (Timestamp, NaN sorunlarını çözer)."""
    if df.empty:
        return []
    # Datetime sütunlarını string'e çevir
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].astype(str)
    # NaN → None
    return df.where(pd.notnull(df), None).to_dict(orient='records')

# ============================================================
# MEVCUT ENDPOINTLERİ
# ============================================================

@bridge_app.route('/bridge/health')
def health():
    return jsonify({'status': 'ok', 'service': 'SQL Bridge API'})

@bridge_app.route('/bridge/test-connection', methods=['POST'])
def test_connection():
    if not verify_key():
        return jsonify({'error': 'Yetkisiz erişim'}), 401
    import time
    conn_data = request.get_json() or {}
    try:
        driver = conn_data.get('driver', 'ODBC Driver 17 for SQL Server')
        server = conn_data.get('server', '').strip()
        port = conn_data.get('port', '').strip()
        database = conn_data.get('database', '').strip()
        username = conn_data.get('username', '').strip()
        password = conn_data.get('password', '')
        trusted_conn = conn_data.get('trusted_connection', False)
        trust_cert = conn_data.get('trust_server_certificate', True)
        timeout = int(conn_data.get('timeout', 5))

        if not server or not database:
            return jsonify({'success': False, 'message': 'Sunucu veya veritabanı boş bırakılamaz.'}), 400

        server_part = f"{server},{port}" if port and str(port).strip() != "1433" else server
        parts = [f"DRIVER={{{driver}}}", f"SERVER={server_part}", f"DATABASE={database}"]
        if trusted_conn:
            parts.append("Trusted_Connection=yes")
        else:
            if username: parts.append(f"UID={username}")
            if password: parts.append(f"PWD={password}")
        if trust_cert:
            parts.append("TrustServerCertificate=yes")
        
        encoded = urllib.parse.quote_plus(";".join(parts) + ";")
        uri = f"mssql+pyodbc:///?odbc_connect={encoded}"
        
        start_time = time.time()
        engine = create_engine(uri, connect_args={"timeout": timeout})
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
        return jsonify({'success': False, 'message': f"Bağlantı hatası: {str(e)}"})

@bridge_app.route('/bridge/cariler')
def bridge_cariler():
    if not verify_key():
        return jsonify({'error': 'Yetkisiz erişim'}), 401
    
    year = request.args.get('year', '2026')
    prefix = '225' if year == '2025' else '226'
    
    try:
        engine = get_engine_by_id(1)
        query = f"SELECT CODE, DEFINITION_ FROM LG_{prefix}_CLCARD WHERE CODE LIKE '120.%' ORDER BY DEFINITION_"
        with engine.connect() as conn:
            df = pd.read_sql(text(query), conn)
        return jsonify(df.to_dict(orient='records'))
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bridge_app.route('/bridge/cari-ekstre')
def bridge_cari_ekstre():
    if not verify_key():
        return jsonify({'error': 'Yetkisiz erişim'}), 401
    
    year = request.args.get('year', '2026')
    date_val = request.args.get('date', f'{year}-01-01')
    cari = request.args.get('cari', '').strip()
    
    if not cari:
        return jsonify({'error': 'Cari kodu eksik'}), 400
    
    prefix = '225' if year == '2025' else '226'
    
    base_q = f"""
    SELECT
        CLF.DATE_ AS TARIH,
        REPLACE(LTRIM(RTRIM(C.SPECODE2)), '  ', ' ') AS OZEL_KOD,
        C.CODE + ' / ' + C.DEFINITION_ AS CARI_UNVAN,
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
             ELSE 'Diğer' END AS ISLEM_TURU,
        CLF.TRANNO AS FIS_NO,
        CAST(CLF.LINEEXP AS VARCHAR(MAX)) AS ACIKLAMA,
        CASE WHEN CLF.SIGN = 0 THEN CLF.TRNET ELSE 0 END AS BORC,
        CASE WHEN CLF.SIGN = 1 THEN CLF.TRNET ELSE 0 END AS ALACAK
    FROM LG_{prefix}_01_CLFLINE CLF
    LEFT JOIN LG_{prefix}_CLCARD C ON C.LOGICALREF = CLF.CLIENTREF
    WHERE CLF.CANCELLED = 0
    AND LTRIM(RTRIM(C.CODE)) = LTRIM(RTRIM(:cari_code))
    AND CAST(CLF.DATE_ AS DATE) >= :date
    ORDER BY CLF.DATE_, CLF.FTIME, CLF.LOGICALREF
    """
    
    devir_q = f"""
    SELECT SUM(CASE WHEN CLF.SIGN = 0 THEN CLF.TRNET ELSE 0 END) -
           SUM(CASE WHEN CLF.SIGN = 1 THEN CLF.TRNET ELSE 0 END) AS DEVIR
    FROM LG_{prefix}_01_CLFLINE CLF
    LEFT JOIN LG_{prefix}_CLCARD C ON C.LOGICALREF = CLF.CLIENTREF
    WHERE CLF.CANCELLED = 0
    AND LTRIM(RTRIM(C.CODE)) = LTRIM(RTRIM(:cari_code))
    AND CAST(CLF.DATE_ AS DATE) < :date
    """
    
    try:
        engine = get_engine_by_id(1)
        params = {'cari_code': cari, 'date': date_val}
        
        with engine.connect() as conn:
            devir_res = conn.execute(text(devir_q), params).fetchone()
            devir = float(devir_res[0] or 0) if devir_res and devir_res[0] is not None else 0.0
            df = pd.read_sql(text(base_q), conn, params=params)
        
        if 'TARIH' in df.columns:
            df['TARIH'] = df['TARIH'].astype(str)
        
        return jsonify({
            'rows': df.to_dict(orient='records'),
            'devir': devir
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============================================================
# NAKİT AKIŞ ENDPOINTLERİ
# ============================================================

@bridge_app.route('/bridge/nakit/own-checks')
def bridge_nakit_own_checks():
    if not verify_key():
        return jsonify({'error': 'Yetkisiz erişim'}), 401
    target_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    limit_date = request.args.get('limit_date', 'true').lower() == 'true'
    
    date_filter = f"AND CONVERT(VARCHAR, CSC.DUEDATE, 23) <= '{target_date}'" if limit_date else ""
    query = f"""
    SELECT 
        (SELECT TOP 1 CL.DEFINITION_ 
         FROM LG_{Firma}_CLCARD CL WITH(NOLOCK)
         JOIN LG_{Firma}_{Donem}_CSTRANS CS WITH(NOLOCK) ON CL.LOGICALREF = CS.CARDREF 
         WHERE CS.CSREF = CSC.LOGICALREF ORDER BY CS.LOGICALREF ASC) AS [CH_UNVANI], 
        CSC.BANKNAME AS [BANKA], 
        CONVERT(VARCHAR(10), CSC.DUEDATE, 23) AS [VADE], 
        CSC.AMOUNT AS [TUTAR],
        CSC.TRCURR AS [DOVIZ_TIPI]
    FROM LG_{Firma}_{Donem}_CSCARD CSC WITH(NOLOCK)
    WHERE CSC.DOC IN (3,4) AND CSC.CURRSTAT NOT IN (8, 6) {date_filter}
    ORDER BY CSC.DUEDATE ASC
    """
    try:
        engine = get_engine_by_id(1)
        with engine.connect() as conn:
            df = pd.read_sql(text(query), conn)
        return jsonify({'rows': df_to_json_safe(df)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bridge_app.route('/bridge/nakit/customer-checks')
def bridge_nakit_customer_checks():
    if not verify_key():
        return jsonify({'error': 'Yetkisiz erişim'}), 401
    target_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    limit_date = request.args.get('limit_date', 'true').lower() == 'true'
    
    date_filter = f"AND CONVERT(VARCHAR, CSC.DUEDATE, 23) <= '{target_date}'" if limit_date else ""
    query = f"""
    SELECT 
        (SELECT TOP 1 CL.DEFINITION_ FROM LG_{Firma}_CLCARD CL WITH(NOLOCK)
         JOIN LG_{Firma}_{Donem}_CSTRANS CS WITH(NOLOCK) ON CL.LOGICALREF = CS.CARDREF 
         WHERE CS.CSREF = CSC.LOGICALREF AND CS.TRCODE = 1) AS [CH_UNVANI], 
        CSC.BANKNAME AS [BANKA], 
        (SELECT TOP 1 BA.DEFINITION_ FROM LG_{Firma}_{Donem}_CSTRANS CST WITH(NOLOCK)
         JOIN LG_{Firma}_BANKACC BA WITH(NOLOCK) ON BA.LOGICALREF = CST.CARDREF
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
    FROM LG_{Firma}_{Donem}_CSCARD CSC WITH(NOLOCK)
    WHERE CSC.DOC = 1 AND CSC.CURRSTAT NOT IN (2, 6, 8) {date_filter}
    ORDER BY CSC.DUEDATE ASC
    """
    try:
        engine = get_engine_by_id(1)
        with engine.connect() as conn:
            df = pd.read_sql(text(query), conn)
        return jsonify({'rows': df_to_json_safe(df)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bridge_app.route('/bridge/nakit/credits')
def bridge_nakit_credits():
    if not verify_key():
        return jsonify({'error': 'Yetkisiz erişim'}), 401
    target_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    limit_date = request.args.get('limit_date', 'true').lower() == 'true'
    
    date_filter = f"WHERE CONVERT(VARCHAR, P.DUEDATE, 23) <= '{target_date}'" if limit_date else ""
    query = f"""
    SELECT C.NAME_ AS [BANKA_KREDI],
        CONVERT(VARCHAR(10), P.DUEDATE, 23) AS [VADE],
        MAX(P.TOTAL) AS [ANAPARA], MAX(P.INTTOTAL) AS [FAIZ],
        MAX(P.TOTAL + P.INTTOTAL) AS [TUTAR], MAX(C.TRCURR) AS [DOVIZ_TIPI]
    FROM LG_{Firma}_BNCREPAYTR P WITH(NOLOCK)
    LEFT JOIN LG_{Firma}_BNCREDITCARD C WITH(NOLOCK) ON C.LOGICALREF = P.CREDITREF
    {date_filter}
    GROUP BY C.CODE, C.NAME_, P.DUEDATE
    HAVING MAX(P.TRANSTYPE) <> 1
    ORDER BY P.DUEDATE ASC
    """
    try:
        engine = get_engine_by_id(1)
        with engine.connect() as conn:
            df = pd.read_sql(text(query), conn)
        return jsonify({'rows': df_to_json_safe(df)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bridge_app.route('/bridge/nakit/credit-cards')
def bridge_nakit_credit_cards():
    if not verify_key():
        return jsonify({'error': 'Yetkisiz erişim'}), 401
    target_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    limit_date = request.args.get('limit_date', 'true').lower() == 'true'
    
    date_filter = f"AND CONVERT(VARCHAR, KSV.MAX_DATE, 23) <= '{target_date}'" if limit_date else ""
    query = f"""
    WITH KartSonVade AS (
        SELECT B.LOGICALREF AS BNACCREF, MAX(ISNULL(P.DATE_, F.DATE_)) AS MAX_DATE
        FROM LG_{Firma}_{Donem}_BNFLINE F WITH(NOLOCK)
        LEFT JOIN LG_{Firma}_{Donem}_PAYTRANS P WITH(NOLOCK) ON P.LOGICALREF = F.SOURCEFREF
        LEFT JOIN LG_{Firma}_BANKACC B WITH(NOLOCK) ON B.LOGICALREF = F.BNACCREF
        WHERE B.CODE LIKE '50.%' AND F.CANCELLED = 0
        GROUP BY B.LOGICALREF
    )
    SELECT B.DEFINITION_ AS [KARTI_ADI],
        CONVERT(VARCHAR(10), KSV.MAX_DATE, 23) AS [VADE],
        SUM(CASE WHEN F.SIGN = 0 THEN F.AMOUNT ELSE -F.AMOUNT END) AS [TUTAR]
    FROM LG_{Firma}_{Donem}_BNFLINE F WITH(NOLOCK)
    JOIN KartSonVade KSV ON KSV.BNACCREF = F.BNACCREF
    LEFT JOIN LG_{Firma}_BANKACC B WITH(NOLOCK) ON B.LOGICALREF = F.BNACCREF
    WHERE F.CANCELLED = 0 {date_filter}
    GROUP BY B.DEFINITION_, KSV.MAX_DATE
    HAVING SUM(CASE WHEN F.SIGN = 0 THEN F.AMOUNT ELSE -F.AMOUNT END) > 0.01
    ORDER BY KSV.MAX_DATE ASC
    """
    try:
        engine = get_engine_by_id(1)
        with engine.connect() as conn:
            df = pd.read_sql(text(query), conn)
        return jsonify({'rows': df_to_json_safe(df)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bridge_app.route('/bridge/nakit/bank-balances')
def bridge_nakit_bank_balances():
    if not verify_key():
        return jsonify({'error': 'Yetkisiz erişim'}), 401
    query = f"""
    SELECT BC.DEFINITION_ AS BANKA_ADI, BA.DEFINITION_ AS HESAP_ADI,
        BA.IBAN AS IBAN, BA.CURRENCY AS DOVIZ_TIPI,
        CASE WHEN BA.CURRENCY = 0 THEN ROUND(ISNULL(SUM(CASE BN.SIGN WHEN 0 THEN BN.AMOUNT ELSE -BN.AMOUNT END), 0), 2)
             ELSE ROUND(ISNULL(SUM(CASE BN.SIGN WHEN 0 THEN BN.TRNET ELSE -BN.TRNET END), 0), 2) END AS BAKIYE
    FROM LG_{Firma}_BANKACC BA WITH(NOLOCK)
    INNER JOIN LG_{Firma}_BNCARD BC WITH(NOLOCK) ON BC.LOGICALREF = BA.BANKREF
    LEFT JOIN LG_{Firma}_{Donem}_BNFLINE BN WITH(NOLOCK) ON BN.BNACCREF = BA.LOGICALREF AND BN.TRANSTYPE = 1 
    WHERE BA.ACTIVE = 0 AND BA.CARDTYPE IN (1, 3) 
    GROUP BY BA.CODE, BA.DEFINITION_, BC.CODE, BC.DEFINITION_, BA.CARDTYPE, BA.CURRENCY, BA.IBAN
    HAVING ROUND(ISNULL(SUM(CASE BN.SIGN WHEN 0 THEN BN.AMOUNT ELSE -BN.AMOUNT END), 0), 2) <> 0
        OR ROUND(ISNULL(SUM(CASE WHEN BA.CURRENCY = 0 THEN 0 ELSE (CASE BN.SIGN WHEN 0 THEN BN.TRNET ELSE -BN.TRNET END) END), 0), 2) <> 0
    ORDER BY BA.CODE
    """
    try:
        engine = get_engine_by_id(1)
        with engine.connect() as conn:
            df = pd.read_sql(text(query), conn)
        return jsonify({'rows': df_to_json_safe(df)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bridge_app.route('/bridge/nakit/kasa-balances')
def bridge_nakit_kasa_balances():
    if not verify_key():
        return jsonify({'error': 'Yetkisiz erişim'}), 401
    target_date = request.args.get('date', '')
    date_filter = f"AND CONVERT(VARCHAR, KS.DATE_, 23) <= '{target_date}'" if target_date else ""
    query = f"""
    SELECT K.CCURRENCY AS DOVIZ_TIPI,
        CASE WHEN K.CCURRENCY = 0 THEN ROUND(ISNULL(SUM(CASE WHEN KS.SIGN = 0 THEN KS.AMOUNT ELSE -KS.AMOUNT END), 0), 2)
             ELSE ROUND(ISNULL(SUM(CASE WHEN KS.SIGN = 0 THEN KS.TRNET ELSE -KS.TRNET END), 0), 2) END AS BAKIYE
    FROM LG_{Firma}_KSCARD K WITH(NOLOCK)
    LEFT JOIN LG_{Firma}_{Donem}_KSLINES KS WITH(NOLOCK) ON KS.CARDREF = K.LOGICALREF AND KS.CANCELLED = 0 {date_filter}
    WHERE K.ACTIVE = 0 AND (K.CODE LIKE '100.01.%' OR K.NAME LIKE '%MERKEZ%')
    GROUP BY K.CCURRENCY
    """
    try:
        engine = get_engine_by_id(1)
        with engine.connect() as conn:
            df = pd.read_sql(text(query), conn)
        return jsonify({'rows': df_to_json_safe(df)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bridge_app.route('/bridge/nakit/receivables')
def bridge_nakit_receivables():
    if not verify_key():
        return jsonify({'error': 'Yetkisiz erişim'}), 401
    query = f"""
    SELECT
        SUM(CASE WHEN CL.CODE LIKE '120.01.%' THEN (CASE WHEN CLF.SIGN = 0 THEN CLF.AMOUNT ELSE -CLF.AMOUNT END) ELSE 0 END) AS BAKIYE_TL,
        SUM(CASE WHEN CL.CODE LIKE '120.05.%' AND CLF.TRCURR = 1 THEN (CASE WHEN CLF.SIGN = 0 THEN CLF.TRNET ELSE -CLF.TRNET END) ELSE 0 END) AS BAKIYE_USD,
        SUM(CASE WHEN CL.CODE LIKE '120.05.%' AND CLF.TRCURR = 20 THEN (CASE WHEN CLF.SIGN = 0 THEN CLF.TRNET ELSE -CLF.TRNET END) ELSE 0 END) AS BAKIYE_EUR
    FROM LG_{Firma}_CLCARD CL WITH(NOLOCK)
    INNER JOIN LG_{Firma}_{Donem}_CLFLINE CLF WITH(NOLOCK) ON CLF.CLIENTREF = CL.LOGICALREF
    WHERE CLF.CANCELLED = 0 AND CL.ACTIVE = 0 AND (CL.CODE LIKE '120.01.%' OR CL.CODE LIKE '120.05.%')
    """
    try:
        engine = get_engine_by_id(1)
        with engine.connect() as conn:
            df = pd.read_sql(text(query), conn)
        if not df.empty:
            return jsonify({'TL': float(df.iloc[0]['BAKIYE_TL'] or 0.0), 'USD': float(df.iloc[0]['BAKIYE_USD'] or 0.0), 'EUR': float(df.iloc[0]['BAKIYE_EUR'] or 0.0)})
        return jsonify({'TL': 0.0, 'USD': 0.0, 'EUR': 0.0})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bridge_app.route('/bridge/nakit/payables')
def bridge_nakit_payables():
    if not verify_key():
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
    FROM LG_{Firma}_CLCARD CL WITH(NOLOCK)
    INNER JOIN LG_{Firma}_{Donem}_CLFLINE CLF WITH(NOLOCK) ON CLF.CLIENTREF = CL.LOGICALREF
    WHERE CLF.CANCELLED = 0 AND CL.ACTIVE = 0 AND (
        CL.CODE LIKE '320.01.%' OR CL.CODE LIKE '320.02.%' OR CL.CODE LIKE '320.03.%' OR
        CL.CODE LIKE '320.04.%' OR CL.CODE LIKE '320.05.%' OR CL.CODE LIKE '320.06.%')
    """
    empty_val = {'TL': 0.0, 'USD': 0.0, 'EUR': 0.0}
    try:
        engine = get_engine_by_id(1)
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

@bridge_app.route('/bridge/nakit/virman')
def bridge_nakit_virman():
    if not verify_key():
        return jsonify({'error': 'Yetkisiz erişim'}), 401
    target_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    query = f"""
    SELECT BNFLINE.LOGICALREF, BNFLINE.SOURCEFREF, BNFICHE.FICHENO,
        CONVERT(VARCHAR(10), BNFLINE.DATE_, 23) AS TARIH, BNFLINE.SIGN,
        BNFLINE.AMOUNT AS TL_TUTAR, BNFLINE.TRNET AS DOVIZLI_TUTAR,
        CASE BANKACC.CURRENCY WHEN 0 THEN 'TL' WHEN 1 THEN 'USD' WHEN 20 THEN 'EUR' ELSE ISNULL(L_CURRENCYLIST.CURCODE, '') END AS HESAP_DOVIZI_RAPOR,
        BANKACC.DEFINITION_ AS HESAP_ACIKLAMASI, BANKACC.CODE AS HESAP_KODU, BANKACC.ACCOUNTNO AS HESAP_NO,
        BNFLINE.LINEEXP AS SATIR_ACIKLAMASI,
        ISNULL(BNFICHE.GENEXP1, '') +' '+ ISNULL(BNFICHE.GENEXP2, '') AS FIS_ACIKLAMASI
    FROM LG_{Firma}_{Donem}_BNFLINE BNFLINE WITH(NOLOCK)
    LEFT JOIN LG_{Firma}_{Donem}_BNFICHE BNFICHE WITH(NOLOCK) ON BNFICHE.LOGICALREF = BNFLINE.SOURCEFREF
    LEFT JOIN LG_{Firma}_BANKACC BANKACC WITH(NOLOCK) ON BANKACC.LOGICALREF = BNFLINE.BNACCREF
    LEFT OUTER JOIN L_CURRENCYLIST WITH(NOLOCK) ON L_CURRENCYLIST.CURTYPE = BANKACC.CURRENCY
    WHERE BNFLINE.MODULENR = 7 AND BNFLINE.TRCODE = 2
      AND NOT (BANKACC.CODE LIKE '50.%' OR BANKACC.CARDTYPE IN (5, 6))
      AND CONVERT(VARCHAR, BNFLINE.DATE_, 23) = '{target_date}'
    ORDER BY BNFLINE.SOURCEFREF, BNFLINE.SIGN
    """
    try:
        engine = get_engine_by_id(1)
        with engine.connect() as conn:
            df = pd.read_sql(text(query), conn)
        return jsonify({'rows': df_to_json_safe(df)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bridge_app.route('/bridge/nakit/incoming')
def bridge_nakit_incoming():
    if not verify_key():
        return jsonify({'error': 'Yetkisiz erişim'}), 401
    target_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
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
    FROM LG_{Firma}_{Donem}_BNFLINE BNFLINE WITH(NOLOCK)
    LEFT OUTER JOIN LG_{Firma}_{Donem}_BNFICHE BNFICHE WITH(NOLOCK) ON BNFICHE.LOGICALREF=BNFLINE.SOURCEFREF AND BNFLINE.MODULENR=7 AND BNFLINE.TRCODE=BNFICHE.TRCODE
    LEFT OUTER JOIN LG_{Firma}_BANKACC BANKACC WITH(NOLOCK) ON BNFLINE.BNACCREF=BANKACC.LOGICALREF
    LEFT JOIN LG_{Firma}_CLCARD CL WITH(NOLOCK) ON BNFLINE.CLIENTREF=CL.LOGICALREF
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
    FROM LG_{Firma}_{Donem}_KSLINES K WITH(NOLOCK)
    LEFT JOIN LG_{Firma}_KSCARD KS WITH(NOLOCK) ON KS.LOGICALREF = K.CARDREF
    LEFT JOIN LG_{Firma}_CLCARD CL WITH(NOLOCK) ON K.VCARDREF = CL.LOGICALREF
    LEFT OUTER JOIN L_CURRENCYLIST WITH(NOLOCK) ON L_CURRENCYLIST.CURTYPE = K.TRCURR
    WHERE K.SIGN = 0 AND K.CANCELLED = 0 {virman_kasa_filter} AND CONVERT(VARCHAR, K.DATE_, 23) = '{target_date}'
    ORDER BY LOGICALREF
    """
    try:
        engine = get_engine_by_id(1)
        with engine.connect() as conn:
            df = pd.read_sql(text(query), conn)
        return jsonify({'rows': df_to_json_safe(df)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bridge_app.route('/bridge/nakit/outgoing')
def bridge_nakit_outgoing():
    if not verify_key():
        return jsonify({'error': 'Yetkisiz erişim'}), 401
    target_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
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
    FROM LG_{Firma}_{Donem}_BNFLINE BNFLINE WITH(NOLOCK)
    LEFT OUTER JOIN LG_{Firma}_{Donem}_BNFICHE BNFICHE WITH(NOLOCK) ON BNFICHE.LOGICALREF=BNFLINE.SOURCEFREF AND BNFLINE.MODULENR=7 AND BNFLINE.TRCODE=BNFICHE.TRCODE
    LEFT OUTER JOIN LG_{Firma}_BANKACC BANKACC WITH(NOLOCK) ON BNFLINE.BNACCREF=BANKACC.LOGICALREF
    LEFT JOIN LG_{Firma}_CLCARD CL WITH(NOLOCK) ON BNFLINE.CLIENTREF=CL.LOGICALREF
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
    FROM LG_{Firma}_{Donem}_KSLINES K WITH(NOLOCK)
    LEFT JOIN LG_{Firma}_KSCARD KS WITH(NOLOCK) ON KS.LOGICALREF = K.CARDREF
    LEFT JOIN LG_{Firma}_CLCARD CL WITH(NOLOCK) ON K.VCARDREF = CL.LOGICALREF
    LEFT OUTER JOIN L_CURRENCYLIST WITH(NOLOCK) ON L_CURRENCYLIST.CURTYPE = K.TRCURR
    WHERE K.SIGN = 1 AND K.CANCELLED = 0 {virman_kasa_filter} AND CONVERT(VARCHAR, K.DATE_, 23) = '{target_date}'
    ORDER BY LOGICALREF
    """
    try:
        engine = get_engine_by_id(1)
        with engine.connect() as conn:
            df = pd.read_sql(text(query), conn)
        return jsonify({'rows': df_to_json_safe(df)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bridge_app.route('/bridge/nakit/unbilled')
def bridge_nakit_unbilled():
    if not verify_key():
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
        engine = get_engine_by_id(1)
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

@bridge_app.route('/bridge/nakit/debug')
def bridge_nakit_debug():
    """Her endpoint'i tek tek test eder, hata varsa döner."""
    if not verify_key():
        return jsonify({'error': 'Yetkisiz erişim'}), 401
    
    results = {}
    today = datetime.now().strftime('%Y-%m-%d')
    
    tests = {
        'own_checks':       f"SELECT COUNT(*) FROM LG_{Firma}_{Donem}_CSCARD WHERE DOC IN (3,4) AND CURRSTAT NOT IN (8,6)",
        'customer_checks':  f"SELECT COUNT(*) FROM LG_{Firma}_{Donem}_CSCARD WHERE DOC = 1 AND CURRSTAT NOT IN (2,6,8)",
        'credits':          f"SELECT COUNT(*) FROM LG_{Firma}_BNCREPAYTR",
        'bank_balances':    f"SELECT COUNT(*) FROM LG_{Firma}_BANKACC WHERE ACTIVE=0 AND CARDTYPE IN (1,3)",
        'kasa_balances':    f"SELECT COUNT(*) FROM LG_{Firma}_KSCARD WHERE ACTIVE=0",
        'receivables':      f"SELECT COUNT(*) FROM LG_{Firma}_{Donem}_CLFLINE WHERE CANCELLED=0",
        'incoming_bank':    f"SELECT COUNT(*) FROM LG_{Firma}_{Donem}_BNFLINE WHERE SIGN=0 AND CONVERT(VARCHAR,DATE_,23)='{today}'",
        'fn_trcode_exists': "SELECT COUNT(*) FROM sys.objects WHERE type='FN' AND name='fn_trcode'",
        'db_name':          "SELECT DB_NAME()",
    }
    
    try:
        engine = get_engine_by_id(1)
        with engine.connect() as conn:
            for test_name, query in tests.items():
                try:
                    result = conn.execute(text(query)).fetchone()
                    results[test_name] = str(result[0]) if result else 'NULL'
                except Exception as e:
                    results[test_name] = f'HATA: {str(e)}'
    except Exception as e:
        return jsonify({'connection_error': str(e)}), 500
    
    return jsonify({'firma': Firma, 'donem': Donem, 'test_date': today, 'results': results})

if __name__ == '__main__':
    print("SQL Köprü API çalışıyor: http://127.0.0.1:5001")
    bridge_app.run(host='0.0.0.0', port=5001, debug=False)


