"""
Cari Hesap Ekstresi Modülü ve SQL Sorguları
Bu modül cari hesap ekstre sorgularını ve veri işleme fonksiyonlarını yönetir.
"""
import json
import os
import io
import urllib
import pandas as pd
import requests as http_requests
from flask import Flask, render_template_string, request, Response, jsonify, abort
from sqlalchemy import create_engine, text

app = Flask(__name__)

# --- SQL SERVER BAĞLANTI YÖNETİMİ ---
def get_engine(db_name=None):
    try:
        from db_manager import get_engine as db_get_engine
        return db_get_engine(1)
    except Exception:
        pass
    server = 'UFUK-SERVER'
    username = 'MDT_REPORT'
    password = 'MDT_REPORT'
    target_db = db_name or 'UFUK2025'
    params = urllib.parse.quote_plus(f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={server};DATABASE={target_db};UID={username};PWD={password}")
    return create_engine(f"mssql+pyodbc:///?odbc_connect={params}")

DB_YEAR_PREFIX_MAP = {
    "2025": {"db": "UFUK2025", "prefix": "225"},
    "2026": {"db": "UFUK2025", "prefix": "226"}
}
DB_MAP = DB_YEAR_PREFIX_MAP

CARI_SETTINGS_FILE = os.path.join(os.path.dirname(__file__), 'cari_ekstre_settings.json')
DEFAULT_CARI_PREFIXES = ['120.01.', '120.02.', '120.03.']

def load_cari_settings():
    """cari_ekstre_settings.json dosyasından cari kod filtre öneklerini yükler."""
    if not os.path.exists(CARI_SETTINGS_FILE):
        default_settings = {'prefixes': DEFAULT_CARI_PREFIXES}
        save_cari_settings(default_settings)
        return default_settings
    try:
        with open(CARI_SETTINGS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, dict) and 'prefixes' in data and isinstance(data['prefixes'], list):
                return data
            elif isinstance(data, list):
                return {'prefixes': data}
            return {'prefixes': []}
    except Exception as e:
        print(f"Cari ayarları okuma hatası: {e}")
        return {'prefixes': []}

def save_cari_settings(settings):
    """Cari kod filtre öneklerini cari_ekstre_settings.json dosyasına kaydeder."""
    try:
        with open(CARI_SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"Cari ayarları kaydetme hatası: {e}")
        return False

def get_queries(year, prefixes=None):
    """
    Seçilen yıla ve veritabanı yapılandırmasında seçili firma/dönem koduna göre cari kart listesi, hareket tablosu ve devir bakiyesi SQL sorgularını döner.
    Eğer prefixes boş (filtre yok) ise tüm cari kartlar getirilir.
    """
    try:
        from db_manager import get_active_firm_period
        fp = get_active_firm_period(1)
        p = fp.get('firm_nr', '225')
        period = fp.get('period_nr', '01')
    except Exception:
        year_str = str(year)
        p = DB_YEAR_PREFIX_MAP.get(year_str, {}).get("prefix", "225")
        period = "01"
    
    if prefixes is None:
        cfg = load_cari_settings()
        prefixes = cfg.get('prefixes', [])
    
    if isinstance(prefixes, str):
        prefixes = [px.strip() for px in prefixes.split(',') if px.strip()]
        
    where_parts = []
    if prefixes:
        for prefix in prefixes:
            prefix_clean = str(prefix).strip()
            if not prefix_clean:
                continue
            if not prefix_clean.endswith('%'):
                prefix_sql = prefix_clean + '%'
            else:
                prefix_sql = prefix_clean
            prefix_sql = prefix_sql.replace("'", "''")
            where_parts.append(f"CODE LIKE '{prefix_sql}'")
            
    if where_parts:
        where_clause = f"WHERE ({' OR '.join(where_parts)})"
    else:
        # Filtre boşsa hiçbir kısıtlama uygulanmaz, tüm cariler listelenir
        where_clause = ""

    cards_query = f"SELECT CODE, DEFINITION_ FROM LG_{p}_CLCARD {where_clause} ORDER BY DEFINITION_"
    
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
             WHEN CLF.TRCODE = 34 THEN 'Alınan hizmet fat.'
             WHEN CLF.TRCODE = 36 THEN 'Alım iade fat.'
             WHEN CLF.TRCODE = 37 THEN 'Perakende satış fat.'
             WHEN CLF.TRCODE = 38 THEN 'Toptan satış fat.'
             WHEN CLF.TRCODE = 39 THEN 'Verilen hizmet faturası'
             WHEN CLF.TRCODE IN (61,62) THEN 'Çek/Senet Girişi'
             WHEN CLF.TRCODE IN (63,64) THEN 'Çek/Senet Çıkışı'
             WHEN CLF.TRCODE = 70 THEN 'Kredi kartı fişi'
             WHEN CLF.TRCODE = 71 THEN 'Kredi kartı iade'
             ELSE 'Diğer' END AS [ISLEM_TURU],
        CLF.TRANNO AS [FIS_NO],
        CAST(CLF.LINEEXP AS VARCHAR(MAX)) AS [ACIKLAMA],
        CASE WHEN CLF.SIGN = 0 THEN CLF.TRNET ELSE 0 END AS [BORC],
        CASE WHEN CLF.SIGN = 1 THEN CLF.TRNET ELSE 0 END AS [ALACAK]
    FROM LG_{p}_{period}_CLFLINE CLF
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
    FROM LG_{p}_{period}_CLFLINE CLF
    LEFT JOIN LG_{p}_CLCARD C ON C.LOGICALREF = CLF.CLIENTREF
    WHERE CLF.CANCELLED = 0 
    AND LTRIM(RTRIM(C.CODE)) = LTRIM(RTRIM(:cari_code)) 
    AND CAST(CLF.DATE_ AS DATE) < :date
    """
    return cards_query, base_query, devir_query

get_cari_queries = get_queries

def currency_formatter(x):
    if pd.isna(x) or x is None:
        return "0,00"
    return "{:,.2f}".format(float(x)).replace(",", "X").replace(".", ",").replace("X", ".")

format_currency = currency_formatter

def get_cari_ekstre_df(year, date_val, cari_code, use_bridge=False, bridge_url='', bridge_key=''):
    """
    Cari hesap ekstresini çeker, devir satırını ekler ve kümülatif bakiye sütununu hesaplayıp DataFrame döner.
    """
    if not cari_code:
        return pd.DataFrame()

    # Köprü Modu (Render / Canlı ortamda SQL köprüsü üzerinden veri çek)
    if use_bridge and bridge_url:
        try:
            resp = http_requests.get(
                f"{bridge_url.rstrip('/')}/bridge/cari-ekstre",
                params={'year': year, 'date': date_val, 'cari': cari_code},
                headers={'X-Bridge-Key': bridge_key},
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
        # Lokal doğrudan SQL bağlantısı
        engine = get_engine(DB_YEAR_PREFIX_MAP.get(str(year), {}).get("db", "UFUK2025"))
        _, base_q, devir_q = get_queries(year)
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

get_processed_df = get_cari_ekstre_df

def get_cariler_list(year="2026", prefixes=None, use_bridge=False, bridge_url='', bridge_key=''):
    """Cari kart listesini filtrelere göre çeker."""
    if prefixes is None:
        cfg = load_cari_settings()
        prefixes = cfg.get('prefixes', DEFAULT_CARI_PREFIXES)

    if use_bridge and bridge_url:
        try:
            params = {'year': year}
            if prefixes:
                params['prefixes'] = ','.join(prefixes) if isinstance(prefixes, list) else str(prefixes)
            resp = http_requests.get(
                f"{bridge_url.rstrip('/')}/bridge/cariler",
                params=params,
                headers={'X-Bridge-Key': bridge_key},
                timeout=15
            )
            return resp.json()
        except Exception as e:
            print(f"Bridge cariler hatası: {e}")
            return []
    try:
        engine = get_engine(DB_YEAR_PREFIX_MAP.get(str(year), {}).get("db", "UFUK2025"))
        cards_q, _, _ = get_queries(year, prefixes=prefixes)
        with engine.connect() as conn:
            return pd.read_sql(text(cards_q), conn).to_dict(orient='records')
    except Exception as e:
        print(f"Cari kartlar getirme hatası: {e}")
        return []

# --- STANDALONE HTML ŞABLONU ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <title>Cari Hareket Raporu</title>
    <link href="https://cdn.jsdelivr.net/npm/select2@4.1.0-rc.0/dist/css/select2.min.css" rel="stylesheet" />
    <script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/select2@4.1.0-rc.0/dist/js/select2.min.js"></script>
    <style>
        body { font-family: 'Segoe UI', sans-serif; margin: 0; background-color: #f4f7f6; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }
        .header-fixed { background: #fff; padding: 10px 20px; border-bottom: 2px solid #e0e0e0; }
        .top-bar { display: flex; align-items: center; gap: 15px; flex-wrap: nowrap; }
        .select2-container { flex: 1; min-width: 500px !important; max-width: 800px; }
        .select2-selection--single { height: 38px !important; display: flex !important; align-items: center; border: 1px solid #ccc !important; }
        .col-tarih { width: 90px; }
        .col-ozel { width: 80px; }
        .col-unvan { width: 250px; }
        .col-tur { width: 140px; }
        .col-fis { width: 160px; }
        .col-aciklama { width: auto; min-width: 400px; white-space: normal !important; word-break: break-all; }
        .col-para { width: 130px; }
        #dateInput, .select-year { padding: 6px; border: 1px solid #ccc; height: 38px; border-radius: 4px; box-sizing: border-box; }
        .table-wrapper { flex: 1; overflow: auto; margin: 15px; background: white; border-radius: 8px; border: 1px solid #ddd; }
        table { border-collapse: collapse; width: 100%; font-size: 13px; table-layout: fixed; min-width: 1500px; }
        th, td { padding: 12px 10px; border-bottom: 1px solid #ddd; text-align: left; vertical-align: top; }
        td:not(.col-aciklama) { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        thead th { position: sticky; top: 0; background: #2c3e50; color: white; z-index: 10; font-weight: 600; border-bottom: 2px solid #1a252f; }
        .text-right { text-align: right !important; font-family: 'Roboto Mono', monospace; }
        tbody tr:nth-child(even) { background-color: #d1e9ff; } 
        tbody tr:nth-child(odd) { background-color: #ffffff; }
        tbody tr:hover { background-color: #b3d9ff !important; }
        .devir-row { background-color: #fff59d !important; font-weight: bold; border-bottom: 2px solid #fbc02d !important; }
        .s-card { background: #ffffff; border: 1px solid #90caf9; padding: 5px 12px; border-radius: 6px; text-align: right; min-width: 110px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
        .s-card span { font-size: 15px; font-weight: bold; color: #0d47a1; }
        .btn-download { background-color: #2e7d32; color: white; padding: 0 20px; border-radius: 4px; cursor: pointer; border:none; font-weight: bold; height: 38px; transition: 0.2s; }
        .btn-download:hover { background-color: #1b5e20; }
    </style>
</head>
<body>
    <div class="header-fixed">
        <div class="top-bar">
            <select id="yearSelect" class="select-year">
                <option value="2026" selected>2026</option>
                <option value="2025">2025</option>
            </select>
            <input type="date" id="dateInput" value="2026-01-01">
            <select id="cariSelect" style="width: 100%"><option value="">-- Cari Seçiniz --</option></select>
            <button id="dlBtn" class="btn-download">EXCEL</button>
            <div id="summary" style="display: flex; gap: 10px; visibility: hidden;">
                <div class="s-card"><label style="display:block; font-size:10px; color:#555;">BORÇ</label><span id="tBorc">0,00</span></div>
                <div class="s-card"><label style="display:block; font-size:10px; color:#555;">ALACAK</label><span id="tAlacak">0,00</span></div>
                <div class="s-card" style="background:#e3f2fd; border-color:#2196f3;"><label style="display:block; font-size:10px; color:#1565c0;">BAKİYE</label><span id="tBakiye" style="color:#0d47a1;">0,00</span></div>
            </div>
        </div>
    </div>
    <div id="tableContainer" class="table-wrapper">
        <div style="text-align:center; padding:100px; color: #777; font-size: 16px;">Lütfen listelemek istediğiniz cari kartı yukarıdan seçin.</div>
    </div>
    <script>
        $(document).ready(function() {
            $('#cariSelect').select2({ placeholder: "Cari Ünvan veya Kod Yazarak Arayın...", width: 'resolve' });
            function loadCariler() {
                $.getJSON(`cariekstre/api/cariler?year=${$('#yearSelect').val()}`, function(data) {
                    let options = '<option value="">-- Cari Seçiniz --</option>';
                    data.forEach(c => { options += `<option value="${c.CODE}">${c.CODE} - ${c.DEFINITION_}</option>`; });
                    $('#cariSelect').html(options).trigger('change');
                });
            }
            loadCariler();
            $('#yearSelect').on('change', function() {
                $('#dateInput').val(`${$(this).val()}-01-01`);
                loadCariler();
            });
            function update() {
                const s = $('#cariSelect').val();
                if(!s) return;
                $('#tableContainer').html('<div style="text-align:center; padding:100px; font-size:18px;">Veriler çekiliyor, lütfen bekleyiniz...</div>');
                $.getJSON(`cariekstre/api/data?year=${$('#yearSelect').val()}&date=${$('#dateInput').val()}&cari=${encodeURIComponent(s)}`, function(data) {
                    if(data.error) alert(data.error);
                    else {
                        $('#tableContainer').html(data.html);
                        $('#tBorc').text(data.t_borc); 
                        $('#tAlacak').text(data.t_alacak); 
                        $('#tBakiye').text(data.t_bakiye);
                        $('#summary').css('visibility', 'visible');
                    }
                });
            }
            $('#dateInput, #cariSelect').on('change', update);
            $('#dlBtn').click(function() {
                const s = $('#cariSelect').val();
                if(s) window.location.href = `cariekstre/download?year=${$('#yearSelect').val()}&date=${$('#dateInput').val()}&cari=${encodeURIComponent(s)}`;
            });
        });
    </script>
</body>
</html>
"""

AUTH_TOKEN = "ufuk_rapor_portal_2026_secure_key"

@app.route("/")
def index(): return render_template_string(HTML_TEMPLATE)

@app.route("/api/cariler")
def api_cariler():
    year = request.args.get("year", "2026")
    try:
        engine = get_engine(DB_YEAR_PREFIX_MAP.get(str(year), {}).get("db", "UFUK2025"))
        cariler = pd.read_sql(get_queries(year)[0], engine).to_dict(orient='records')
        return jsonify(cariler)
    except: return jsonify([])

@app.route("/api/data")
def api_data():
    y, d, c = request.args.get("year"), request.args.get("date"), request.args.get("cari")
    try:
        df = get_cari_ekstre_df(y, d, c)
        if df.empty: return jsonify({"html": "<div style='padding:50px; text-align:center;'>Veri bulunamadı.</div>"})
        t_borc, t_alacak = df['BORC'].sum(), df['ALACAK'].sum()
        
        html = '<table><thead><tr>'
        html += '<th class="col-tarih">TARIH</th><th class="col-ozel">OZEL_KOD</th><th class="col-unvan">CARI_UNVAN</th>'
        html += '<th class="col-tur">ISLEM_TURU</th><th class="col-fis">FIS_NO</th><th class="col-aciklama">ACIKLAMA</th>'
        html += '<th class="col-para">BORC</th><th class="col-para">ALACAK</th><th class="col-para">BAKIYE</th></tr></thead><tbody>'
        
        for _, row in df.iterrows():
            is_devir = row['CARI_UNVAN'] == 'Önceki Dönemden Devir'
            tr_date = row['TARIH'].strftime('%d.%m.%Y') if row['TARIH'] is not None and not pd.isna(row['TARIH']) else 'DEVİR'
            cls_row = ' class="devir-row"' if is_devir else ''
            html += f'<tr{cls_row}>'
            html += f'<td class="col-tarih">{tr_date}</td>'
            html += f'<td class="col-ozel">{row["OZEL_KOD"]}</td>'
            html += f'<td class="col-unvan">{row["CARI_UNVAN"]}</td>'
            html += f'<td class="col-tur">{row["ISLEM_TURU"]}</td>'
            html += f'<td class="col-fis">{row["FIS_NO"]}</td>'
            html += f'<td class="col-aciklama">{row["ACIKLAMA"]}</td>'
            html += f'<td class="text-right col-para">{currency_formatter(row["BORC"])}</td>'
            html += f'<td class="text-right col-para">{currency_formatter(row["ALACAK"])}</td>'
            html += f'<td class="text-right col-para">{currency_formatter(row["BAKIYE"])}</td></tr>'
        html += '</tbody></table>'
        
        return jsonify({
            "html": html, 
            "t_borc": currency_formatter(t_borc), 
            "t_alacak": currency_formatter(t_alacak), 
            "t_bakiye": currency_formatter(t_borc - t_alacak)
        })
    except Exception as e: return jsonify({"error": str(e)})

@app.route("/download")
def download():
    y, d, c = request.args.get("year"), request.args.get("date"), request.args.get("cari")
    df = get_cari_ekstre_df(y, d, c)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Ekstre')
    output.seek(0)
    return Response(output.read(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename=Ekstre_{c}.xlsx"})

if __name__ == '__main__':
    app.run(port=5003, debug=True)
