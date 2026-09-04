# tahilsat konuşma kodu



import pandas as pd
import pyodbc
import io
import sqlite3
import json
import os
from datetime import datetime
from flask import Flask, render_template_string, request, send_file, abort, jsonify
from waitress import serve

app = Flask(__name__)

def format_date_tr(date_str):
    if not date_str or date_str == '-':
        return date_str
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        days = ['Pazartesi', 'Salı', 'Çarşamba', 'Perşembe', 'Cuma', 'Cumartesi', 'Pazar']
        return dt.strftime(f'%d.%m.%Y {days[dt.weekday()]}')
    except Exception:
        try:
            dt = datetime.strptime(date_str, '%d.%m.%Y')
            days = ['Pazartesi', 'Salı', 'Çarşamba', 'Perşembe', 'Cuma', 'Cumartesi', 'Pazar']
            return dt.strftime(f'%d.%m.%Y {days[dt.weekday()]}')
        except Exception:
            return date_str

app.jinja_env.filters['format_date_tr'] = format_date_tr

# --- KONUŞMA VERİTABANI (SQLite) ---
KONUSMA_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tahsilat_konusmalari.db')

def init_konusma_db():
    conn = sqlite3.connect(KONUSMA_DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS konusmalar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cari_kodu TEXT NOT NULL,
            cari_unvan TEXT,
            kategoriler TEXT,
            kesin_gelecek_tarih TEXT,
            soz_verilen_tarih TEXT,
            kiminle TEXT,
            aciklama TEXT,
            olusturma_tarihi TEXT DEFAULT (datetime('now','localtime')),
            guncelleme_tarihi TEXT DEFAULT (datetime('now','localtime')),
            tutar REAL,
            doviz TEXT DEFAULT 'TL',
            kaydeden TEXT,
            vade_tarih TEXT,
            itiraz_tarih TEXT,
            ulasilamadi_tarih TEXT,
            cek_tarih TEXT
        )
    ''')
    try:
        c.execute("ALTER TABLE konusmalar ADD COLUMN tutar REAL")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE konusmalar ADD COLUMN doviz TEXT DEFAULT 'TL'")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE konusmalar ADD COLUMN kaydeden TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE konusmalar ADD COLUMN vade_tarih TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE konusmalar ADD COLUMN itiraz_tarih TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE konusmalar ADD COLUMN ulasilamadi_tarih TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE konusmalar ADD COLUMN cek_tarih TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

init_konusma_db()

# --- CARİ UYARILARI KONFİGÜRASYONU ---
UYARILAR_JSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cari_uyarilar.json')

def load_cari_uyarilar():
    if not os.path.exists(UYARILAR_JSON_PATH):
        try:
            with open(UYARILAR_JSON_PATH, 'w', encoding='utf-8') as f:
                json.dump({}, f, ensure_ascii=False, indent=4)
        except Exception:
            pass
        return {}
    try:
        with open(UYARILAR_JSON_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Uyari okuma hatası: {e}")
        return {}


# --- KONFİGÜRASYON ---
DB_CONFIG = {
    'server': 'UFUK-SERVER',
    'database': 'UFUK2025',
    'username': 'MDT_REPORT',
    'password': 'MDT_REPORT',
    'driver': '{SQL Server}'
}

AKTIF_YIL = "226"
GECMIS_YILLAR = ["225", "226"]
DONEM = "01"

def get_db_connection():
    try:
        from db_manager import get_connection_config
        cfg = get_connection_config(1)
        if cfg:
            driver = cfg.get('driver', 'SQL Server')
            server = cfg.get('server', DB_CONFIG['server'])
            port = cfg.get('port', '1433')
            database = cfg.get('database', DB_CONFIG['database'])
            server_part = f"{server},{port}" if port and str(port).strip() != "1433" else server
            if cfg.get('trusted_connection'):
                conn_str = f"DRIVER={{{driver}}};SERVER={server_part};DATABASE={database};Trusted_Connection=yes;"
            else:
                conn_str = f"DRIVER={{{driver}}};SERVER={server_part};DATABASE={database};UID={cfg.get('username', DB_CONFIG['username'])};PWD={cfg.get('password', DB_CONFIG['password'])};"
            if cfg.get('trust_server_certificate'):
                conn_str += "TrustServerCertificate=yes;"
            return pyodbc.connect(conn_str)
    except Exception as e:
        print(f"db_manager bağlantı fallback: {e}")
    conn_str = f"DRIVER={DB_CONFIG['driver']};SERVER={DB_CONFIG['server']};DATABASE={DB_CONFIG['database']};UID={DB_CONFIG['username']};PWD={DB_CONFIG['password']}"
    return pyodbc.connect(conn_str)

def get_ek_toplam_rakam():
    try:
        conn = get_db_connection()
        ek_sql = """
        SELECT
            SUM((SHK.KDVsizBirimFiyat * AD.DovizAlis) * (1 + (CASE WHEN SHK.KDVDurumu = 'M' THEN 0 ELSE SHK.KDVOrani END / 100.0))) AS GENEL_TOPLAM
        FROM LojistikERP_UFUK.dbo.DY_STOK_HAREKETLERI SHK
        INNER JOIN LojistikERP_UFUK.dbo.DY_FATURALAR FT ON FT.FaturaKodu = SHK.FaturaKodu
        LEFT JOIN LojistikERP_UFUK.dbo.CH_ANLIK_DOVIZ_KURLARI AD ON AD.PB = SHK.PB AND AD.StokHareketKodu = SHK.StokHareketKodu
        WHERE FT.FaturaTipiKodu = 2
          AND FT.ResmiMi = 1
          AND (FT.FaturaNo IS NULL OR LTRIM(RTRIM(FT.FaturaNo)) = '')
          AND FT.FaturaTarihi >= '2026-01-01';
        """
        df_ek = pd.read_sql(ek_sql, conn)
        conn.close()
        return df_ek['GENEL_TOPLAM'].iloc[0] if not df_ek.empty else 0
    except Exception as e:
        print(f"Ek Sorgu Hatası: {e}")
        return 0

def get_fatura_data(secilen_tarih, secili_cariler=None, secili_temsilciler=None):
    secilen_tarih_dt = datetime.strptime(secilen_tarih, '%Y-%m-%d')
    rapor_tarihi_sql = secilen_tarih_dt.strftime('%Y-%m-%d')
    conn = get_db_connection()
 
    doviz_map = {0: "TL", 1: "USD", 2: "EUR", 11: "GBP", 20: "EUR"}

    bakiye_sql = f"""
    SELECT
        C.CODE AS [CARI_KODU],
        C.DEFINITION_ AS [CARI_UNVAN],
        C.CODE + ' - ' + C.DEFINITION_ AS [CARI_FULL],
        ISNULL(NULLIF(C.SPECODE2, ''), 'TANIMSIZ') AS [TEMSILCI],
        ROUND(SUM(CASE WHEN CLF.SIGN = 0 THEN CLF.AMOUNT ELSE -CLF.AMOUNT END), 2) AS BAKIYE,
        ROUND(SUM(CASE
            WHEN CLF.SIGN = 0 THEN (CASE WHEN CLF.TRCURR = C.CCURRENCY THEN CLF.TRNET ELSE 0 END)
            ELSE -(CASE WHEN CLF.TRCURR = C.CCURRENCY THEN CLF.TRNET ELSE 0 END)
        END), 2) AS DOVIZ_BAKIYE,
        C.CCURRENCY AS CARI_DOVIZ_TIPI
    FROM LG_{AKTIF_YIL}_{DONEM}_CLFLINE CLF WITH(NOLOCK)
    INNER JOIN LG_{AKTIF_YIL}_CLCARD C WITH(NOLOCK) ON C.LOGICALREF = CLF.CLIENTREF
    WHERE CLF.CANCELLED = 0
      AND (C.CODE LIKE '120.01.%' OR C.CODE LIKE '120.05.%')
      AND CLF.DATE_ <= '{rapor_tarihi_sql}'
    GROUP BY C.CODE, C.DEFINITION_, C.SPECODE2, C.CCURRENCY
    HAVING ABS(SUM(CASE WHEN CLF.SIGN = 0 THEN CLF.AMOUNT ELSE -CLF.AMOUNT END)) > 0.05
    ORDER BY C.CODE
    """
 
    df_bakiyeler = pd.read_sql(bakiye_sql, conn)
 
    if df_bakiyeler.empty:
        conn.close()
        return pd.DataFrame(), [], [], {}, {}, {}, 0, pd.DataFrame()

    tum_cari_listesi = sorted(df_bakiyeler['CARI_FULL'].unique().tolist())
    tum_temsilci_listesi = sorted(df_bakiyeler['TEMSILCI'].unique().tolist())

    if secili_temsilciler:
        df_bakiyeler = df_bakiyeler[df_bakiyeler['TEMSILCI'].isin(secili_temsilciler)]
    if secili_cariler:
        df_bakiyeler = df_bakiyeler[df_bakiyeler['CARI_FULL'].isin(secili_cariler)]

    fatura_sorgulari = []
    for f in GECMIS_YILLAR:
        fatura_sql = f"""
        SELECT
            INV.DATE_ AS [FATURA_TARIHI],
            ISNULL((SELECT MAX(DATE_) FROM LG_{f}_{DONEM}_PAYTRANS PT WITH(NOLOCK) WHERE PT.FICHEREF = INV.LOGICALREF AND PT.MODULENR = 4 AND PT.CANCELLED = 0), INV.DATE_) AS [VADE_TARIHI],
            DATEDIFF(DAY, INV.DATE_, ISNULL((SELECT MAX(DATE_) FROM LG_{f}_{DONEM}_PAYTRANS PT WITH(NOLOCK) WHERE PT.FICHEREF = INV.LOGICALREF AND PT.MODULENR = 4 AND PT.CANCELLED = 0), INV.DATE_)) AS [VADE_GUN_SAYISI],
            INV.FICHENO + ' / ' + ISNULL(NULLIF(INV.DOCODE, ''), '-') AS [BELGE_NO],
            CL.CODE AS [CARI_KODU],
            INV.NETTOTAL AS [FATURA_TUTARI],
            INV.TRCURR AS [DOVIZ_TIPI],
            INV.TRNET AS [DOVIZ_TUTARI]
        FROM LG_{f}_{DONEM}_INVOICE INV WITH(NOLOCK)
        INNER JOIN LG_{f}_CLCARD CL WITH(NOLOCK) ON INV.CLIENTREF = CL.LOGICALREF
        WHERE INV.TRCODE IN (8, 9) AND INV.CANCELLED = 0 AND INV.DATE_ <= '{rapor_tarihi_sql}'
        """
        fatura_sorgulari.append(fatura_sql)

    df_faturalar = pd.read_sql(" UNION ALL ".join(fatura_sorgulari), conn)
    conn.close()
 
    df_faturalar = df_faturalar.sort_values(by=['CARI_KODU', 'FATURA_TARIHI'], ascending=[True, False])
 
    final_result = []
    toplam_gecikmis, toplam_vade, toplam_genel = {}, {}, {}
    toplam_gecikme_gun_carpimi = 0
    t_gecikmis_tl_payda = 0
    firma_gecikme_analizi = []

    for _, b_row in df_bakiyeler.iterrows():
        c_kod = b_row['CARI_KODU']
        c_full = b_row['CARI_FULL']
        c_birim = doviz_map.get(b_row['CARI_DOVIZ_TIPI'], "TL")
 
        ana_bakiye = float(b_row['DOVIZ_BAKIYE']) if c_birim != "TL" else float(b_row['BAKIYE'])
        tl_bakiye = float(b_row['BAKIYE'])

        if c_birim not in toplam_genel:
            toplam_genel[c_birim] = 0
            toplam_gecikmis[c_birim] = 0
            toplam_vade[c_birim] = 0
 
        toplam_genel[c_birim] += round(ana_bakiye, 2)
        kalan_bakiye_ana = round(ana_bakiye, 2)
        kalan_bakiye_tl_izleme = round(tl_bakiye, 2)
        cari_toplam_gecikme_tl = 0
 
        cari_faturalari = df_faturalar[df_faturalar['CARI_KODU'] == c_kod]
 
        for _, f_row in cari_faturalari.iterrows():
            if kalan_bakiye_ana <= 0.005: break
 
            f_tl = float(f_row['FATURA_TUTARI'])
            f_dvz = float(f_row['DOVIZ_TUTARI'])
            f_birim = doviz_map.get(f_row['DOVIZ_TIPI'], "TL")

            if c_birim != "TL" and f_birim == c_birim:
                acik_ana = min(kalan_bakiye_ana, f_dvz)
                oran = acik_ana / f_dvz if f_dvz > 0 else 0
                acik_tl = f_tl * oran
            else:
                acik_tl = min(kalan_bakiye_tl_izleme, f_tl)
                oran = acik_tl / f_tl if f_tl > 0 else 0
                acik_ana = f_dvz * oran if c_birim != "TL" else acik_tl

            vade_dt = pd.to_datetime(f_row['VADE_TARIHI'])
            gecikme = (secilen_tarih_dt - vade_dt).days

            final_result.append({
                'TEMSILCI': b_row['TEMSILCI'],
                'CARI_UNVAN': c_full,
                'CARI_KODU': c_kod,
                'FATURA TARİHİ': f_row['FATURA_TARIHI'],
                'VADE TARİHİ': f_row['VADE_TARIHI'],
                'VADE_GUN': int(f_row['VADE_GUN_SAYISI']) if pd.notnull(f_row['VADE_GUN_SAYISI']) else 0,
                'BELGE NO': f_row['BELGE_NO'],
                'ACIK_TUTAR': round(acik_ana, 2),
                'BIRIM': c_birim,
                'GECİKME GÜN': int(gecikme)
            })
 
            if gecikme >= 0:
                toplam_gecikmis[c_birim] += acik_ana
                toplam_gecikme_gun_carpimi += (acik_tl * gecikme)
                t_gecikmis_tl_payda += acik_tl
                cari_toplam_gecikme_tl += acik_tl
            else:
                toplam_vade[c_birim] += acik_ana

            kalan_bakiye_ana -= acik_ana
            kalan_bakiye_tl_izleme -= acik_tl

        if kalan_bakiye_ana > 0.05:
            toplam_gecikmis[c_birim] += kalan_bakiye_ana
            final_result.append({
                'TEMSILCI': b_row['TEMSILCI'], 'CARI_UNVAN': c_full, 'CARI_KODU': c_kod,
                'FATURA TARİHİ': None, 'VADE TARİHİ': None, 'VADE_GUN': 0, 'BELGE NO': 'DEVRALAN BAKİYE',
                'ACIK_TUTAR': round(kalan_bakiye_ana, 2), 'BIRIM': c_birim, 'GECİKME GÜN': 999
            })
            toplam_gecikme_gun_carpimi += (kalan_bakiye_tl_izleme * 30)
            t_gecikmis_tl_payda += kalan_bakiye_tl_izleme
            cari_toplam_gecikme_tl += kalan_bakiye_tl_izleme
        elif kalan_bakiye_ana < -0.05:
            toplam_vade[c_birim] += kalan_bakiye_ana
            final_result.append({
                'TEMSILCI': b_row['TEMSILCI'], 'CARI_UNVAN': c_full, 'CARI_KODU': c_kod,
                'FATURA TARİHİ': None, 'VADE TARİHİ': None, 'VADE_GUN': 0, 'BELGE NO': 'ALACAK BAKİYESİ',
                'ACIK_TUTAR': round(kalan_bakiye_ana, 2), 'BIRIM': c_birim, 'GECİKME GÜN': -999
            })

        if cari_toplam_gecikme_tl > 1:
            firma_gecikme_analizi.append({'CARI': c_full, 'GECIKMIS_TL': cari_toplam_gecikme_tl})

    ort_gecikme = int(round(toplam_gecikme_gun_carpimi / t_gecikmis_tl_payda)) if t_gecikmis_tl_payda > 0 else 0
    toplam_gecikmis = {k: round(v, 2) for k, v in toplam_gecikmis.items()}
    toplam_vade = {k: round(v, 2) for k, v in toplam_vade.items()}
    toplam_genel = {k: round(v, 2) for k, v in toplam_genel.items()}

    df_risk = pd.DataFrame(firma_gecikme_analizi)
    if not df_risk.empty:
        df_risk = df_risk.sort_values(by='GECIKMIS_TL', ascending=False).head(10)

    return pd.DataFrame(final_result), tum_cari_listesi, tum_temsilci_listesi, toplam_gecikmis, toplam_vade, toplam_genel, ort_gecikme, df_risk

def get_short_bank_name(tur, aciklama):
    if tur == 'BANKA':
        acik_upper = aciklama.upper()
        if 'GARANT' in acik_upper:
            return 'Garanti'
        elif 'ZİRAAT' in acik_upper or 'ZIRAAT' in acik_upper:
            return 'Ziraat'
        elif 'YAPI' in acik_upper or 'Y.ve KRED' in acik_upper or 'KREDİ' in acik_upper or 'KREDI' in acik_upper:
            return 'Yapı Kredi'
        elif 'AKBANK' in acik_upper:
            return 'Akbank'
        elif 'VAKIF' in acik_upper:
            return 'Vakıfbank'
        elif 'HALK' in acik_upper:
            return 'Halkbank'
        elif 'TEB' in acik_upper or 'EKONOM' in acik_upper:
            return 'TEB'
        elif 'İŞ' in acik_upper or 'IS ' in acik_upper or 'İS ' in acik_upper:
            return 'İş Bankası'
        elif 'QNB' in acik_upper or 'FINANS' in acik_upper or 'FİNANS' in acik_upper:
            return 'QNB Finansbank'
        elif 'DENİZ' in acik_upper or 'DENIZ' in acik_upper:
            return 'Denizbank'
        elif 'ING' in acik_upper:
            return 'ING'
        elif 'KUVEYT' in acik_upper:
            return 'Kuveyt Türk'
        elif 'ALBARAKA' in acik_upper:
            return 'Albaraka'
        else:
            words = aciklama.split()
            return " ".join(words[:2]) if words else 'Banka'
    elif tur == 'CEK/SENET':
        acik_upper = aciklama.upper()
        label = 'Çek' if ('CEK' in acik_upper or 'ÇEK' in acik_upper) else 'Senet'
        if 'BANKA:' in acik_upper:
            parts = aciklama.split('Banka:')
            if len(parts) > 1:
                bank_part = parts[1].replace(')', '').strip().split()
                if bank_part:
                    return f"{label} ({bank_part[0]})"
        return label
    elif tur == 'KASA':
        return 'Kasa'
    return 'Diğer'

def get_tahsilat_listesi(secilen_tarih, df_aging, secili_cariler=None, secili_temsilciler=None):
    doviz_map = {0: "TL", 1: "USD", 2: "EUR", 11: "GBP", 20: "EUR"}
 
    conn = get_db_connection()
 
    query = f"""
    SELECT
        'BANKA' AS TUR,
        B.DATE_ AS TARIH,
        C.CODE AS CARI_KODU,
        C.DEFINITION_ AS CARI_UNVAN,
        C.CODE + ' - ' + C.DEFINITION_ AS CARI_FULL,
        ISNULL(NULLIF(C.SPECODE2, ''), 'TANIMSIZ') AS TEMSILCI,
        CASE WHEN B.TRCURR = 0 THEN B.AMOUNT ELSE B.TRNET END AS TUTAR,
        BA.DEFINITION_ + ' (' + BA.CODE + ') - ' + ISNULL(B.LINEEXP, '') AS ACIKLAMA,
        B.TRCURR AS DOVIZ_TIPI,
        NULL AS VADE_TARIHI
    FROM LG_{AKTIF_YIL}_{DONEM}_BNFLINE B WITH(NOLOCK)
    INNER JOIN LG_{AKTIF_YIL}_CLCARD C WITH(NOLOCK) ON C.LOGICALREF = B.CLIENTREF
    LEFT JOIN LG_{AKTIF_YIL}_BANKACC BA WITH(NOLOCK) ON BA.LOGICALREF = B.BNACCREF
    WHERE B.TRCODE = 3 AND B.CANCELLED = 0 AND B.DATE_ = '{secilen_tarih}'
      AND (C.CODE LIKE '120.01.%' OR C.CODE LIKE '120.05.%')
 
    UNION ALL
 
    SELECT
        'CEK/SENET' AS TUR,
        CS.DATE_ AS TARIH,
        C.CODE AS CARI_KODU,
        C.DEFINITION_ AS CARI_UNVAN,
        C.CODE + ' - ' + C.DEFINITION_ AS CARI_FULL,
        ISNULL(NULLIF(C.SPECODE2, ''), 'TANIMSIZ') AS TEMSILCI,
        CASE WHEN CARD.TRCURR = 0 THEN CARD.AMOUNT ELSE CARD.TRNET END AS TUTAR,
        CASE WHEN CARD.DOC = 1 THEN 'CEK' ELSE 'SENET' END + ' (Seri: ' + CARD.NEWSERINO + ', Vade: ' + CONVERT(VARCHAR(10), CARD.DUEDATE, 104) + ', Banka: ' + ISNULL(CARD.BANKNAME, '') + ')' AS ACIKLAMA,
        CARD.TRCURR AS DOVIZ_TIPI,
        CARD.DUEDATE AS VADE_TARIHI
    FROM LG_{AKTIF_YIL}_01_CSTRANS CS WITH(NOLOCK)
    INNER JOIN LG_{AKTIF_YIL}_01_CSCARD CARD WITH(NOLOCK) ON CARD.LOGICALREF = CS.CSREF
    INNER JOIN LG_{AKTIF_YIL}_CLCARD C WITH(NOLOCK) ON C.LOGICALREF = CS.CARDREF
    WHERE CS.TRCODE IN (1, 2) AND CS.CANCELLED = 0 AND CS.DATE_ = '{secilen_tarih}'
      AND (C.CODE LIKE '120.01.%' OR C.CODE LIKE '120.05.%')
 
    UNION ALL
 
    SELECT
        'KASA' AS TUR,
        K.DATE_ AS TARIH,
        C.CODE AS CARI_KODU,
        C.DEFINITION_ AS CARI_UNVAN,
        C.CODE + ' - ' + C.DEFINITION_ AS CARI_FULL,
        ISNULL(NULLIF(C.SPECODE2, ''), 'TANIMSIZ') AS TEMSILCI,
        CASE WHEN K.TRCURR = 0 THEN K.AMOUNT ELSE K.TRNET END AS TUTAR,
        KS.NAME + ' - ' + ISNULL(K.LINEEXP, '') AS ACIKLAMA,
        K.TRCURR AS DOVIZ_TIPI,
        NULL AS VADE_TARIHI
    FROM LG_{AKTIF_YIL}_{DONEM}_KSLINES K WITH(NOLOCK)
    INNER JOIN LG_{AKTIF_YIL}_CLCARD C WITH(NOLOCK) ON C.LOGICALREF = K.VCARDREF
    LEFT JOIN LG_{AKTIF_YIL}_KSCARD KS WITH(NOLOCK) ON KS.LOGICALREF = K.CARDREF
    WHERE K.TRCODE = 11 AND K.CANCELLED = 0 AND K.DATE_ = '{secilen_tarih}'
      AND (C.CODE LIKE '120.01.%' OR C.CODE LIKE '120.05.%')
    """
 
    df_coll = pd.read_sql(query, conn)
    conn.close()
 
    if df_coll.empty:
        return []
 
    if secili_temsilciler:
        df_coll = df_coll[df_coll['TEMSILCI'].isin(secili_temsilciler)]
    if secili_cariler:
        df_coll = df_coll[df_coll['CARI_FULL'].isin(secili_cariler)]
 
    grouped_coll = {}
    for _, col_row in df_coll.iterrows():
        c_kod = col_row['CARI_KODU']
        c_unvan = col_row['CARI_UNVAN']
        tutar = float(col_row['TUTAR'])
        doviz = doviz_map.get(col_row['DOVIZ_TIPI'], 'TL')
        tur = col_row['TUR']
        aciklama = col_row['ACIKLAMA']
        bank_short = get_short_bank_name(tur, aciklama)
 
        if c_kod not in grouped_coll:
            grouped_coll[c_kod] = {
                'CARI_KODU': c_kod,
                'CARI_UNVAN': c_unvan,
                'TUTAR': 0.0,
                'DOVIZ': doviz,
                'BANKS': set(),
                'ACIKLAMALAR': [],
                'VADE_TARIHLER': []
            }
        grouped_coll[c_kod]['TUTAR'] += tutar
        grouped_coll[c_kod]['BANKS'].add(bank_short)
        grouped_coll[c_kod]['ACIKLAMALAR'].append(f"{bank_short}: {aciklama}")
        vade_dt = col_row['VADE_TARIHI']
        if pd.notnull(vade_dt):
            vade_str = vade_dt.strftime('%d.%m.%Y')
            grouped_coll[c_kod]['VADE_TARIHLER'].append(vade_str)
 
    results = []
    for c_kod, item in grouped_coll.items():
        tutar = item['TUTAR']
        doviz = item['DOVIZ']
        c_unvan = item['CARI_UNVAN']
        banks_str = ", ".join(sorted(list(item['BANKS'])))
        details_str = "; ".join(item['ACIKLAMALAR'])
 
        overdue = 0
        not_due = 0
 
        if not df_aging.empty:
            cust_aging = df_aging[df_aging['CARI_KODU'] == c_kod]
            if not cust_aging.empty:
                overdue = cust_aging[cust_aging['GECİKME GÜN'] >= 0]['ACIK_TUTAR'].sum()
                not_due = cust_aging[cust_aging['GECİKME GÜN'] < 0]['ACIK_TUTAR'].sum()
 
        kalan_para = overdue + not_due
 
        # Calculate weighted average days overdue for Günü Geçen
        weighted_avg_days = 0
        if not df_aging.empty:
            cust_aging = df_aging[df_aging['CARI_KODU'] == c_kod]
            overdue_invoices = cust_aging[cust_aging['GECİKME GÜN'] >= 0]
            if not overdue_invoices.empty and overdue > 0.05:
                weighted_sum = 0.0
                for _, inv_row in overdue_invoices.iterrows():
                    days = 30 if inv_row['GECİKME GÜN'] == 999 else inv_row['GECİKME GÜN']
                    weighted_sum += float(inv_row['ACIK_TUTAR']) * days
                weighted_avg_days = int(round(weighted_sum / overdue))
 
        vades_str = ", ".join(sorted(list(set(item['VADE_TARIHLER'])))) if item['VADE_TARIHLER'] else '-'
        results.append({
            'CARI_KODU': c_kod,
            'CARI_UNVAN': c_unvan,
            'BANKA': banks_str,
            'ACIKLAMA': details_str,
            'TUTAR': tutar,
            'DOVIZ': doviz,
            'CARI_BAKIYE': kalan_para,
            'GUNU_GELMEYEN': not_due,
            'GUNU_GECEN': overdue,
            'GUNU_GECEN_KALAN': max(0.0, overdue - tutar),
            'ORT_GECIKME_GUN': weighted_avg_days,
            'VADE_TARIHI': vades_str
        })
 
    def tr_sort_key(item):
        translation_table = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosucgiosu")
        return item['CARI_UNVAN'].translate(translation_table).lower()
 
    results.sort(key=tr_sort_key)
    return results

def get_doviz_tahsilat_listesi(baslangic_tarihi='2026-01-01'):
    doviz_map = {0: "TL", 1: "USD", 2: "EUR", 11: "GBP", 20: "EUR"}
 
    conn = get_db_connection()
 
    query = f"""
    SELECT
        'BANKA' AS TUR,
        B.DATE_ AS TARIH,
        C.CODE AS CARI_KODU,
        C.DEFINITION_ AS CARI_UNVAN,
        B.AMOUNT AS TL_TUTAR,
        B.TRNET AS DOVIZ_TUTAR,
        B.TRCURR AS DOVIZ_TIPI,
        BA.DEFINITION_ + ' (' + BA.CODE + ') - ' + ISNULL(B.LINEEXP, '') AS ACIKLAMA,
        NULL AS VADE_TARIHI
    FROM LG_{AKTIF_YIL}_{DONEM}_BNFLINE B WITH(NOLOCK)
    INNER JOIN LG_{AKTIF_YIL}_CLCARD C WITH(NOLOCK) ON C.LOGICALREF = B.CLIENTREF
    LEFT JOIN LG_{AKTIF_YIL}_BANKACC BA WITH(NOLOCK) ON BA.LOGICALREF = B.BNACCREF
    WHERE B.TRCODE = 3 AND B.CANCELLED = 0 AND B.DATE_ >= ?
      AND (C.CODE LIKE '120.01.%' OR C.CODE LIKE '120.05.%')
 
    UNION ALL
 
    SELECT
        'CEK/SENET' AS TUR,
        CS.DATE_ AS TARIH,
        C.CODE AS CARI_KODU,
        C.DEFINITION_ AS CARI_UNVAN,
        CARD.AMOUNT AS TL_TUTAR,
        CARD.TRNET AS DOVIZ_TUTAR,
        CARD.TRCURR AS DOVIZ_TIPI,
        CASE WHEN CARD.DOC = 1 THEN 'CEK' ELSE 'SENET' END + ' (Seri: ' + CARD.NEWSERINO + ', Vade: ' + CONVERT(VARCHAR(10), CARD.DUEDATE, 104) + ', Banka: ' + ISNULL(CARD.BANKNAME, '') + ')' AS ACIKLAMA,
        CARD.DUEDATE AS VADE_TARIHI
    FROM LG_{AKTIF_YIL}_01_CSTRANS CS WITH(NOLOCK)
    INNER JOIN LG_{AKTIF_YIL}_01_CSCARD CARD WITH(NOLOCK) ON CARD.LOGICALREF = CS.CSREF
    INNER JOIN LG_{AKTIF_YIL}_CLCARD C WITH(NOLOCK) ON C.LOGICALREF = CS.CARDREF
    WHERE CS.TRCODE IN (1, 2) AND CS.CANCELLED = 0 AND CS.DATE_ >= ?
      AND (C.CODE LIKE '120.01.%' OR C.CODE LIKE '120.05.%')
 
    UNION ALL
 
    SELECT
        'KASA' AS TUR,
        K.DATE_ AS TARIH,
        C.CODE AS CARI_KODU,
        C.DEFINITION_ AS CARI_UNVAN,
        K.AMOUNT AS TL_TUTAR,
        K.TRNET AS DOVIZ_TUTAR,
        K.TRCURR AS DOVIZ_TIPI,
        KS.NAME + ' - ' + ISNULL(K.LINEEXP, '') AS ACIKLAMA,
        NULL AS VADE_TARIHI
    FROM LG_{AKTIF_YIL}_{DONEM}_KSLINES K WITH(NOLOCK)
    INNER JOIN LG_{AKTIF_YIL}_CLCARD C WITH(NOLOCK) ON C.LOGICALREF = K.VCARDREF
    LEFT JOIN LG_{AKTIF_YIL}_KSCARD KS WITH(NOLOCK) ON KS.LOGICALREF = K.CARDREF
    WHERE K.TRCODE = 11 AND K.CANCELLED = 0 AND K.DATE_ >= ?
      AND (C.CODE LIKE '120.01.%' OR C.CODE LIKE '120.05.%')
    """
 
    df = pd.read_sql(query, conn, params=[baslangic_tarihi, baslangic_tarihi, baslangic_tarihi])
    conn.close()
 
    if df.empty:
        return []
 
    grouped = {}
    for _, row in df.iterrows():
        tarih_dt = row['TARIH']
        tarih_str = tarih_dt.strftime('%Y-%m-%d') if pd.notnull(tarih_dt) else '-'
        try:
            week_no = int(tarih_dt.isocalendar().week) if pd.notnull(tarih_dt) else 0
        except Exception:
            week_no = 0
 
        tur = row['TUR']
        aciklama = row['ACIKLAMA'] or ''
        bank_short = get_short_bank_name(tur, aciklama)
 
        doviz = doviz_map.get(row['DOVIZ_TIPI'], 'TL')
 
        tl_tutar = 0.0
        usd_tutar = 0.0
        eur_tutar = 0.0
 
        if doviz == 'TL':
            tl_tutar = float(row['TL_TUTAR'] or 0)
        elif doviz == 'USD':
            usd_tutar = float(row['DOVIZ_TUTAR'] or 0)
        elif doviz == 'EUR':
            eur_tutar = float(row['DOVIZ_TUTAR'] or 0)
 
        vade_dt = row['VADE_TARIHI']
        vade_str = vade_dt.strftime('%Y-%m-%d') if pd.notnull(vade_dt) else ''
 
        key = (tarih_str, week_no, row['CARI_KODU'], row['CARI_UNVAN'], bank_short, vade_str)
        if key not in grouped:
            grouped[key] = {
                'TARIH': tarih_str,
                'HAFTA': week_no,
                'CARI_KODU': row['CARI_KODU'],
                'CARI_UNVAN': row['CARI_UNVAN'],
                'TAHSILAT_CINSI': bank_short,
                'TL': 0.0,
                'USD': 0.0,
                'EUR': 0.0,
                'VADE_TARIHI': vade_str
            }
        grouped[key]['TL'] += tl_tutar
        grouped[key]['USD'] += usd_tutar
        grouped[key]['EUR'] += eur_tutar
 
    results = list(grouped.values())
    results.sort(key=lambda x: x['TARIH'], reverse=True)
    return results

def get_borc_yaslandirma(df_aging, p1, p2, p3, p4):
    if df_aging.empty:
        return [], [], {}
 
    limits = sorted(list(set([p1, p2, p3, p4])))
 
    bucket_labels = []
    prev_limit = -1
    for i, limit in enumerate(limits):
        if i == 0:
            bucket_labels.append(f"0 - {limit} Gün")
        else:
            bucket_labels.append(f"{prev_limit + 1} - {limit} Gün")
        prev_limit = limit
    bucket_labels.append(f"{prev_limit + 1}+ Gün")
 
    grouped = {}
    for _, row in df_aging.iterrows():
        c_kod = row['CARI_KODU']
        c_unvan = row['CARI_UNVAN']
        acik_tutar = float(row['ACIK_TUTAR'])
        birim = row['BIRIM']
        gecikme = int(row['GECİKME GÜN'])
        temsilci = row.get('TEMSILCI', '')
 
        c_unvan_clean = c_unvan.split(' - ', 1)[1] if ' - ' in c_unvan else c_unvan
 
        if c_kod not in grouped:
            grouped[c_kod] = {
                'TEMSILCI': temsilci,
                'CARI_KODU': c_kod,
                'CARI_UNVAN': c_unvan_clean,
                'BIRIM': birim,
                'CARI_BAKIYE': 0.0,
                'GUNU_GELMEYEN': 0.0,
                'GUNU_GECEN': 0.0,
                'BUCKETS': [0.0] * len(bucket_labels)
            }
 
        grouped[c_kod]['CARI_BAKIYE'] += acik_tutar
 
        if gecikme < 0:
            grouped[c_kod]['GUNU_GELMEYEN'] += acik_tutar
        else:
            grouped[c_kod]['GUNU_GECEN'] += acik_tutar
            bucket_idx = len(bucket_labels) - 1
            for idx, limit in enumerate(limits):
                if gecikme <= limit:
                    bucket_idx = idx
                    break
            grouped[c_kod]['BUCKETS'][bucket_idx] += acik_tutar
 
    results = list(grouped.values())
 
    def yaslandirma_sort_key(x):
        curr = x['BIRIM']
        is_tl = 0 if curr == 'TL' else 1
        if curr == 'USD':
            curr_order = 0
        elif curr == 'EUR':
            curr_order = 1
        else:
            curr_order = 2
        return (is_tl, curr_order, curr, -x['CARI_BAKIYE'])
 
    results.sort(key=yaslandirma_sort_key)
 
    footer_totals = {}
    for res in results:
        currency = res['BIRIM']
        if currency not in footer_totals:
            footer_totals[currency] = {
                'CARI_BAKIYE': 0.0,
                'GUNU_GELMEYEN': 0.0,
                'GUNU_GECEN': 0.0,
                'BUCKETS': [0.0] * len(bucket_labels)
            }
        footer_totals[currency]['CARI_BAKIYE'] += res['CARI_BAKIYE']
        footer_totals[currency]['GUNU_GELMEYEN'] += res['GUNU_GELMEYEN']
        footer_totals[currency]['GUNU_GECEN'] += res['GUNU_GECEN']
        for i in range(len(bucket_labels)):
            footer_totals[currency]['BUCKETS'][i] += res['BUCKETS'][i]
 
    return results, bucket_labels, footer_totals

def get_tahsilat_konusma_summary_list(df_aging):
    if df_aging.empty:
        return []
    
    # 1. Fetch latest conversations from SQLite
    latest_konusmalar = {}
    try:
        conn = sqlite3.connect(KONUSMA_DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('''
            SELECT k1.cari_kodu, k1.kesin_gelecek_tarih, k1.soz_verilen_tarih, k1.kategoriler, k1.aciklama, k1.tutar, k1.doviz, k1.olusturma_tarihi,
                   k1.vade_tarih, k1.itiraz_tarih, k1.ulasilamadi_tarih, k1.cek_tarih
            FROM konusmalar k1
            INNER JOIN (
                SELECT cari_kodu, MAX(id) as max_id
                FROM konusmalar
                GROUP BY cari_kodu
            ) k2 ON k1.id = k2.max_id
        ''')
        rows = c.fetchall()
        conn.close()
        for row in rows:
            latest_konusmalar[row['cari_kodu']] = dict(row)
    except Exception as e:
        print(f"Error fetching latest conversations: {e}")

    # 2. Group open invoices by CARI and currency
    grouped = {}
    for _, row in df_aging.iterrows():
        c_kod = row['CARI_KODU']
        c_unvan = row['CARI_UNVAN']
        acik_tutar = float(row['ACIK_TUTAR'])
        birim = row['BIRIM']
        gecikme = int(row['GECİKME GÜN'])
        temsilci = row.get('TEMSILCI', '')

        # Clean CARI_UNVAN
        c_unvan_clean = c_unvan.split(' - ', 1)[1] if ' - ' in c_unvan else c_unvan
        
        key = (c_kod, birim)
        if key not in grouped:
            grouped[key] = {
                'TEMSILCI': temsilci,
                'CARI_KODU': c_kod,
                'CARI_UNVAN': c_unvan_clean,
                'CARI_FULL': f"{c_kod} - {c_unvan_clean}",
                'BIRIM': birim,
                'BAKIYE': 0.0,
                'GUNU_GELMEYEN': 0.0,
                'GUNU_GECEN': 0.0,
                'OVERDUE_WEIGHTED_SUM': 0.0
            }
        
        grouped[key]['BAKIYE'] += acik_tutar
        if gecikme < 0:
            grouped[key]['GUNU_GELMEYEN'] += acik_tutar
        else:
            grouped[key]['GUNU_GECEN'] += acik_tutar
            days = 30 if gecikme == 999 else gecikme
            grouped[key]['OVERDUE_WEIGHTED_SUM'] += acik_tutar * days

    def format_to_tr_date(d_str):
        if not d_str:
            return ""
        try:
            return datetime.strptime(d_str, "%Y-%m-%d").strftime("%d.%m.%Y")
        except Exception:
            return d_str

    results = []
    for key, item in grouped.items():
        c_kod = key[0]
        gunu_gecen = item['GUNU_GECEN']
        ort_gecikme = 0
        if gunu_gecen > 0.05:
            ort_gecikme = int(round(item['OVERDUE_WEIGHTED_SUM'] / gunu_gecen))
        
        latest = latest_konusmalar.get(c_kod, None)
        latest_str = '-'
        latest_date_formatted = '-'
        latest_kat = '-'
        latest_aciklama = '-'
        latest_tutar_str = '-'
        if latest:
            latest_date = latest['kesin_gelecek_tarih'] or latest['olusturma_tarihi'][:10]
            try:
                dt = datetime.strptime(latest_date[:10], '%Y-%m-%d')
                latest_date_formatted = dt.strftime('%d.%m.%Y')
            except Exception:
                latest_date_formatted = latest_date
                
            latest_kat = latest['kategoriler'] or ''
            latest_aciklama = latest['aciklama'] or ''
            latest_tutar = latest['tutar']
            latest_doviz = latest['doviz'] or 'TL'
            
            tutar_part = ""
            if latest_tutar is not None:
                formatted_tutar = f"{latest_tutar:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
                tutar_part = f" - {formatted_tutar} {latest_doviz}"
                latest_tutar_str = f"{formatted_tutar} {latest_doviz}"
            
            kat_part = f" [{latest_kat}]" if latest_kat else ""
            latest_str = f"{latest_date_formatted}{kat_part} - {latest_aciklama}{tutar_part}"

        results.append({
            'TEMSILCI': item['TEMSILCI'],
            'CARI_KODU': item['CARI_KODU'],
            'CARI_UNVAN': item['CARI_UNVAN'],
            'CARI_FULL': item['CARI_FULL'],
            'BIRIM': item['BIRIM'],
            'BAKIYE': round(item['BAKIYE'], 2),
            'GUNU_GELMEYEN': round(item['GUNU_GELMEYEN'], 2),
            'GUNU_GECEN': round(item['GUNU_GECEN'], 2),
            'ORT': ort_gecikme,
            'SON_KONUSMA': latest_str,
            'SON_KONUSMA_TARIH': latest_date_formatted,
            'SON_KONUSMA_KAT': latest_kat,
            'SON_KONUSMA_ACIKLAMA': latest_aciklama,
            'SON_KONUSMA_TUTAR_STR': latest_tutar_str,
            'KESIN_GELECEK_TARIH': format_to_tr_date(latest.get('kesin_gelecek_tarih')) if latest else '',
            'SOZ_VERILEN_TARIH': format_to_tr_date(latest.get('soz_verilen_tarih')) if latest else '',
            'VADE_TARIH': format_to_tr_date(latest.get('vade_tarih')) if latest else '',
            'ITIRAZ_TARIH': format_to_tr_date(latest.get('itiraz_tarih')) if latest else '',
            'ULASILAMADI_TARIH': format_to_tr_date(latest.get('ulasilamadi_tarih')) if latest else '',
            'CEK_TARIH': format_to_tr_date(latest.get('cek_tarih')) if latest else ''
        })

    def sort_key(x):
        curr = x['BIRIM']
        is_tl = 0 if curr == 'TL' else 1
        if curr == 'USD':
            curr_order = 0
        elif curr == 'EUR':
            curr_order = 1
        else:
            curr_order = 2
        return (is_tl, curr_order, -x['BAKIYE'])

    results.sort(key=sort_key)
    return results

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <title>Risk ve Finans Paneli</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
    <link href="https://cdn.jsdelivr.net/npm/select2@4.1.0-rc.0/dist/css/select2.min.css" rel="stylesheet" />
    <style>
        body { background: #f4f7f6; font-family: 'Inter', sans-serif; font-size: 13px; }
        .dashboard-card { border: none; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.05); background: white; margin-bottom: 20px; }
        .text-header { font-weight: 800; color: #2c3e50; }
        .table td, .table th { white-space: nowrap !important; vertical-align: middle; }
        .table thead th { background: #34495e; color: white; font-weight: 500; font-size: 11px; text-transform: uppercase; border: none; }
        .row-danger { background-color: #fff5f5 !important; }
        .row-borc { background-color: #fff7f7 !important; }
        .row-alacak { background-color: #f6faf6 !important; }
        .val-large { font-size: 18px; font-weight: 800; }
        .kpi-container { display: flex; gap: 10px; margin-bottom: 20px; }
        .kpi-card-mini { flex: 1; display: flex; align-items: center; justify-content: space-between; padding: 10px 15px; border-radius: 8px; color: white; box-shadow: 0 2px 10px rgba(0,0,0,0.08); }
        .kpi-date-mini { background: #34495e; }
        .kpi-delay-mini { background: #c0392b; }
        .kpi-label { font-size: 11px; font-weight: 700; text-transform: uppercase; opacity: 0.9; }
        .kpi-val { font-size: 16px; font-weight: 800; }
        .badge-status { width: 110px; font-size: 10px; display: inline-block; }
        .nav-pills .nav-link { color: #34495e; font-weight: 600; border-radius: 8px; margin-right: 10px; transition: all 0.3s ease; }
        .nav-pills .nav-link.active { background-color: #34495e; color: white; }
        .nav-pills .nav-link:hover:not(.active) { background-color: rgba(52, 73, 94, 0.1); color: #34495e; }
        .kpi-card-doviz {
            transition: transform 0.2s ease, box-shadow 0.2s ease;
        }
        .kpi-card-doviz:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 15px rgba(0,0,0,0.15) !important;
        }
 
        @keyframes pulse-red-alert {
            0% {
                background-color: rgba(220, 53, 69, 0.1);
                border-color: rgba(220, 53, 69, 0.6);
                box-shadow: 0 4px 15px rgba(220, 53, 69, 0.2);
            }
            50% {
                background-color: rgba(220, 53, 69, 0.25);
                border-color: rgba(220, 53, 69, 1);
                box-shadow: 0 4px 30px rgba(220, 53, 69, 0.6), 0 0 10px rgba(220, 53, 69, 0.4);
            }
            100% {
                background-color: rgba(220, 53, 69, 0.1);
                border-color: rgba(220, 53, 69, 0.6);
                box-shadow: 0 4px 15px rgba(220, 53, 69, 0.2);
            }
        }
        @keyframes text-pulse-red {
            0% { color: #dc3545; }
            50% { color: #850009; }
            100% { color: #dc3545; }
        }
        .cari-warning-card {
            border: 2px solid #dc3545;
            border-radius: 10px;
            padding: 14px 18px;
            margin-bottom: 20px;
            font-weight: bold;
            color: #dc3545;
            background-color: rgba(220, 53, 69, 0.1);
            animation: pulse-red-alert 1.2s infinite, text-pulse-red 1.2s infinite;
            font-size: 14px;
        }
        tr.hover-warning-active {
            background-color: rgba(220, 53, 69, 0.08) !important;
            outline: 2px solid rgba(220, 53, 69, 0.5) !important;
            transition: all 0.2s ease-in-out;
            cursor: pointer;
        }
        .marquee-container {
            position: fixed;
            bottom: 0;
            left: 0;
            width: 100%;
            background: linear-gradient(90deg, #c0392b, #e74c3c);
            color: white;
            font-weight: 700;
            font-size: 14px;
            height: 38px;
            display: flex;
            align-items: center;
            overflow: hidden;
            white-space: nowrap;
            box-shadow: 0 -4px 15px rgba(192, 57, 43, 0.4);
            z-index: 999999;
        }
        .marquee-content {
            display: inline-block;
            padding-left: 100%;
            animation: marquee-scroll 18s linear infinite;
        }
        @keyframes marquee-scroll {
            0% { transform: translate3d(0, 0, 0); }
            100% { transform: translate3d(-100%, 0, 0); }
        }
        @keyframes screen-red-flash {
            0% { background-color: rgba(220, 53, 69, 0); }
            25% { background-color: rgba(220, 53, 69, 0.35); }
            50% { background-color: rgba(220, 53, 69, 0); }
            75% { background-color: rgba(220, 53, 69, 0.35); }
            100% { background-color: rgba(220, 53, 69, 0); }
        }
        .screen-flash-active {
            position: fixed;
            top: 0;
            left: 0;
            width: 100vw;
            height: 100vh;
            background-color: rgba(220, 53, 69, 0);
            z-index: 99999;
            pointer-events: none;
            animation: screen-red-flash 1.2s ease-in-out;
        }
        .kategori-kpi-card {
            position: relative;
            overflow: hidden;
            flex: 1 1 150px;
            min-width: 170px;
            padding: 14px 16px !important;
            border-radius: 12px !important;
            color: white;
            cursor: pointer;
            border: 1px solid rgba(255, 255, 255, 0.15) !important;
            box-shadow: 0 4px 15px rgba(0, 0, 0, 0.08) !important;
            transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1) !important;
        }
        .kategori-kpi-card::after {
            content: '';
            position: absolute;
            top: 0; left: 0; right: 0; bottom: 0;
            background: linear-gradient(135deg, rgba(255, 255, 255, 0.18) 0%, rgba(0, 0, 0, 0.15) 100%);
            pointer-events: none;
            z-index: 1;
        }
        .kategori-kpi-card:hover {
            transform: translateY(-4px) !important;
            box-shadow: 0 10px 25px rgba(0, 0, 0, 0.18) !important;
            filter: brightness(1.05);
        }
        .kategori-kpi-title {
            font-size: 11px;
            font-weight: 700;
            border-bottom: 1px solid rgba(255, 255, 255, 0.3);
            padding-bottom: 6px;
            margin-bottom: 8px;
            letter-spacing: 0.3px;
            text-shadow: 0 1px 2px rgba(0, 0, 0, 0.15);
            position: relative;
            z-index: 2;
        }
        .kategori-kpi-values {
            display: flex;
            flex-direction: column;
            gap: 3px;
            position: relative;
            z-index: 2;
        }
        .kategori-kpi-values > div {
            display: flex;
            justify-content: space-between;
            font-size: 12.5px;
            font-weight: 800;
            text-shadow: 0 1px 2px rgba(0, 0, 0, 0.15);
        }
        .sortable-detail { cursor: pointer; user-select: none; position: relative; }
        .sortable-detail:hover { background-color: rgba(255,255,255,0.15) !important; }
        .sort-icon-detail { font-size: 10px; margin-left: 4px; }

        /* --- MODERN MULTI-SELECT DROPDOWN STYLES --- */
        .custom-multiselect-wrapper {
            position: relative;
            width: 100%;
        }
        .custom-multiselect-btn {
            display: flex;
            align-items: center;
            justify-content: space-between;
            width: 100%;
            padding: 0.375rem 0.75rem;
            font-size: 13px;
            font-weight: 500;
            color: #212529;
            background-color: #fff;
            border: 1px solid #ced4da;
            border-radius: 6px;
            cursor: pointer;
            text-align: left;
            transition: border-color 0.15s ease-in-out, box-shadow 0.15s ease-in-out;
            height: 38px;
            user-select: none;
        }
        .custom-multiselect-btn:hover {
            border-color: #86b7fe;
        }
        .custom-multiselect-btn:focus, .custom-multiselect-wrapper.open .custom-multiselect-btn {
            border-color: #86b7fe;
            outline: 0;
            box-shadow: 0 0 0 0.25rem rgba(13, 110, 253, 0.25);
        }
        .custom-multiselect-text {
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            margin-right: 6px;
            flex: 1;
        }
        .custom-multiselect-badge {
            background-color: #0d6efd;
            color: white;
            font-size: 11px;
            font-weight: 700;
            padding: 2px 7px;
            border-radius: 12px;
            margin-right: 6px;
        }
        .custom-multiselect-chevron {
            transition: transform 0.2s ease;
            font-size: 10px;
            color: #6c757d;
        }
        .custom-multiselect-wrapper.open .custom-multiselect-chevron {
            transform: rotate(180deg);
        }
        .custom-multiselect-dropdown {
            display: none;
            position: absolute;
            top: calc(100% + 4px);
            left: 0;
            background: #ffffff;
            border: 1px solid #ced4da;
            border-radius: 8px;
            box-shadow: 0 12px 30px rgba(0, 0, 0, 0.22);
            z-index: 999999 !important;
            width: 100%;
            min-width: 320px;
            max-width: 450px;
            padding: 8px;
        }
        .custom-multiselect-wrapper.open .custom-multiselect-dropdown {
            display: block !important;
            animation: multiselectFadeIn 0.15s ease;
        }
        @keyframes multiselectFadeIn {
            from { opacity: 0; transform: translateY(-6px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .custom-multiselect-search {
            width: 100%;
            padding: 6px 10px;
            font-size: 12px;
            border: 1px solid #dee2e6;
            border-radius: 6px;
            margin-bottom: 6px;
            outline: none;
        }
        .custom-multiselect-search:focus {
            border-color: #86b7fe;
            box-shadow: 0 0 0 2px rgba(13, 110, 253, 0.15);
        }
        .custom-multiselect-actions {
            display: flex;
            justify-content: space-between;
            gap: 6px;
            margin-bottom: 6px;
            padding-bottom: 6px;
            border-bottom: 1px solid #e9ecef;
        }
        .custom-multiselect-action-btn {
            background: none;
            border: none;
            font-size: 11px;
            font-weight: 600;
            color: #0d6efd;
            cursor: pointer;
            padding: 2px 4px;
            border-radius: 4px;
        }
        .custom-multiselect-action-btn:hover {
            background-color: #f0f4ff;
        }
        .custom-multiselect-action-btn.clear {
            color: #dc3545;
        }
        .custom-multiselect-action-btn.clear:hover {
            background-color: #fff0f2;
        }
        .custom-multiselect-list {
            max-height: 230px;
            overflow-y: auto;
            margin: 0;
            padding: 0;
            list-style: none;
        }
        .custom-multiselect-list::-webkit-scrollbar {
            width: 6px;
        }
        .custom-multiselect-list::-webkit-scrollbar-thumb {
            background: #cbd5e1;
            border-radius: 3px;
        }
        .custom-multiselect-item {
            display: flex;
            align-items: center;
            padding: 6px 8px;
            border-radius: 5px;
            cursor: pointer;
            font-size: 12px;
            color: #334155;
            user-select: none;
            transition: background-color 0.1s ease;
        }
        .custom-multiselect-item:hover {
            background-color: #f1f5f9;
        }
        .custom-multiselect-item.selected {
            background-color: #e0f2fe;
            color: #0369a1;
            font-weight: 600;
        }
        .custom-multiselect-item input[type="checkbox"] {
            margin-right: 8px;
            cursor: pointer;
            accent-color: #0d6efd;
            width: 15px;
            height: 15px;
            flex-shrink: 0;
        }
        .custom-multiselect-item-text {
            flex: 1;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
    </style>
    <script>
    /* --- CUSTOM MULTI-SELECT FUNCTIONS (GLOBAL IN HEAD) --- */
    function toggleMultiSelectDropdown(wrapperId, e) {
        if (e) {
            if (typeof e.preventDefault === 'function') e.preventDefault();
            if (typeof e.stopPropagation === 'function') e.stopPropagation();
        }
        var wrapper = document.getElementById(wrapperId);
        if (!wrapper) return;
        var wasOpen = wrapper.classList.contains('open');
        
        var all = document.querySelectorAll('.custom-multiselect-wrapper.open');
        for (var i = 0; i < all.length; i++) {
            all[i].classList.remove('open');
        }
        
        if (!wasOpen) {
            wrapper.classList.add('open');
            var searchInput = wrapper.querySelector('.custom-multiselect-search');
            if (searchInput) {
                searchInput.value = '';
                filterMultiSelectList(searchInput);
                setTimeout(function() { searchInput.focus(); }, 50);
            }
        }
    }

    document.addEventListener('click', function(e) {
        if (!e.target.closest('.custom-multiselect-wrapper')) {
            var all = document.querySelectorAll('.custom-multiselect-wrapper.open');
            for (var i = 0; i < all.length; i++) {
                all[i].classList.remove('open');
            }
        }
    });

    function filterMultiSelectList(input) {
        var query = (input.value || '').toLocaleLowerCase('tr').trim();
        var dropdown = input.closest('.custom-multiselect-dropdown');
        if (!dropdown) return;
        var list = dropdown.querySelector('.custom-multiselect-list');
        if (!list) return;
        var items = list.querySelectorAll('.custom-multiselect-item');
        for (var i = 0; i < items.length; i++) {
            var text = (items[i].innerText || '').toLocaleLowerCase('tr');
            if (!query || text.indexOf(query) > -1) {
                items[i].style.display = 'flex';
            } else {
                items[i].style.display = 'none';
            }
        }
    }

    function toggleItemMultiSelect(selectId, wrapperId, itemEl, evt) {
        if (evt) {
            if (typeof evt.stopPropagation === 'function') evt.stopPropagation();
        }
        var select = document.getElementById(selectId);
        var checkbox = itemEl.querySelector('input[type="checkbox"]');
        var val = itemEl.getAttribute('data-val');
        if (!select || !checkbox || !val) return;
        
        if (evt && evt.target && evt.target.tagName === 'INPUT') {
            // already toggled
        } else {
            checkbox.checked = !checkbox.checked;
        }
        
        if (checkbox.checked) {
            itemEl.classList.add('selected');
        } else {
            itemEl.classList.remove('selected');
        }
        
        for (var i = 0; i < select.options.length; i++) {
            if (select.options[i].value === val) {
                select.options[i].selected = checkbox.checked;
                break;
            }
        }
        updateMultiSelectSummary(selectId, wrapperId);
    }

    function selectAllMultiSelect(selectId, wrapperId) {
        var select = document.getElementById(selectId);
        var wrapper = document.getElementById(wrapperId);
        if (!select || !wrapper) return;
        
        var items = wrapper.querySelectorAll('.custom-multiselect-item');
        for (var i = 0; i < items.length; i++) {
            if (items[i].style.display !== 'none') {
                var cb = items[i].querySelector('input[type="checkbox"]');
                if (cb) cb.checked = true;
                items[i].classList.add('selected');
                var val = items[i].getAttribute('data-val');
                for (var j = 0; j < select.options.length; j++) {
                    if (select.options[j].value === val) {
                        select.options[j].selected = true;
                        break;
                    }
                }
            }
        }
        updateMultiSelectSummary(selectId, wrapperId);
    }

    function clearAllMultiSelect(selectId, wrapperId) {
        var select = document.getElementById(selectId);
        var wrapper = document.getElementById(wrapperId);
        if (!select || !wrapper) return;
        
        var items = wrapper.querySelectorAll('.custom-multiselect-item');
        for (var i = 0; i < items.length; i++) {
            var cb = items[i].querySelector('input[type="checkbox"]');
            if (cb) cb.checked = false;
            items[i].classList.remove('selected');
        }
        for (var j = 0; j < select.options.length; j++) {
            select.options[j].selected = false;
        }
        updateMultiSelectSummary(selectId, wrapperId);
    }

    function updateMultiSelectSummary(selectId, wrapperId) {
        var select = document.getElementById(selectId);
        var wrapper = document.getElementById(wrapperId);
        if (!select || !wrapper) return;
        
        var textEl = wrapper.querySelector('.custom-multiselect-text');
        var badgeEl = wrapper.querySelector('.custom-multiselect-badge');
        var selectedOptions = Array.from(select.selectedOptions);
        var count = selectedOptions.length;
        var defaultText = selectId === 'temsilciSelect' ? 'Tüm Temsilciler' : 'Tüm Cari Hesaplar';
        
        if (count === 0) {
            if (textEl) {
                textEl.textContent = defaultText;
                textEl.style.color = '#6c757d';
            }
            if (badgeEl) badgeEl.classList.add('d-none');
        } else if (count === 1) {
            if (textEl) {
                textEl.textContent = selectedOptions[0].text;
                textEl.style.color = '#212529';
            }
            if (badgeEl) badgeEl.classList.add('d-none');
        } else {
            var firstText = selectedOptions[0].text;
            if (firstText.length > 22) firstText = firstText.substring(0, 22) + '...';
            if (textEl) {
                textEl.textContent = firstText + ' (+' + (count - 1) + ')';
                textEl.style.color = '#212529';
            }
            if (badgeEl) {
                badgeEl.textContent = count;
                badgeEl.classList.remove('d-none');
            }
        }
    }

    document.addEventListener('DOMContentLoaded', function() {
        updateMultiSelectSummary('temsilciSelect', 'temsilciDropdownWrapper');
        updateMultiSelectSummary('cariSelect', 'cariDropdownWrapper');
    });
    </script>
</head>
<body>
    <datalist id="cari_suggestions">
        {% for cari in tum_cariler %}
        <option value="{{ cari }}"></option>
        {% endfor %}
    </datalist>
    <div class="container-fluid py-4">
        {% if aktif_uyarilar %}
        <div class="cari-warning-card text-start" id="main_aktif_uyarilar_card">
            <div class="d-flex align-items-center gap-2 mb-2">
                <span style="font-size: 20px;">🚨</span>
                <h6 class="m-0 fw-bold" style="color: #850009;">FİRMA ÖZEL UYARILARI / NOTLARI:</h6>
            </div>
            <ul class="mb-0 ps-3" style="line-height: 1.6;">
                {% for cari, uyari in aktif_uyarilar %}
                <li><strong>{{ cari }}:</strong> <span style="font-size: 15px;">{{ uyari }}</span></li>
                {% endfor %}
            </ul>
        </div>
        {% endif %}
        <div class="d-flex justify-content-between align-items-center mb-4 flex-wrap gap-3">
            <div>
                <div class="mb-2">
                    <a href="/finans" class="btn btn-sm btn-outline-secondary" style="font-size: 12px; border-radius: 6px; text-decoration: none; padding: 4px 10px; font-weight: 600;">
                        ← Finans Paneline Dön
                    </a>
                </div>
                <h3 class="text-header m-0">📊 Müşteri Finansal Risk Yönetimi</h3>
                <span class="text-muted small">Logo Yazılım Entegre Finansal Analiz</span>
            </div>
            <div class="d-flex align-items-center gap-3">
                <div class="p-2 px-3 bg-white border rounded-3 shadow-sm d-flex align-items-center gap-2" style="font-size: 13px; border-left: 4px solid #198754 !important;">
                    <span class="fw-bold text-success">💵 TCMB USD Alış:</span>
                    <span class="fw-bold text-dark">{{ "%.4f"|format(tcmb_usd) }} TL</span>
                </div>
                <div class="p-2 px-3 bg-white border rounded-3 shadow-sm d-flex align-items-center gap-2" style="font-size: 13px; border-left: 4px solid #0d6efd !important;">
                    <span class="fw-bold text-primary">💶 TCMB EUR Alış:</span>
                    <span class="fw-bold text-dark">{{ "%.4f"|format(tcmb_eur) }} TL</span>
                </div>
            </div>
        </div>

        <!-- Sekme Menüsü -->
        <ul class="nav nav-pills mb-4" id="pills-tab" role="tablist">
            <li class="nav-item" role="presentation">
                <button class="nav-link active" id="pills-konusma-tab" data-bs-toggle="pill" data-bs-target="#pills-konusma" type="button" role="tab" aria-controls="pills-konusma" aria-selected="true">💬 Tahsilat Konuşmaları</button>
            </li>
            <li class="nav-item" role="presentation">
                <button class="nav-link" id="pills-risk-tab" data-bs-toggle="pill" data-bs-target="#pills-risk" type="button" role="tab" aria-controls="pills-risk" aria-selected="false">📊 Finansal Risk Analizi</button>
            </li>
            <li class="nav-item" role="presentation">
                <button class="nav-link" id="pills-tahsilat-tab" data-bs-toggle="pill" data-bs-target="#pills-tahsilat" type="button" role="tab" aria-controls="pills-tahsilat" aria-selected="false">💰 Günlük Tahsilat</button>
            </li>
            <li class="nav-item" role="presentation">
                <button class="nav-link" id="pills-tahsilat-ek-tab" data-bs-toggle="pill" data-bs-target="#pills-tahsilat-ek" type="button" role="tab" aria-controls="pills-tahsilat-ek" aria-selected="false">💰 Tahsilat Listesi</button>
            </li>
            <li class="nav-item" role="presentation">
                <button class="nav-link" id="pills-yaslandirma-tab" data-bs-toggle="pill" data-bs-target="#pills-yaslandirma" type="button" role="tab" aria-controls="pills-yaslandirma" aria-selected="false">⏳ Borç Yaşlandırma</button>
            </li>
            <li class="nav-item" role="presentation">
                <button class="nav-link" id="pills-ekstre-tab" data-bs-toggle="pill" data-bs-target="#pills-ekstre" type="button" role="tab" aria-controls="pills-ekstre" aria-selected="false">📑 Cari Hesap Ekstre</button>
            </li>
            <li class="nav-item" role="presentation">
                <button class="nav-link" id="pills-fatura-detay-tab" data-bs-toggle="pill" data-bs-target="#pills-fatura-detay" type="button" role="tab" aria-controls="pills-fatura-detay" aria-selected="false">📋 Kesilecek Fatura Detay</button>
            </li>
            <li class="nav-item" role="presentation">
                <button class="nav-link" id="pills-uyari-tab" data-bs-toggle="pill" data-bs-target="#pills-uyari" type="button" role="tab" aria-controls="pills-uyari" aria-selected="false">🚨 Firma Özel Notları</button>
            </li>
        </ul>

        <div class="tab-content" id="pills-tabContent">
            <!-- TAB 1: Finansal Risk Analizi -->
            <div class="tab-pane fade" id="pills-risk" role="tabpanel" aria-labelledby="pills-risk-tab">
                <div class="card dashboard-card p-3 mb-4">
                    <form method="GET" class="row g-3 align-items-end">
                        <input type="hidden" name="p1" value="{{ p1 }}">
                        <input type="hidden" name="p2" value="{{ p2 }}">
                        <input type="hidden" name="p3" value="{{ p3 }}">
                        <input type="hidden" name="p4" value="{{ p4 }}">
                        <div class="col-md-2">
                            <label class="form-label fw-bold">Rapor Tarihi</label>
                            <input type="date" name="rapor_tarihi" class="form-control" value="{{ secili_tarih }}">
                        </div>
                        <div class="col-md-3">
                            <label class="form-label fw-bold">Temsilci</label>
                            <!-- Hidden native select for standard form GET/POST submission & sync -->
                            <select name="temsilciler" id="temsilciSelect" class="d-none" multiple>
                                {% for t in tum_temsilciler %}
                                <option value="{{ t }}" {% if t in secili_temsilciler %}selected{% endif %}>{{ t }}</option>
                                {% endfor %}
                            </select>
                            
                            <!-- Custom Multi-Select Dropdown Trigger -->
                            <div class="custom-multiselect-wrapper" id="temsilciDropdownWrapper">
                                <button type="button" class="custom-multiselect-btn" onclick="toggleMultiSelectDropdown('temsilciDropdownWrapper', event)">
                                    <span class="custom-multiselect-text" id="temsilciDropdownText">Tüm Temsilciler</span>
                                    <span class="custom-multiselect-badge d-none" id="temsilciDropdownBadge">0</span>
                                    <span class="custom-multiselect-chevron">▼</span>
                                </button>
                                <div class="custom-multiselect-dropdown" onclick="event.stopPropagation()">
                                    <input type="text" class="custom-multiselect-search" placeholder="Temsilci ara..." oninput="filterMultiSelectList(this)">
                                    <div class="custom-multiselect-actions">
                                        <button type="button" class="custom-multiselect-action-btn" onclick="selectAllMultiSelect('temsilciSelect', 'temsilciDropdownWrapper')">✓ Tümünü Seç</button>
                                        <button type="button" class="custom-multiselect-action-btn clear" onclick="clearAllMultiSelect('temsilciSelect', 'temsilciDropdownWrapper')">✕ Temizle</button>
                                    </div>
                                    <ul class="custom-multiselect-list">
                                        {% for t in tum_temsilciler %}
                                        <li class="custom-multiselect-item {% if t in secili_temsilciler %}selected{% endif %}" data-val="{{ t }}" onclick="toggleItemMultiSelect('temsilciSelect', 'temsilciDropdownWrapper', this, event)">
                                            <input type="checkbox" value="{{ t }}" {% if t in secili_temsilciler %}checked{% endif %}>
                                            <span class="custom-multiselect-item-text" title="{{ t }}">{{ t }}</span>
                                        </li>
                                        {% endfor %}
                                    </ul>
                                </div>
                            </div>
                        </div>
                        <div class="col-md-3">
                            <label class="form-label fw-bold">Cari Hesap</label>
                            <!-- Hidden native select for standard form GET/POST submission & sync -->
                            <select name="cariler" id="cariSelect" class="d-none" multiple>
                                {% for cari in tum_cariler %}
                                <option value="{{ cari }}" {% if cari in secili_cariler %}selected{% endif %}>{{ cari }}</option>
                                {% endfor %}
                            </select>
                            
                            <!-- Custom Multi-Select Dropdown Trigger -->
                            <div class="custom-multiselect-wrapper" id="cariDropdownWrapper">
                                <button type="button" class="custom-multiselect-btn" onclick="toggleMultiSelectDropdown('cariDropdownWrapper', event)">
                                    <span class="custom-multiselect-text" id="cariDropdownText">Tüm Cari Hesaplar</span>
                                    <span class="custom-multiselect-badge d-none" id="cariDropdownBadge">0</span>
                                    <span class="custom-multiselect-chevron">▼</span>
                                </button>
                                <div class="custom-multiselect-dropdown" onclick="event.stopPropagation()">
                                    <input type="text" class="custom-multiselect-search" placeholder="Cari kod / ünvan ara..." oninput="filterMultiSelectList(this)">
                                    <div class="custom-multiselect-actions">
                                        <button type="button" class="custom-multiselect-action-btn" onclick="selectAllMultiSelect('cariSelect', 'cariDropdownWrapper')">✓ Tümünü Seç</button>
                                        <button type="button" class="custom-multiselect-action-btn clear" onclick="clearAllMultiSelect('cariSelect', 'cariDropdownWrapper')">✕ Temizle</button>
                                    </div>
                                    <ul class="custom-multiselect-list">
                                        {% for cari in tum_cariler %}
                                        <li class="custom-multiselect-item {% if cari in secili_cariler %}selected{% endif %}" data-val="{{ cari }}" onclick="toggleItemMultiSelect('cariSelect', 'cariDropdownWrapper', this, event)">
                                            <input type="checkbox" value="{{ cari }}" {% if cari in secili_cariler %}checked{% endif %}>
                                            <span class="custom-multiselect-item-text" title="{{ cari }}">{{ cari }}</span>
                                        </li>
                                        {% endfor %}
                                    </ul>
                                </div>
                            </div>
                        </div>
                        <div class="col-md-1">
                            <label class="form-label fw-bold">Faiz %</label>
                            <input type="number" name="faiz_orani" class="form-control" value="{{ faiz_orani }}" step="0.1">
                        </div>
                        <div class="col-md-3 d-flex gap-2">
                            <button type="submit" class="btn btn-primary w-100">Sorgula</button>
                            <button type="submit" name="export" value="excel" class="btn btn-success w-100">Excel</button>
                        </div>
                    </form>
                </div>
                <div class="row">
            <div class="col-md-8">
                <div class="card dashboard-card">
                    <table class="table mb-0">
                        <thead>
                            <tr>
                                <th>Birim</th>
                                <th class="text-end">Risk (Gecikmiş)</th>
                                <th class="text-end">Gelecek (Vade)</th>
                                <th class="text-end">Toplam (Hesaplanan)</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for birim, tutar in toplam_genel.items() %}
                            <tr>
                                <td><span class="badge bg-secondary">{{ birim }}</span></td>
                                <td class="text-end text-danger fw-bold val-large">{{ "{:,.2f}".format(toplam_gecikmis.get(birim, 0)).replace(',', 'X').replace('.', ',').replace('X', '.') }}</td>
                                <td class="text-end text-success fw-bold val-large">{{ "{:,.2f}".format(toplam_vade.get(birim, 0)).replace(',', 'X').replace('.', ',').replace('X', '.') }}</td>
                                <td class="text-end text-dark fw-bold val-large">{{ "{:,.2f}".format(toplam_gecikmis.get(birim, 0) + toplam_vade.get(birim, 0)).replace(',', 'X').replace('.', ',').replace('X', '.') }}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                        <tfoot style="border-top: 2px solid #34495e; background: #fdfdfd;">
                            <tr>
                                <td colspan="3" class="text-end py-2">
                                    <span class="text-muted fw-bold small">TASLAK / KESİLMEMİŞ RESMİ FATURALAR TOPLAMI:</span>
                                </td>
                                <td class="text-end py-2">
                                    <span class="val-large text-primary">{{ "{:,.2f}".format(ek_toplam).replace(',', 'X').replace('.', ',').replace('X', '.') }}</span>
                                </td>
                            </tr>
                            <tr style="background: #fff5f5; border-top: 1px solid #dee2e6;">
                                <td colspan="3" class="text-end py-2">
                                    <span class="text-danger fw-bold small">TOPLAM VADE FARKI (ADAT %{{ faiz_orani }}):</span>
                                </td>
                                <td class="text-end py-2">
                                    <span class="val-large text-danger" style="border-bottom: 3px double #dc3545;">{{ "{:,.2f}".format(toplam_adat).replace(',', 'X').replace('.', ',').replace('X', '.') }}</span>
                                </td>
                            </tr>
                        </tfoot>
                    </table>
                </div>
            </div>
 
            <div class="col-md-4">
                <div class="kpi-container">
                    <div class="kpi-card-mini kpi-date-mini">
                        <span class="kpi-label">TARİH</span>
                        <span class="kpi-val">{{ secili_tarih | format_date_tr }}</span>
                    </div>
                    <div class="kpi-card-mini kpi-delay-mini">
                        <span class="kpi-label">ORT. GECİKME</span>
                        <span class="kpi-val">{{ ort_gecikme }} <small style="font-size: 10px;">GÜN</small></span>
                    </div>
                </div>

                <div class="card dashboard-card">
                    <div class="bg-danger text-white p-2 text-center fw-bold rounded-top" style="font-size: 11px;">RİSKLİ İLK 10 FİRMA (TL)</div>
                    <table class="table table-sm mb-0">
                        {% for index, row in risk_data.iterrows() %}
                        <tr class="border-bottom">
                            <td class="ps-3 py-2 text-truncate" style="max-width: 180px; font-size: 12px;">{{ row['CARI'] }}</td>
                            <td class="text-end pe-3 text-danger fw-bold" style="font-size: 12px;">{{ "{:,.0f}".format(row['GECIKMIS_TL']).replace(',', '.') }}</td>
                        </tr>
                        {% endfor %}
                    </table>
                </div>
            </div>
        </div>

        <!-- Filtre Kartı Taşındı -->

        <div class="card dashboard-card overflow-hidden">
            <div class="p-3 bg-light border-bottom d-flex justify-content-between align-items-center flex-wrap gap-2">
                <span class="fw-bold text-dark m-0" style="font-size: 13px;">📋 Fatura Listesi</span>
                <input type="text" id="risk_table_search" list="cari_suggestions" class="form-control form-control-sm" placeholder="Tabloda ara (Cari, Temsilci, Belge No...)" style="max-width: 300px;">
            </div>
            <div class="table-responsive">
                <table class="table table-hover mb-0" id="riskTable">
                    <thead>
                        <tr>
                            <th class="ps-3">Temsilci</th>
                            <th>Cari Hesap</th>
                            <th>Fatura Tar.</th>
                            <th>Vade Tar.</th>
                            <th class="text-center">Vade Gün</th>
                            <th>Belge No</th>
                            <th class="text-end">Açık Tutar</th>
                            <th class="text-center">Döviz</th>
                            <th class="text-center">Durum</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for row in data %}
                        <tr class="{% if row['GECİKME GÜN'] >= 0 and row['GECİKME GÜN'] != 999 %}row-danger{% endif %}" data-cari-kodu="{{ row['CARI_KODU'] }}">
                            <td class="ps-3 text-muted small">{{ row['TEMSILCI'] }}</td>
                            <td class="fw-bold">{{ row['CARI_UNVAN'] }}</td>
                            <td>{{ row['FATURA TARİHİ'] or '-' }}</td>
                            <td>{{ row['VADE TARİHİ'] or '-' }}</td>
                            <td class="text-center"><span class="badge bg-dark">{{ row['VADE_GUN'] }}</span></td>
                            <td><code>{{ row['BELGE NO'] }}</code></td>
                            <td class="text-end fw-bold text-primary">{{ "{:,.2f}".format(row['ACIK_TUTAR']).replace(',', 'X').replace('.', ',').replace('X', '.') }}</td>
                            <td class="text-center"><span class="badge bg-light text-dark">{{ row['BIRIM'] }}</span></td>
                            <td class="text-center">
                                {% if row['GECİKME GÜN'] == 999 %}
                                    <span class="badge bg-warning text-dark badge-status">DEVİR BAKİYE</span>
                                {% elif row['GECİKME GÜN'] >= 0 %}
                                    <span class="badge bg-danger badge-status">{{ row['GECİKME GÜN'] }} GÜN GECİKTİ</span>
                                {% else %}
                                    <span class="badge bg-success badge-status">{{ row['GECİKME GÜN']|abs }} GÜN VAR</span>
                                {% endif %}
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
            </div> <!-- TAB 1 Bitiş -->

            <!-- TAB 2: Günlük Tahsilat -->
            <div class="tab-pane fade" id="pills-tahsilat" role="tabpanel" aria-labelledby="pills-tahsilat-tab">
                <!-- Yerel Tarih Filtre Barı -->
                <div class="card dashboard-card p-3" style="background: #f8f9fa; border: 1px solid #e3e6f0; margin-bottom: 15px;">
                    <div class="row g-2 align-items-end">
                        <div class="col-md-3 col-sm-6">
                            <label class="form-label fw-bold mb-1" style="font-size: 11px;">Tahsilat Tarihi</label>
                            <input type="date" id="tahsilat_tarihi_input" class="form-control form-control-sm" value="{{ secili_tarih }}">
                        </div>
                        <div class="col-md-2 col-sm-6">
                            <button class="btn btn-primary btn-sm w-100" onclick="loadTahsilatListesi()">🔍 Tahsilatları Getir</button>
                        </div>
                    </div>
                </div>

                <div class="card dashboard-card overflow-hidden">
                    <div class="p-3 bg-light border-bottom d-flex justify-content-between align-items-center flex-wrap gap-2">
                        <span class="fw-bold text-dark m-0" style="font-size: 13px;">📋 Günlük Tahsilat Detayları</span>
                        <input type="text" id="tahsilat_table_search" list="cari_suggestions" class="form-control form-control-sm" placeholder="Tabloda ara (Cari, Banka...)" style="max-width: 300px;">
                    </div>
                    <div class="table-responsive">
                        <table class="table table-hover mb-0" id="tahsilatTable">
                            <thead>
                                <tr>
                                    <th class="ps-3">Banka</th>
                                    <th>Firma Kodu</th>
                                    <th>Cari Ünvan</th>
                                    <th class="text-end">Cari Bakiye</th>
                                    <th class="text-end">Günü Gelmeyen</th>
                                    <th class="text-end">Günü Geçen</th>
                                    <th class="text-end">Yapılan Tahsilat</th>
                                    <th class="text-end">Günü Geçen Kalan</th>
                                    <th class="text-center">Vade Tarihi</th>
                                    <th class="text-center">Ort. Gecikme</th>
                                </tr>
                            </thead>
                            <tbody id="tahsilatBody">
                                {% if tahsilat_list %}
                                    {% for row in tahsilat_list %}
                                    <tr data-cari-kodu="{{ row['CARI_KODU'] }}">
                                        <td class="ps-3"><span class="badge bg-primary">{{ row['BANKA'] }}</span></td>
                                        <td class="font-monospace text-muted">{{ row['CARI_KODU'] }}</td>
                                        <td class="fw-bold">{{ row['CARI_UNVAN'] }}</td>
                                        <td class="text-end fw-bold text-dark">{{ "{:,.2f}".format(row['CARI_BAKIYE']).replace(',', 'X').replace('.', ',').replace('X', '.') }} {{ row['DOVIZ'] }}</td>
                                        <td class="text-end text-success">{{ "{:,.2f}".format(row['GUNU_GELMEYEN']).replace(',', 'X').replace('.', ',').replace('X', '.') }} {{ row['DOVIZ'] }}</td>
                                        <td class="text-end fw-bold" style="color: #d35400 !important;">{{ "{:,.2f}".format(row['GUNU_GECEN']).replace(',', 'X').replace('.', ',').replace('X', '.') }} {{ row['DOVIZ'] }}</td>
                                        <td class="text-end fw-bold text-success">{{ "{:,.2f}".format(row['TUTAR']).replace(',', 'X').replace('.', ',').replace('X', '.') }} {{ row['DOVIZ'] }}</td>
                                        <td class="text-end fw-bold" style="color: #c0392b !important;">{{ "{:,.2f}".format(row['GUNU_GECEN_KALAN']).replace(',', 'X').replace('.', ',').replace('X', '.') }} {{ row['DOVIZ'] }}</td>
                                        <td class="text-center">
                                            {% if row['VADE_TARIHI'] and row['VADE_TARIHI'] != '-' %}
                                                <span class="badge bg-warning text-dark">{{ row['VADE_TARIHI'] }}</span>
                                            {% else %}
                                                <span class="text-muted">-</span>
                                            {% endif %}
                                        </td>
                                        <td class="text-center">
                                            {% if row['ORT_GECIKME_GUN'] > 0 %}
                                                <span class="badge bg-danger">{{ row['ORT_GECIKME_GUN'] }} Gün</span>
                                            {% else %}
                                                <span class="text-muted">-</span>
                                            {% endif %}
                                        </td>
                                    </tr>
                                    {% endfor %}
                                {% else %}
                                    <tr>
                                        <td colspan="9" class="text-center py-4 text-muted">
                                            Seçilen tarihte ve kriterlerde herhangi bir tahsilat bulunamadı.
                                        </td>
                                    </tr>
                                {% endif %}
                            </tbody>
                            <tfoot id="tahsilatFoot" style="border-top: 2px solid #34495e; background: #fdfdfd; {% if not tahsilat_list %}display: none;{% endif %}">
                                <tr>
                                    <td colspan="6" class="text-end py-2 align-middle">
                                        <span class="text-muted fw-bold small">TOPLAM TAHSİLAT:</span>
                                    </td>
                                    <td id="tahsilatFootTotals" class="text-end py-2 align-middle">
                                        {% for doviz, toplam in tahsilat_toplamlari.items() %}
                                            <div class="text-success fw-bold" style="font-size: 14px;">
                                                {{ "{:,.2f}".format(toplam).replace(',', 'X').replace('.', ',').replace('X', '.') }} {{ doviz }}
                                            </div>
                                        {% endfor %}
                                    </td>
                                    <td></td>
                                    <td></td>
                                    <td></td>
                                </tr>
                            </tfoot>
                        </table>
                    </div>
                </div>
            </div>

            <!-- TAB: Tahsilat (Döviz Tahsilatları) -->
            <div class="tab-pane fade" id="pills-tahsilat-ek" role="tabpanel" aria-labelledby="pills-tahsilat-ek-tab">
                <div class="row mb-3 align-items-stretch">
                    <div class="col-md-7">
                        <div class="card dashboard-card p-3 h-100 mb-0">
                            <div class="row g-2 align-items-end h-100">
                                <div class="col-md-3 col-sm-6">
                                    <label class="form-label fw-bold mb-1" style="font-size: 11px;">Başlangıç Tarihi</label>
                                    <input type="date" id="doviz_tahsilat_baslangic" class="form-control form-control-sm" value="2026-01-01">
                                </div>
                                <div class="col-md-3 col-sm-6">
                                    <label class="form-label fw-bold mb-1" style="font-size: 11px;">Bitiş Tarihi</label>
                                    <input type="date" id="doviz_tahsilat_bitis" class="form-control form-control-sm" value="{{ secili_tarih }}">
                                </div>
                                <div class="col-md-3 col-sm-12">
                                    <label class="form-label fw-bold mb-1" style="font-size: 11px;">Arama (Cari/Açıklama)</label>
                                    <input type="text" id="doviz_tahsilat_search" list="cari_suggestions" class="form-control form-control-sm" placeholder="Cari kod, ünvan veya açıklama...">
                                </div>
                                <div class="col-md-3 col-sm-12 d-flex gap-2">
                                    <button class="btn btn-primary btn-sm w-100" onclick="loadDovizTahsilatListesi()">🔍 Getir</button>
                                    <button class="btn btn-success btn-sm w-100" onclick="exportDovizTahsilatExcel()">🟢 Excel</button>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div class="col-md-5">
                        <div class="row g-2 h-100">
                            <div class="col-4">
                                <div class="p-2 text-center rounded text-white shadow-sm d-flex flex-column justify-content-center align-items-center h-100 kpi-card-doviz" style="background: linear-gradient(135deg, #2c3e50, #1c2833); border-bottom: 4px solid #141f29; min-height: 80px;">
                                    <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; opacity: 0.85; letter-spacing: 0.5px; margin-bottom: 4px;">TOPLAM TL</div>
                                    <div class="fw-bold mt-1" id="total_doviz_tl" style="font-size: 17px; line-height: 1.2; text-shadow: 1px 1px 2px rgba(0,0,0,0.15);">0,00 TL</div>
                                </div>
                            </div>
                            <div class="col-4">
                                <div class="p-2 text-center rounded text-white shadow-sm d-flex flex-column justify-content-center align-items-center h-100 kpi-card-doviz" style="background: linear-gradient(135deg, #1a5276, #113f5c); border-bottom: 4px solid #0b2b40; min-height: 80px;">
                                    <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; opacity: 0.85; letter-spacing: 0.5px; margin-bottom: 4px;">TOPLAM USD</div>
                                    <div class="fw-bold mt-1" id="total_doviz_usd" style="font-size: 17px; line-height: 1.2; text-shadow: 1px 1px 2px rgba(0,0,0,0.15);">0,00 USD</div>
                                </div>
                            </div>
                            <div class="col-4">
                                <div class="p-2 text-center rounded text-white shadow-sm d-flex flex-column justify-content-center align-items-center h-100 kpi-card-doviz" style="background: linear-gradient(135deg, #145a32, #0d3e22); border-bottom: 4px solid #082914; min-height: 80px;">
                                    <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; opacity: 0.85; letter-spacing: 0.5px; margin-bottom: 4px;">TOPLAM EUR</div>
                                    <div class="fw-bold mt-1" id="total_doviz_eur" style="font-size: 17px; line-height: 1.2; text-shadow: 1px 1px 2px rgba(0,0,0,0.15);">0,00 EUR</div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

                <div class="card dashboard-card overflow-hidden">
                    <div class="table-responsive" style="max-height: 550px; overflow-y: auto;">
                        <table class="table table-hover mb-0">
                            <thead style="position: sticky; top: 0; z-index: 10;">
                                <tr>
                                    <th class="ps-3">Tarih</th>
                                    <th class="text-center">Hafta No</th>
                                    <th>Cari Kodu</th>
                                    <th>Cari Ünvan</th>
                                    <th class="text-center">Tahsilat Cinsi</th>
                                    <th class="text-center">Vade Tarihi</th>
                                    <th class="text-end">TL Tutar</th>
                                    <th class="text-end">USD Tutar</th>
                                    <th class="text-end">EUR Tutar</th>
                                </tr>
                            </thead>
                            <tbody id="dovizTahsilatBody">
                                <tr>
                                    <td colspan="9" class="text-center py-4 text-muted">
                                        Filtreleri belirleyip 'Getir' butonuna tıklayın.
                                    </td>
                                </tr>
                            </tbody>
                            <tfoot id="dovizTahsilatFoot" style="position: sticky; bottom: 0; z-index: 10; background: #fdfdfd; border-top: 2px solid #34495e;">
                                <tr class="fw-bold">
                                    <td colspan="6" class="ps-3 text-end">TOPLAM:</td>
                                    <td id="total_table_tl" class="text-end text-dark">0,00 TL</td>
                                    <td id="total_table_usd" class="text-end text-primary">0,00 USD</td>
                                    <td id="total_table_eur" class="text-end text-success">0,00 EUR</td>
                                </tr>
                            </tfoot>
                        </table>
                    </div>
                </div>
            </div>

            <!-- TAB 3: Borç Yaşlandırma -->
            <div class="tab-pane fade" id="pills-yaslandirma" role="tabpanel" aria-labelledby="pills-yaslandirma-tab">
                <style>
                    #yaslandirmaTable thead th.sortable { cursor: pointer; user-select: none; position: relative; }
                    #yaslandirmaTable thead th.sortable:hover { background-color: rgba(255,255,255,0.15) !important; }
                    .sort-icon-yaslandirma { font-size: 10px; margin-left: 4px; color: #fff; }
                </style>
                <div class="card dashboard-card p-3">
                    <form method="GET" class="row g-2 align-items-end mb-3">
                        <input type="hidden" name="rapor_tarihi" value="{{ secili_tarih }}">
                        {% for c in secili_cariler %}
                            <input type="hidden" name="cariler" value="{{ c }}">
                        {% endfor %}
                        {% for t in secili_temsilciler %}
                            <input type="hidden" name="temsilciler" value="{{ t }}">
                        {% endfor %}
                        <input type="hidden" name="faiz_orani" value="{{ faiz_orani }}">

                        <div class="col-md-2 col-sm-6">
                            <label class="form-label fw-bold mb-1" style="font-size: 11px;">1. Parametre</label>
                            <select name="p1" class="form-select form-select-sm">
                                <option value="5" {% if p1 == 5 %}selected{% endif %}>5 Gün</option>
                                <option value="7" {% if p1 == 7 %}selected{% endif %}>7 Gün</option>
                                <option value="10" {% if p1 == 10 %}selected{% endif %}>10 Gün</option>
                                <option value="15" {% if p1 == 15 %}selected{% endif %}>15 Gün</option>
                                <option value="20" {% if p1 == 20 %}selected{% endif %}>20 Gün</option>
                                <option value="30" {% if p1 == 30 %}selected{% endif %}>30 Gün</option>
                            </select>
                        </div>
                        <div class="col-md-2 col-sm-6">
                            <label class="form-label fw-bold mb-1" style="font-size: 11px;">2. Parametre</label>
                            <select name="p2" class="form-select form-select-sm">
                                <option value="10" {% if p2 == 10 %}selected{% endif %}>10 Gün</option>
                                <option value="15" {% if p2 == 15 %}selected{% endif %}>15 Gün</option>
                                <option value="20" {% if p2 == 20 %}selected{% endif %}>20 Gün</option>
                                <option value="30" {% if p2 == 30 %}selected{% endif %}>30 Gün</option>
                                <option value="45" {% if p2 == 45 %}selected{% endif %}>45 Gün</option>
                                <option value="60" {% if p2 == 60 %}selected{% endif %}>60 Gün</option>
                            </select>
                        </div>
                        <div class="col-md-2 col-sm-6">
                            <label class="form-label fw-bold mb-1" style="font-size: 11px;">3. Parametre</label>
                            <select name="p3" class="form-select form-select-sm">
                                <option value="20" {% if p3 == 20 %}selected{% endif %}>20 Gün</option>
                                <option value="30" {% if p3 == 30 %}selected{% endif %}>30 Gün</option>
                                <option value="45" {% if p3 == 45 %}selected{% endif %}>45 Gün</option>
                                <option value="60" {% if p3 == 60 %}selected{% endif %}>60 Gün</option>
                                <option value="90" {% if p3 == 90 %}selected{% endif %}>90 Gün</option>
                                <option value="120" {% if p3 == 120 %}selected{% endif %}>120 Gün</option>
                            </select>
                        </div>
                        <div class="col-md-2 col-sm-6">
                            <label class="form-label fw-bold mb-1" style="font-size: 11px;">4. Parametre</label>
                            <select name="p4" class="form-select form-select-sm">
                                <option value="30" {% if p4 == 30 %}selected{% endif %}>30 Gün</option>
                                <option value="60" {% if p4 == 60 %}selected{% endif %}>60 Gün</option>
                                <option value="90" {% if p4 == 90 %}selected{% endif %}>90 Gün</option>
                                <option value="120" {% if p4 == 120 %}selected{% endif %}>120 Gün</option>
                                <option value="180" {% if p4 == 180 %}selected{% endif %}>180 Gün</option>
                                <option value="360" {% if p4 == 360 %}selected{% endif %}>360 Gün</option>
                            </select>
                        </div>
                        <div class="col-md-2">
                            <button type="submit" class="btn btn-primary btn-sm w-100">Uygula</button>
                        </div>
                    </form>

                    <div class="p-3 bg-light border-bottom d-flex justify-content-between align-items-center flex-wrap gap-2 mt-3">
                        <span class="fw-bold text-dark m-0" style="font-size: 13px;">⏳ Yaşlandırma Raporu</span>
                        <input type="text" id="yaslandirma_table_search" list="cari_suggestions" class="form-control form-control-sm" placeholder="Tabloda ara (Cari, Temsilci...)" style="max-width: 300px;">
                    </div>
                    <div class="table-responsive">
                        <table class="table table-hover mb-0" id="yaslandirmaTable">
                            <thead class="table-dark">
                                <tr>
                                    <th class="ps-3 sortable" onclick="sortYaslandirmaTable(0)">Temsilci<span class="sort-icon-yaslandirma"></span></th>
                                    <th class="sortable" onclick="sortYaslandirmaTable(1)">Firma Kodu<span class="sort-icon-yaslandirma"></span></th>
                                    <th class="sortable" onclick="sortYaslandirmaTable(2)">Cari Ünvan<span class="sort-icon-yaslandirma"></span></th>
                                    <th class="text-end sortable" onclick="sortYaslandirmaTable(3)">Cari Bakiye<span class="sort-icon-yaslandirma"></span></th>
                                    <th class="text-end sortable" onclick="sortYaslandirmaTable(4)">Günü Gelmeyen<span class="sort-icon-yaslandirma"></span></th>
                                    <th class="text-end sortable" onclick="sortYaslandirmaTable(5)">Günü Geçen<span class="sort-icon-yaslandirma"></span></th>
                                    {% for label in bucket_labels %}
                                        {% set col_idx = loop.index0 + 6 %}
                                        <th class="text-end sortable" onclick="sortYaslandirmaTable({{ col_idx }})">{{ label }}<span class="sort-icon-yaslandirma"></span></th>
                                    {% endfor %}
                                </tr>
                            </thead>
                            <tbody>
                                {% if yaslandirma_list %}
                                    {% set ns = namespace(prev_birim='') %}
                                    {% for row in yaslandirma_list %}
                                        {# --- Yeni grup başlangıcı: Para birimi değişti --- #}
                                        {% if row['BIRIM'] != ns.prev_birim %}
                                            {# Önceki grubun ara toplamını göster (ilk grup değilse) #}
                                            {% if ns.prev_birim != '' and ns.prev_birim in yaslandirma_toplamlari %}
                                                {% set totals = yaslandirma_toplamlari[ns.prev_birim] %}
                                                <tr style="background: #f0f4f8; border-top: 2px solid #5d6d7e; border-bottom: 2px solid #5d6d7e;">
                                                    <td colspan="3" class="text-end py-2 align-middle">
                                                        <span class="fw-bold" style="font-size:12px; color:#2c3e50;">⬆ ARA TOPLAM ({{ ns.prev_birim }}):</span>
                                                    </td>
                                                    <td class="text-end py-2 align-middle fw-bold text-dark">
                                                        {{ "{:,.2f}".format(totals['CARI_BAKIYE']).replace(',', 'X').replace('.', ',').replace('X', '.') }} {{ ns.prev_birim }}
                                                    </td>
                                                    <td class="text-end py-2 align-middle fw-bold text-success">
                                                        {{ "{:,.2f}".format(totals['GUNU_GELMEYEN']).replace(',', 'X').replace('.', ',').replace('X', '.') }} {{ ns.prev_birim }}
                                                    </td>
                                                    <td class="text-end py-2 align-middle fw-bold" style="color: #d35400 !important;">
                                                        {{ "{:,.2f}".format(totals['GUNU_GECEN']).replace(',', 'X').replace('.', ',').replace('X', '.') }} {{ ns.prev_birim }}
                                                    </td>
                                                    {% for val in totals['BUCKETS'] %}
                                                        <td class="text-end py-2 align-middle fw-bold text-danger">
                                                            {{ "{:,.2f}".format(val).replace(',', 'X').replace('.', ',').replace('X', '.') }} {{ ns.prev_birim }}
                                                        </td>
                                                    {% endfor %}
                                                </tr>
                                            {% endif %}
                                            {# Yeni grup başlık satırı #}
                                            {% if row['BIRIM'] == 'TL' %}
                                                <tr style="background: #2c3e50;">
                                                    <td colspan="{{ 6 + bucket_labels|length }}" class="py-2 ps-3">
                                                        <span class="fw-bold text-white" style="font-size: 12px; letter-spacing: 1px;">🇹🇷 TL CARİLER</span>
                                                    </td>
                                                </tr>
                                            {% elif row['BIRIM'] == 'USD' %}
                                                <tr style="background: #1a5276;">
                                                    <td colspan="{{ 6 + bucket_labels|length }}" class="py-2 ps-3">
                                                        <span class="fw-bold text-white" style="font-size: 12px; letter-spacing: 1px;">🇺🇸 USD CARİLER (Dövizli)</span>
                                                    </td>
                                                </tr>
                                            {% elif row['BIRIM'] == 'EUR' %}
                                                <tr style="background: #145a32;">
                                                    <td colspan="{{ 6 + bucket_labels|length }}" class="py-2 ps-3">
                                                        <span class="fw-bold text-white" style="font-size: 12px; letter-spacing: 1px;">🇪🇺 EUR CARİLER (Dövizli)</span>
                                                    </td>
                                                </tr>
                                            {% else %}
                                                <tr style="background: #4a235a;">
                                                    <td colspan="{{ 6 + bucket_labels|length }}" class="py-2 ps-3">
                                                        <span class="fw-bold text-white" style="font-size: 12px; letter-spacing: 1px;">💱 {{ row['BIRIM'] }} CARİLER (Dövizli)</span>
                                                    </td>
                                                </tr>
                                            {% endif %}
                                            {% set ns.prev_birim = row['BIRIM'] %}
                                        {% endif %}
                                        {# Cari satırı #}
                                        <tr class="yaslandirma-row" data-cari-kodu="{{ row['CARI_KODU'] }}">
                                            <td class="ps-3 text-muted small">{{ row['TEMSILCI'] }}</td>
                                            <td class="font-monospace text-muted">{{ row['CARI_KODU'] }}</td>
                                            <td class="fw-bold">{{ row['CARI_UNVAN'] }}</td>
                                            <td class="text-end fw-bold text-dark">{{ "{:,.2f}".format(row['CARI_BAKIYE']).replace(',', 'X').replace('.', ',').replace('X', '.') }} {{ row['BIRIM'] }}</td>
                                            <td class="text-end text-success">{{ "{:,.2f}".format(row['GUNU_GELMEYEN']).replace(',', 'X').replace('.', ',').replace('X', '.') }} {{ row['BIRIM'] }}</td>
                                            <td class="text-end fw-bold" style="color: #d35400 !important;">{{ "{:,.2f}".format(row['GUNU_GECEN']).replace(',', 'X').replace('.', ',').replace('X', '.') }} {{ row['BIRIM'] }}</td>
                                            {% for val in row['BUCKETS'] %}
                                                <td class="text-end {% if val > 0 %}text-danger fw-bold{% else %}text-muted{% endif %}">
                                                    {% if val > 0 %}
                                                        {{ "{:,.2f}".format(val).replace(',', 'X').replace('.', ',').replace('X', '.') }} {{ row['BIRIM'] }}
                                                    {% else %}
                                                        0,00 {{ row['BIRIM'] }}
                                                    {% endif %}
                                                </td>
                                            {% endfor %}
                                        </tr>
                                    {% endfor %}
                                    {# Son grubun ara toplamı #}
                                    {% if ns.prev_birim != '' and ns.prev_birim in yaslandirma_toplamlari %}
                                        {% set totals = yaslandirma_toplamlari[ns.prev_birim] %}
                                        <tr style="background: #f0f4f8; border-top: 2px solid #5d6d7e; border-bottom: 2px solid #5d6d7e;">
                                            <td colspan="3" class="text-end py-2 align-middle">
                                                <span class="fw-bold" style="font-size:12px; color:#2c3e50;">⬆ ARA TOPLAM ({{ ns.prev_birim }}):</span>
                                            </td>
                                            <td class="text-end py-2 align-middle fw-bold text-dark">
                                                {{ "{:,.2f}".format(totals['CARI_BAKIYE']).replace(',', 'X').replace('.', ',').replace('X', '.') }} {{ ns.prev_birim }}
                                            </td>
                                            <td class="text-end py-2 align-middle fw-bold text-success">
                                                {{ "{:,.2f}".format(totals['GUNU_GELMEYEN']).replace(',', 'X').replace('.', ',').replace('X', '.') }} {{ ns.prev_birim }}
                                            </td>
                                            <td class="text-end py-2 align-middle fw-bold" style="color: #d35400 !important;">
                                                {{ "{:,.2f}".format(totals['GUNU_GECEN']).replace(',', 'X').replace('.', ',').replace('X', '.') }} {{ ns.prev_birim }}
                                            </td>
                                            {% for val in totals['BUCKETS'] %}
                                                <td class="text-end py-2 align-middle fw-bold text-danger">
                                                    {{ "{:,.2f}".format(val).replace(',', 'X').replace('.', ',').replace('X', '.') }} {{ ns.prev_birim }}
                                                </td>
                                            {% endfor %}
                                        </tr>
                                    {% endif %}
                                {% else %}
                                    <tr>
                                        <td colspan="{{ 6 + bucket_labels|length }}" class="text-center py-4 text-muted">
                                            Açık bakiye bulunmamaktadır.
                                        </td>
                                    </tr>
                                {% endif %}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- TAB: Cari Hesap Ekstre -->
            <div class="tab-pane fade" id="pills-ekstre" role="tabpanel" aria-labelledby="pills-ekstre-tab">
                <div class="card dashboard-card p-3" style="background: #f8f9fa; border: 1px solid #e3e6f0; margin-bottom: 15px;">
                    <div class="row g-2 align-items-start">
                        <div class="col-md-6 col-sm-12">
                            <label class="form-label fw-bold mb-1" style="font-size: 11px;">Cari Hesap Seçin</label>
                            <select id="ekstre_cari" class="form-control form-control-sm">
                                <option value="">-- Cari Seçin --</option>
                                {% for cari in tum_cariler %}
                                    <option value="{{ cari }}">{{ cari }}</option>
                                {% endfor %}
                            </select>
                            
                            <!-- Dinamik Cari Filtre Alanı -->
                            <div class="mt-2">
                                <div class="input-group input-group-sm shadow-sm" style="border-radius: 6px; overflow: hidden;">
                                    <span class="input-group-text bg-white border-end-0 text-primary fw-bold" style="font-size: 11px; padding: 0.35rem 0.55rem;">
                                        ⚡ Dinamik Filtre
                                    </span>
                                    <input type="text" id="ekstre_cari_dinamik_filtre" class="form-control form-control-sm border-start-0 border-end-0" placeholder="Yazdıkça listeyi anında filtreler (kod / ünvan)..." autocomplete="off" style="font-size: 11.5px;">
                                    <button class="btn btn-outline-secondary border-start-0 bg-white" type="button" id="btn_temizle_cari_filtre" onclick="temizleEkstreCariFiltre()" title="Filtreyi Temizle" style="font-size: 11px; padding: 0.35rem 0.6rem;">✕</button>
                                </div>
                                <div id="ekstre_cari_filtre_info" class="d-flex justify-content-between align-items-center mt-1 px-1" style="font-size: 10.5px; min-height: 16px;">
                                    <span id="ekstre_cari_filtre_sayisi" class="text-muted"></span>
                                    <span id="ekstre_cari_filtre_hint" class="text-muted fst-italic" style="display:none;">[Enter] ile ekstreyi getir</span>
                                </div>
                            </div>
                        </div>
                        <div class="col-md-2 col-sm-12">
                            <label class="form-label fw-bold mb-1" style="font-size: 11px;">Başlangıç Tarihi (Devir İçin)</label>
                            <input type="date" id="ekstre_baslangic" class="form-control form-control-sm" value="{{ secili_tarih[:4] }}-01-01">
                        </div>
                        <div class="col-md-2 col-sm-6" style="margin-top: 20px;">
                            <button class="btn btn-primary btn-sm w-100 fw-bold" onclick="loadCariEkstre()" style="height: 31px;">🔍 Ekstre</button>
                        </div>
                        <div class="col-md-2 col-sm-6" style="margin-top: 20px;">
                            <button class="btn btn-success btn-sm w-100 fw-bold" onclick="exportEkstreExcel()" style="height: 31px;">🟢 Excel İndir</button>
                        </div>
                    </div>
                </div>

                <!-- Ekstre Cari Uyarı Kartı -->
                <div id="ekstre_cari_uyari_box" style="display:none;" class="cari-warning-card">
                    <div>
                        <span>⚠️ <strong>FİRMA ÖZEL UYARISI / NOTU:</strong></span>
                        <span id="ekstre_cari_uyari_text" class="ms-1"></span>
                    </div>
                </div>

                <div class="card dashboard-card overflow-hidden mt-3" id="ekstreResultCard" style="display: none;">
                    <div class="p-3 bg-light border-bottom d-flex justify-content-between align-items-center flex-wrap gap-2">
                        <h6 class="fw-bold m-0" id="ekstreCariTitle" style="color: #2c3e50; font-size: 13px;"></h6>
                        <div class="d-flex align-items-center gap-2">
                            <input type="text" id="ekstre_table_search" list="cari_suggestions" class="form-control form-control-sm" placeholder="Ekstrede ara..." style="max-width: 250px;">
                            <span class="badge fw-bold" id="ekstreBakiyeBadge" style="font-size: 12px; padding: 6px 12px; border-radius: 8px;"></span>
                        </div>
                    </div>
                    <div class="table-responsive">
                        <table class="table table-hover mb-0" id="ekstreTable">
                            <thead>
                                <tr>
                                    <th class="ps-3">Tarih</th>
                                    <th>İşlem Türü</th>
                                    <th>Belge No</th>
                                    <th>Açıklama</th>
                                    <th class="text-end">Borç</th>
                                    <th class="text-end">Alacak</th>
                                    <th class="text-end">Bakiye</th>
                                    <th class="text-center">Döviz</th>
                                </tr>
                            </thead>
                            <tbody id="ekstreBody">
                                <tr><td colspan="8" class="text-center py-4 text-muted">Cari hareket bulunmamaktadır.</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- TAB: Kesilecek Fatura Detay -->
            <div class="tab-pane fade" id="pills-fatura-detay" role="tabpanel" aria-labelledby="pills-fatura-detay-tab">
                <div class="card dashboard-card p-3" style="background: #f8f9fa; border: 1px solid #e3e6f0; margin-bottom: 15px;">
                    <div class="d-flex justify-content-between align-items-center flex-wrap gap-2">
                        <div class="d-flex align-items-center gap-2">
                            <input type="text" id="fatura_detay_search" class="form-control form-control-sm" placeholder="🔍 Fatura veya Cari Ara..." style="max-width: 250px; border-radius: 6px;">
                        </div>
                        <div>
                            <button class="btn btn-success btn-sm fw-bold px-3" style="border-radius: 6px;" onclick="exportFaturaDetayExcel()">🟢 Excel İndir</button>
                        </div>
                    </div>
                </div>

                <!-- Currency Summary Cards -->
                <div class="row g-3 mb-3" id="fatura_detay_summary_cards">
                    <!-- Loaded dynamically via JavaScript -->
                </div>

                <div class="card dashboard-card overflow-hidden mt-3" style="border: 1px solid #e3e6f0; border-radius: 8px;">
                    <div class="p-3 bg-light border-bottom">
                        <h6 class="fw-bold m-0" style="color: #1e3a8a; font-size: 14px;"><i class="fa fa-list me-2"></i>Kesilecek Faturalar Listesi</h6>
                    </div>
                    <div class="table-responsive">
                        <table class="table table-hover align-middle mb-0" id="faturaDetayTable" style="font-size: 11.5px;">
                            <style>
                                #faturaDetayTable thead th.sortable-fatura-detay { cursor: pointer; user-select: none; position: relative; }
                                #faturaDetayTable thead th.sortable-fatura-detay:hover { background-color: rgba(0,0,0,0.05); }
                                .sort-icon-fatura-detay { font-size: 10px; margin-left: 4px; color: #7f8c8d; }
                                .fatura-aciklama-wrapper {
                                    cursor: pointer;
                                    white-space: nowrap;
                                    overflow: hidden;
                                    text-overflow: ellipsis;
                                    max-width: 320px;
                                    display: block;
                                    color: #6c757d;
                                }
                                .fatura-aciklama-wrapper.expanded {
                                    white-space: normal;
                                    overflow: visible;
                                    max-width: none;
                                    color: #212529;
                                }
                            </style>
                            <thead class="table-light fw-bold" style="border-bottom: 2px solid #e3e6f0;">
                                <tr>
                                    <th class="ps-3 sortable-fatura-detay" onclick="sortFaturaDetayTable(0)" style="width: 10%;">Fatura Tarihi<span class="sort-icon-fatura-detay"></span></th>
                                    <th class="sortable-fatura-detay" onclick="sortFaturaDetayTable(1)" style="width: 10%;">Cari Kodu<span class="sort-icon-fatura-detay"></span></th>
                                    <th class="sortable-fatura-detay" onclick="sortFaturaDetayTable(2)" style="width: 25%;">Cari Ünvanı<span class="sort-icon-fatura-detay"></span></th>
                                    <th class="sortable-fatura-detay" onclick="sortFaturaDetayTable(3)">Açıklama<span class="sort-icon-fatura-detay"></span></th>
                                    <th class="text-end sortable-fatura-detay" onclick="sortFaturaDetayTable(4)" style="width: 11%;">Matrah<span class="sort-icon-fatura-detay"></span></th>
                                    <th class="text-end sortable-fatura-detay" onclick="sortFaturaDetayTable(5)" style="width: 8%;">KDV<span class="sort-icon-fatura-detay"></span></th>
                                    <th class="text-end sortable-fatura-detay" onclick="sortFaturaDetayTable(6)" style="width: 12%;">Genel Toplam<span class="sort-icon-fatura-detay"></span></th>
                                    <th class="text-center sortable-fatura-detay" onclick="sortFaturaDetayTable(7)" style="width: 6%;">Döviz<span class="sort-icon-fatura-detay"></span></th>
                                </tr>
                            </thead>
                            <tbody id="faturaDetayBody">
                                <tr>
                                    <td colspan="8" class="text-center py-4 text-muted">
                                        <div class="spinner-border spinner-border-sm text-primary me-2" role="status"></div>
                                        Veriler yükleniyor, lütfen bekleyin...
                                    </td>
                                </tr>
                            </tbody>
                            <tfoot id="faturaDetayFooter" style="border-top: 2px solid #cbd5e1;">
                                <!-- Dynamic totals based on filters loaded here -->
                            </tfoot>
                        </table>
                    </div>
                </div>
            </div>

            <!-- TAB 4: Tahsilat Konuşmaları -->
            <div class="tab-pane fade show active" id="pills-konusma" role="tabpanel" aria-labelledby="pills-konusma-tab">
                <style>
                    .konusma-kategori-badges .form-check { display: inline-block; margin-right: 8px; margin-bottom: 6px; }
                    .konusma-kategori-badges .form-check-input { display: none; }
                    .konusma-kategori-badges .form-check-label {
                        padding: 4px 14px; border-radius: 20px; border: 2px solid #dee2e6;
                        cursor: pointer; font-size: 12px; font-weight: 600; transition: all 0.2s;
                        background: white; color: #495057;
                    }
                    .konusma-kategori-badges .form-check-input:checked + .form-check-label {
                        border-color: #34495e; background: #34495e; color: white;
                    }
                    .badge-kat { font-size: 10px; margin: 1px; }
                    #konusmaCariTable thead th.sortable { cursor: pointer; user-select: none; position: relative; }
                    #konusmaCariTable thead th.sortable:hover { background-color: rgba(0,0,0,0.05); }
                    .sort-icon { font-size: 10px; margin-left: 4px; color: #7f8c8d; }

                    /* Cari Kod/Unvan ve Bakiye arasındaki mesafeyi daraltmak için Cari sütununu sınırlıyoruz */
                    #konusmaCariTable th:nth-child(2),
                    #konusmaCariTable td:nth-child(2) {
                        white-space: normal !important;
                        max-width: 250px;
                        word-break: break-word;
                    }

                    /* Sondaki konuşma sütunlarını genişletiyoruz */
                    #konusmaCariTable th:nth-child(7),
                    #konusmaCariTable td:nth-child(7) {
                        min-width: 120px;
                    }
                    #konusmaCariTable th:nth-child(8),
                    #konusmaCariTable td:nth-child(8) {
                        min-width: 130px;
                        white-space: normal !important;
                    }
                    #konusmaCariTable th:nth-child(9),
                    #konusmaCariTable td:nth-child(9) {
                        min-width: 110px;
                    }
                    #konusmaCariTable th:nth-child(10),
                    #konusmaCariTable td:nth-child(10) {
                        white-space: normal !important;
                        min-width: 450px;
                        word-break: break-word;
                    }
                </style>

                <!-- Kategori Toplamları KPI Kartları -->
                <div id="kategori_toplamlari_container" class="d-flex flex-wrap gap-3 mb-4 mt-2 w-100" style="padding: 0 10px;"></div>

                <div class="p-3 bg-light border-bottom d-flex justify-content-between align-items-center flex-wrap gap-2 mb-3">
                    <span class="fw-bold text-dark m-0" style="font-size: 13px;">💬 Tahsilat Görüşmeleri ve Cari Risk Takibi</span>
                    <input type="text" id="konusma_tab_search" class="form-control form-control-sm" placeholder="Tabloda ara (Cari, Temsilci...)" style="max-width: 300px;">
                </div>

                <div class="card dashboard-card overflow-hidden">
                    <div class="table-responsive">
                        {% set KAT_MAP = {
                            'Kesin Gelecek': '#3498db',
                            'Vade İstedi': '#27ae60',
                            'Ödeme Sözü': '#e67e22',
                            'İtirazlı': '#9b59b6',
                            'Ulaşılamadı': '#e74c3c',
                            'Çek Gönderecek': '#1abc9c'
                        } %}
                        <table class="table table-hover align-middle mb-0" id="konusmaCariTable">
                            <thead>
                                <tr>
                                    <th class="ps-3 sortable" onclick="sortKonusmaTable(0)">Temsilci<span class="sort-icon"></span></th>
                                    <th class="sortable" onclick="sortKonusmaTable(1)">Cari Kod Unvan<span class="sort-icon"></span></th>
                                    <th class="text-end sortable" onclick="sortKonusmaTable(2)">Bakiye<span class="sort-icon"></span></th>
                                    <th class="text-end sortable" onclick="sortKonusmaTable(3)">Günü Gelmeyen<span class="sort-icon"></span></th>
                                    <th class="text-end sortable" onclick="sortKonusmaTable(4)">Günü Geçen<span class="sort-icon"></span></th>
                                    <th class="text-center sortable" onclick="sortKonusmaTable(5)">Ort<span class="sort-icon"></span></th>
                                    <th class="sortable" onclick="sortKonusmaTable(6)">Tarih<span class="sort-icon"></span></th>
                                    <th class="sortable" onclick="sortKonusmaTable(7)">Kategori<span class="sort-icon"></span></th>
                                    <th class="text-end sortable" onclick="sortKonusmaTable(8)">Tutar<span class="sort-icon"></span></th>
                                    <th class="sortable" onclick="sortKonusmaTable(9)">Açıklama<span class="sort-icon"></span></th>
                                </tr>
                            </thead>
                            <tbody>
                                {% if tahsilat_konusma_list %}
                                    {% for row in tahsilat_konusma_list %}
                                    <tr data-cari-full="{{ row.CARI_FULL }}" data-birim="{{ row.BIRIM }}" onclick="openKonusmaModal(this.getAttribute('data-cari-full'), this.getAttribute('data-birim'))" style="cursor:pointer;" class="hover-warning-active">
                                        <td class="ps-3 text-muted small">{{ row.TEMSILCI }}</td>
                                        <td class="fw-bold">{{ row.CARI_FULL }}</td>
                                        <td class="text-end fw-bold text-dark">{{ "{:,.2f}".format(row.BAKIYE).replace(',', 'X').replace('.', ',').replace('X', '.') }} {{ row.BIRIM }}</td>
                                        <td class="text-end text-success">{{ "{:,.2f}".format(row.GUNU_GELMEYEN).replace(',', 'X').replace('.', ',').replace('X', '.') }} {{ row.BIRIM }}</td>
                                        <td class="text-end fw-bold text-danger">{{ "{:,.2f}".format(row.GUNU_GECEN).replace(',', 'X').replace('.', ',').replace('X', '.') }} {{ row.BIRIM }}</td>
                                        <td class="text-center">
                                            {% if row.ORT > 0 %}
                                                <span class="badge bg-danger">{{ row.ORT }} Gün</span>
                                            {% else %}
                                                <span class="text-muted">-</span>
                                            {% endif %}
                                        </td>
                                        <td class="text-muted small">{{ row.SON_KONUSMA_TARIH }}</td>
                                        <td>
                                            {% if row.SON_KONUSMA_KAT and row.SON_KONUSMA_KAT != '-' and row.SON_KONUSMA_KAT != '' %}
                                                {% for kat in row.SON_KONUSMA_KAT.split(',') %}
                                                    {% set clean_kat = kat.strip() %}
                                                    {% set badge_color = KAT_MAP.get(clean_kat, '#7f8c8d') %}
                                                    {% set kat_date = '' %}
                                                    {% if clean_kat == 'Kesin Gelecek' and row.KESIN_GELECEK_TARIH %}
                                                        {% set kat_date = ' (' ~ row.KESIN_GELECEK_TARIH ~ ')' %}
                                                    {% elif clean_kat == 'Vade İstedi' and row.VADE_TARIH %}
                                                        {% set kat_date = ' (' ~ row.VADE_TARIH ~ ')' %}
                                                    {% elif clean_kat == 'Ödeme Sözü' and row.SOZ_VERILEN_TARIH %}
                                                        {% set kat_date = ' (' ~ row.SOZ_VERILEN_TARIH ~ ')' %}
                                                    {% elif clean_kat == 'İtirazlı' and row.ITIRAZ_TARIH %}
                                                        {% set kat_date = ' (' ~ row.ITIRAZ_TARIH ~ ')' %}
                                                    {% elif clean_kat == 'Ulaşılamadı' and row.ULASILAMADI_TARIH %}
                                                        {% set kat_date = ' (' ~ row.ULASILAMADI_TARIH ~ ')' %}
                                                    {% elif clean_kat == 'Çek Gönderecek' and row.CEK_TARIH %}
                                                        {% set kat_date = ' (' ~ row.CEK_TARIH ~ ')' %}
                                                    {% endif %}
                                                    <span class="badge badge-kat" style="background: {{ badge_color }};">{{ clean_kat }}{{ kat_date }}</span>
                                                {% endfor %}
                                            {% else %}
                                                <span class="text-muted">-</span>
                                            {% endif %}
                                        </td>
                                        <td class="text-end fw-bold text-success small">{{ row.SON_KONUSMA_TUTAR_STR }}</td>
                                        <td class="text-muted small" style="white-space: normal;">{{ row.SON_KONUSMA_ACIKLAMA }}</td>
                                    </tr>
                                    {% endfor %}
                                {% else %}
                                    <tr>
                                        <td colspan="10" class="text-center py-4 text-muted">Açık bakiye detayları bulunmamaktadır.</td>
                                    </tr>
                                {% endif %}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div> <!-- TAB 4 Bitiş -->

            <!-- TAB 5: Firma Özel Notları -->
            <div class="tab-pane fade" id="pills-uyari" role="tabpanel" aria-labelledby="pills-uyari-tab">
                <div class="row">
                    <!-- Sol Kısım: Not Ekleme / Düzenleme Formu -->
                    <div class="col-md-4">
                        <div class="card dashboard-card p-4">
                            <h6 class="fw-bold mb-3" style="color:#2c3e50;">➕ Firma Özel Notu Ekle / Düzenle</h6>
                            <form id="cariUyariForm">
                                <div class="mb-3">
                                    <label class="form-label fw-bold" style="font-size:11px;">Cari Hesap *</label>
                                    <select id="uyari_cari_select" class="form-control" required style="width: 100%;">
                                        <option value="">-- Cari Seçin --</option>
                                        {% for cari in tum_cariler %}
                                            <option value="{{ cari }}">{{ cari }}</option>
                                        {% endfor %}
                                    </select>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label fw-bold" style="font-size:11px;">Özel Not / Uyarı *</label>
                                    <textarea id="uyari_text_input" class="form-control" rows="4" placeholder="Firmaya ait özel notu veya uyarıyı girin..." required style="font-size: 12px;"></textarea>
                                </div>
                                <div class="d-flex gap-2">
                                    <button type="submit" class="btn btn-primary px-4 fw-bold" style="font-size: 12px;">💾 Kaydet</button>
                                    <button type="button" id="btn_delete_uyari" class="btn btn-danger px-3 fw-bold" style="display:none; font-size: 12px;" onclick="deleteCariUyariFromTab()">🗑️ Sil</button>
                                </div>
                            </form>
                        </div>
                    </div>
 
                    <!-- Sağ Kısım: Notların Listesi -->
                    <div class="col-md-8">
                        <div class="card dashboard-card p-4">
                            <div class="d-flex justify-content-between align-items-center mb-3 flex-wrap gap-2">
                                <h6 class="fw-bold m-0" style="color:#2c3e50;">📋 Tüm Özel Notlar ve Uyarılar</h6>
                                <input type="text" id="uyari_table_search" class="form-control form-control-sm" placeholder="Notlarda veya carilerde ara..." style="max-width: 250px; font-size: 11px;">
                            </div>
                            <div class="table-responsive">
                                <table class="table table-hover mb-0" id="uyariListTable">
                                    <thead>
                                        <tr>
                                            <th class="ps-3" style="width: 30%;">Cari Bilgisi</th>
                                            <th style="width: 55%;">Özel Not / Uyarı</th>
                                            <th class="text-center" style="width: 15%;">İşlemler</th>
                                        </tr>
                                    </thead>
                                    <tbody id="uyariListBody">
                                        <tr><td colspan="3" class="text-center py-4 text-muted">Yükleniyor...</td></tr>
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                </div>
            </div> <!-- TAB 5 Bitiş -->

        </div> <!-- Tab Content Bitiş -->
    </div>

    <!-- MODAL: Tahsilat Görüşme Kayıt & Geçmiş -->
    <div class="modal fade" id="konusmaKayitModal" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-xl">
            <div class="modal-content" style="border-radius:12px; overflow:hidden;">
                <div class="modal-header bg-dark text-white">
                    <h5 class="modal-title fw-bold" id="konusmaKayitModalTitle">💬 Görüşme Kaydet</h5>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body p-4">
                    <div class="row">
                        <!-- Sol Sütun: Yeni Görüşme Formu -->
                        <div class="col-md-5 border-end">
                            <h6 class="fw-bold mb-3 text-primary">➕ Yeni Konuşma Kaydı</h6>
                            
                            <!-- Cari Bakiye Bilgi Divi -->
                            <div id="modal_konusma_cari_info" class="mb-3 p-3 border rounded bg-light" style="font-size:12px; line-height: 1.5; display:none;"></div>
                            
                            <!-- Cari Uyarı Kartı -->
                            <div id="modal_konusma_cari_uyari_box" style="display:none;" class="cari-warning-card mb-3">
                                <div>
                                    <span>⚠️ <strong>FİRMA UYARISI:</strong></span>
                                    <span id="modal_konusma_cari_uyari_text" class="ms-1"></span>
                                </div>
                            </div>
                            
                            <form id="modalKonusmaForm">
                                <input type="hidden" id="modal_konusma_cari_full">
                                
                                <div class="row g-2 mb-3">
                                    <div class="col-6">
                                        <label class="form-label fw-bold" style="font-size:11px;">Görüşülen Kişi</label>
                                        <input type="text" id="modal_konusma_kiminle" class="form-control form-control-sm" placeholder="Yetkili adı...">
                                    </div>
                                    <div class="col-6">
                                        <label class="form-label fw-bold" style="font-size:11px;">Görüşmeyi Yapan</label>
                                        <input type="text" id="modal_konusma_kaydeden" class="form-control form-control-sm" placeholder="Adınız...">
                                    </div>
                                </div>
                                
                                <div class="row g-2 mb-3">
                                    <div class="col-12">
                                        <label class="form-label fw-bold" style="font-size:11px;">Tutar (Rakam)</label>
                                        <div class="input-group input-group-sm">
                                            <input type="number" step="0.01" id="modal_konusma_tutar" class="form-control" placeholder="0,00">
                                            <select id="modal_konusma_doviz" class="form-select" style="max-width: 80px;">
                                                <option value="TL">TL</option>
                                                <option value="USD">USD</option>
                                                <option value="EUR">EUR</option>
                                            </select>
                                        </div>
                                    </div>
                                </div>

                                <div class="row g-2 mb-3" id="modal_date_fields_container">
                                    <div class="col-6 d-none" id="grp_kesin_tarih">
                                        <label class="form-label fw-bold text-success" style="font-size:11px;">✅ Kesin Gelecek Tarihi</label>
                                        <input type="date" id="modal_konusma_kesin_tarih" class="form-control form-control-sm">
                                    </div>
                                    <div class="col-6 d-none" id="grp_vade_tarih">
                                        <label class="form-label fw-bold text-primary" style="font-size:11px;">📅 Vade İstediği Tarih</label>
                                        <input type="date" id="modal_konusma_vade_tarih" class="form-control form-control-sm">
                                    </div>
                                    <div class="col-6 d-none" id="grp_soz_tarih">
                                        <label class="form-label fw-bold text-warning" style="font-size:11px;">🤝 Ödeme Sözü Tarihi</label>
                                        <input type="date" id="modal_konusma_soz_tarih" class="form-control form-control-sm">
                                    </div>
                                    <div class="col-6 d-none" id="grp_itiraz_tarih">
                                        <label class="form-label fw-bold text-danger" style="font-size:11px;">⚠️ İtiraz Tarihi</label>
                                        <input type="date" id="modal_konusma_itiraz_tarih" class="form-control form-control-sm">
                                    </div>
                                    <div class="col-6 d-none" id="grp_ulasilamadi_tarih">
                                        <label class="form-label fw-bold text-secondary" style="font-size:11px;">📵 Ulaşılamadı Tarihi</label>
                                        <input type="date" id="modal_konusma_ulasilamadi_tarih" class="form-control form-control-sm">
                                    </div>
                                    <div class="col-6 d-none" id="grp_cek_tarih">
                                        <label class="form-label fw-bold text-info" style="font-size:11px;">🏦 Çek Göndereceği Tarih</label>
                                        <input type="date" id="modal_konusma_cek_tarih" class="form-control form-control-sm">
                                    </div>
                                </div>
                                
                                <div class="mb-3">
                                    <label class="form-label fw-bold" style="font-size:11px;">Açıklama</label>
                                    <textarea id="modal_konusma_aciklama" class="form-control form-control-sm" rows="2" placeholder="Konuşma notları..."></textarea>
                                </div>
                                
                                <div class="mb-3">
                                    <label class="form-label fw-bold" style="font-size:11px;">Kategori (Çoklu Seçim)</label>
                                    <div class="konusma-kategori-badges" style="font-size: 11px;">
                                        <div class="form-check"><input class="form-check-input modal-kat-cb" type="checkbox" value="Kesin Gelecek" id="m_kat1"><label class="form-check-label" for="m_kat1">✅ Kesin Gelecek</label></div>
                                        <div class="form-check"><input class="form-check-input modal-kat-cb" type="checkbox" value="Vade İstedi" id="m_kat2"><label class="form-check-label" for="m_kat2">📅 Vade İstedi</label></div>
                                        <div class="form-check"><input class="form-check-input modal-kat-cb" type="checkbox" value="Ödeme Sözü" id="m_kat3"><label class="form-check-label" for="m_kat3">🤝 Ödeme Sözü</label></div>
                                        <div class="form-check"><input class="form-check-input modal-kat-cb" type="checkbox" value="İtirazlı" id="m_kat4"><label class="form-check-label" for="m_kat4">⚠️ İtirazlı</label></div>
                                        <div class="form-check"><input class="form-check-input modal-kat-cb" type="checkbox" value="Ulaşılamadı" id="m_kat5"><label class="form-check-label" for="m_kat5">📵 Ulaşılamadı</label></div>
                                        <div class="form-check"><input class="form-check-input modal-kat-cb" type="checkbox" value="Çek Gönderecek" id="m_kat6"><label class="form-check-label" for="m_kat6">🏦 Çek Gönderecek</label></div>
                                    </div>
                                </div>
                                
                                <div class="d-flex gap-2">
                                    <button type="submit" class="btn btn-primary btn-sm px-4">💾 Kaydet</button>
                                    <button type="button" class="btn btn-secondary btn-sm px-3" data-bs-dismiss="modal">Kapat</button>
                                    <span id="modalKonusmaMsg" class="align-self-center text-success fw-bold small"></span>
                                </div>
                            </form>
                        </div>
                        
                        <!-- Sağ Sütun: Geçmiş Görüşmeler -->
                        <div class="col-md-7 ps-4">
                            <h6 class="fw-bold mb-3 text-secondary">⏳ Geçmiş Görüşmeler</h6>
                            <div class="table-responsive" style="max-height: 380px; overflow-y: auto;">
                                <table class="table table-bordered align-middle" style="font-size:12px;">
                                    <thead class="table-light text-uppercase" style="font-size:11px; position: sticky; top: 0; background: white; z-index: 1;">
                                        <tr>
                                            <th style="width: 20%;">Tarih</th>
                                            <th style="width: 25%;">Kategori</th>
                                            <th style="width: 30%;">Açıklama</th>
                                            <th style="width: 15%;">Tutar</th>
                                            <th style="width: 10%; text-align: center;">İşlemler</th>
                                        </tr>
                                    </thead>
                                    <tbody id="konusma_gecmis_tbody">
                                        <tr><td colspan="5" class="text-center py-3 text-muted">Geçmiş görüşme bulunamadı.</td></tr>
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Düzenleme Modal -->
    <div class="modal fade" id="editModal">
        <div class="modal-dialog modal-lg">
            <div class="modal-content" style="border-radius:12px; overflow:hidden;">
                <div class="modal-header">
                    <h6 class="modal-title fw-bold">✏️ Konuşma Düzenle</h6>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                </div>
                <div class="modal-body p-4">
                    <input type="hidden" id="edit_id">
 
                    <!-- Modal Cari Uyarı Kartı -->
                    <div id="edit_cari_uyari_box" style="display:none;" class="cari-warning-card">
                        <div>
                            <span>⚠️ <strong>FİRMA ÖZEL UYARISI / NOTU:</strong></span>
                            <span id="edit_cari_uyari_text" class="ms-1"></span>
                        </div>
                    </div>

                    <div class="row g-3">
                        <div class="col-md-4">
                            <label class="form-label fw-bold" style="font-size:11px;">Cari Kodu</label>
                            <select id="edit_cari" class="form-control">
                                {% for cari in tum_cariler %}
                                <option value="{{ cari }}">{{ cari }}</option>
                                {% endfor %}
                            </select>
                            <div id="edit_cari_info" class="mt-2 p-2 border rounded bg-light" style="display:none; font-size:11px; line-height: 1.4;">
                                <!-- Dynamic rows loaded here -->
                            </div>
                        </div>
                        <div class="col-md-4">
                            <label class="form-label fw-bold" style="font-size:11px;">Kiminle Konuşuldu</label>
                            <input type="text" id="edit_kiminle" class="form-control">
                        </div>
                        <div class="col-md-4">
                            <label class="form-label fw-bold" style="font-size:11px;">Görüşmeyi Yapan</label>
                            <input type="text" id="edit_kaydeden" class="form-control">
                        </div>
                        <div class="col-md-4">
                            <label class="form-label fw-bold" style="font-size:11px;">Tutar (Rakam)</label>
                            <div class="input-group">
                                <input type="number" step="0.01" id="edit_tutar" class="form-control" placeholder="0,00">
                                <select id="edit_doviz" class="form-select" style="max-width: 80px;">
                                    <option value="TL">TL</option>
                                    <option value="USD">USD</option>
                                    <option value="EUR">EUR</option>
                                </select>
                            </div>
                        </div>
                        <div class="col-md-4 d-none" id="edit_grp_kesin_tarih">
                            <label class="form-label fw-bold text-success" style="font-size:11px;">✅ Kesin Gelecek Tarihi</label>
                            <input type="date" id="edit_kesin_tarih" class="form-control">
                        </div>
                        <div class="col-md-4 d-none" id="edit_grp_vade_tarih">
                            <label class="form-label fw-bold text-primary" style="font-size:11px;">📅 Vade İstediği Tarih</label>
                            <input type="date" id="edit_vade_tarih" class="form-control">
                        </div>
                        <div class="col-md-4 d-none" id="edit_grp_soz_tarih">
                            <label class="form-label fw-bold text-warning" style="font-size:11px;">🤝 Ödeme Sözü Tarihi</label>
                            <input type="date" id="edit_soz_tarih" class="form-control">
                        </div>
                        <div class="col-md-4 d-none" id="edit_grp_itiraz_tarih">
                            <label class="form-label fw-bold text-danger" style="font-size:11px;">⚠️ İtiraz Tarihi</label>
                            <input type="date" id="edit_itiraz_tarih" class="form-control">
                        </div>
                        <div class="col-md-4 d-none" id="edit_grp_ulasilamadi_tarih">
                            <label class="form-label fw-bold text-secondary" style="font-size:11px;">📵 Ulaşılamadı Tarihi</label>
                            <input type="date" id="edit_ulasilamadi_tarih" class="form-control">
                        </div>
                        <div class="col-md-4 d-none" id="edit_grp_cek_tarih">
                            <label class="form-label fw-bold text-info" style="font-size:11px;">🏦 Çek Göndereceği Tarih</label>
                            <input type="date" id="edit_cek_tarih" class="form-control">
                        </div>
                        <div class="col-md-12">
                            <label class="form-label fw-bold" style="font-size:11px;">Açıklama</label>
                            <input type="text" id="edit_aciklama" class="form-control">
                        </div>
                        <div class="col-md-12">
                            <label class="form-label fw-bold" style="font-size:11px;">Kategori</label>
                            <div class="konusma-kategori-badges" id="edit_kategoriler">
                                <div class="form-check"><input class="form-check-input edit-kat-cb" type="checkbox" value="Kesin Gelecek" id="ekat1"><label class="form-check-label" for="ekat1">✅ Kesin Gelecek</label></div>
                                <div class="form-check"><input class="form-check-input edit-kat-cb" type="checkbox" value="Vade İstedi" id="ekat2"><label class="form-check-label" for="ekat2">📅 Vade İstedi</label></div>
                                <div class="form-check"><input class="form-check-input edit-kat-cb" type="checkbox" value="Ödeme Sözü" id="ekat3"><label class="form-check-label" for="ekat3">🤝 Ödeme Sözü</label></div>
                                <div class="form-check"><input class="form-check-input edit-kat-cb" type="checkbox" value="İtirazlı" id="ekat4"><label class="form-check-label" for="ekat4">⚠️ İtirazlı</label></div>
                                <div class="form-check"><input class="form-check-input edit-kat-cb" type="checkbox" value="Ulaşılamadı" id="ekat5"><label class="form-check-label" for="ekat5">📵 Ulaşılamadı</label></div>
                                <div class="form-check"><input class="form-check-input edit-kat-cb" type="checkbox" value="Çek Gönderecek" id="ekat6"><label class="form-check-label" for="ekat6">🏦 Çek Gönderecek</label></div>
                            </div>
                        </div>
                    </div>
                </div>
                <div class="modal-footer">
                    <button class="btn btn-secondary" data-bs-dismiss="modal">İptal</button>
                    <button class="btn btn-primary" onclick="saveEdit()">💾 Güncelle</button>
                </div>
            </div>
        </div>
    </div>

    <!-- Kayan Yazı Uyarı Çubuğu -->
    <div id="kayan_uyari_bar" style="display:none;" class="marquee-container">
        <div class="marquee-content">
            <span style="font-size: 16px; margin-right: 8px;">🚨</span>
            <strong style="text-transform: uppercase;">FİRMA ÖZEL NOTU / UYARISI:</strong>
            <span id="kayan_uyari_text" style="margin-left: 10px;"></span>
        </div>
    </div>

    <!-- Özel Not / Uyarı Pop-up Modalı -->
    <div class="modal fade" id="warningPopupModal" tabindex="-1" aria-hidden="true" style="z-index: 1060;">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content" style="border: 2px solid #dc3545; border-radius: 12px; box-shadow: 0 10px 30px rgba(220, 53, 69, 0.3);">
                <div class="modal-header bg-danger text-white">
                    <h5 class="modal-title fw-bold" id="warningPopupTitle">🚨 FİRMA ÖZEL NOTU / UYARISI</h5>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="Kapat"></button>
                </div>
                <div class="modal-body p-4 text-center">
                    <h5 class="fw-bold mb-3 text-danger" id="warningPopupCariName"></h5>
                    <p class="fs-5 mb-0 fw-semibold text-dark" id="warningPopupContent" style="white-space: pre-line;"></p>
                </div>
                <div class="modal-footer bg-light justify-content-center">
                    <button type="button" class="btn btn-danger px-4 fw-bold" data-bs-dismiss="modal">Okudum, Anladım</button>
                </div>
            </div>
        </div>
    </div>

    <!-- MODAL: Kategori Detay Listesi (Hangi Gün Ne Kadar Para Gelecek) -->
    <div class="modal fade" id="categoryDetailModal" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-xl modal-dialog-scrollable">
            <div class="modal-content" style="border-radius:12px; overflow:hidden;">
                <div class="modal-header text-white" id="categoryDetailModalHeader" style="background-color: #34495e;">
                    <h5 class="modal-title fw-bold" id="categoryDetailModalTitle">📋 Kategori Detayları</h5>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body p-4">
                    <div class="row g-3 mb-3 align-items-center">
                        <div class="col-sm-8">
                            <span class="text-muted fw-bold" id="categoryDetailSub">Kategoriye ait kayıtlar listeleniyor.</span>
                        </div>
                        <div class="col-sm-4 text-end">
                            <div id="categoryDetailTotals" class="d-flex gap-2 justify-content-end"></div>
                        </div>
                    </div>
                    
                    <!-- Günlük Toplamlar Bölümü -->
                    <div class="mb-3 p-3 bg-light border rounded" id="categoryDetailDailyTotalsSection" style="display:none;">
                        <h6 class="fw-bold text-dark border-bottom pb-2 mb-2" style="font-size: 13px;">📅 Gün Bazlı Ödeme Toplamları</h6>
                        <div id="categoryDetailDailyTotalsContainer" class="d-flex flex-wrap gap-2" style="font-size: 11px;"></div>
                    </div>

                    <div class="table-responsive">
                        <table class="table table-hover align-middle" style="font-size:12px;" id="categoryDetailTable">
                            <thead class="table-dark">
                                <tr>
                                    <th style="width: 20%;" class="sortable-detail" onclick="sortDetailTable(0)">Ödeme Tarihi<span class="sort-icon-detail"></span></th>
                                    <th style="width: 35%;" class="sortable-detail" onclick="sortDetailTable(1)">Cari Hesap<span class="sort-icon-detail"></span></th>
                                    <th style="width: 15%; text-align: right;" class="sortable-detail" onclick="sortDetailTable(2)">Tutar<span class="sort-icon-detail"></span></th>
                                    <th style="width: 15%;" class="sortable-detail" onclick="sortDetailTable(3)">Temsilci<span class="sort-icon-detail"></span></th>
                                    <th style="width: 15%;">Açıklama</th>
                                </tr>
                            </thead>
                            <tbody id="categoryDetailTbody">
                                <!-- Dynamic rows -->
                            </tbody>
                        </table>
                    </div>
                </div>
                <div class="modal-footer d-flex justify-content-between">
                    <button type="button" class="btn btn-success btn-sm px-3 fw-bold" onclick="copyCategoryDetailForWhatsApp()" id="btnCopyWhatsApp">
                        🟢 WhatsApp İçin Kopyala
                    </button>
                    <span id="copyFeedback" class="text-success small fw-bold me-auto ms-2" style="display:none;"></span>
                    <button type="button" class="btn btn-secondary btn-sm" data-bs-dismiss="modal">Kapat</button>
                </div>
            </div>
        </div>
    </div>

    <!-- Cari Ünvan Balloon Tooltip Container -->
    <div id="fatura-tooltip-balloon" style="display: none; position: absolute; z-index: 10000; background: #ffffff; color: #1e293b; padding: 10px 14px; border-radius: 8px; font-size: 12px; box-shadow: 0 10px 25px rgba(0,0,0,0.15); pointer-events: none; max-width: 350px; line-height: 1.4; border: 1px solid #cbd5e1;"></div>

    <script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/select2@4.1.0-rc.0/dist/js/select2.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        var TCMB_USD_RATE = {{ tcmb_usd | default(0.0) | tojson }};
        var TCMB_EUR_RATE = {{ tcmb_eur | default(0.0) | tojson }};
        var globalCariUyarilar = {{ uyarilar_json | safe }};
        var globalFaturaDetayTotalsByCari = {};

        $(document).ready(function() {
            // Fetch unbilled invoice totals in the background for tooltips
            fetch('/faturadetay/liste')
                .then(r => r.json())
                .then(res => {
                    if (res.success && res.data) {
                        var totals = {};
                        res.data.forEach(row => {
                            var code = (row.CariHesapKodu || '').trim().toUpperCase();
                            var doviz = row.DovizTipi;
                            var tutar = row.GenelToplam || 0;
                            if (!totals[code]) {
                                totals[code] = {};
                            }
                            if (!totals[code][doviz]) {
                                totals[code][doviz] = 0;
                            }
                            totals[code][doviz] += tutar;
                        });
                        globalFaturaDetayTotalsByCari = totals;
                    }
                })
                .catch(err => console.error("Error fetching fatura detay totals:", err));

            // Tooltip balloon for unbilled invoice totals on hovering customer name in Tahsilat Konuşmaları
            $(document).on('mouseenter', '#konusmaCariTable tbody tr td:nth-child(2)', function(e) {
                var fullVal = ($(this).parent().attr('data-cari-full') || '').trim();
                var cKod = '';
                if (fullVal.includes(' - ')) {
                    cKod = fullVal.split(' - ')[0].trim();
                } else {
                    cKod = fullVal.trim();
                }
                cKod = cKod.toUpperCase();
                var unvan = $(this).text().trim();
                var text = '<div class="fw-bold border-bottom pb-1 mb-1" style="color: #1e3a8a; font-size: 12.5px;">' + unvan + '</div>';
                
                if (cKod && globalFaturaDetayTotalsByCari[cKod]) {
                    var currencyDict = globalFaturaDetayTotalsByCari[cKod];
                    var parts = [];
                    Object.keys(currencyDict).forEach(dov => {
                        parts.push('<span style="color: #0f172a; font-weight: bold;">' + formatCurrencyJS(currencyDict[dov]) + ' ' + dov + '</span>');
                    });
                    text += '<span style="color: #b45309; font-weight: bold;">📋 Kesilmeyi Bekleyen Toplam:</span><br>' + parts.join('<br>');
                } else {
                    text += '<span style="color: #64748b;">📋 Kesilmeyi bekleyen resmi fatura bulunmamaktadır.</span>';
                }
                
                $('#fatura-tooltip-balloon').html(text).show();
            }).on('mousemove', '#konusmaCariTable tbody tr td:nth-child(2)', function(e) {
                $('#fatura-tooltip-balloon').css({
                    top: (e.pageY + 15) + 'px',
                    left: (e.pageX + 15) + 'px'
                });
            }).on('mouseleave', '#konusmaCariTable tbody tr td:nth-child(2)', function() {
                $('#fatura-tooltip-balloon').hide();
            });
            // Hover warning on table rows (scrolling ticker bar)
            $(document).on('mouseenter', 'tr[data-cari-kodu]', function() {
                var cKod = ($(this).attr('data-cari-kodu') || '').trim();
                if (cKod && globalCariUyarilar[cKod]) {
                    $('#kayan_uyari_text').text(globalCariUyarilar[cKod]);
                    $('#kayan_uyari_bar').stop(true, true).slideDown(150);
                    $(this).addClass('hover-warning-active');
                }
            }).on('mouseleave', 'tr[data-cari-kodu]', function() {
                $('#kayan_uyari_bar').stop(true, true).slideUp(150);
                $(this).removeClass('hover-warning-active');
            });

            updateMultiSelectSummary('temsilciSelect', 'temsilciDropdownWrapper');
            updateMultiSelectSummary('cariSelect', 'cariDropdownWrapper');
            $('#filtre_cari').select2({ placeholder: 'Tüm Cariler', dropdownParent: $('#filtre_cari').parent() });
            $('#edit_cari').select2({ placeholder: 'Cari seçin...', dropdownParent: $('#editModal') });
            $('#ekstre_cari').select2({ placeholder: 'Cari seçin...', dropdownParent: $('#pills-ekstre'), width: '100%' });
            
            // --- Cari Ekstre Sekmesi Dinamik Filtreleme Mantığı ---
            var rawEkstreCariler = [];
            $('#ekstre_cari option').each(function() {
                var v = $(this).val();
                if (v) {
                    rawEkstreCariler.push({
                        value: v,
                        text: $(this).text()
                    });
                }
            });

            function normalizeTR(str) {
                if (!str) return '';
                return str
                    .replace(/İ/g, 'i')
                    .replace(/I/g, 'ı')
                    .replace(/ı/g, 'i')
                    .replace(/ş/g, 's')
                    .replace(/Ş/g, 's')
                    .replace(/ğ/g, 'g')
                    .replace(/Ğ/g, 'g')
                    .replace(/ü/g, 'u')
                    .replace(/Ü/g, 'u')
                    .replace(/ö/g, 'o')
                    .replace(/Ö/g, 'o')
                    .replace(/ç/g, 'c')
                    .replace(/Ç/g, 'c')
                    .toLowerCase();
            }

            function applyEkstreCariDinamikFiltre() {
                var q = ($('#ekstre_cari_dinamik_filtre').val() || '').trim();
                var normQ = normalizeTR(q);
                var currentVal = $('#ekstre_cari').val();
                
                var filtered = [];
                if (!normQ) {
                    filtered = rawEkstreCariler;
                } else {
                    for (var i = 0; i < rawEkstreCariler.length; i++) {
                        var item = rawEkstreCariler[i];
                        if (normalizeTR(item.text).indexOf(normQ) !== -1 || normalizeTR(item.value).indexOf(normQ) !== -1) {
                            filtered.push(item);
                        }
                    }
                }

                // Select kutusunu yeniden oluştur
                var $select = $('#ekstre_cari');
                $select.empty();
                $select.append($('<option>', { value: '', text: '-- Cari Seçin --' }));
                
                var hasCurrent = false;
                filtered.forEach(function(item) {
                    var opt = $('<option>', { value: item.value, text: item.text });
                    if (item.value === currentVal) {
                        opt.prop('selected', true);
                        hasCurrent = true;
                    }
                    $select.append(opt);
                });

                // Durum göstergesi ve tek eşleşmede otomatik seçim
                var $sayac = $('#ekstre_cari_filtre_sayisi');
                var $hint = $('#ekstre_cari_filtre_hint');
                if (normQ) {
                    if (filtered.length === 0) {
                        $sayac.html('<span class="text-danger fw-bold">❌ Eşleşen cari bulunamadı</span>');
                        $hint.hide();
                    } else if (filtered.length === 1) {
                        $select.val(filtered[0].value);
                        $sayac.html('<span class="text-success fw-bold">✓ 1 cari eşleşti (Seçildi)</span>');
                        $hint.show();
                    } else {
                        $sayac.html('<span class="text-primary fw-bold">⚡ ' + filtered.length + ' cari eşleşti</span>');
                        $hint.show();
                    }
                } else {
                    $sayac.text('');
                    $hint.hide();
                }

                $select.trigger('change.select2');
            }

            window.temizleEkstreCariFiltre = function() {
                $('#ekstre_cari_dinamik_filtre').val('');
                applyEkstreCariDinamikFiltre();
                $('#ekstre_cari_dinamik_filtre').focus();
            };

            $('#ekstre_cari_dinamik_filtre').on('input', applyEkstreCariDinamikFiltre);

            $('#ekstre_cari_dinamik_filtre').on('keydown', function(e) {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    var selectedCari = $('#ekstre_cari').val();
                    if (!selectedCari && $('#ekstre_cari option').length > 1) {
                        var firstVal = $('#ekstre_cari option').eq(1).val();
                        $('#ekstre_cari').val(firstVal).trigger('change.select2');
                        selectedCari = firstVal;
                    }
                    if (selectedCari) {
                        loadCariEkstre();
                    }
                }
            });

            $('#ekstre_cari').on('change', function() {
                var val = $(this).val();
                if (val) {
                    $('#ekstre_cari_filtre_hint').show();
                }
            });
            // ---------------------------------------------------

            $('#uyari_cari_select').select2({ placeholder: 'Cari seçin...', dropdownParent: $('#pills-uyari') });
            $('#edit_cari').on('change', function() {
                fetchCariBakiyeDetay($(this).val(), 'edit_cari_info');
            });

            // Listeners for date requirement changes in Kaydet modal
            $(document).on('change', '.modal-kat-cb', function() {
                updateDateFieldsRequirement();
            });

            // Listeners for date requirement changes in Edit modal
            $(document).on('change', '.edit-kat-cb', function() {
                updateEditDateFieldsRequirement();
            });

            // Bind change event for warnings tab select
            $('#uyari_cari_select').on('change', function() {
                var val = $(this).val();
                if (!val) {
                    document.getElementById('uyari_text_input').value = '';
                    document.getElementById('btn_delete_uyari').style.display = 'none';
                    return;
                }
                var cKod = val.indexOf(' - ') > -1 ? val.split(' - ')[0].trim() : val.trim();
                if (globalCariUyarilar[cKod]) {
                    document.getElementById('uyari_text_input').value = globalCariUyarilar[cKod];
                    document.getElementById('btn_delete_uyari').style.display = 'inline-block';
                } else {
                    document.getElementById('uyari_text_input').value = '';
                    document.getElementById('btn_delete_uyari').style.display = 'none';
                }
            });

            // Debounce helper
            function debounce(func, wait) {
                var timeout;
                return function() {
                    var context = this, args = arguments;
                    clearTimeout(timeout);
                    timeout = setTimeout(function() {
                        func.apply(context, args);
                    }, wait);
                };
            }

            // Live Search for Doviz Tahsilat list (keystroke live filter via API)
            $('#doviz_tahsilat_search').on('input', debounce(function() {
                loadDovizTahsilatListesi();
            }, 250));

            // Auto-fetch Doviz Tahsilat on date change
            $('#doviz_tahsilat_baslangic, #doviz_tahsilat_bitis').on('change', function() {
                loadDovizTahsilatListesi();
            });

            // Auto-fetch Günlük Tahsilat on date change
            $('#tahsilat_tarihi_input').on('change', function() {
                loadTahsilatListesi();
            });

            // Auto-fetch Ekstre on select/date change
            $('#ekstre_cari, #ekstre_baslangic').on('change', function() {
                loadCariEkstre();
            });

            // Auto-filter Tahsilat Konuşmaları on dropdown change
            $('#filtre_cari, #filtre_kat').on('change', function() {
                loadKonusmalar();
            });

            // Live text filter for Tahsilat Konuşmaları table (client-side)
            $('#filtre_konusma_arama').on('input', function() {
                var value = $(this).val().toLowerCase();
                $('#konusmaBody tr').filter(function() {
                    $(this).toggle($(this).text().toLowerCase().indexOf(value) > -1);
                });
                updateCategoryTotalsFromVisible();
            });

            // Client-side search filters for all tables
            $('#risk_table_search').on('input', function() {
                var value = $(this).val().toLowerCase();
                $('#riskTable tbody tr').filter(function() {
                    $(this).toggle($(this).text().toLowerCase().indexOf(value) > -1);
                });
            });

            $('#tahsilat_table_search').on('input', function() {
                var value = $(this).val().toLowerCase();
                $('#tahsilatTable tbody tr').filter(function() {
                    $(this).toggle($(this).text().toLowerCase().indexOf(value) > -1);
                });
            });

            $('#yaslandirma_table_search').on('input', function() {
                var value = $(this).val().toLowerCase();
                $('.yaslandirma-row').filter(function() {
                    $(this).toggle($(this).text().toLowerCase().indexOf(value) > -1);
                });
            });

            $('#ekstre_table_search').on('input', function() {
                var value = $(this).val().toLowerCase();
                $('#ekstreBody tr').filter(function() {
                    $(this).toggle($(this).text().toLowerCase().indexOf(value) > -1);
                });
            });

            $('#fatura_detay_search').on('input', function() {
                var value = $(this).val().toLowerCase();
                $('#faturaDetayBody tr').filter(function() {
                    $(this).toggle($(this).text().toLowerCase().indexOf(value) > -1);
                });
                updateFaturaDetayTotals();
            });

            // Restore active tab from localStorage
            var activeTab = localStorage.getItem('activeTab');
            if (activeTab) {
                var tabEl = document.querySelector('#' + activeTab);
                if (tabEl) {
                    var tabTrigger = new bootstrap.Tab(tabEl);
                    tabTrigger.show();
                    if (activeTab === 'pills-fatura-detay-tab') {
                        loadFaturaDetayListesi();
                    }
                }
            }

            // Save active tab on click
            var tabElList = [].slice.call(document.querySelectorAll('button[data-bs-toggle="pill"]'))
            tabElList.forEach(function (tabEl) {
                tabEl.addEventListener('shown.bs.tab', function (event) {
                    localStorage.setItem('activeTab', event.target.id);
                    if (event.target.id === 'pills-konusma-tab') {
                        loadKonusmalar();
                        updateCategoryTotalsFromVisible();
                    } else if (event.target.id === 'pills-tahsilat-ek-tab') {
                        loadDovizTahsilatListesi();
                    } else if (event.target.id === 'pills-fatura-detay-tab') {
                        loadFaturaDetayListesi();
                    } else if (event.target.id === 'pills-uyari-tab') {
                        loadCariUyarilarTab();
                    }
                });
            });

            // Modal Konuşma form submit
            document.getElementById('modalKonusmaForm').addEventListener('submit', function(e) {
                e.preventDefault();
                var kategoriler = [];
                document.querySelectorAll('.modal-kat-cb:checked').forEach(function(cb) { kategoriler.push(cb.value); });
                var cariVal = document.getElementById('modal_konusma_cari_full').value;
                if (!cariVal) { alert('Lütfen cari seçin!'); return; }
                var kaydedenVal = document.getElementById('modal_konusma_kaydeden').value;
                localStorage.setItem('tahsilat_kaydeden', kaydedenVal);
 
                var payload = {
                    cari_full: cariVal,
                    kiminle: document.getElementById('modal_konusma_kiminle').value,
                    kaydeden: kaydedenVal,
                    tutar: document.getElementById('modal_konusma_tutar').value,
                    doviz: document.getElementById('modal_konusma_doviz').value,
                    kesin_gelecek_tarih: document.getElementById('modal_konusma_kesin_tarih').value,
                    vade_tarih: document.getElementById('modal_konusma_vade_tarih').value,
                    soz_verilen_tarih: document.getElementById('modal_konusma_soz_tarih').value,
                    itiraz_tarih: document.getElementById('modal_konusma_itiraz_tarih').value,
                    ulasilamadi_tarih: document.getElementById('modal_konusma_ulasilamadi_tarih').value,
                    cek_tarih: document.getElementById('modal_konusma_cek_tarih').value,
                    aciklama: document.getElementById('modal_konusma_aciklama').value,
                    kategoriler: kategoriler
                };
                fetch('/konusma/ekle', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                }).then(r => r.json()).then(function(res) {
                    if (res.ok) {
                        document.getElementById('modalKonusmaMsg').textContent = '✅ Kaydedildi!';
                        
                        // Clear inputs
                        document.getElementById('modal_konusma_kiminle').value = '';
                        document.getElementById('modal_konusma_tutar').value = '';
                        document.getElementById('modal_konusma_kesin_tarih').value = '';
                        document.getElementById('modal_konusma_vade_tarih').value = '';
                        document.getElementById('modal_konusma_soz_tarih').value = '';
                        document.getElementById('modal_konusma_itiraz_tarih').value = '';
                        document.getElementById('modal_konusma_ulasilamadi_tarih').value = '';
                        document.getElementById('modal_konusma_cek_tarih').value = '';
                        document.getElementById('modal_konusma_aciklama').value = '';
                        document.querySelectorAll('.modal-kat-cb').forEach(function(cb) { cb.checked = false; });
                        updateDateFieldsRequirement();
                        
                        setTimeout(function() { document.getElementById('modalKonusmaMsg').textContent = ''; }, 3000);
                        
                        // Reload history in modal (which also updates dashboard dynamically)
                        loadCariKonusmaHistory(cariVal);
                    } else {
                        alert('Hata: ' + res.error);
                    }
                });
            });

            // Client-side search for tahsilat konusma summary table
            document.getElementById('konusma_tab_search').addEventListener('input', function() {
                var val = this.value.toLowerCase().trim();
                var rows = document.querySelectorAll('#konusmaCariTable tbody tr');
                rows.forEach(function(row) {
                    var text = row.textContent.toLowerCase();
                    if (text.indexOf(val) > -1) {
                        row.style.display = '';
                    } else {
                        row.style.display = 'none';
                    }
                });
                updateCategoryTotalsFromVisible();
            });

            // Cari uyarı formu submit
            document.getElementById('cariUyariForm').addEventListener('submit', function(e) {
                e.preventDefault();
                var cariVal = document.getElementById('uyari_cari_select').value;
                var uyariVal = document.getElementById('uyari_text_input').value;
 
                if (!cariVal) { alert('Lütfen cari seçin!'); return; }
 
                var payload = {
                    cari_full: cariVal,
                    uyari: uyariVal
                };
 
                fetch('/cari/uyari/kaydet', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                })
                .then(r => r.json())
                .then(function(res) {
                    if (res.ok) {
                        alert('Cari özel not kaydedildi!');
                        var cKod = cariVal.indexOf(' - ') > -1 ? cariVal.split(' - ')[0].trim() : cariVal.trim();
                        if (uyariVal.trim()) {
                            globalCariUyarilar[cKod] = uyariVal.trim();
                        } else {
                            delete globalCariUyarilar[cKod];
                        }
 
                        // Reset form
                        $('#uyari_cari_select').val('').trigger('change');
                        document.getElementById('uyari_text_input').value = '';
                        document.getElementById('btn_delete_uyari').style.display = 'none';
 
                        loadCariUyarilarTab();
                    } else {
                        alert('Hata: ' + res.error);
                    }
                });
            });

            // Live search for warnings table
            $('#uyari_table_search').on('input', function() {
                var value = $(this).val().toLowerCase();
                $('#uyariListBody tr').filter(function() {
                    $(this).toggle($(this).text().toLowerCase().indexOf(value) > -1);
                });
            });

            // İlk yükleme
            var activeTabVal = localStorage.getItem('activeTab');
            if (!activeTabVal || activeTabVal === 'pills-konusma-tab') {
                loadKonusmalar();
            } else if (activeTabVal === 'pills-tahsilat-ek-tab') {
                loadDovizTahsilatListesi();
            } else if (activeTabVal === 'pills-uyari-tab') {
                loadCariUyarilarTab();
            }
            updateCategoryTotalsFromVisible();

            // Sayfa yüklendiğinde aktif uyarı varsa ekranı flaş et
            if (document.getElementById('main_aktif_uyarilar_card')) {
                triggerScreenFlash();
            }
        });

        function fetchCariBakiyeDetay(cariVal, infoDivId) {
            var isModal = (infoDivId === 'edit_cari_info');
            var isNewModal = (infoDivId === 'modal_konusma_cari_info');
            var infoDiv = document.getElementById(infoDivId);
            
            var warningBoxId = 'cari_uyari_box';
            var warningTextId = 'cari_uyari_text';
            if (isModal) {
                warningBoxId = 'edit_cari_uyari_box';
                warningTextId = 'edit_cari_uyari_text';
            } else if (isNewModal) {
                warningBoxId = 'modal_konusma_cari_uyari_box';
                warningTextId = 'modal_konusma_cari_uyari_text';
            }
 
            var warningBox = document.getElementById(warningBoxId);
            var warningText = document.getElementById(warningTextId);
 
            if (!cariVal) {
                infoDiv.style.display = 'none';
                infoDiv.innerHTML = '';
                if (warningBox) warningBox.style.display = 'none';
                return;
            }
 
            var tarih = '{{ secili_tarih }}';
            var url = '/cari/bakiye_detay?cari=' + encodeURIComponent(cariVal) + '&tarih=' + encodeURIComponent(tarih);
 
            fetch(url)
                .then(r => r.json())
                .then(function(res) {
                    if (res.ok && res.detaylar && res.detaylar.length > 0) {
                        var html = '<div class="row fw-bold text-secondary mb-1 border-bottom pb-1" style="font-size:10px;"><div class="col-3">Döviz</div><div class="col-3 text-end">Bakiye</div><div class="col-3 text-end text-danger">Günü Geçen</div><div class="col-3 text-end text-success">Günü Gelmeyen</div></div>';
                        res.detaylar.forEach(function(det) {
                            html += '<div class="row py-0.5" style="border-bottom: 1px dashed #eee;">';
                            html += '  <div class="col-3 fw-bold">' + escHtml(det.doviz) + '</div>';
                            html += '  <div class="col-3 text-end fw-bold text-dark">' + formatMoney(det.bakiye) + '</div>';
                            html += '  <div class="col-3 text-end fw-bold text-danger">' + formatMoney(det.gunu_gecen) + '</div>';
                            html += '  <div class="col-3 text-end fw-bold text-success">' + formatMoney(det.gunu_gelmeyen) + '</div>';
                            html += '</div>';
                        });
                        infoDiv.innerHTML = html;
                        infoDiv.style.display = 'block';
                    } else {
                        infoDiv.style.display = 'none';
                        infoDiv.innerHTML = '';
                    }
 
                    if (res.ok) {
                        if (res.cari_doviz) {
                            if (isModal) {
                                if (!window.isOpeningEditModal) {
                                    var selectEl = document.getElementById('edit_doviz');
                                    if (selectEl) selectEl.value = res.cari_doviz;
                                }
                            } else if (isNewModal) {
                                var selectEl = document.getElementById('modal_konusma_doviz');
                                if (selectEl) selectEl.value = res.cari_doviz;
                            } else {
                                var selectEl = document.getElementById('konusma_doviz');
                                if (selectEl) selectEl.value = res.cari_doviz;
                            }
                        }
                        if (res.uyari) {
                            if (warningText) warningText.textContent = res.uyari;
                            if (warningBox) warningBox.style.display = 'block';
 
                            showWarningModal(cariVal, res.uyari);
 
                            triggerScreenFlash();
                        } else {
                            if (warningBox) warningBox.style.display = 'none';
                        }
                    } else {
                        if (warningBox) warningBox.style.display = 'none';
                    }
                })
                .catch(function(err) {
                    console.error("Cari bakiye detayı alınamadı:", err);
                    infoDiv.style.display = 'none';
                    infoDiv.innerHTML = '';
                    if (warningBox) warningBox.style.display = 'none';
                });
        }

        function triggerScreenFlash() {
            var overlay = document.getElementById('screen-flash-overlay');
            if (!overlay) {
                overlay = document.createElement('div');
                overlay.id = 'screen-flash-overlay';
                document.body.appendChild(overlay);
            }
            overlay.classList.remove('screen-flash-active');
            void overlay.offsetWidth; // Force reflow
            overlay.classList.add('screen-flash-active');
            setTimeout(function() {
                overlay.classList.remove('screen-flash-active');
            }, 1300);
        }

        function showWarningModal(cariVal, uyariText) {
            document.getElementById('warningPopupCariName').textContent = cariVal;
            document.getElementById('warningPopupContent').textContent = uyariText;
            var warningModal = new bootstrap.Modal(document.getElementById('warningPopupModal'));
            warningModal.show();
        }

        function loadCariUyarilarTab() {
            fetch('/cari/uyari/liste')
                .then(r => r.json())
                .then(function(res) {
                    var tbody = document.getElementById('uyariListBody');
                    if (!res.ok || !res.uyarilar || res.uyarilar.length === 0) {
                        tbody.innerHTML = '<tr><td colspan="3" class="text-center py-4 text-muted">Kayıtlı özel not bulunmamaktadır.</td></tr>';
                        return;
                    }
                    var html = '';
                    res.uyarilar.forEach(function(row) {
                        html += '<tr data-cari-kodu="' + escHtml(row.cari_kodu) + '">';
                        html += '<td class="ps-3"><strong class="font-monospace text-muted" style="font-size:11px;">' + escHtml(row.cari_kodu) + '</strong><br><span class="fw-bold" style="font-size:12px;">' + escHtml(row.cari_unvan) + '</span></td>';
                        html += '<td style="font-size:13px; white-space: normal; line-height: 1.4;">' + escHtml(row.uyari) + '</td>';
                        html += '<td class="text-center">';
                        html += '  <button class="btn btn-outline-primary btn-sm me-1" onclick="editCariUyariInTab(' + JSON.stringify(row).replace(/"/g,'&quot;') + ')" title="Düzenle">✏️</button>';
                        html += '  <button class="btn btn-outline-danger btn-sm" onclick="deleteCariUyariInTab(' + JSON.stringify(row.cari_full).replace(/"/g,'&quot;') + ')" title="Sil">🗑️</button>';
                        html += '</td></tr>';
                    });
                    tbody.innerHTML = html;
 
                    var searchVal = $('#uyari_table_search').val();
                    if (searchVal) {
                        $('#uyari_table_search').trigger('input');
                    }
                });
        }

        function editCariUyariInTab(row) {
            $('#uyari_cari_select').val(row.cari_full).trigger('change');
            document.getElementById('uyari_text_input').value = row.uyari;
            document.getElementById('btn_delete_uyari').style.display = 'inline-block';
        }

        function deleteCariUyariInTab(cariFull) {
            if (!confirm('Bu özel notu silmek istediğinize emin misiniz?')) return;
 
            var payload = {
                cari_full: cariFull,
                uyari: ''
            };
 
            fetch('/cari/uyari/kaydet', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            })
            .then(r => r.json())
            .then(function(res) {
                if (res.ok) {
                    alert('Cari özel not silindi!');
                    var cKod = cariFull.indexOf(' - ') > -1 ? cariFull.split(' - ')[0].trim() : cariFull.trim();
                    delete globalCariUyarilar[cKod];
 
                    if ($('#uyari_cari_select').val() === cariFull) {
                        $('#uyari_cari_select').val('').trigger('change');
                        document.getElementById('uyari_text_input').value = '';
                        document.getElementById('btn_delete_uyari').style.display = 'none';
                    }
 
                    loadCariUyarilarTab();
                } else {
                    alert('Hata: ' + res.error);
                }
            });
        }

        function deleteCariUyariFromTab() {
            var cariVal = $('#uyari_cari_select').val();
            if (!cariVal) return;
            deleteCariUyariInTab(cariVal);
        }

        function formatMoney(amount) {
            if (amount === undefined || amount === null) return '0,00';
            var parts = parseFloat(amount).toFixed(2).split('.');
            parts[0] = parts[0].replace(/\\B(?=(\\d{3})+(?!\\d))/g, ".");
            return parts.join(',');
        }

        function loadTahsilatListesi() {
            var tarih = document.getElementById('tahsilat_tarihi_input').value;
            if (!tarih) { alert('Lütfen bir tarih seçin!'); return; }
 
            var cariler = $('#cariSelect').val() || [];
            var temsilciler = $('#temsilciSelect').val() || [];
 
            var params = new URLSearchParams();
            params.append('tarih', tarih);
            cariler.forEach(function(c) { params.append('cariler', c); });
            temsilciler.forEach(function(t) { params.append('temsilciler', t); });
 
            var tbody = document.getElementById('tahsilatBody');
            tbody.innerHTML = '<tr><td colspan="10" class="text-center py-4 text-muted">Yükleniyor, lütfen bekleyin...</td></tr>';
 
            fetch('/tahsilat/liste?' + params.toString())
                .then(r => r.json())
                .then(function(res) {
                    if (res.error) {
                        tbody.innerHTML = '<tr><td colspan="10" class="text-center py-4 text-danger">Hata: ' + escHtml(res.error) + '</td></tr>';
                        document.getElementById('tahsilatFoot').style.display = 'none';
                        return;
                    }
 
                    var html = '';
                    if (!res.list || res.list.length === 0) {
                        html = '<tr><td colspan="10" class="text-center py-4 text-muted">Seçilen tarihte ve kriterlerde herhangi bir tahsilat bulunamadı.</td></tr>';
                        tbody.innerHTML = html;
                        document.getElementById('tahsilatFoot').style.display = 'none';
                        return;
                    }
 
                    res.list.forEach(function(row) {
                        var bankaBadge = '<span class="badge bg-primary">' + escHtml(row.BANKA) + '</span>';
                        var bakiyeStr = formatMoney(row.CARI_BAKIYE) + ' ' + row.DOVIZ;
                        var gelmeyenStr = formatMoney(row.GUNU_GELMEYEN) + ' ' + row.DOVIZ;
 
                        var gecenStyle = row.GUNU_GECEN > 0.05 ? 'style="color: #d35400 !important;"' : '';
                        var gecenStr = formatMoney(row.GUNU_GECEN) + ' ' + row.DOVIZ;
                        var tutarStr = formatMoney(row.TUTAR) + ' ' + row.DOVIZ;
 
                        var gecenKalanStyle = row.GUNU_GECEN_KALAN > 0.05 ? 'style="color: #c0392b !important;"' : '';
                        var gecenKalanStr = formatMoney(row.GUNU_GECEN_KALAN) + ' ' + row.DOVIZ;
 
                        var vadeStr = '<span class="text-muted">-</span>';
                        if (row.VADE_TARIHI && row.VADE_TARIHI !== '-') {
                            vadeStr = '<span class="badge bg-warning text-dark">' + escHtml(row.VADE_TARIHI) + '</span>';
                        }
 
                        var ortGecikme = '<span class="text-muted">-</span>';
                        if (row.ORT_GECIKME_GUN > 0) {
                            ortGecikme = '<span class="badge bg-danger">' + row.ORT_GECIKME_GUN + ' Gün</span>';
                        }
 
                        html += '<tr data-cari-kodu="' + escHtml(row.CARI_KODU) + '">';
                        html += '<td class="ps-3">' + bankaBadge + '</td>';
                        html += '<td class="font-monospace text-muted">' + escHtml(row.CARI_KODU) + '</td>';
                        html += '<td class="fw-bold">' + escHtml(row.CARI_UNVAN) + '</td>';
                        html += '<td class="text-end fw-bold text-dark">' + bakiyeStr + '</td>';
                        html += '<td class="text-end text-success">' + gelmeyenStr + '</td>';
                        html += '<td class="text-end fw-bold" ' + gecenStyle + '>' + gecenStr + '</td>';
                        html += '<td class="text-end fw-bold text-success">' + tutarStr + '</td>';
                        html += '<td class="text-end fw-bold" ' + gecenKalanStyle + '>' + gecenKalanStr + '</td>';
                        html += '<td class="text-center">' + vadeStr + '</td>';
                        html += '<td class="text-center">' + ortGecikme + '</td>';
                        html += '</tr>';
                    });
 
                    tbody.innerHTML = html;
 
                    var totalsHtml = '';
                    if (res.toplamlar && Object.keys(res.toplamlar).length > 0) {
                        for (var doviz in res.toplamlar) {
                            var toplamVal = res.toplamlar[doviz];
                            totalsHtml += '<div class="text-success fw-bold" style="font-size: 14px;">' +
                                          formatMoney(toplamVal) + ' ' + doviz + '</div>';
                        }
                        document.getElementById('tahsilatFootTotals').innerHTML = totalsHtml;
                        document.getElementById('tahsilatFoot').style.display = '';
                    } else {
                        document.getElementById('tahsilatFoot').style.display = 'none';
                    }

                    // Re-apply table search filter if search field has a value
                    var searchVal = $('#tahsilat_table_search').val();
                    if (searchVal) {
                        $('#tahsilat_table_search').trigger('input');
                    }
                })
                .catch(function(err) {
                    tbody.innerHTML = '<tr><td colspan="9" class="text-center py-4 text-danger">İletişim Hatası: ' + escHtml(err.message) + '</td></tr>';
                    document.getElementById('tahsilatFoot').style.display = 'none';
                });
        }

        function loadCariEkstre() {
            var cari = document.getElementById('ekstre_cari').value;
            var warningBox = document.getElementById('ekstre_cari_uyari_box');
            if (!cari) {
                if (warningBox) warningBox.style.display = 'none';
                alert('Lütfen bir cari hesap seçin!');
                return;
            }
 
            // Check for warnings
            var warningText = document.getElementById('ekstre_cari_uyari_text');
            if (warningBox) warningBox.style.display = 'none';
 
            var tarih = '{{ secili_tarih }}';
            fetch('/cari/bakiye_detay?cari=' + encodeURIComponent(cari) + '&tarih=' + encodeURIComponent(tarih))
                .then(r => r.json())
                .then(function(res) {
                    if (res.ok && res.uyari) {
                        if (warningText) warningText.textContent = res.uyari;
                        if (warningBox) warningBox.style.display = 'block';
                        triggerScreenFlash();
                    }
                });

            var baslangic = document.getElementById('ekstre_baslangic').value;
 
            var tbody = document.getElementById('ekstreBody');
            tbody.innerHTML = '<tr><td colspan="8" class="text-center py-4 text-muted">Yükleniyor, lütfen bekleyin...</td></tr>';
            document.getElementById('ekstreResultCard').style.display = 'none';
 
            fetch('/ekstre/liste?cari=' + encodeURIComponent(cari) + '&baslangic=' + encodeURIComponent(baslangic))
                .then(r => r.json())
                .then(function(res) {
                    if (res.error) {
                        tbody.innerHTML = '<tr><td colspan="8" class="text-center py-4 text-danger">Hata: ' + escHtml(res.error) + '</td></tr>';
                        return;
                    }
 
                    var html = '';
                    if (!res.list || res.list.length === 0) {
                        html = '<tr><td colspan="8" class="text-center py-4 text-muted">Bu cari hesap için herhangi bir hareket bulunamadı.</td></tr>';
                        tbody.innerHTML = html;
                        document.getElementById('ekstreResultCard').style.display = '';
                        document.getElementById('ekstreCariTitle').textContent = cari;
                        document.getElementById('ekstreBakiyeBadge').textContent = 'Bakiye: 0,00 TL';
                        return;
                    }
 
                    var lastBakiye = 0.0;
                    var lastDoviz = 'TL';
                    var cKod = cari.indexOf(' - ') > -1 ? cari.split(' - ')[0].trim() : cari.trim();
 
                    res.list.forEach(function(row) {
                        var borcStr = row.BORC > 0 ? formatMoney(row.BORC) : '-';
                        var alacakStr = row.ALACAK > 0 ? formatMoney(row.ALACAK) : '-';
 
                        var bakiyeStyle = '';
                        if (row.BAKIYE > 0.05) {
                            bakiyeStyle = 'class="text-danger fw-bold"';
                        } else if (row.BAKIYE < -0.05) {
                            bakiyeStyle = 'class="text-success fw-bold"';
                        }
                        var bakiyeStr = formatMoney(row.BAKIYE);
 
                        // Convert YYYY-MM-DD from API back to DD.MM.YYYY for UI display
                        var dateParts = row.TARIH.split('-');
                        var dispDate = row.TARIH;
                        if (dateParts.length === 3) {
                            dispDate = dateParts[2] + '.' + dateParts[1] + '.' + dateParts[0];
                        }
 
                        var isDevir = (row.ISLEM_TURU === 'DEVİR BAKİYESİ');
                        var badgeBg = isDevir ? 'bg-primary text-white' : 'bg-secondary';
 
                        var rowClass = '';
                        if (isDevir) {
                            rowClass = 'table-light fw-bold';
                        } else if (row.BORC > 0) {
                            rowClass = 'row-borc';
                        } else if (row.ALACAK > 0) {
                            rowClass = 'row-alacak';
                        }
 
                        html += '<tr class="' + rowClass + '" data-cari-kodu="' + escHtml(cKod) + '">';
                        html += '<td class="ps-3">' + escHtml(dispDate) + '</td>';
                        html += '<td><span class="badge ' + badgeBg + '">' + escHtml(row.ISLEM_TURU) + '</span></td>';
                        html += '<td><code>' + escHtml(row.FIS_NO || '-') + '</code></td>';
                        html += '<td style="max-width: 300px; white-space: normal;">' + escHtml(row.ACIKLAMA || '-') + '</td>';
                        html += '<td class="text-end text-danger">' + borcStr + '</td>';
                        html += '<td class="text-end text-success">' + alacakStr + '</td>';
                        html += '<td class="text-end" ' + bakiyeStyle + '>' + bakiyeStr + '</td>';
                        html += '<td class="text-center"><span class="badge bg-light text-dark">' + escHtml(row.DOVIZ) + '</span></td>';
                        html += '</tr>';
 
                        lastBakiye = row.BAKIYE;
                        lastDoviz = row.DOVIZ;
                    });
 
                    tbody.innerHTML = html;
                    document.getElementById('ekstreCariTitle').textContent = cari;
 
                    var bakiyeText = 'Bakiye: ' + formatMoney(lastBakiye) + ' ' + lastDoviz;
                    var badgeClass = 'bg-secondary';
                    if (lastBakiye > 0.05) {
                        badgeClass = 'bg-danger';
                    } else if (lastBakiye < -0.05) {
                        badgeClass = 'bg-success';
                    }
 
                    document.getElementById('ekstreBakiyeBadge').className = 'badge fw-bold ' + badgeClass;
                    document.getElementById('ekstreBakiyeBadge').textContent = bakiyeText;
                    document.getElementById('ekstreResultCard').style.display = '';

                    // Re-apply ekstre search filter if search field has a value
                    var searchVal = $('#ekstre_table_search').val();
                    if (searchVal) {
                        $('#ekstre_table_search').trigger('input');
                    }
                })
                .catch(function(err) {
                    tbody.innerHTML = '<tr><td colspan="8" class="text-center py-4 text-danger">İletişim Hatası: ' + escHtml(err.message) + '</td></tr>';
                });
        }
 
        function exportEkstreExcel() {
            var cari = document.getElementById('ekstre_cari').value;
            if (!cari) { alert('Lütfen bir cari hesap seçin!'); return; }
            var baslangic = document.getElementById('ekstre_baslangic').value;
            window.location.href = '/ekstre/export?cari=' + encodeURIComponent(cari) + '&baslangic=' + encodeURIComponent(baslangic);
        }

        function loadFaturaDetayListesi() {
            var body = document.getElementById('faturaDetayBody');
            body.innerHTML = '<tr><td colspan="8" class="text-center py-4 text-muted"><div class="spinner-border spinner-border-sm text-primary me-2" role="status"></div>Yükleniyor...</td></tr>';
            
            fetch('/faturadetay/liste')
                .then(response => response.json())
                .then(res => {
                    if (!res.success) {
                        body.innerHTML = '<tr><td colspan="8" class="text-center py-4 text-danger">Hata: ' + res.error + '</td></tr>';
                        return;
                    }
                    
                    var data = res.data;
                    if (!data || data.length === 0) {
                        body.innerHTML = '<tr><td colspan="8" class="text-center py-4 text-muted">Kesilecek fatura bulunmamaktadır.</td></tr>';
                        document.getElementById('fatura_detay_summary_cards').innerHTML = '';
                        return;
                    }
                    
                    // Calculate totals by currency
                    var currencyTotals = {};
                    data.forEach(row => {
                        var c = row.DovizTipi || 'TL';
                        if (!currencyTotals[c]) {
                            currencyTotals[c] = { matrah: 0, kdv: 0, toplam: 0 };
                        }
                        currencyTotals[c].matrah += row.Matrah || 0;
                        currencyTotals[c].kdv += row.KDV || 0;
                        currencyTotals[c].toplam += row.GenelToplam || 0;
                    });
                    
                    // Render summary cards
                    var summaryHtml = '';
                    var colors = { 'TL': 'primary', 'USD': 'success', 'EUR': 'warning', 'GBP': 'info' };
                    Object.keys(currencyTotals).forEach(c => {
                        var totals = currencyTotals[c];
                        var col = colors[c] || 'secondary';
                        summaryHtml += `
                            <div class="col-md-3">
                                <div class="card h-100 shadow-sm border-${col} border-start border-4">
                                    <div class="card-body py-2 px-3">
                                        <div class="text-muted small fw-bold text-uppercase">${c} TOPLAMI</div>
                                        <div class="fs-5 fw-bold text-${col}">${formatCurrencyJS(totals.toplam)} ${c}</div>
                                        <div class="text-muted small" style="font-size: 10px;">
                                            Matrah: ${formatCurrencyJS(totals.matrah)} | KDV: ${formatCurrencyJS(totals.kdv)}
                                        </div>
                                    </div>
                                </div>
                            </div>
                        `;
                    });

                    // Calculate overall TL equivalent using TCMB rates
                    var overallTlEquivalent = 0;
                    Object.keys(currencyTotals).forEach(c => {
                        var totals = currencyTotals[c];
                        if (c === 'TL' || c === 'TRY') {
                            overallTlEquivalent += totals.toplam;
                        } else if (c === 'USD') {
                            overallTlEquivalent += totals.toplam * (TCMB_USD_RATE || 0);
                        } else if (c === 'EUR') {
                            overallTlEquivalent += totals.toplam * (TCMB_EUR_RATE || 0);
                        } else {
                            overallTlEquivalent += totals.toplam;
                        }
                    });

                    // Render Grand Total card
                    summaryHtml += `
                        <div class="col-md-3">
                            <div class="card h-100 shadow-sm border-danger border-start border-4" style="background-color: #fffbfa;">
                                <div class="card-body py-2 px-3">
                                    <div class="text-muted small fw-bold text-uppercase">TCMB KURLU TOPLAM (TL)</div>
                                    <div class="fs-5 fw-bold text-danger">${formatCurrencyJS(overallTlEquivalent)} TL</div>
                                    <div class="text-muted small" style="font-size: 10px;">
                                        Kurlar: USD=${TCMB_USD_RATE.toFixed(4)} | EUR=${TCMB_EUR_RATE.toFixed(4)}
                                    </div>
                                </div>
                            </div>
                        </div>
                    `;

                    document.getElementById('fatura_detay_summary_cards').innerHTML = summaryHtml;
                    
                    // Render table rows
                    renderFaturaDetayRows(data);
                })
                .catch(err => {
                    body.innerHTML = '<tr><td colspan="8" class="text-center py-4 text-danger">Yükleme hatası: ' + err + '</td></tr>';
                });
        }
        
        function formatCurrencyJS(val) {
            return new Intl.NumberFormat('tr-TR', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(val || 0);
        }
        
        var faturaDetayFullData = [];
        
        function renderFaturaDetayRows(data) {
            faturaDetayFullData = data;
            var body = document.getElementById('faturaDetayBody');
            var html = '';
            
            data.forEach(row => {
                var faturaTarihiFormatted = '';
                if (row.FaturaTarihi) {
                    var parts = row.FaturaTarihi.split('-');
                    if (parts.length === 3) {
                        faturaTarihiFormatted = parts[2] + '.' + parts[1] + '.' + parts[0];
                    } else {
                        faturaTarihiFormatted = row.FaturaTarihi;
                    }
                }
                
                html += `
                    <tr data-cari-kodu="${row.CariHesapKodu}">
                        <td class="ps-3 fw-bold">${faturaTarihiFormatted}</td>
                        <td class="text-muted">${row.CariHesapKodu}</td>
                        <td class="text-start fw-bold text-dark text-truncate" style="max-width: 250px;">${row.CariUnvan}</td>
                        <td class="text-start">
                            <div class="fatura-aciklama-wrapper" onclick="toggleFaturaAciklama(this)" title="Tıklayarak tam metni açın/kapatın">
                                ${row.FaturaAciklama}
                            </div>
                        </td>
                        <td class="text-end fw-bold">${formatCurrencyJS(row.Matrah)}</td>
                        <td class="text-end text-muted">${formatCurrencyJS(row.KDV)}</td>
                        <td class="text-end fw-bold text-primary">${formatCurrencyJS(row.GenelToplam)}</td>
                        <td class="text-center"><span class="badge bg-light text-dark border">${row.DovizTipi}</span></td>
                    </tr>
                `;
            });
            body.innerHTML = html;
            updateFaturaDetayTotals();
        }
        
        function toggleFaturaAciklama(el) {
            el.classList.toggle('expanded');
        }

        function updateFaturaDetayTotals() {
            var tbody = document.getElementById('faturaDetayBody');
            var rows = Array.from(tbody.querySelectorAll('tr'));
            
            if (rows.length === 1 && rows[0].cells.length < 5) {
                document.getElementById('faturaDetayFooter').innerHTML = '';
                return;
            }
            
            var totals = {};
            rows.forEach(row => {
                if (row.style.display !== 'none') {
                    var doviz = row.cells[7].textContent.trim();
                    var matrahStr = row.cells[4].textContent.trim();
                    var kdvStr = row.cells[5].textContent.trim();
                    var toplamStr = row.cells[6].textContent.trim();
                    
                    const parseVal = (str) => {
                        let s = str.replace(/[A-Z]/gi, '').trim();
                        s = s.split('.').join('').replace(',', '.');
                        let val = parseFloat(s);
                        return isNaN(val) ? 0 : val;
                    };
                    
                    var matrah = parseVal(matrahStr);
                    var kdv = parseVal(kdvStr);
                    var toplam = parseVal(toplamStr);
                    
                    if (!totals[doviz]) {
                        totals[doviz] = { matrah: 0, kdv: 0, toplam: 0 };
                    }
                    totals[doviz].matrah += matrah;
                    totals[doviz].kdv += kdv;
                    totals[doviz].toplam += toplam;
                }
            });
            
            var footer = document.getElementById('faturaDetayFooter');
            var keys = Object.keys(totals);
            if (keys.length === 0) {
                footer.innerHTML = '';
                return;
            }
            
            var html = '';
            keys.forEach(doviz => {
                var t = totals[doviz];
                html += `
                    <tr style="background-color: #f1f5f9; border-top: 1px solid #cbd5e1;">
                        <td colspan="4" class="text-end fw-bold pe-3" style="color: #1e3a8a;">FİLTRELENMİŞ TOPLAM (${doviz})</td>
                        <td class="text-end fw-bold">${formatCurrencyJS(t.matrah)}</td>
                        <td class="text-end text-muted">${formatCurrencyJS(t.kdv)}</td>
                        <td class="text-end fw-bold text-primary">${formatCurrencyJS(t.toplam)}</td>
                        <td class="text-center"><span class="badge bg-primary">${doviz}</span></td>
                    </tr>
                `;
            });
            footer.innerHTML = html;
        }
        
        function exportFaturaDetayExcel() {
            window.location.href = '/faturadetay/export';
        }

        let faturaDetaySortDirections = {};
        function sortFaturaDetayTable(colIndex) {
            const table = document.getElementById("faturaDetayTable");
            const tbody = document.getElementById("faturaDetayBody");
            const rows = Array.from(tbody.querySelectorAll("tr"));
            
            if (rows.length <= 1 && (rows.length === 0 || rows[0].cells.length < 5)) return;

            const currentDir = faturaDetaySortDirections[colIndex] || 'desc';
            const nextDir = currentDir === 'asc' ? 'desc' : 'asc';
            faturaDetaySortDirections = {};
            faturaDetaySortDirections[colIndex] = nextDir;

            const headers = table.querySelectorAll("thead th");
            headers.forEach((th, idx) => {
                const iconSpan = th.querySelector(".sort-icon-fatura-detay");
                if (iconSpan) {
                    if (idx === colIndex) {
                        iconSpan.innerHTML = nextDir === 'asc' ? ' ▲' : ' ▼';
                    } else {
                        iconSpan.innerHTML = '';
                    }
                }
            });

            rows.sort((a, b) => {
                let cellA = a.cells[colIndex].textContent.trim();
                let cellB = b.cells[colIndex].textContent.trim();

                // Fatura Tarihi column (index 0)
                if (colIndex === 0) {
                    const parseDateVal = (str) => {
                        if (!str || str === '-') return new Date(0);
                        let parts = str.split('.');
                        if (parts.length === 3) {
                            return new Date(parts[2], parts[1] - 1, parts[0]);
                        }
                        return new Date(str);
                    };
                    let dateA = parseDateVal(cellA);
                    let dateB = parseDateVal(cellB);
                    return nextDir === 'asc' ? dateA - dateB : dateB - dateA;
                }

                // Numeric columns (index 4, 5, 6)
                if (colIndex === 4 || colIndex === 5 || colIndex === 6) {
                    const parseVal = (str) => {
                        let s = str.replace(/[A-Z]/gi, '').trim();
                        s = s.split('.').join('').replace(',', '.');
                        let val = parseFloat(s);
                        return isNaN(val) ? 0 : val;
                    };
                    let valA = parseVal(cellA);
                    let valB = parseVal(cellB);
                    return nextDir === 'asc' ? valA - valB : valB - valA;
                }

                // General strings
                return nextDir === 'asc' 
                    ? cellA.localeCompare(cellB, 'tr') 
                    : cellB.localeCompare(cellA, 'tr');
            });

            rows.forEach(row => tbody.appendChild(row));
            updateFaturaDetayTotals();
        }

        function loadDovizTahsilatListesi() {
            var baslangic = document.getElementById('doviz_tahsilat_baslangic').value;
            var bitis = document.getElementById('doviz_tahsilat_bitis').value;
            var query = document.getElementById('doviz_tahsilat_search').value;
 
            var params = new URLSearchParams();
            if (baslangic) params.append('baslangic', baslangic);
            if (bitis) params.append('bitis', bitis);
            if (query) params.append('q', query);
 
            var tbody = document.getElementById('dovizTahsilatBody');
            tbody.innerHTML = '<tr><td colspan="9" class="text-center py-4 text-muted">Yükleniyor, lütfen bekleyin...</td></tr>';
 
            fetch('/tahsilat/doviz_liste?' + params.toString())
                .then(r => r.json())
                .then(function(res) {
                    if (res.error) {
                        tbody.innerHTML = '<tr><td colspan="9" class="text-center py-4 text-danger">Hata: ' + escHtml(res.error) + '</td></tr>';
                        return;
                    }
 
                    document.getElementById('total_doviz_tl').textContent = formatMoney(res.toplam_tl) + ' TL';
                    document.getElementById('total_doviz_usd').textContent = formatMoney(res.toplam_usd) + ' USD';
                    document.getElementById('total_doviz_eur').textContent = formatMoney(res.toplam_eur) + ' EUR';
 
                    document.getElementById('total_table_tl').textContent = formatMoney(res.toplam_tl) + ' TL';
                    document.getElementById('total_table_usd').textContent = formatMoney(res.toplam_usd) + ' USD';
                    document.getElementById('total_table_eur').textContent = formatMoney(res.toplam_eur) + ' EUR';
 
                    var html = '';
                    if (!res.list || res.list.length === 0) {
                        html = '<tr><td colspan="9" class="text-center py-4 text-muted">Arama kriterlerine uygun tahsilat bulunamadı.</td></tr>';
                        tbody.innerHTML = html;
                        return;
                    }
 
                    res.list.forEach(function(row) {
                        var dateParts = row.TARIH.split('-');
                        var dispDate = row.TARIH;
                        if (dateParts.length === 3) {
                            dispDate = dateParts[2] + '.' + dateParts[1] + '.' + dateParts[0];
                        }
 
                        var badgeBg = 'bg-secondary';
                        if (row.TAHSILAT_CINSI === 'Garanti' || row.TAHSILAT_CINSI.includes('Garanti')) badgeBg = 'bg-success';
                        else if (row.TAHSILAT_CINSI === 'Yapı Kredi' || row.TAHSILAT_CINSI.includes('Yapı Kredi')) badgeBg = 'bg-primary';
                        else if (row.TAHSILAT_CINSI === 'Akbank' || row.TAHSILAT_CINSI.includes('Akbank')) badgeBg = 'bg-danger';
                        else if (row.TAHSILAT_CINSI.includes('Çek')) badgeBg = 'bg-warning text-dark';
                        else if (row.TAHSILAT_CINSI === 'Kasa') badgeBg = 'bg-info text-dark';
 
                        var typeBadge = '<span class="badge ' + badgeBg + '">' + escHtml(row.TAHSILAT_CINSI) + '</span>';
 
                        var tlStr = row.TL > 0 ? formatMoney(row.TL) : '-';
                        var usdStr = row.USD > 0 ? formatMoney(row.USD) : '-';
                        var eurStr = row.EUR > 0 ? formatMoney(row.EUR) : '-';
 
                        var vadeStr = '<span class="text-muted">-</span>';
                        if (row.VADE_TARIHI && row.VADE_TARIHI !== '') {
                            var vDateParts = row.VADE_TARIHI.split('-');
                            var dispVade = row.VADE_TARIHI;
                            if (vDateParts.length === 3) {
                                dispVade = vDateParts[2] + '.' + vDateParts[1] + '.' + vDateParts[0];
                            }
                            vadeStr = '<span class="badge bg-warning text-dark">' + dispVade + '</span>';
                        }
 
                        html += '<tr data-cari-kodu="' + escHtml(row.CARI_KODU) + '">';
                        html += '<td class="ps-3 fw-semibold">' + dispDate + '</td>';
                        html += '<td class="text-center"><span class="badge bg-dark">' + row.HAFTA + '</span></td>';
                        html += '<td class="font-monospace text-muted">' + escHtml(row.CARI_KODU) + '</td>';
                        html += '<td class="fw-bold">' + escHtml(row.CARI_UNVAN) + '</td>';
                        html += '<td class="text-center">' + typeBadge + '</td>';
                        html += '<td class="text-center">' + vadeStr + '</td>';
                        html += '<td class="text-end fw-bold text-dark">' + tlStr + '</td>';
                        html += '<td class="text-end fw-bold text-primary">' + usdStr + '</td>';
                        html += '<td class="text-end fw-bold text-success">' + eurStr + '</td>';
                        html += '</tr>';
                    });
 
                    tbody.innerHTML = html;
                })
                .catch(function(err) {
                    tbody.innerHTML = '<tr><td colspan="9" class="text-center py-4 text-danger">İletişim Hatası: ' + escHtml(err.message) + '</td></tr>';
                });
        }
 
        function exportDovizTahsilatExcel() {
            var baslangic = document.getElementById('doviz_tahsilat_baslangic').value;
            var bitis = document.getElementById('doviz_tahsilat_bitis').value;
            var query = document.getElementById('doviz_tahsilat_search').value;
 
            var url = '/tahsilat/doviz_export?';
            if (baslangic) url += 'baslangic=' + encodeURIComponent(baslangic) + '&';
            if (bitis) url += 'bitis=' + encodeURIComponent(bitis) + '&';
            if (query) url += 'q=' + encodeURIComponent(query);
 
            window.location.href = url;
        }

        var KAT_COLORS = ['#3498db','#27ae60','#e67e22','#9b59b6','#e74c3c','#1abc9c'];
        var KAT_MAP = {'Kesin Gelecek':0,'Vade İstedi':1,'Ödeme Sözü':2,'İtirazlı':3,'Ulaşılamadı':4,'Çek Gönderecek':5};

        function katBadge(kat, row) {
            var idx = KAT_MAP[kat] !== undefined ? KAT_MAP[kat] : 0;
            var color = KAT_COLORS[idx % KAT_COLORS.length];
            var dateStr = '';
            if (row) {
                if (kat === 'Kesin Gelecek' && row.kesin_gelecek_tarih) {
                    dateStr = ' (' + formatDate(row.kesin_gelecek_tarih) + ')';
                } else if (kat === 'Vade İstedi' && row.vade_tarih) {
                    dateStr = ' (' + formatDate(row.vade_tarih) + ')';
                } else if (kat === 'Ödeme Sözü' && row.soz_verilen_tarih) {
                    dateStr = ' (' + formatDate(row.soz_verilen_tarih) + ')';
                } else if (kat === 'İtirazlı' && row.itiraz_tarih) {
                    dateStr = ' (' + formatDate(row.itiraz_tarih) + ')';
                } else if (kat === 'Ulaşılamadı' && row.ulasilamadi_tarih) {
                    dateStr = ' (' + formatDate(row.ulasilamadi_tarih) + ')';
                } else if (kat === 'Çek Gönderecek' && row.cek_tarih) {
                    dateStr = ' (' + formatDate(row.cek_tarih) + ')';
                }
            }
            return '<span class="badge badge-kat" style="background:'+color+';">' + kat + dateStr + '</span>';
        }

        function loadKonusmalar() {
            var filtreCariEl = document.getElementById('filtre_cari');
            var filtreKatEl = document.getElementById('filtre_kat');
            var cari = filtreCariEl ? filtreCariEl.value : '';
            var kat = filtreKatEl ? filtreKatEl.value : '';
            var url = '/konusma/liste?';
            if (cari) url += 'cari=' + encodeURIComponent(cari) + '&';
            if (kat) url += 'kategori=' + encodeURIComponent(kat) + '&';
            fetch(url).then(r => r.json()).then(function(data) {
                var tbody = document.getElementById('konusmaBody');
                if (!tbody) return;
                if (!data || data.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="11" class="text-center py-4 text-muted">Kayıt bulunamadı.</td></tr>';
                    updateCategoryTotalsFromVisible();
                    return;
                }
                var html = '';
                data.forEach(function(row) {
                    var kats = row.kategoriler ? row.kategoriler.split(',') : [];
                    var katHtml = kats.map(function(k) { return katBadge(k.trim(), row); }).join(' ');
                    var kesinTarih = row.kesin_gelecek_tarih ? formatDate(row.kesin_gelecek_tarih) : '<span class="text-muted">-</span>';
                    var sozTarih = row.soz_verilen_tarih ? formatDate(row.soz_verilen_tarih) : '<span class="text-muted">-</span>';
                    var tutarText = row.tutar ? (formatMoney(row.tutar) + ' ' + (row.doviz || 'TL')) : '<span class="text-muted">-</span>';
                    html += '<tr data-cari-kodu="' + escHtml(row.cari_kodu) + '">';
                    html += '<td class="ps-3 font-monospace text-muted" style="font-size:12px;">' + escHtml(row.cari_kodu) + '</td>';
                    html += '<td class="fw-bold" style="font-size:12px;">' + escHtml(row.cari_unvan) + '</td>';
                    html += '<td style="font-size:12px;">' + escHtml(row.kiminle || '-') + '</td>';
                    html += '<td style="font-size:12px; font-weight: 500; color: #555;">' + escHtml(row.kaydeden || '-') + '</td>';
                    html += '<td>' + (katHtml || '<span class="text-muted">-</span>') + '</td>';
                    html += '<td style="font-size:12px;">' + kesinTarih + '</td>';
                    html += '<td style="font-size:12px;">' + sozTarih + '</td>';
                    html += '<td style="font-size:12px; font-weight:bold; color:#2e7d32;">' + tutarText + '</td>';
                    html += '<td style="font-size:12px; max-width:200px; white-space:normal;">' + escHtml(row.aciklama || '-') + '</td>';
                    html += '<td style="font-size:11px; color:#6c757d;">' + escHtml(row.olusturma_tarihi || '') + '</td>';
                    html += '<td class="konusma-row-actions">';
                    html += '<button class="btn btn-outline-primary btn-sm me-1" onclick="openEdit(' + JSON.stringify(row).replace(/"/g,'&quot;') + ')" title="Düzenle">✏️</button>';
                    html += '<button class="btn btn-outline-danger btn-sm" onclick="deleteKonusma(' + row.id + ')" title="Sil">🗑️</button>';
                    html += '</td></tr>';
                });
                tbody.innerHTML = html;

                // Re-apply live text filter if search field has a value
                var searchVal = $('#filtre_konusma_arama').val();
                if (searchVal) {
                    $('#filtre_konusma_arama').trigger('input');
                } else {
                    updateCategoryTotalsFromVisible();
                }
            });
        }

        let sortDirections = {};
        function sortKonusmaTable(colIndex) {
            const table = document.getElementById("konusmaCariTable");
            const tbody = table.querySelector("tbody");
            const rows = Array.from(tbody.querySelectorAll("tr"));
            
            if (rows.length === 1 && rows[0].cells.length < 5) return; 

            const currentDir = sortDirections[colIndex] || 'desc';
            const nextDir = currentDir === 'asc' ? 'desc' : 'asc';
            sortDirections = {};
            sortDirections[colIndex] = nextDir;

            const headers = table.querySelectorAll("thead th");
            headers.forEach((th, idx) => {
                const iconSpan = th.querySelector(".sort-icon");
                if (iconSpan) {
                    if (idx === colIndex) {
                        iconSpan.innerHTML = nextDir === 'asc' ? ' ▲' : ' ▼';
                    } else {
                        iconSpan.innerHTML = '';
                    }
                }
            });

            rows.sort((a, b) => {
                let cellA = a.cells[colIndex].textContent.trim();
                let cellB = b.cells[colIndex].textContent.trim();

                if (colIndex === 1) {
                    if (cellA.includes(' - ')) {
                        cellA = cellA.split(' - ').slice(1).join(' - ').trim();
                    }
                    if (cellB.includes(' - ')) {
                        cellB = cellB.split(' - ').slice(1).join(' - ').trim();
                    }
                    return nextDir === 'asc' 
                        ? cellA.localeCompare(cellB, 'tr') 
                        : cellB.localeCompare(cellA, 'tr');
                }

                if (colIndex === 2 || colIndex === 3 || colIndex === 4 || colIndex === 5 || colIndex === 8) {
                    const parseVal = (str) => {
                        let s = str.replace(/[A-Z]/gi, '').trim();
                        s = s.split('.').join('').replace(',', '.');
                        let val = parseFloat(s);
                        return isNaN(val) ? 0 : val;
                    };
                    let valA = parseVal(cellA);
                    let valB = parseVal(cellB);
                    return nextDir === 'asc' ? valA - valB : valB - valA;
                }

                if (colIndex === 6) {
                    const parseDateVal = (str) => {
                        if (!str || str === '-') return new Date(0);
                        let parts = str.split('.');
                        if (parts.length === 3) {
                            return new Date(parts[2], parts[1] - 1, parts[0]);
                        }
                        return new Date(str);
                    };
                    let dateA = parseDateVal(cellA);
                    let dateB = parseDateVal(cellB);
                    return nextDir === 'asc' ? dateA - dateB : dateB - dateA;
                }

                return nextDir === 'asc' 
                    ? cellA.localeCompare(cellB, 'tr') 
                    : cellB.localeCompare(cellA, 'tr');
            });

            rows.forEach(row => tbody.appendChild(row));
        }

        function updateDateFieldsRequirement() {
            var categories = [
                { cbId: 'm_kat1', grpId: 'grp_kesin_tarih', inputId: 'modal_konusma_kesin_tarih', labelText: 'Kesin Gelecek Tarihi' },
                { cbId: 'm_kat2', grpId: 'grp_vade_tarih', inputId: 'modal_konusma_vade_tarih', labelText: 'Vade İstediği Tarih' },
                { cbId: 'm_kat3', grpId: 'grp_soz_tarih', inputId: 'modal_konusma_soz_tarih', labelText: 'Ödeme Sözü Tarihi' },
                { cbId: 'm_kat4', grpId: 'grp_itiraz_tarih', inputId: 'modal_konusma_itiraz_tarih', labelText: 'İtiraz Tarihi' },
                { cbId: 'm_kat5', grpId: 'grp_ulasilamadi_tarih', inputId: 'modal_konusma_ulasilamadi_tarih', labelText: 'Ulaşılamadı Tarihi' },
                { cbId: 'm_kat6', grpId: 'grp_cek_tarih', inputId: 'modal_konusma_cek_tarih', labelText: 'Çek Göndereceği Tarih' }
            ];
            
            categories.forEach(function(cat) {
                var isChecked = document.getElementById(cat.cbId).checked;
                var grp = document.getElementById(cat.grpId);
                var input = document.getElementById(cat.inputId);
                var label = grp ? grp.querySelector('label') : null;
                
                if (isChecked) {
                    if (grp) grp.classList.remove('d-none');
                    if (input) input.required = true;
                    if (label) label.innerHTML = cat.labelText + ' <span class="text-danger">*</span>';
                } else {
                    if (grp) grp.classList.add('d-none');
                    if (input) {
                        input.required = false;
                        input.value = '';
                    }
                    if (label) label.innerHTML = cat.labelText;
                }
            });
        }

        function updateEditDateFieldsRequirement() {
            var categories = [
                { cbId: 'ekat1', grpId: 'edit_grp_kesin_tarih', inputId: 'edit_kesin_tarih', labelText: 'Kesin Gelecek Tarihi' },
                { cbId: 'ekat2', grpId: 'edit_grp_vade_tarih', inputId: 'edit_vade_tarih', labelText: 'Vade İstediği Tarih' },
                { cbId: 'ekat3', grpId: 'edit_grp_soz_tarih', inputId: 'edit_soz_tarih', labelText: 'Ödeme Sözü Tarihi' },
                { cbId: 'ekat4', grpId: 'edit_grp_itiraz_tarih', inputId: 'edit_itiraz_tarih', labelText: 'İtiraz Tarihi' },
                { cbId: 'ekat5', grpId: 'edit_grp_ulasilamadi_tarih', inputId: 'edit_ulasilamadi_tarih', labelText: 'Ulaşılamadı Tarihi' },
                { cbId: 'ekat6', grpId: 'edit_grp_cek_tarih', inputId: 'edit_cek_tarih', labelText: 'Çek Göndereceği Tarih' }
            ];
            
            categories.forEach(function(cat) {
                var cb = document.getElementById(cat.cbId);
                if (!cb) return;
                var isChecked = cb.checked;
                var grp = document.getElementById(cat.grpId);
                var input = document.getElementById(cat.inputId);
                var label = grp ? grp.querySelector('label') : null;
                
                if (isChecked) {
                    if (grp) grp.classList.remove('d-none');
                    if (input) input.required = true;
                    if (label) label.innerHTML = cat.labelText + ' <span class="text-danger">*</span>';
                } else {
                    if (grp) grp.classList.add('d-none');
                    if (input) {
                        input.required = false;
                    }
                    if (label) label.innerHTML = cat.labelText;
                }
            });
        }

        function openKonusmaModal(cariFull, cariDoviz) {
            // Set cari information
            document.getElementById('modal_konusma_cari_full').value = cariFull;
            document.getElementById('konusmaKayitModalTitle').innerText = '💬 Görüşme Kaydet - ' + cariFull;
            
            // Set currency or default
            var dovizEl = document.getElementById('modal_konusma_doviz');
            if (dovizEl) dovizEl.value = cariDoviz || 'TL';
            
            // Clear other form fields
            document.getElementById('modal_konusma_kiminle').value = '';
            document.getElementById('modal_konusma_tutar').value = '';
            document.getElementById('modal_konusma_kesin_tarih').value = '';
            document.getElementById('modal_konusma_vade_tarih').value = '';
            document.getElementById('modal_konusma_soz_tarih').value = '';
            document.getElementById('modal_konusma_itiraz_tarih').value = '';
            document.getElementById('modal_konusma_ulasilamadi_tarih').value = '';
            document.getElementById('modal_konusma_cek_tarih').value = '';
            document.getElementById('modal_konusma_aciklama').value = '';
            document.querySelectorAll('.modal-kat-cb').forEach(function(cb) { cb.checked = false; });
            
            updateDateFieldsRequirement();
            
            // Initialize default kaydeden
            var defaultKaydeden = '{{ default_kaydeden | safe }}';
            var savedKaydeden = localStorage.getItem('tahsilat_kaydeden');
            document.getElementById('modal_konusma_kaydeden').value = savedKaydeden || defaultKaydeden;
            
            // Load balance details and warnings
            fetchCariBakiyeDetay(cariFull, 'modal_konusma_cari_info');
            
            // Load history
            loadCariKonusmaHistory(cariFull);
            
            // Show modal
            var myModal = new bootstrap.Modal(document.getElementById('konusmaKayitModal'));
            myModal.show();
        }

        function loadCariKonusmaHistory(cariFull) {
            var cariKodu = cariFull;
            if (cariFull.indexOf(' - ') > -1) {
                cariKodu = cariFull.split(' - ')[0].trim();
            }
            
            var tbody = document.getElementById('konusma_gecmis_tbody');
            if (tbody) {
                tbody.innerHTML = '<tr><td colspan="5" class="text-center py-3 text-muted">Yükleniyor...</td></tr>';
            }
            
            fetch('/konusma/cari_gecmis/' + encodeURIComponent(cariKodu))
                .then(r => r.json())
                .then(function(data) {
                    if (!tbody) return;
                    if (!data || data.length === 0) {
                        tbody.innerHTML = '<tr><td colspan="5" class="text-center py-3 text-muted">Geçmiş görüşme bulunamadı.</td></tr>';
                        updateDashboardRowWithLatest(cariFull, null);
                        return;
                    }
                    
                    var html = '';
                    data.forEach(function(row) {
                        var dateStr = row.kesin_gelecek_tarih ? formatDate(row.kesin_gelecek_tarih) : (row.olusturma_tarihi ? formatDate(row.olusturma_tarihi.substring(0, 10)) : '-');
                        var aciklamaStr = row.aciklama || '-';
                        var tutarStr = row.tutar ? (formatMoney(row.tutar) + ' ' + (row.doviz || 'TL')) : '-';
                        var kats = row.kategoriler ? row.kategoriler.split(',') : [];
                        var katHtml = kats.map(function(k) { return katBadge(k.trim(), row); }).join(' ');
                        
                        var rowJson = JSON.stringify(row).replace(/"/g, '&quot;');
                        
                        html += '<tr>';
                        html += '<td class="font-monospace">' + dateStr + '</td>';
                        html += '<td>' + (katHtml || '<span class="text-muted">-</span>') + '</td>';
                        html += '<td style="white-space: normal;">' + escHtml(aciklamaStr) + '</td>';
                        html += '<td class="fw-bold text-end text-dark">' + tutarStr + '</td>';
                        html += '<td class="text-center" style="white-space: nowrap;">';
                        html += '<button type="button" class="btn btn-outline-primary btn-sm me-1 py-0 px-1" style="font-size:11px;" onclick="openEdit(' + rowJson + ')" title="Düzenle">✏️</button>';
                        html += '<button type="button" class="btn btn-outline-danger btn-sm py-0 px-1" style="font-size:11px;" onclick="deleteKonusma(' + row.id + ')" title="Sil">🗑️</button>';
                        html += '</td>';
                        html += '</tr>';
                    });
                    tbody.innerHTML = html;
                    updateDashboardRowWithLatest(cariFull, data[0]);
                })
                .catch(err => {
                    console.error("Geçmiş konuşmalar yüklenemedi:", err);
                    if (tbody) {
                        tbody.innerHTML = '<tr><td colspan="5" class="text-center py-3 text-danger">Hata oluştu.</td></tr>';
                    }
                });
        }

        function formatDate(d) {
            if (!d) return '-';
            var parts = d.split('-');
            if (parts.length === 3) return parts[2] + '.' + parts[1] + '.' + parts[0];
            return d;
        }

        function escHtml(s) {
            if (!s) return '';
            return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
        }

        function updateDashboardRowWithLatest(cariFull, latestRow) {
            var row = document.querySelector('#konusmaCariTable tbody tr[data-cari-full="' + cariFull.replace(/"/g, '\\"') + '"]');
            if (!row) {
                var rows = document.querySelectorAll('#konusmaCariTable tbody tr');
                for (var i = 0; i < rows.length; i++) {
                    var cell = rows[i].cells[1];
                    if (cell && cell.textContent.trim() === cariFull.trim()) {
                        row = rows[i];
                        break;
                    }
                }
            }
            if (!row) return;

            var dateCell = row.cells[6];
            var katCell = row.cells[7];
            var tutarCell = row.cells[8];
            var aciklamaCell = row.cells[9];

            if (!latestRow) {
                if (dateCell) dateCell.innerHTML = '<span class="text-muted">-</span>';
                if (katCell) katCell.innerHTML = '<span class="text-muted">-</span>';
                if (tutarCell) tutarCell.innerHTML = '<span class="text-muted">-</span>';
                if (aciklamaCell) aciklamaCell.innerHTML = '<span class="text-muted">-</span>';
            } else {
                var latest_date = latestRow.kesin_gelecek_tarih || (latestRow.olusturma_tarihi ? latestRow.olusturma_tarihi.substring(0, 10) : '');
                if (dateCell) dateCell.textContent = formatDate(latest_date);

                if (katCell) {
                    var kats = latestRow.kategoriler ? latestRow.kategoriler.split(',') : [];
                    var katHtml = kats.map(function(k) { return katBadge(k.trim(), latestRow); }).join(' ');
                    katCell.innerHTML = katHtml || '<span class="text-muted">-</span>';
                }

                if (tutarCell) {
                    var tutarText = '-';
                    if (latestRow.tutar !== null && latestRow.tutar !== undefined && latestRow.tutar !== '') {
                        tutarText = formatMoney(latestRow.tutar) + ' ' + (latestRow.doviz || 'TL');
                    }
                    tutarCell.textContent = tutarText;
                }

                if (aciklamaCell) {
                    aciklamaCell.textContent = latestRow.aciklama || '-';
                }
            }

            updateCategoryTotalsFromVisible();
        }

        function updateCategoryTotalsFromVisible() {
            var categoryTotals = {
                'Kesin Gelecek': { 'TL': 0.0, 'USD': 0.0, 'EUR': 0.0 },
                'Vade İstedi': { 'TL': 0.0, 'USD': 0.0, 'EUR': 0.0 },
                'Ödeme Sözü': { 'TL': 0.0, 'USD': 0.0, 'EUR': 0.0 },
                'İtirazlı': { 'TL': 0.0, 'USD': 0.0, 'EUR': 0.0 },
                'Ulaşılamadı': { 'TL': 0.0, 'USD': 0.0, 'EUR': 0.0 },
                'Çek Gönderecek': { 'TL': 0.0, 'USD': 0.0, 'EUR': 0.0 }
            };
 
            $('#konusmaCariTable tbody tr:visible').each(function() {
                var row = $(this);
                if (row.find('td').length < 9) return;
 
                var tutarText = row.find('td').eq(8).text().trim();
                if (!tutarText || tutarText === '-') return;
 
                var parts = tutarText.split(' ');
                var tutar = 0.0;
                var doviz = 'TL';
                if (parts.length >= 1) {
                    tutar = parseFloat(parts[0].replace(/\\./g, '').replace(',', '.')) || 0.0;
                }
                if (parts.length >= 2) {
                    doviz = parts[1].trim().toUpperCase();
                }
                if (doviz !== 'USD' && doviz !== 'EUR') {
                    doviz = 'TL';
                }
 
                row.find('td').eq(7).find('.badge-kat').each(function() {
                    var fullText = $(this).text().trim();
                    var katName = fullText.split('(')[0].trim();
                    if (categoryTotals[katName] !== undefined) {
                        categoryTotals[katName][doviz] += tutar;
                    }
                });
            });
 
            var categories = [
                { name: 'Kesin Gelecek', icon: '✅', color: '#3498db' },
                { name: 'Vade İstedi', icon: '📅', color: '#27ae60' },
                { name: 'Ödeme Sözü', icon: '🤝', color: '#e67e22' },
                { name: 'İtirazlı', icon: '⚠️', color: '#9b59b6' },
                { name: 'Ulaşılamadı', icon: '📵', color: '#e74c3c' },
                { name: 'Çek Gönderecek', icon: '🏦', color: '#1abc9c' }
            ];
 
            var totalsHtml = '';
            categories.forEach(function(cat) {
                var currs = categoryTotals[cat.name];
                totalsHtml += '<div class="kategori-kpi-card" onclick="showCategoryDetail(\\\'' + cat.name + '\\\')" style="background-color: ' + cat.color + ';">';
                totalsHtml += '  <div class="kategori-kpi-title">' + cat.icon + ' ' + cat.name + '</div>';
                totalsHtml += '  <div class="kategori-kpi-values">';
                totalsHtml += '    <div><span>TL:</span><span>' + formatMoney(currs['TL']) + '</span></div>';
                totalsHtml += '    <div><span>USD:</span><span>' + formatMoney(currs['USD']) + '</span></div>';
                totalsHtml += '    <div><span>EUR:</span><span>' + formatMoney(currs['EUR']) + '</span></div>';
                totalsHtml += '  </div>';
                totalsHtml += '</div>';
            });
            var container = document.getElementById('kategori_toplamlari_container');
            if (container) {
                container.innerHTML = totalsHtml;
            }
        }

        function parseDateString(str) {
            if (!str || str === '-') return null;
            var cleanStr = str.trim().split(' ')[0];
            var parts = cleanStr.split('.');
            if (parts.length === 3) {
                return new Date(parts[2], parts[1] - 1, parts[0]);
            }
            var partsYmd = cleanStr.split('-');
            if (partsYmd.length === 3) {
                return new Date(partsYmd[0], partsYmd[1] - 1, partsYmd[2]);
            }
            return null;
        }

        function showCategoryDetail(catName) {
            var records = [];
            var totals = { 'TL': 0.0, 'USD': 0.0, 'EUR': 0.0 };
            
            $('#konusmaCariTable tbody tr:visible').each(function() {
                var row = $(this);
                if (row.find('td').length < 9) return;
                
                var rowKats = [];
                var katDates = {};
                row.find('td').eq(7).find('.badge-kat').each(function() {
                    var fullText = $(this).text().trim();
                    var cleanKat = fullText;
                    var datePart = null;
                    if (fullText.includes('(')) {
                        var parts = fullText.split('(');
                        cleanKat = parts[0].trim();
                        datePart = parts[1].replace(')', '').trim();
                    }
                    rowKats.push(cleanKat);
                    if (datePart) {
                        katDates[cleanKat] = datePart;
                    }
                });
                
                if (rowKats.includes(catName)) {
                    var temsilci = row.find('td').eq(0).text().trim();
                    var cariFull = row.find('td').eq(1).text().trim();
                    var tarihStr = row.find('td').eq(6).text().trim();
                    var tutarText = row.find('td').eq(8).text().trim();
                    var aciklama = row.find('td').eq(9).text().trim();
                    
                    var recordDate = katDates[catName] || tarihStr;
                    
                    var tutar = 0.0;
                    var doviz = 'TL';
                    if (tutarText && tutarText !== '-') {
                        var parts = tutarText.split(' ');
                        if (parts.length >= 1) {
                            tutar = parseFloat(parts[0].replace(/\\./g, '').replace(',', '.')) || 0.0;
                        }
                        if (parts.length >= 2) {
                            doviz = parts[1].trim().toUpperCase();
                        }
                    }
                    
                    if (tutar > 0) {
                        totals[doviz] += tutar;
                    }
                    
                    records.push({
                        temsilci: temsilci,
                        cariFull: cariFull,
                        tarih: recordDate,
                        tarihRaw: parseDateString(recordDate),
                        tutar: tutar,
                        doviz: doviz,
                        aciklama: aciklama
                    });
                }
            });
            
            records.sort(function(a, b) {
                if (a.tarihRaw && b.tarihRaw) {
                    return a.tarihRaw - b.tarihRaw;
                }
                if (a.tarihRaw) return -1;
                if (b.tarihRaw) return 1;
                return 0;
            });
            
            var categories = [
                { name: 'Kesin Gelecek', color: '#3498db' },
                { name: 'Vade İstedi', color: '#27ae60' },
                { name: 'Ödeme Sözü', color: '#e67e22' },
                { name: 'İtirazlı', color: '#9b59b6' },
                { name: 'Ulaşılamadı', color: '#e74c3c' },
                { name: 'Çek Gönderecek', color: '#1abc9c' }
            ];
            var catObj = categories.find(c => c.name === catName);
            var headerBg = catObj ? catObj.color : '#34495e';
            document.getElementById('categoryDetailModalHeader').style.backgroundColor = headerBg;
            document.getElementById('categoryDetailModalTitle').innerHTML = '📋 ' + catName + ' Detay Listesi';
            
            var totalsHtml = '';
            for (var curr in totals) {
                if (totals[curr] > 0.005) {
                    var badgeBg = 'bg-secondary';
                    if (curr === 'TL') badgeBg = 'bg-dark';
                    else if (curr === 'USD') badgeBg = 'bg-primary';
                    else if (curr === 'EUR') badgeBg = 'bg-success';
                    totalsHtml += '<span class="badge ' + badgeBg + '" style="font-size:12px; font-weight:800;">' + curr + ': ' + formatMoney(totals[curr]) + '</span>';
                }
            }
            if (!totalsHtml) {
                totalsHtml = '<span class="badge bg-secondary" style="font-size:12px;">Toplam: 0,00 TL</span>';
            }
            document.getElementById('categoryDetailTotals').innerHTML = totalsHtml;
            
            // Calculate daily totals
            var dailyTotals = {};
            records.forEach(function(row) {
                var date = row.tarih || 'Tarihsiz';
                if (!dailyTotals[date]) {
                    dailyTotals[date] = { 'TL': 0, 'USD': 0, 'EUR': 0 };
                }
                if (row.tutar > 0) {
                    dailyTotals[date][row.doviz] += row.tutar;
                }
            });
            
            var dailyHtml = '';
            var sortedDates = Object.keys(dailyTotals).sort(function(a, b) {
                var dateA = parseDateString(a) || new Date(0);
                var dateB = parseDateString(b) || new Date(0);
                return dateA - dateB;
            });
            
            sortedDates.forEach(function(date) {
                var currs = dailyTotals[date];
                var parts = [];
                for (var curr in currs) {
                    if (currs[curr] > 0.005) {
                        parts.push('<strong>' + formatMoney(currs[curr]) + ' ' + curr + '</strong>');
                    }
                }
                if (parts.length > 0) {
                    dailyHtml += '<div class="p-2 border bg-white rounded shadow-sm d-flex flex-column align-items-center text-center" style="min-width: 130px;">';
                    dailyHtml += '  <span class="text-secondary fw-bold border-bottom pb-1 mb-1 w-100" style="font-size: 11px;">' + date + '</span>';
                    dailyHtml += '  <span class="text-success fw-bold" style="font-size: 12px; line-height: 1.4;">' + parts.join('<br>') + '</span>';
                    dailyHtml += '</div>';
                }
            });
            
            var dailySection = document.getElementById('categoryDetailDailyTotalsSection');
            var dailyContainer = document.getElementById('categoryDetailDailyTotalsContainer');
            if (dailySection && dailyContainer) {
                if (dailyHtml) {
                    dailyContainer.innerHTML = dailyHtml;
                    dailySection.style.display = 'block';
                } else {
                    dailySection.style.display = 'none';
                }
            }

            var tbody = document.getElementById('categoryDetailTbody');
            var html = '';
            if (records.length === 0) {
                html = '<tr><td colspan="5" class="text-center py-4 text-muted">Bu kategoriye ait açık kayıt bulunmamaktadır.</td></tr>';
            } else {
                records.forEach(function(row) {
                    var tutarStr = '-';
                    if (row.tutar > 0) {
                        tutarStr = formatMoney(row.tutar) + ' ' + row.doviz;
                    }
                    html += '<tr>';
                    html += '<td class="fw-semibold">' + escHtml(row.tarih) + '</td>';
                    html += '<td class="fw-bold">' + escHtml(row.cariFull) + '</td>';
                    html += '<td class="text-end fw-bold text-success">' + tutarStr + '</td>';
                    html += '<td class="text-muted small">' + escHtml(row.temsilci) + '</td>';
                    html += '<td class="text-muted small" style="white-space: normal;">' + escHtml(row.aciklama || '-') + '</td>';
                    html += '</tr>';
                });
            }
            tbody.innerHTML = html;
            
            $('#categoryDetailTable thead th.sortable-detail').each(function() {
                var iconSpan = $(this).find('.sort-icon-detail');
                if (iconSpan.length) iconSpan.html('');
            });
            window.detailSortDirections = {};
            window.currentDetailRecords = records;
            
            var modal = new bootstrap.Modal(document.getElementById('categoryDetailModal'));
            modal.show();
        }

        function copyCategoryDetailForWhatsApp() {
            var catName = document.getElementById('categoryDetailModalTitle').innerText.replace('📋 ', '').replace(' Detay Listesi', '').trim();
            var records = window.currentDetailRecords || [];
            if (records.length === 0) {
                alert('Kopyalanacak kayıt bulunmamaktadır.');
                return;
            }
            
            var text = '*💬 ' + catName.toUpperCase() + ' DETAY LİSTESİ*\\n';
            text += '----------------------------------\\n';
            
            records.forEach(function(row, idx) {
                var tutarStr = '-';
                if (row.tutar > 0) {
                    tutarStr = formatMoney(row.tutar) + ' ' + row.doviz;
                }
                text += (idx + 1) + '. *' + row.tarih + '* | *' + row.cariFull + '* | *' + tutarStr + '*';
                if (row.aciklama && row.aciklama !== '-') {
                    text += ' | _' + row.aciklama + '_';
                }
                text += '\\n';
            });
            text += '----------------------------------\\n';
            
            // Calculate daily totals for text
            var dailyTotals = {};
            records.forEach(function(row) {
                var date = row.tarih || 'Tarihsiz';
                if (!dailyTotals[date]) {
                    dailyTotals[date] = { 'TL': 0, 'USD': 0, 'EUR': 0 };
                }
                if (row.tutar > 0) {
                    dailyTotals[date][row.doviz] += row.tutar;
                }
            });
            
            var sortedDates = Object.keys(dailyTotals).sort(function(a, b) {
                var dateA = parseDateString(a) || new Date(0);
                var dateB = parseDateString(b) || new Date(0);
                return dateA - dateB;
            });
            
            text += '*📅 GÜN BAZLI TOPLAMLAR:*\\n';
            sortedDates.forEach(function(date) {
                var currs = dailyTotals[date];
                var parts = [];
                for (var curr in currs) {
                    if (currs[curr] > 0.005) {
                        parts.push(formatMoney(currs[curr]) + ' ' + curr);
                    }
                }
                if (parts.length > 0) {
                    text += '• *' + date + '*: ' + parts.join(' / ') + '\\n';
                }
            });
            text += '----------------------------------\\n';
            
            // Calculate grand totals for text
            var grandTotals = { 'TL': 0, 'USD': 0, 'EUR': 0 };
            records.forEach(function(row) {
                if (row.tutar > 0) {
                    grandTotals[row.doviz] += row.tutar;
                }
            });
            
            text += '*📊 GENEL TOPLAMLAR:*\\n';
            for (var curr in grandTotals) {
                if (grandTotals[curr] > 0.005) {
                    text += '• *' + curr + '*: *' + formatMoney(grandTotals[curr]) + '*\\n';
                }
            }
            
            // Copy to clipboard
            navigator.clipboard.writeText(text).then(function() {
                var feedback = document.getElementById('copyFeedback');
                if (feedback) {
                    feedback.textContent = 'Kopyalandı!';
                    feedback.style.display = 'inline';
                    setTimeout(function() { feedback.style.display = 'none'; }, 2500);
                }
            }).catch(function(err) {
                alert('Kopyalama başarısız: ' + err);
            });
        }

        window.detailSortDirections = {};
        function sortDetailTable(colIndex) {
            var records = window.currentDetailRecords;
            if (!records || records.length <= 1) return;
            
            var currentDir = window.detailSortDirections[colIndex] || 'desc';
            var nextDir = currentDir === 'asc' ? 'desc' : 'asc';
            window.detailSortDirections = {};
            window.detailSortDirections[colIndex] = nextDir;
            
            $('#categoryDetailTable thead th.sortable-detail').each(function(idx) {
                var iconSpan = $(this).find('.sort-icon-detail');
                if (idx === colIndex) {
                    iconSpan.html(nextDir === 'asc' ? ' ▲' : ' ▼');
                } else {
                    iconSpan.html('');
                }
            });
            
            records.sort(function(a, b) {
                if (colIndex === 0) {
                    if (a.tarihRaw && b.tarihRaw) {
                        return nextDir === 'asc' ? a.tarihRaw - b.tarihRaw : b.tarihRaw - a.tarihRaw;
                    }
                    if (a.tarihRaw) return nextDir === 'asc' ? -1 : 1;
                    if (b.tarihRaw) return nextDir === 'asc' ? 1 : -1;
                    return 0;
                }
                else if (colIndex === 1) {
                    var nameA = a.cariFull.toLowerCase();
                    var nameB = b.cariFull.toLowerCase();
                    return nextDir === 'asc' ? nameA.localeCompare(nameB, 'tr') : nameB.localeCompare(nameA, 'tr');
                }
                else if (colIndex === 2) {
                    return nextDir === 'asc' ? a.tutar - b.tutar : b.tutar - a.tutar;
                }
                else if (colIndex === 3) {
                    return nextDir === 'asc' ? a.temsilci.localeCompare(b.temsilci, 'tr') : b.temsilci.localeCompare(a.temsilci, 'tr');
                }
                return 0;
            });
            
            var tbody = document.getElementById('categoryDetailTbody');
            var html = '';
            records.forEach(function(row) {
                var tutarStr = '-';
                if (row.tutar > 0) {
                    tutarStr = formatMoney(row.tutar) + ' ' + row.doviz;
                }
                html += '<tr>';
                html += '<td class="fw-semibold">' + escHtml(row.tarih) + '</td>';
                html += '<td class="fw-bold">' + escHtml(row.cariFull) + '</td>';
                html += '<td class="text-end fw-bold text-success">' + tutarStr + '</td>';
                html += '<td class="text-muted small">' + escHtml(row.temsilci) + '</td>';
                html += '<td class="text-muted small" style="white-space: normal;">' + escHtml(row.aciklama || '-') + '</td>';
                html += '</tr>';
            });
            tbody.innerHTML = html;
        }

        window.yaslandirmaSortDirections = {};
        function sortYaslandirmaTable(colIndex) {
            var table = document.getElementById("yaslandirmaTable");
            if (!table) return;
            var tbody = table.querySelector("tbody");
            if (!tbody) return;
            var allRows = Array.from(tbody.querySelectorAll("tr"));
            
            // Group rows into blocks based on currency groups
            var blocks = [];
            var currentBlock = { header: null, data: [], footer: null };
            
            allRows.forEach(function(row) {
                if (row.classList.contains("yaslandirma-row")) {
                    currentBlock.data.push(row);
                } else {
                    // Check if it is a subtotal row (has "ARA TOPLAM" text)
                    if (row.textContent.indexOf("ARA TOPLAM") !== -1) {
                        currentBlock.footer = row;
                        blocks.push(currentBlock);
                        currentBlock = { header: null, data: [], footer: null };
                    } else {
                        // It is a group header row (e.g. "TL CARİLER")
                        if (currentBlock.header || currentBlock.data.length > 0) {
                            blocks.push(currentBlock);
                            currentBlock = { header: null, data: [], footer: null };
                        }
                        currentBlock.header = row;
                    }
                }
            });
            if (currentBlock.header || currentBlock.data.length > 0 || currentBlock.footer) {
                blocks.push(currentBlock);
            }

            var currentDir = window.yaslandirmaSortDirections[colIndex] || 'desc';
            var nextDir = currentDir === 'asc' ? 'desc' : 'asc';
            window.yaslandirmaSortDirections = {};
            window.yaslandirmaSortDirections[colIndex] = nextDir;

            // Update sort icons in the thead
            var headers = table.querySelectorAll("thead th");
            headers.forEach(function(th, idx) {
                var iconSpan = th.querySelector(".sort-icon-yaslandirma");
                if (iconSpan) {
                    if (idx === colIndex) {
                        iconSpan.innerHTML = nextDir === 'asc' ? ' ▲' : ' ▼';
                    } else {
                        iconSpan.innerHTML = '';
                    }
                }
            });

            blocks.forEach(function(block) {
                block.data.sort(function(a, b) {
                    var cellA = a.cells[colIndex].textContent.trim();
                    var cellB = b.cells[colIndex].textContent.trim();

                    // String columns: Temsilci (0), Firma Kodu (1), Cari Ünvan (2)
                    if (colIndex === 0 || colIndex === 1 || colIndex === 2) {
                        return nextDir === 'asc' 
                            ? cellA.localeCompare(cellB, 'tr') 
                            : cellB.localeCompare(cellA, 'tr');
                    }

                    // Numeric columns: Cari Bakiye (3), Günü Gelmeyen (4), Günü Geçen (5), and bucket columns (6+)
                    var parseVal = function(str) {
                        var s = str.replace(/[A-Z]/gi, '').trim(); // Remove currency symbols
                        s = s.split('.').join('').replace(',', '.');
                        var val = parseFloat(s);
                        return isNaN(val) ? 0 : val;
                    };
                    var valA = parseVal(cellA);
                    var valB = parseVal(cellB);
                    return nextDir === 'asc' ? valA - valB : valB - valA;
                });
            });

            // Rebuild the tbody
            tbody.innerHTML = '';
            blocks.forEach(function(block) {
                if (block.header) tbody.appendChild(block.header);
                block.data.forEach(function(row) { tbody.appendChild(row); });
                if (block.footer) tbody.appendChild(block.footer);
            });
        }

        function deleteKonusma(id) {
            if (!confirm('Bu kaydı silmek istediğinize emin misiniz?')) return;
            fetch('/konusma/sil/' + id, {method: 'DELETE'}).then(r => r.json()).then(function(res) {
                if (res.ok) {
                    loadKonusmalar();
                    var activeCari = document.getElementById('modal_konusma_cari_full').value;
                    if (activeCari) {
                        loadCariKonusmaHistory(activeCari);
                    }
                }
                else alert('Silme hatası: ' + res.error);
            });
        }

        function openEdit(row) {
            document.getElementById('edit_id').value = row.id;
 
            // Set flag to prevent fetchCariBakiyeDetay from overwriting the record's specific currency
            window.isOpeningEditModal = true;
 
            $('#edit_cari').val(row.cari_kodu + ' - ' + row.cari_unvan).trigger('change');
            var opts = document.getElementById('edit_cari').options;
            for (var i=0; i<opts.length; i++) {
                if (opts[i].value.startsWith(row.cari_kodu)) {
                    document.getElementById('edit_cari').value = opts[i].value;
                    $('#edit_cari').trigger('change');
                    break;
                }
            }
            document.getElementById('edit_kiminle').value = row.kiminle || '';
            document.getElementById('edit_kaydeden').value = row.kaydeden || '';
            document.getElementById('edit_tutar').value = row.tutar || '';
 
            var dovizVal = row.doviz || 'TL';
            var editDovizEl = document.getElementById('edit_doviz');
            if (editDovizEl) editDovizEl.value = dovizVal;
 
            document.getElementById('edit_kesin_tarih').value = row.kesin_gelecek_tarih || '';
            document.getElementById('edit_vade_tarih').value = row.vade_tarih || '';
            document.getElementById('edit_soz_tarih').value = row.soz_verilen_tarih || '';
            document.getElementById('edit_itiraz_tarih').value = row.itiraz_tarih || '';
            document.getElementById('edit_ulasilamadi_tarih').value = row.ulasilamadi_tarih || '';
            document.getElementById('edit_cek_tarih').value = row.cek_tarih || '';
            document.getElementById('edit_aciklama').value = row.aciklama || '';
            var kats = row.kategoriler ? row.kategoriler.split(',').map(function(k){return k.trim();}) : [];
            document.querySelectorAll('.edit-kat-cb').forEach(function(cb) {
                cb.checked = kats.includes(cb.value);
            });
            updateEditDateFieldsRequirement();
            var modal = new bootstrap.Modal(document.getElementById('editModal'));
            modal.show();
 
            setTimeout(function() {
                window.isOpeningEditModal = false;
            }, 500);
        }
 
        function saveEdit() {
            var id = document.getElementById('edit_id').value;
            var kategoriler = [];
            document.querySelectorAll('.edit-kat-cb:checked').forEach(function(cb) { kategoriler.push(cb.value); });
            var payload = {
                cari_full: document.getElementById('edit_cari').value,
                kiminle: document.getElementById('edit_kiminle').value,
                kaydeden: document.getElementById('edit_kaydeden').value,
                tutar: document.getElementById('edit_tutar').value,
                doviz: document.getElementById('edit_doviz').value,
                kesin_gelecek_tarih: document.getElementById('edit_kesin_tarih').value,
                vade_tarih: document.getElementById('edit_vade_tarih').value,
                soz_verilen_tarih: document.getElementById('edit_soz_tarih').value,
                itiraz_tarih: document.getElementById('edit_itiraz_tarih').value,
                ulasilamadi_tarih: document.getElementById('edit_ulasilamadi_tarih').value,
                cek_tarih: document.getElementById('edit_cek_tarih').value,
                aciklama: document.getElementById('edit_aciklama').value,
                kategoriler: kategoriler
            };
            fetch('/konusma/guncelle/' + id, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            }).then(r => r.json()).then(function(res) {
                if (res.ok) {
                    bootstrap.Modal.getInstance(document.getElementById('editModal')).hide();
                    loadKonusmalar();
                    var activeCari = document.getElementById('modal_konusma_cari_full').value;
                    if (activeCari) {
                        loadCariKonusmaHistory(activeCari);
                    }
                } else {
                    alert('Güncelleme hatası: ' + res.error);
        // --- 5 DAKİKA HAREKETSİZLİK ZAMANLAYICISI ---
        (function() {
            let inactivitySeconds = 0;
            const MAX_INACTIVITY_SECONDS = 300; // 5 Dakika (300 saniye)

            function resetInactivity() {
                inactivitySeconds = 0;
            }

            window.addEventListener('mousemove', resetInactivity, { passive: true });
            window.addEventListener('keydown', resetInactivity, { passive: true });
            window.addEventListener('click', resetInactivity, { passive: true });
            window.addEventListener('scroll', resetInactivity, { passive: true });
            window.addEventListener('touchstart', resetInactivity, { passive: true });

            setInterval(() => {
                inactivitySeconds += 1;
                if (inactivitySeconds >= MAX_INACTIVITY_SECONDS) {
                    window.location.href = '/logout?timeout=1';
                }
            }, 1000);

            let lastActiveTime = Date.now();
            document.addEventListener('visibilitychange', () => {
                if (!document.hidden) {
                    const elapsed = (Date.now() - lastActiveTime) / 1000;
                    if (elapsed >= MAX_INACTIVITY_SECONDS) {
                        window.location.href = '/logout?timeout=1';
                    }
                }
                lastActiveTime = Date.now();
            });
        })();
    </script>
</body>
</html>
"""
# Bu anahtar Dashboard ile haberleşmeyi sağlar (Sabit kalmalıdır)
AUTH_TOKEN = "ufuk_rapor_portal_2026_secure_key"
@app.before_request
def check_dashboard_auth():
    # Yerel testler ve IP/localhost/LAN erişimleri için doğrulamayı bypass et (çerez domaini ufuklojistik.com olduğu için)
    if "ufuklojistik.com" not in request.host.lower():
        return
 
    # Dashboard tarafından set edilen 'ufuk_auth' çerezini kontrol et
    user_token = request.cookies.get('ufuk_auth')
 
    if user_token != AUTH_TOKEN:
        # Yetkisiz erişim durumunda durdur
        abort(401, description="Lütfen önce Dashboard üzerinden giriş yapın.")
 
_TCMB_RATES_CACHE = {
    'rates': {},
    'last_updated': None
}

def get_tcmb_rates():
    global _TCMB_RATES_CACHE
    import time
    now = time.time()
    
    if _TCMB_RATES_CACHE['rates'] and _TCMB_RATES_CACHE['last_updated'] and (now - _TCMB_RATES_CACHE['last_updated'] < 900):
        return _TCMB_RATES_CACHE['rates']
        
    url = "https://www.tcmb.gov.tr/kurlar/today.xml"
    try:
        import urllib.request
        import xml.etree.ElementTree as ET
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        with urllib.request.urlopen(req, timeout=3) as response:
            xml_data = response.read()
        
        root = ET.fromstring(xml_data)
        rates = {}
        for currency in root.findall('Currency'):
            code = currency.get('CurrencyCode')
            if code in ('USD', 'EUR'):
                forex_buying = currency.find('ForexBuying').text
                rates[code] = float(forex_buying) if forex_buying else 0.0
                
        if rates:
            _TCMB_RATES_CACHE['rates'] = rates
            _TCMB_RATES_CACHE['last_updated'] = now
            return rates
    except Exception as e:
        print(f"Error fetching TCMB rates: {e}")
        
    if _TCMB_RATES_CACHE['rates']:
        return _TCMB_RATES_CACHE['rates']
    return {'USD': 0.0, 'EUR': 0.0}

@app.route('/')
@app.route('/riskpanel')
def index():
    tcmb_rates = get_tcmb_rates()
    tcmb_usd = tcmb_rates.get('USD', 0.0)
    tcmb_eur = tcmb_rates.get('EUR', 0.0)
    secilen_tarih = request.args.get('rapor_tarihi', datetime.now().strftime('%Y-%m-%d'))
    secili_cariler = request.args.getlist('cariler')
    secili_temsilciler = request.args.getlist('temsilciler')
    faiz_orani = float(request.args.get('faiz_orani', 40))
 
    # Age parameters
    p1 = int(request.args.get('p1', 15))
    p2 = int(request.args.get('p2', 30))
    p3 = int(request.args.get('p3', 60))
    p4 = int(request.args.get('p4', 90))
 
    df, tum_cariler, tum_temsilciler, t_gec, t_vade, t_genel, ort_gec, df_risk = get_fatura_data(secilen_tarih, secili_cariler, secili_temsilciler)
 
    toplam_adat = 0
    if not df.empty:
        gecikenler = df[df['GECİKME GÜN'] >= 0]
        for _, row in gecikenler.iterrows():
            gun = 30 if row['GECİKME GÜN'] == 999 else row['GECİKME GÜN']
            satir_adat = (float(row['ACIK_TUTAR']) * gun * faiz_orani) / 36000
            toplam_adat += satir_adat

    ek_toplam = get_ek_toplam_rakam()
 
    if request.args.get('export') == 'excel' and not df.empty:
        output = io.BytesIO()
        df_excel = df.copy()
        excel_columns = ['TEMSILCI', 'CARI_UNVAN', 'FATURA TARİHİ', 'VADE TARİHİ', 'VADE_GUN', 'BELGE NO', 'ACIK_TUTAR', 'BIRIM', 'GECİKME GÜN']
        df_excel = df_excel[excel_columns]
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df_excel.to_excel(writer, index=False)
        output.seek(0)
        return send_file(output, as_attachment=True, download_name=f"Risk_Raporu_{secilen_tarih}.xlsx")
 
    data_list = []
    if not df.empty:
        df_disp = df.copy()
        for col in ['FATURA TARİHİ', 'VADE TARİHİ']:
            df_disp[col] = df_disp[col].apply(lambda x: x.strftime('%d.%m.%Y') if pd.notnull(x) else x)
        data_list = df_disp.to_dict(orient='records')
 
    tahsilat_list = get_tahsilat_listesi(secilen_tarih, df, secili_cariler, secili_temsilciler)
 
    tahsilat_toplamlari = {}
    for row in tahsilat_list:
        doviz = row['DOVIZ']
        tutar = row['TUTAR']
        tahsilat_toplamlari[doviz] = tahsilat_toplamlari.get(doviz, 0.0) + tutar
 
    yaslandirma_list, bucket_labels, yaslandirma_toplamlari = get_borc_yaslandirma(df, p1, p2, p3, p4)
    tahsilat_konusma_list = get_tahsilat_konusma_summary_list(df)
 
    # Seçili cariler için uyarılar
    uyarilar = load_cari_uyarilar()
    aktif_uyarilar = []
    for c_full in secili_cariler:
        if not c_full:
            continue
        if ' - ' in c_full:
            c_kod = c_full.split(' - ', 1)[0].strip()
        else:
            c_kod = c_full.strip()
        uy_notu = uyarilar.get(c_kod)
        if uy_notu:
            aktif_uyarilar.append((c_full, uy_notu))
 
    import socket
    client_ip = request.remote_addr
    default_kaydeden = "Bilinmeyen"
    try:
        default_kaydeden = socket.gethostbyaddr(client_ip)[0]
        if '.' in default_kaydeden:
            default_kaydeden = default_kaydeden.split('.')[0]
    except Exception:
        try:
            default_kaydeden = socket.gethostname()
        except Exception:
            default_kaydeden = "Bilinmeyen"
 
    return render_template_string(HTML_TEMPLATE, data=data_list, toplam_gecikmis=t_gec,
                                  toplam_vade=t_vade, toplam_genel=t_genel, ort_gecikme=ort_gec,
                                  secili_tarih=secilen_tarih, tum_cariler=tum_cariler,
                                  tum_temsilciler=tum_temsilciler, secili_cariler=secili_cariler,
                                  secili_temsilciler=secili_temsilciler, risk_data=df_risk,
                                  ek_toplam=ek_toplam, toplam_adat=toplam_adat, faiz_orani=faiz_orani,
                                  tahsilat_list=tahsilat_list, tahsilat_toplamlari=tahsilat_toplamlari,
                                  yaslandirma_list=yaslandirma_list, bucket_labels=bucket_labels,
                                  yaslandirma_toplamlari=yaslandirma_toplamlari,
                                  p1=p1, p2=p2, p3=p3, p4=p4, aktif_uyarilar=aktif_uyarilar,
                                  uyarilar_json=json.dumps(uyarilar, ensure_ascii=False),
                                  default_kaydeden=default_kaydeden,
                                  tahsilat_konusma_list=tahsilat_konusma_list,
                                  tcmb_usd=tcmb_usd, tcmb_eur=tcmb_eur)

# --- TAHSİLAT API ---

@app.route('/tahsilat/liste', methods=['GET'])
def tahsilat_liste_api():
    try:
        tarih = request.args.get('tarih', datetime.now().strftime('%Y-%m-%d'))
        secili_cariler = request.args.getlist('cariler')
        secili_temsilciler = request.args.getlist('temsilciler')
 
        # Calculate aging data for that date
        df_aging, _, _, _, _, _, _, _ = get_fatura_data(tarih, secili_cariler, secili_temsilciler)
 
        # Get tahsilat list
        tahsilat_list = get_tahsilat_listesi(tarih, df_aging, secili_cariler, secili_temsilciler)
 
        # Calculate totals
        tahsilat_toplamlari = {}
        for row in tahsilat_list:
            doviz = row['DOVIZ']
            tutar = row['TUTAR']
            tahsilat_toplamlari[doviz] = tahsilat_toplamlari.get(doviz, 0.0) + tutar
 
        return jsonify({
            'list': tahsilat_list,
            'toplamlar': tahsilat_toplamlari
        })
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/tahsilat/doviz_liste', methods=['GET'])
def tahsilat_doviz_liste_api():
    try:
        baslangic = request.args.get('baslangic', '2026-01-01')
        bitis = request.args.get('bitis', datetime.now().strftime('%Y-%m-%d'))
        query_str = request.args.get('q', '').strip()
 
        tahsilat_list = get_doviz_tahsilat_listesi(baslangic)
 
        if bitis:
            tahsilat_list = [r for r in tahsilat_list if r['TARIH'] <= bitis]
 
        if query_str:
            q_lower = query_str.lower()
            tahsilat_list = [
                r for r in tahsilat_list
                if q_lower in r['CARI_KODU'].lower() or q_lower in r['CARI_UNVAN'].lower()
            ]
 
        toplam_tl = sum(r['TL'] for r in tahsilat_list)
        toplam_usd = sum(r['USD'] for r in tahsilat_list)
        toplam_eur = sum(r['EUR'] for r in tahsilat_list)
 
        return jsonify({
            'list': tahsilat_list,
            'toplam_tl': toplam_tl,
            'toplam_usd': toplam_usd,
            'toplam_eur': toplam_eur
        })
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/tahsilat/doviz_export', methods=['GET'])
def tahsilat_doviz_export_api():
    try:
        baslangic = request.args.get('baslangic', '2026-01-01')
        bitis = request.args.get('bitis', datetime.now().strftime('%Y-%m-%d'))
        query_str = request.args.get('q', '').strip()
 
        tahsilat_list = get_doviz_tahsilat_listesi(baslangic)
 
        if bitis:
            tahsilat_list = [r for r in tahsilat_list if r['TARIH'] <= bitis]
 
        if query_str:
            q_lower = query_str.lower()
            tahsilat_list = [
                r for r in tahsilat_list
                if q_lower in r['CARI_KODU'].lower() or q_lower in r['CARI_UNVAN'].lower()
            ]
 
        if not tahsilat_list:
            return abort(404, "Kriterlere uygun tahsilat bulunamadı.")
 
        df = pd.DataFrame(tahsilat_list)
 
        df['TARIH'] = df['TARIH'].apply(lambda x: datetime.strptime(x, '%Y-%m-%d').strftime('%d.%m.%Y') if x != '-' else '-')
 
        df = df[['TARIH', 'HAFTA', 'CARI_KODU', 'CARI_UNVAN', 'TAHSILAT_CINSI', 'VADE_TARIHI', 'TL', 'USD', 'EUR']]
        df['VADE_TARIHI'] = df['VADE_TARIHI'].apply(lambda x: datetime.strptime(x, '%Y-%m-%d').strftime('%d.%m.%Y') if x else '-')
        df.columns = ['Tarih', 'Hafta No', 'Cari Kodu', 'Cari Ünvan', 'Tahsilat Cinsi', 'Vade Tarihi', 'TL Tutar', 'USD Tutar', 'EUR Tutar']
 
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Doviz Tahsilatlar')
 
            workbook  = writer.book
            worksheet = writer.sheets['Doviz Tahsilatlar']
 
            num_format = workbook.add_format({'num_format': '#,##0.00'})
            align_center = workbook.add_format({'align': 'center'})
 
            header_format = workbook.add_format({
                'bold': True,
                'text_wrap': True,
                'valign': 'top',
                'fg_color': '#34495e',
                'font_color': 'white',
                'border': 1
            })
 
            for col_num, value in enumerate(df.columns.values):
                worksheet.write(0, col_num, value, header_format)
 
            worksheet.set_column('A:A', 12, align_center)
            worksheet.set_column('B:B', 10, align_center)
            worksheet.set_column('C:C', 15)
            worksheet.set_column('D:D', 35)
            worksheet.set_column('E:E', 18, align_center)
            worksheet.set_column('F:F', 12, align_center)
            worksheet.set_column('G:G', 15, num_format)
            worksheet.set_column('H:H', 15, num_format)
            worksheet.set_column('I:I', 15, num_format)
 
        output.seek(0)
        filename = f"Tahsilat_Listesi_{baslangic}_to_{bitis}.xlsx"
        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        return abort(500, str(e))

# --- TAHSİLAT KONUŞMALARI API ---

@app.route('/cari/bakiye_detay', methods=['GET'])
def cari_bakiye_detay():
    try:
        cari_full = request.args.get('cari', '')
        tarih = request.args.get('tarih', datetime.now().strftime('%Y-%m-%d'))
        if not cari_full:
            return jsonify({'ok': False, 'error': 'Cari seçilmedi'})
 
        if ' - ' in cari_full:
            cari_kodu = cari_full.split(' - ', 1)[0].strip()
        else:
            cari_kodu = cari_full.strip()
 
        df, _, _, t_gec, t_vade, t_genel, _, _ = get_fatura_data(tarih, secili_cariler=[cari_full])
 
        currencies = set(list(t_genel.keys()) + list(t_gec.keys()) + list(t_vade.keys()))
        detaylar = []
        for curr in currencies:
            detaylar.append({
                'doviz': curr,
                'bakiye': t_genel.get(curr, 0.0),
                'gunu_gecen': t_gec.get(curr, 0.0),
                'gunu_gelmeyen': t_vade.get(curr, 0.0)
            })
 
        cari_doviz = "TL"
        if not df.empty and 'BIRIM' in df.columns:
            cari_doviz = df.iloc[0]['BIRIM']
        else:
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute(f"SELECT CCURRENCY FROM LG_{AKTIF_YIL}_CLCARD WHERE CODE = ?", (cari_kodu,))
                row = cursor.fetchone()
                if row:
                    ccurrency = row[0]
                    doviz_map = {0: "TL", 1: "USD", 2: "EUR", 11: "GBP", 20: "EUR"}
                    cari_doviz = doviz_map.get(ccurrency, "TL")
                conn.close()
            except Exception as e:
                print(f"Error querying Logo DB for client currency: {e}")
 
        uyarilar = load_cari_uyarilar()
        uyari = uyarilar.get(cari_kodu, '')
 
        return jsonify({'ok': True, 'detaylar': detaylar, 'uyari': uyari, 'cari_doviz': cari_doviz})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

@app.route('/cari/uyari/liste', methods=['GET'])
def cari_uyari_liste_api():
    try:
        uyarilar = load_cari_uyarilar()
        if not uyarilar:
            return jsonify({'ok': True, 'uyarilar': []})
 
        conn = get_db_connection()
        placeholders = ','.join(['?'] * len(uyarilar))
        query = f"SELECT CODE, DEFINITION_ FROM LG_{AKTIF_YIL}_CLCARD WHERE CODE IN ({placeholders})"
        df_names = pd.read_sql(query, conn, params=list(uyarilar.keys()))
        conn.close()
 
        name_map = dict(zip(df_names['CODE'].str.strip(), df_names['DEFINITION_'].str.strip()))
 
        result = []
        for kod, notu in uyarilar.items():
            unvan = name_map.get(kod.strip(), kod)
            result.append({
                'cari_kodu': kod,
                'cari_unvan': unvan,
                'cari_full': f"{kod} - {unvan}",
                'uyari': notu
            })
        return jsonify({'ok': True, 'uyarilar': result})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

@app.route('/cari/uyari/kaydet', methods=['POST'])
def cari_uyari_kaydet():
    try:
        data = request.get_json()
        cari_full = data.get('cari_full', '')
        uyari_notu = data.get('uyari', '').strip()
        if not cari_full:
            return jsonify({'ok': False, 'error': 'Cari seçilmedi'})
 
        if ' - ' in cari_full:
            cari_kodu = cari_full.split(' - ', 1)[0].strip()
        else:
            cari_kodu = cari_full.strip()
 
        uyarilar = load_cari_uyarilar()
        if uyari_notu:
            uyarilar[cari_kodu] = uyari_notu
        else:
            uyarilar.pop(cari_kodu, None)
 
        with open(UYARILAR_JSON_PATH, 'w', encoding='utf-8') as f:
            json.dump(uyarilar, f, ensure_ascii=False, indent=4)
 
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


@app.route('/konusma/ekle', methods=['POST'])
def konusma_ekle():
    try:
        data = request.get_json()
        cari_full = data.get('cari_full', '')
        # cari_full formatı: "120.01.001 - FİRMA ADI"
        if ' - ' in cari_full:
            parts = cari_full.split(' - ', 1)
            cari_kodu = parts[0].strip()
            cari_unvan = parts[1].strip()
        else:
            cari_kodu = cari_full.strip()
            cari_unvan = cari_full.strip()
 
        kategoriler = ','.join(data.get('kategoriler', []))
        kesin_gelecek_tarih = data.get('kesin_gelecek_tarih', '') or None
        vade_tarih = data.get('vade_tarih', '') or None
        soz_verilen_tarih = data.get('soz_verilen_tarih', '') or None
        itiraz_tarih = data.get('itiraz_tarih', '') or None
        ulasilamadi_tarih = data.get('ulasilamadi_tarih', '') or None
        cek_tarih = data.get('cek_tarih', '') or None
        kiminle = data.get('kiminle', '')
        aciklama = data.get('aciklama', '')
 
        tutar = data.get('tutar', None)
        if tutar == '':
            tutar = None
        else:
            try:
                tutar = float(tutar)
            except (ValueError, TypeError):
                tutar = None
 
        doviz = data.get('doviz', 'TL') or 'TL'
        kaydeden = data.get('kaydeden', '')
 
        conn = sqlite3.connect(KONUSMA_DB_PATH)
        c = conn.cursor()
        c.execute('''INSERT INTO konusmalar
                     (cari_kodu, cari_unvan, kategoriler, kesin_gelecek_tarih, soz_verilen_tarih, kiminle, aciklama, tutar, doviz, kaydeden, vade_tarih, itiraz_tarih, ulasilamadi_tarih, cek_tarih)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (cari_kodu, cari_unvan, kategoriler, kesin_gelecek_tarih, soz_verilen_tarih, kiminle, aciklama, tutar, doviz, kaydeden, vade_tarih, itiraz_tarih, ulasilamadi_tarih, cek_tarih))
        conn.commit()
        conn.close()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

@app.route('/konusma/liste', methods=['GET'])
def konusma_liste():
    try:
        cari_filtre = request.args.get('cari', '')
        kat_filtre = request.args.get('kategori', '')
 
        conn = sqlite3.connect(KONUSMA_DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
 
        sql = 'SELECT * FROM konusmalar WHERE 1=1'
        params = []
 
        if cari_filtre:
            # cari_filtre formatı: "120.01.001 - FİRMA ADI"
            if ' - ' in cari_filtre:
                cari_kodu_f = cari_filtre.split(' - ', 1)[0].strip()
            else:
                cari_kodu_f = cari_filtre.strip()
            sql += ' AND cari_kodu = ?'
            params.append(cari_kodu_f)
 
        if kat_filtre:
            sql += ' AND kategoriler LIKE ?'
            params.append(f'%{kat_filtre}%')
 
        sql += ' ORDER BY id DESC'
        c.execute(sql, params)
        rows = c.fetchall()
        conn.close()
 
        result = [dict(row) for row in rows]
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/konusma/cari_gecmis/<cari_kodu>', methods=['GET'])
def konusma_cari_gecmis(cari_kodu):
    try:
        conn = sqlite3.connect(KONUSMA_DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('''
            SELECT id, cari_kodu, cari_unvan, kesin_gelecek_tarih, soz_verilen_tarih, kategoriler, aciklama, tutar, doviz, olusturma_tarihi, kiminle, kaydeden, vade_tarih, itiraz_tarih, ulasilamadi_tarih, cek_tarih
            FROM konusmalar
            WHERE cari_kodu = ?
            ORDER BY id DESC
        ''', (cari_kodu,))
        rows = c.fetchall()
        conn.close()
        return jsonify([dict(row) for row in rows])
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/konusma/sil/<int:kid>', methods=['DELETE'])
def konusma_sil(kid):
    try:
        conn = sqlite3.connect(KONUSMA_DB_PATH)
        c = conn.cursor()
        c.execute('DELETE FROM konusmalar WHERE id = ?', (kid,))
        conn.commit()
        conn.close()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

@app.route('/konusma/guncelle/<int:kid>', methods=['PUT'])
def konusma_guncelle(kid):
    try:
        data = request.get_json()
        cari_full = data.get('cari_full', '')
        if ' - ' in cari_full:
            parts = cari_full.split(' - ', 1)
            cari_kodu = parts[0].strip()
            cari_unvan = parts[1].strip()
        else:
            cari_kodu = cari_full.strip()
            cari_unvan = cari_full.strip()
 
        kategoriler = ','.join(data.get('kategoriler', []))
        kesin_gelecek_tarih = data.get('kesin_gelecek_tarih', '') or None
        vade_tarih = data.get('vade_tarih', '') or None
        soz_verilen_tarih = data.get('soz_verilen_tarih', '') or None
        itiraz_tarih = data.get('itiraz_tarih', '') or None
        ulasilamadi_tarih = data.get('ulasilamadi_tarih', '') or None
        cek_tarih = data.get('cek_tarih', '') or None
        kiminle = data.get('kiminle', '')
        aciklama = data.get('aciklama', '')
 
        tutar = data.get('tutar', None)
        if tutar == '':
            tutar = None
        else:
            try:
                tutar = float(tutar)
            except (ValueError, TypeError):
                tutar = None
 
        doviz = data.get('doviz', 'TL') or 'TL'
        kaydeden = data.get('kaydeden', '')
 
        conn = sqlite3.connect(KONUSMA_DB_PATH)
        c = conn.cursor()
        c.execute('''UPDATE konusmalar SET
                     cari_kodu=?, cari_unvan=?, kategoriler=?, kesin_gelecek_tarih=?,
                     soz_verilen_tarih=?, kiminle=?, aciklama=?, tutar=?, doviz=?, kaydeden=?,
                     vade_tarih=?, itiraz_tarih=?, ulasilamadi_tarih=?, cek_tarih=?,
                     guncelleme_tarihi=datetime('now','localtime')
                     WHERE id=?''',
                  (cari_kodu, cari_unvan, kategoriler, kesin_gelecek_tarih,
                   soz_verilen_tarih, kiminle, aciklama, tutar, doviz, kaydeden,
                   vade_tarih, itiraz_tarih, ulasilamadi_tarih, cek_tarih, kid))
        conn.commit()
        conn.close()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

# --- CARİ HESAP EKSTRE API ---

def get_cari_ekstre(cari_kodu, baslangic_tarihi=None):
    conn = get_db_connection()
 
    doviz_map = {0: "TL", 1: "USD", 2: "EUR", 11: "GBP", 20: "EUR"}
    trcode_map = {
        1: "Nakit Tahsilat",
        2: "Nakit Ödeme",
        3: "Borç Dekontu",
        4: "Alacak Dekontu",
        5: "Virman İşlemi",
        6: "Kur Farkı Fişi",
        12: "Özel Fiş",
        14: "Açılış Fişi",
        20: "Gelen Havale",
        21: "Gönderilen Havale",
        31: "Satınalma Faturası",
        32: "Perakende İade",
        33: "Toptan İade",
        37: "Satınalma İade",
        38: "Toptan Satış Faturası",
        39: "Perakende Satış Faturası",
        61: "Çek Girişi",
        62: "Senet Girişi",
        63: "Çek Çıkışı",
        64: "Senet Çıkışı",
        70: "Kredi Kartı Fişi",
        71: "Kredi Kartı İade"
    }

    query = f"""
    SELECT
        CLF.DATE_ AS TARIH,
        CLF.TRANNO AS FIS_NO,
        CLF.TRCODE AS ISLEM_TURU_KODU,
        CLF.LINEEXP AS ACIKLAMA,
        ROUND(CASE WHEN CLF.SIGN = 0 THEN CLF.AMOUNT ELSE 0 END, 2) AS BORC,
        ROUND(CASE WHEN CLF.SIGN = 1 THEN CLF.AMOUNT ELSE 0 END, 2) AS ALACAK,
        CLF.TRCURR AS DOVIZ_TIPI
    FROM LG_{AKTIF_YIL}_{DONEM}_CLFLINE CLF WITH(NOLOCK)
    INNER JOIN LG_{AKTIF_YIL}_CLCARD C WITH(NOLOCK) ON C.LOGICALREF = CLF.CLIENTREF
    WHERE CLF.CANCELLED = 0
      AND C.CODE = ?
    ORDER BY CLF.DATE_, CLF.LOGICALREF
    """
 
    df = pd.read_sql(query, conn, params=[cari_kodu])
    conn.close()
 
    if df.empty:
        return []
 
    baslangic_dt = None
    if baslangic_tarihi:
        try:
            baslangic_dt = datetime.strptime(baslangic_tarihi, '%Y-%m-%d')
        except ValueError:
            pass

    entries = []
    bakiye = 0.0
    devir_borc = 0.0
    devir_alacak = 0.0
 
    default_doviz = 'TL'
    if not df.empty:
        default_doviz = doviz_map.get(df.iloc[0]['DOVIZ_TIPI'], 'TL')
 
    detail_rows = []
 
    for _, row in df.iterrows():
        tarih_dt = row['TARIH']
        borc = float(row['BORC'])
        alacak = float(row['ALACAK'])
 
        if baslangic_dt and tarih_dt < baslangic_dt:
            bakiye += (borc - alacak)
            if (borc - alacak) >= 0:
                devir_borc += (borc - alacak)
            else:
                devir_alacak += (alacak - borc)
        else:
            detail_rows.append(row)
 
    if baslangic_dt:
        entries.append({
            'TARIH': baslangic_tarihi,
            'FIS_NO': '',
            'ISLEM_TURU': 'DEVİR BAKİYESİ',
            'ACIKLAMA': 'Seçilen Tarih Öncesi Devir Bakiyesi',
            'BORC': round(devir_borc, 2) if devir_borc > 0 else 0.0,
            'ALACAK': round(devir_alacak, 2) if devir_alacak > 0 else 0.0,
            'BAKIYE': round(bakiye, 2),
            'DOVIZ': default_doviz
        })
 
    for row in detail_rows:
        tarih_dt = row['TARIH']
        tarih_str = tarih_dt.strftime('%Y-%m-%d') if pd.notnull(tarih_dt) else '-'
 
        tr_code = row['ISLEM_TURU_KODU']
        islem_turu = trcode_map.get(tr_code, f"Diğer ({tr_code})")
 
        borc = float(row['BORC'])
        alacak = float(row['ALACAK'])
        bakiye += (borc - alacak)
 
        doviz = doviz_map.get(row['DOVIZ_TIPI'], 'TL')
 
        entries.append({
            'TARIH': tarih_str,
            'FIS_NO': row['FIS_NO'] or '',
            'ISLEM_TURU': islem_turu,
            'ACIKLAMA': row['ACIKLAMA'] or '',
            'BORC': borc,
            'ALACAK': alacak,
            'BAKIYE': round(bakiye, 2),
            'DOVIZ': doviz
        })
 
    return entries

@app.route('/ekstre/liste', methods=['GET'])
def ekstre_liste_api():
    try:
        cari_full = request.args.get('cari', '')
        baslangic = request.args.get('baslangic', '')
        if not cari_full:
            return jsonify({'error': 'Lütfen bir cari hesap seçin!'})
 
        if ' - ' in cari_full:
            cari_kodu = cari_full.split(' - ', 1)[0].strip()
        else:
            cari_kodu = cari_full.strip()
 
        entries = get_cari_ekstre(cari_kodu, baslangic)
        return jsonify({'list': entries})
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/ekstre/export', methods=['GET'])
def ekstre_export_api():
    try:
        cari_full = request.args.get('cari', '')
        baslangic = request.args.get('baslangic', '')
        if not cari_full:
            return abort(400, "Lütfen bir cari hesap seçin!")
 
        if ' - ' in cari_full:
            cari_kodu = cari_full.split(' - ', 1)[0].strip()
            cari_unvan = cari_full.split(' - ', 1)[1].strip()
        else:
            cari_kodu = cari_full.strip()
            cari_unvan = cari_full.strip()
 
        entries = get_cari_ekstre(cari_kodu, baslangic)
        if not entries:
            return abort(404, "Bu cari için hareket bulunamadı.")
 
        df = pd.DataFrame(entries)
        df['TARIH'] = df['TARIH'].apply(lambda x: datetime.strptime(x, '%Y-%m-%d').strftime('%d.%m.%Y') if x != '-' else '-')
 
        df = df[['TARIH', 'ISLEM_TURU', 'FIS_NO', 'ACIKLAMA', 'BORC', 'ALACAK', 'BAKIYE', 'DOVIZ']]
        df.columns = ['Tarih', 'İşlem Türü', 'Fiş/Belge No', 'Açıklama', 'Borç', 'Alacak', 'Bakiye', 'Döviz']
 
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Cari Ekstre')
 
            workbook  = writer.book
            worksheet = writer.sheets['Cari Ekstre']
 
            num_format = workbook.add_format({'num_format': '#,##0.00'})
            align_right = workbook.add_format({'align': 'right'})
 
            header_format = workbook.add_format({
                'bold': True,
                'text_wrap': True,
                'valign': 'top',
                'fg_color': '#34495e',
                'font_color': 'white',
                'border': 1
            })
 
            for col_num, value in enumerate(df.columns.values):
                worksheet.write(0, col_num, value, header_format)
 
            worksheet.set_column('A:A', 12)
            worksheet.set_column('B:B', 20)
            worksheet.set_column('C:C', 15)
            worksheet.set_column('D:D', 35)
            worksheet.set_column('E:E', 15, num_format)
            worksheet.set_column('F:F', 15, num_format)
            worksheet.set_column('G:G', 18, num_format)
            worksheet.set_column('H:H', 10, align_right)
 
        output.seek(0)
        filename = f"Ekstre_{cari_kodu}.xlsx"
        from urllib.parse import quote
        safe_filename = quote(filename)
        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        return abort(500, str(e))

@app.route('/faturadetay/liste', methods=['GET'])
def fatura_detay_liste_api():
    try:
        conn = get_db_connection()
        sql = """
        SELECT 
            FT.FaturaKodu,
            FT.FaturaTarihi,
            CH.TDKodu AS CariHesapKodu,
            CH.Unvan AS CariUnvan,
            FT.FaturaAciklama,
            SHK.PB AS DovizKodu,
            PB.PBAdi AS DovizTipi,
            SUM(SHK.KapAdedi * SHK.KDVsizBirimFiyat) AS Matrah,
            SUM(SHK.KapAdedi * SHK.KDVsizBirimFiyat * (CASE WHEN SHK.KDVDurumu = 'M' THEN 0 ELSE SHK.KDVOrani END / 100.0)) AS KDV,
            SUM(SHK.KapAdedi * SHK.KDVsizBirimFiyat * (1 + (CASE WHEN SHK.KDVDurumu = 'M' THEN 0 ELSE SHK.KDVOrani END / 100.0))) AS GenelToplam
        FROM LojistikERP_UFUK.dbo.DY_STOK_HAREKETLERI SHK 
        INNER JOIN LojistikERP_UFUK.dbo.DY_FATURALAR FT ON FT.FaturaKodu = SHK.FaturaKodu 
        INNER JOIN LojistikERP_UFUK.dbo.CH_CARI_HESAPLAR CH ON CH.CariHesapKodu = FT.CariHesapKodu 
        INNER JOIN LojistikERP_UFUK.dbo.CH_PARA_BIRIMLERI PB ON PB.PBKodu = SHK.PB 
        WHERE FT.FaturaTipiKodu = 2 
          AND FT.ResmiMi = '1' 
          AND (FT.FaturaNo IS NULL OR LTRIM(RTRIM(FT.FaturaNo)) = '') 
          AND FT.FaturaTarihi >= '2026-01-01'
          AND CH.Unvan NOT LIKE '%UFUK INTERMODAL%'
          AND CH.Unvan NOT LIKE '%UFUK İNTERMODAL%'
        GROUP BY 
            FT.FaturaKodu,
            FT.FaturaTarihi,
            CH.TDKodu,
            CH.Unvan,
            FT.FaturaAciklama,
            SHK.PB,
            PB.PBAdi
        ORDER BY FT.FaturaTarihi DESC
        """
        df = pd.read_sql(sql, conn)
        conn.close()

        records = []
        if not df.empty:
            df['FaturaTarihi'] = df['FaturaTarihi'].apply(lambda x: x.strftime('%Y-%m-%d') if pd.notnull(x) else '')
            df['CariHesapKodu'] = df['CariHesapKodu'].fillna('')
            for col in ['CariUnvan', 'FaturaAciklama']:
                df[col] = df[col].fillna('').apply(lambda x: x.replace('\r\n', ' ').replace('\n', ' ').replace('\r', ' ').strip())
            records = df.to_dict(orient='records')

        return jsonify({'success': True, 'data': records})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/faturadetay/export', methods=['GET'])
def fatura_detay_export_api():
    try:
        conn = get_db_connection()
        sql = """
        SELECT 
            FT.FaturaTarihi AS [Fatura Tarihi],
            CH.TDKodu AS [Cari Hesap Kodu],
            CH.Unvan AS [Cari Ünvan],
            FT.FaturaAciklama AS [Fatura Açıklaması],
            SUM(SHK.KapAdedi * SHK.KDVsizBirimFiyat) AS [Matrah],
            SUM(SHK.KapAdedi * SHK.KDVsizBirimFiyat * (CASE WHEN SHK.KDVDurumu = 'M' THEN 0 ELSE SHK.KDVOrani END / 100.0)) AS [KDV],
            SUM(SHK.KapAdedi * SHK.KDVsizBirimFiyat * (1 + (CASE WHEN SHK.KDVDurumu = 'M' THEN 0 ELSE SHK.KDVOrani END / 100.0))) AS [Genel Toplam],
            PB.PBAdi AS [Döviz Tipi]
        FROM LojistikERP_UFUK.dbo.DY_STOK_HAREKETLERI SHK 
        INNER JOIN LojistikERP_UFUK.dbo.DY_FATURALAR FT ON FT.FaturaKodu = SHK.FaturaKodu 
        INNER JOIN LojistikERP_UFUK.dbo.CH_CARI_HESAPLAR CH ON CH.CariHesapKodu = FT.CariHesapKodu 
        INNER JOIN LojistikERP_UFUK.dbo.CH_PARA_BIRIMLERI PB ON PB.PBKodu = SHK.PB 
        WHERE FT.FaturaTipiKodu = 2 
          AND FT.ResmiMi = '1' 
          AND (FT.FaturaNo IS NULL OR LTRIM(RTRIM(FT.FaturaNo)) = '') 
          AND FT.FaturaTarihi >= '2026-01-01'
          AND CH.Unvan NOT LIKE '%UFUK INTERMODAL%'
          AND CH.Unvan NOT LIKE '%UFUK İNTERMODAL%'
        GROUP BY 
            FT.FaturaKodu,
            FT.FaturaTarihi,
            CH.TDKodu,
            CH.Unvan,
            FT.FaturaAciklama,
            SHK.PB,
            PB.PBAdi
        ORDER BY FT.FaturaTarihi DESC
        """
        df = pd.read_sql(sql, conn)
        conn.close()

        if df.empty:
            return abort(404, "Kesilecek fatura bulunamadı.")

        df['Fatura Tarihi'] = df['Fatura Tarihi'].apply(lambda x: x.strftime('%d.%m.%Y') if pd.notnull(x) else '')
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Kesilecek Faturalar')

            workbook  = writer.book
            worksheet = writer.sheets['Kesilecek Faturalar']

            num_format = workbook.add_format({'num_format': '#,##0.00'})
            align_right = workbook.add_format({'align': 'right'})

            header_format = workbook.add_format({
                'bold': True,
                'text_wrap': True,
                'valign': 'top',
                'fg_color': '#1e3a8a',
                'font_color': 'white',
                'border': 1
            })

            for col_num, value in enumerate(df.columns.values):
                worksheet.write(0, col_num, value, header_format)

            worksheet.set_column('A:A', 15)
            worksheet.set_column('B:B', 18)
            worksheet.set_column('C:C', 40)
            worksheet.set_column('D:D', 50)
            worksheet.set_column('E:E', 15, num_format)
            worksheet.set_column('F:F', 12, num_format)
            worksheet.set_column('G:G', 18, num_format)
            worksheet.set_column('H:H', 12, align_right)

        output.seek(0)
        filename = "Kesilecek_Faturalar_Detayli.xlsx"
        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        return abort(500, str(e))

if __name__ == '__main__':
    print("Tahsilat ve Risk Takip servisi başlatılıyor: http://127.0.0.1:5004/riskpanel")
    serve(app, host='127.0.0.1', port=5004, url_prefix='/riskpanel')