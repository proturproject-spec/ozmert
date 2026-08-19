"""
SQL Köprü API Servisi
Bu servis yerel bilgisayarda çalışır, SQL Server'a bağlanır ve
Render'daki ana uygulamaya veri sağlar.
"""
import os
import json
import urllib.parse
from flask import Flask, request, jsonify
from sqlalchemy import create_engine, text
import pandas as pd

bridge_app = Flask(__name__)

# --- GÜVENLİK: API anahtarı ile sadece Render erişebilir ---
BRIDGE_API_KEY = os.environ.get('BRIDGE_API_KEY', 'nexlog_bridge_2026_secure_xKj9')

# --- SQL BAĞLANTI (db_config.json'dan oku) ---
CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'db_config.json')

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
    key = request.headers.get('X-Bridge-Key') or request.args.get('key')
    return key == BRIDGE_API_KEY

# --- API ENDPOINTLERİ ---

@bridge_app.route('/bridge/health')
def health():
    return jsonify({'status': 'ok', 'service': 'SQL Bridge API'})

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
        
        # Pandas Timestamp'leri JSON'a uygun string'e çevir
        if 'TARIH' in df.columns:
            df['TARIH'] = df['TARIH'].astype(str)
        
        return jsonify({
            'rows': df.to_dict(orient='records'),
            'devir': devir
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print("SQL Köprü API çalışıyor: http://127.0.0.1:5001")
    bridge_app.run(host='0.0.0.0', port=5001, debug=False)
