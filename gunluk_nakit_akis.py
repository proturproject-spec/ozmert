from flask import Flask, render_template, request, redirect, url_for, abort, jsonify
from werkzeug.middleware.proxy_fix import ProxyFix
from sqlalchemy import create_engine, text
import json
import os
import re
import pandas as pd
import urllib
from datetime import datetime, timedelta

try:
    from export_eski_sablon import generate_eski_sablon_excel
except ImportError:
    generate_eski_sablon_excel = None

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

@app.template_filter('format_currency')
def format_currency(value):
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

# --- SQL BAĞLANTI AYARLARI (lazy loading - import sırasında bağlantı kurulmaz) ---
from db_manager import get_engine
import os

_engine_cache = {}

def _get_engine_lazy(conn_id):
    """Engine'i ilk kullanımda oluşturur. Render/bridge modunda None döner."""
    if os.environ.get('BRIDGE_URL', '').strip():
        return None  # Bridge modu aktif, yerel SQL bağlantısı gereksiz
    if conn_id not in _engine_cache:
        try:
            _engine_cache[conn_id] = get_engine(conn_id)
        except Exception as _e:
            print(f"Engine {conn_id} başlatma uyarısı: {_e}")
            _engine_cache[conn_id] = None
    return _engine_cache[conn_id]

# Geriye dönük uyumluluk için property-like erişim
class _LazyEngine:
    def __init__(self, conn_id):
        self._id = conn_id
    def __bool__(self):
        return _get_engine_lazy(self._id) is not None
    def connect(self):
        e = _get_engine_lazy(self._id)
        if e is None:
            raise RuntimeError(f"SQL Engine {self._id} bu ortamda kullanılamaz (Bridge modu aktif).")
        return e.connect()
    def begin(self):
        e = _get_engine_lazy(self._id)
        if e is None:
            raise RuntimeError(f"SQL Engine {self._id} bu ortamda kullanılamaz (Bridge modu aktif).")
        return e.begin()

engine = _LazyEngine(1)
engine_nexlog = _LazyEngine(2)

_NEXLOG_CATEGORIES_CACHE = None

def clear_nexlog_categories_cache():
    global _NEXLOG_CATEGORIES_CACHE
    _NEXLOG_CATEGORIES_CACHE = None

def get_nexlog_categories():
    global _NEXLOG_CATEGORIES_CACHE
    if _NEXLOG_CATEGORIES_CACHE is not None:
        return _NEXLOG_CATEGORIES_CACHE
    try:
        with engine_nexlog.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM dbo.manuel_giris_kaotgeri")).scalar()
            if count == 0:
                defaults = [
                    'Vergi', 'Maaş', 'KDV', '2 Nolu KDV', 'Navlun',
                    'Kredi Kartı', 'Yeni Satış Tahsilat', 'C/H Tahsilat', 'Diğer'
                ]
                with engine_nexlog.begin() as tx_conn:
                    for idx, cat in enumerate(defaults, 1):
                        tx_conn.execute(
                            text("INSERT INTO dbo.manuel_giris_kaotgeri (id, katogeri) VALUES (:id, :katogeri)"),
                            {"id": idx, "katogeri": cat}
                        )
            
            rows = conn.execute(text("SELECT id, katogeri FROM dbo.manuel_giris_kaotgeri ORDER BY id")).fetchall()
            _NEXLOG_CATEGORIES_CACHE = [{"id": r[0], "name": r[1]} for r in rows]
            return _NEXLOG_CATEGORIES_CACHE
    except Exception as e:
        print(f"Error getting NEXLOG categories: {e}")
        defaults = [
            'Vergi', 'Maaş', 'KDV', '2 Nolu KDV', 'Navlun',
            'Kredi Kartı', 'Yeni Satış Tahsilat', 'C/H Tahsilat', 'Diğer'
        ]
        return [{"id": i, "name": cat} for i, cat in enumerate(defaults, 1)]

Firma = "226"
Donem = "01"

def is_resmi_tatil(d):
    # Sabit tarihli resmi tatiller
    # 1 Ocak, 23 Nisan, 1 Mayıs, 19 Mayıs, 15 Temmuz, 30 Ağustos, 29 Ekim
    if (d.month == 1 and d.day == 1) or \
       (d.month == 4 and d.day == 23) or \
       (d.month == 5 and d.day == 1) or \
       (d.month == 5 and d.day == 19) or \
       (d.month == 7 and d.day == 15) or \
       (d.month == 8 and d.day == 30) or \
       (d.month == 10 and d.day == 29):
        return True
        
    # Yıllara göre dini tatiller (Ramazan ve Kurban Bayramları)
    year = d.year
    if year == 2025:
        # Ramazan Bayramı: 30 Mart - 1 Nisan
        if (d.month == 3 and d.day in (30, 31)) or (d.month == 4 and d.day == 1):
            return True
        # Kurban Bayramı: 6 Haziran - 9 Haziran
        if d.month == 6 and d.day in (6, 7, 8, 9):
            return True
    elif year == 2026:
        # Ramazan Bayramı: 20 Mart - 22 Mart
        if d.month == 3 and d.day in (20, 21, 22):
            return True
        # Kurban Bayramı: 27 Mayıs - 30 Mayıs
        if d.month == 5 and d.day in (27, 28, 29, 30):
            return True
    elif year == 2027:
        # Ramazan Bayramı: 9 Mart - 11 Mart
        if d.month == 3 and d.day in (9, 10, 11):
            return True
        # Kurban Bayramı: 16 Mayıs - 19 Mayıs
        if d.month == 5 and d.day in (16, 17, 18, 19):
            return True
            
    return False

def is_resmi_tatil_or_weekend(d):
    # Hafta sonu kontrolü
    if d.weekday() in (5, 6):  # 5: Cumartesi, 6: Pazar
        return True
    
    return is_resmi_tatil(d)
    


def get_first_business_day(d):
    # d bir datetime.date, datetime.datetime veya pandas Timestamp olabilir
    if hasattr(d, 'date'):
        d_date = d.date()
    elif isinstance(d, str):
        try:
            d_date = datetime.strptime(d[:10], '%Y-%m-%d').date()
        except Exception:
            return d
    else:
        d_date = d

    while is_resmi_tatil_or_weekend(d_date):
        d_date = d_date + timedelta(days=1)
        
    return datetime(d_date.year, d_date.month, d_date.day)

def get_own_check_details(target_date, limit_date=True):
    # limit_date=True: sadece seçilen tarihe eşit vadeli çekler (o günün ödemeleri)
    # limit_date=False: tüm gelecek vadeli çekler (rapor için)
    if limit_date:
        date_filter = f"AND CONVERT(VARCHAR, CSC.DUEDATE, 23) <= '{target_date}'"
    else:
        date_filter = ""
    query = f"""
    SELECT 
        (SELECT TOP 1 CL.DEFINITION_ 
         FROM LG_{Firma}_CLCARD CL WITH(NOLOCK)
         JOIN LG_{Firma}_{Donem}_CSTRANS CS WITH(NOLOCK) 
           ON CL.LOGICALREF = CS.CARDREF 
         WHERE CS.CSREF = CSC.LOGICALREF 
         ORDER BY CS.LOGICALREF ASC) AS [CH_UNVANI], 
        CSC.BANKNAME AS [BANKA], 
        CSC.DUEDATE AS [VADE], 
        CSC.AMOUNT AS [TUTAR],
        CSC.TRCURR AS [DOVIZ_TIPI]
    FROM LG_{Firma}_{Donem}_CSCARD CSC WITH(NOLOCK)
    WHERE CSC.DOC IN (3,4) 
      AND CSC.CURRSTAT NOT IN (8, 6) 
      {date_filter}
    ORDER BY CSC.DUEDATE ASC
    """
    with engine.connect() as conn:
        df = pd.read_sql(text(query), conn)
        
    if not df.empty:
        df['VADE'] = pd.to_datetime(df['VADE'])
        df['VADE'] = df['VADE'].apply(get_first_business_day)
        if limit_date:
            # Exact date: iş günü düzeltmesi sonrası seçilen tarihe eşit olanlar
            target_dt = pd.to_datetime(target_date)
            df = df[df['VADE'] <= target_dt]
        df = df.sort_values(by='VADE')
        
    return df

def get_customer_check_details(target_date, limit_date=True):
    date_filter = f"AND CONVERT(VARCHAR, CSC.DUEDATE, 23) <= '{target_date}'" if limit_date else ""
    query = f"""
    SELECT 
        (SELECT TOP 1 CL.DEFINITION_ 
         FROM LG_{Firma}_CLCARD CL WITH(NOLOCK)
         JOIN LG_{Firma}_{Donem}_CSTRANS CS WITH(NOLOCK) 
           ON CL.LOGICALREF = CS.CARDREF 
         WHERE CS.CSREF = CSC.LOGICALREF 
           AND CS.TRCODE = 1) AS [CH_UNVANI], 
        CSC.BANKNAME AS [BANKA], 
        (SELECT TOP 1 BA.DEFINITION_ 
         FROM LG_{Firma}_{Donem}_CSTRANS CST WITH(NOLOCK)
         JOIN LG_{Firma}_BANKACC BA WITH(NOLOCK) ON BA.LOGICALREF = CST.CARDREF
         WHERE CST.CSREF = CSC.LOGICALREF 
           AND CST.STATUS = CSC.CURRSTAT 
           AND CST.CARDMD = 7
         ORDER BY CST.LOGICALREF DESC) AS [OUR_BANK],
        CSC.NEWSERINO AS [CEK_NO],
        CSC.DUEDATE AS [VADE], 
        CSC.AMOUNT AS [TUTAR],
        CSC.TRCURR AS [DOVIZ_TIPI],
        CSC.CURRSTAT AS [DURUM_KODU],
        CASE CSC.CURRSTAT
            WHEN 1 THEN 'Portföyde'
            WHEN 2 THEN 'Ciro Edildi'
            WHEN 3 THEN 'Teminata Verildi'
            WHEN 4 THEN 'Tahsile Verildi'
            WHEN 5 THEN 'Protestolu Tahsile Verildi'
            WHEN 6 THEN 'İade Edildi'
            WHEN 7 THEN 'Protesto Edildi'
            WHEN 8 THEN 'Tahsil Edildi'
            WHEN 9 THEN 'Kendi Çekimiz'
            WHEN 10 THEN 'Borç Senedimiz'
            WHEN 11 THEN 'Karşılıksız'
            WHEN 12 THEN 'Tahsil Edilemiyor'
            ELSE 'Bilinmeyen'
        END AS [DURUM]
    FROM LG_{Firma}_{Donem}_CSCARD CSC WITH(NOLOCK)
    WHERE CSC.DOC = 1 
      AND CSC.CURRSTAT NOT IN (2, 6, 8)
      {date_filter}
    ORDER BY CSC.DUEDATE ASC
    """
    with engine.connect() as conn:
        df = pd.read_sql(text(query), conn)
        
    if not df.empty:
        df['VADE'] = pd.to_datetime(df['VADE'])
        df['ORJ_VADE'] = df['VADE'].dt.strftime('%d.%m.%Y')
        
        def adjust_vade(row):
            banka = str(row.get('BANKA', '')).upper().replace('İ', 'I').replace('Ş', 'S').replace('Ğ', 'G').replace('Ü', 'U').replace('Ö', 'O').replace('Ç', 'C')
            our_bank = str(row.get('OUR_BANK', '')).upper().replace('İ', 'I').replace('Ş', 'S').replace('Ğ', 'G').replace('Ü', 'U').replace('Ö', 'O').replace('Ç', 'C')
            
            if 'QNB' in banka: banka = banka.replace('QNB', 'FINANSBANK')
            if 'QNB' in our_bank: our_bank = our_bank.replace('QNB', 'FINANSBANK')
            
            vade = row['VADE']
            durum = row.get('DURUM_KODU')
            
            if durum == 1:
                return get_first_business_day(vade)
            
            if pd.isna(row.get('BANKA')) or pd.isna(row.get('OUR_BANK')) or not row.get('BANKA') or not row.get('OUR_BANK'):
                return get_first_business_day(vade + timedelta(days=1))
                
            ignore = {'BANKASI', 'BANK', 'A.S.', 'T.A.S.', 'A.S', 'T.A.S', 'VE', 'TURKIYE', 'TC', 'T.C.', 'T.C', 'YENI', 'SUBESI', 'SUBE'}
            b_words = [w for w in banka.split() if len(w) > 2 and w not in ignore]
            o_words = [w for w in our_bank.split() if len(w) > 2 and w not in ignore]
            
            is_same = False
            for bw in b_words:
                if any(bw in ow for ow in o_words) or bw in our_bank:
                    is_same = True
                    break
            if not is_same:
                for ow in o_words:
                    if any(ow in bw for bw in b_words) or ow in banka:
                        is_same = True
                        break
                        
            if is_same:
                return get_first_business_day(vade)
            else:
                return get_first_business_day(vade + timedelta(days=1))
                
        df['VADE'] = df.apply(adjust_vade, axis=1)
        
        # Tahsil bankası ile çek numarasını birleştir
        def format_our_bank(row):
            b = str(row.get('OUR_BANK', '')).strip() if pd.notna(row.get('OUR_BANK')) else ''
            c = str(row.get('CEK_NO', '')).strip() if pd.notna(row.get('CEK_NO')) else ''
            if b and c:
                return f"{b} - Çek No: {c}"
            elif c:
                return f"Çek No: {c}"
            elif b:
                return b
            return ''
            
        df['OUR_BANK'] = df.apply(format_our_bank, axis=1)
        
        if limit_date:
            target_dt = pd.to_datetime(target_date)
            df = df[df['VADE'] <= target_dt]
        df = df.sort_values(by='VADE')
        
    return df

def get_credit_details(target_date, limit_date=True):
    date_filter = f"WHERE CONVERT(VARCHAR, P.DUEDATE, 23) <= '{target_date}'" if limit_date else ""
    query = f"""
    SELECT 
        C.NAME_ AS [BANKA_KREDI],
        P.DUEDATE AS [VADE],
        MAX(P.TOTAL) AS [ANAPARA],
        MAX(P.INTTOTAL) AS [FAIZ],
        MAX(P.TOTAL + P.INTTOTAL) AS [TUTAR],
        MAX(C.TRCURR) AS [DOVIZ_TIPI]
    FROM LG_{Firma}_BNCREPAYTR P WITH(NOLOCK)
    LEFT JOIN LG_{Firma}_BNCREDITCARD C WITH(NOLOCK) 
      ON C.LOGICALREF = P.CREDITREF
    {date_filter}
    GROUP BY C.CODE, C.NAME_, P.DUEDATE
    HAVING MAX(P.TRANSTYPE) <> 1
    ORDER BY P.DUEDATE ASC
    """
    with engine.connect() as conn:
        return pd.read_sql(text(query), conn)

def get_credit_card_details(target_date, limit_date=True):
    today = datetime.now().date()
    date_filter = f"AND CONVERT(VARCHAR, KSV.MAX_DATE, 23) <= '{target_date}'" if limit_date else ""
    query = f"""
    WITH KartSonVade AS (
        SELECT 
            B.LOGICALREF AS BNACCREF,
            MAX(ISNULL(P.DATE_, F.DATE_)) AS MAX_DATE
        FROM LG_{Firma}_{Donem}_BNFLINE F WITH(NOLOCK)
        LEFT JOIN LG_{Firma}_{Donem}_PAYTRANS P WITH(NOLOCK) 
          ON P.LOGICALREF = F.SOURCEFREF
        LEFT JOIN LG_{Firma}_BANKACC B WITH(NOLOCK) 
          ON B.LOGICALREF = F.BNACCREF
        WHERE B.CODE LIKE '50.%' 
          AND F.CANCELLED = 0
        GROUP BY B.LOGICALREF
    )
    SELECT 
        B.DEFINITION_ AS [KARTI_ADI],
        KSV.MAX_DATE AS [VADE],
        SUM(CASE WHEN F.SIGN = 0 
                 THEN F.AMOUNT 
                 ELSE -F.AMOUNT END) AS [TUTAR]
    FROM LG_{Firma}_{Donem}_BNFLINE F WITH(NOLOCK)
    JOIN KartSonVade KSV 
      ON KSV.BNACCREF = F.BNACCREF
    LEFT JOIN LG_{Firma}_BANKACC B WITH(NOLOCK) 
      ON B.LOGICALREF = F.BNACCREF
    WHERE F.CANCELLED = 0
      {date_filter}
    GROUP BY B.DEFINITION_, KSV.MAX_DATE
    HAVING SUM(CASE WHEN F.SIGN = 0 
                    THEN F.AMOUNT 
                    ELSE -F.AMOUNT END) > 0.01
    ORDER BY KSV.MAX_DATE ASC
    """
    with engine.connect() as conn:
        df = pd.read_sql(text(query), conn)
        if not df.empty:
            df['VADE'] = pd.to_datetime(df['VADE'])
            df['KALAN_GUN'] = (df['VADE'].dt.date - today).apply(lambda x: x.days)
        return df

def get_bank_balances():
    query = f"""
    SELECT 
        BC.DEFINITION_ AS BANKA_ADI,
        BA.DEFINITION_ AS HESAP_ADI,
        BA.IBAN AS IBAN,
        BA.CURRENCY AS DOVIZ_TIPI,
        CASE 
            WHEN BA.CURRENCY = 0 THEN ROUND(ISNULL(SUM(CASE BN.SIGN WHEN 0 THEN BN.AMOUNT ELSE -BN.AMOUNT END), 0), 2)
            ELSE ROUND(ISNULL(SUM(CASE BN.SIGN WHEN 0 THEN BN.TRNET ELSE -BN.TRNET END), 0), 2)
        END AS BAKIYE
    FROM LG_{Firma}_BANKACC BA WITH(NOLOCK)
    INNER JOIN LG_{Firma}_BNCARD BC WITH(NOLOCK) ON BC.LOGICALREF = BA.BANKREF
    LEFT JOIN LG_{Firma}_{Donem}_BNFLINE BN WITH(NOLOCK) ON BN.BNACCREF = BA.LOGICALREF 
        AND BN.TRANSTYPE = 1 
    WHERE 
        BA.ACTIVE = 0 
        AND BA.CARDTYPE IN (1, 3) 
    GROUP BY 
        BA.CODE, 
        BA.DEFINITION_, 
        BC.CODE, 
        BC.DEFINITION_, 
        BA.CARDTYPE, 
        BA.CURRENCY,
        BA.IBAN
    HAVING 
        ROUND(ISNULL(SUM(CASE BN.SIGN WHEN 0 THEN BN.AMOUNT ELSE -BN.AMOUNT END), 0), 2) <> 0
        OR
        ROUND(ISNULL(SUM(CASE WHEN BA.CURRENCY = 0 THEN 0 ELSE (CASE BN.SIGN WHEN 0 THEN BN.TRNET ELSE -BN.TRNET END) END), 0), 2) <> 0
    ORDER BY BA.CODE
    """
    with engine.connect() as conn:
        return pd.read_sql(text(query), conn)

def get_kasa_balances(target_date=None):
    date_filter = f"AND CONVERT(VARCHAR, KS.DATE_, 23) <= '{target_date}'" if target_date else ""
    query = f"""
    SELECT 
        K.CCURRENCY AS DOVIZ_TIPI,
        CASE 
            WHEN K.CCURRENCY = 0 THEN ROUND(ISNULL(SUM(CASE WHEN KS.SIGN = 0 THEN KS.AMOUNT ELSE -KS.AMOUNT END), 0), 2)
            ELSE ROUND(ISNULL(SUM(CASE WHEN KS.SIGN = 0 THEN KS.TRNET ELSE -KS.TRNET END), 0), 2)
        END AS BAKIYE
    FROM LG_{Firma}_KSCARD K WITH(NOLOCK)
    LEFT JOIN LG_{Firma}_{Donem}_KSLINES KS WITH(NOLOCK) 
        ON KS.CARDREF = K.LOGICALREF 
       AND KS.CANCELLED = 0 
       {date_filter}
    WHERE K.ACTIVE = 0
      AND (K.CODE LIKE '100.01.%' OR K.NAME LIKE '%MERKEZ%')
    GROUP BY K.CCURRENCY
    """
    with engine.connect() as conn:
        return pd.read_sql(text(query), conn)

def get_receivables_balances():
    query = f"""
    SELECT
        SUM(CASE WHEN CL.CODE LIKE '120.01.%' THEN (CASE WHEN CLF.SIGN = 0 THEN CLF.AMOUNT ELSE -CLF.AMOUNT END) ELSE 0 END) AS BAKIYE_TL,
        SUM(CASE WHEN CL.CODE LIKE '120.05.%' AND CLF.TRCURR = 1 THEN (CASE WHEN CLF.SIGN = 0 THEN CLF.TRNET ELSE -CLF.TRNET END) ELSE 0 END) AS BAKIYE_USD,
        SUM(CASE WHEN CL.CODE LIKE '120.05.%' AND CLF.TRCURR = 20 THEN (CASE WHEN CLF.SIGN = 0 THEN CLF.TRNET ELSE -CLF.TRNET END) ELSE 0 END) AS BAKIYE_EUR
    FROM LG_{Firma}_CLCARD CL WITH(NOLOCK)
    INNER JOIN LG_{Firma}_{Donem}_CLFLINE CLF WITH(NOLOCK) ON CLF.CLIENTREF = CL.LOGICALREF
    WHERE CLF.CANCELLED = 0
      AND CL.ACTIVE = 0
      AND (CL.CODE LIKE '120.01.%' OR CL.CODE LIKE '120.05.%')
    """
    with engine.connect() as conn:
        df = pd.read_sql(text(query), conn)
        if not df.empty:
            return {
                'TL': float(df.iloc[0]['BAKIYE_TL'] or 0.0),
                'USD': float(df.iloc[0]['BAKIYE_USD'] or 0.0),
                'EUR': float(df.iloc[0]['BAKIYE_EUR'] or 0.0)
            }
        return {'TL': 0.0, 'USD': 0.0, 'EUR': 0.0}

def get_open_payables_balances():
    query = f"""
    SELECT
        -- TL balances
        SUM(CASE WHEN CL.CODE LIKE '320.01.%' THEN (CASE WHEN CLF.SIGN = 1 THEN CLF.AMOUNT ELSE -CLF.AMOUNT END) ELSE 0 END) AS AKARYAKIT_TL,
        SUM(CASE WHEN CL.CODE LIKE '320.02.%' THEN (CASE WHEN CLF.SIGN = 1 THEN CLF.AMOUNT ELSE -CLF.AMOUNT END) ELSE 0 END) AS SIGORTA_TL,
        SUM(CASE WHEN (CL.CODE LIKE '320.03.%' OR CL.CODE LIKE '320.04.%') THEN (CASE WHEN CLF.SIGN = 1 THEN CLF.AMOUNT ELSE -CLF.AMOUNT END) ELSE 0 END) AS SANAYI_TL,
        SUM(CASE WHEN (CL.CODE LIKE '320.05.%' OR CL.CODE LIKE '320.06.%') THEN (CASE WHEN CLF.SIGN = 1 THEN CLF.AMOUNT ELSE -CLF.AMOUNT END) ELSE 0 END) AS NAVLUN_TL,
        
        -- USD balances
        SUM(CASE WHEN (CL.CODE LIKE '320.01.%') AND CLF.TRCURR = 1 THEN (CASE WHEN CLF.SIGN = 1 THEN CLF.TRNET ELSE -CLF.TRNET END) ELSE 0 END) AS AKARYAKIT_USD,
        SUM(CASE WHEN (CL.CODE LIKE '320.02.%') AND CLF.TRCURR = 1 THEN (CASE WHEN CLF.SIGN = 1 THEN CLF.TRNET ELSE -CLF.TRNET END) ELSE 0 END) AS SIGORTA_USD,
        SUM(CASE WHEN (CL.CODE LIKE '320.03.%' OR CL.CODE LIKE '320.04.%') AND CLF.TRCURR = 1 THEN (CASE WHEN CLF.SIGN = 1 THEN CLF.TRNET ELSE -CLF.TRNET END) ELSE 0 END) AS SANAYI_USD,
        SUM(CASE WHEN (CL.CODE LIKE '320.05.%' OR CL.CODE LIKE '320.06.%') AND CLF.TRCURR = 1 THEN (CASE WHEN CLF.SIGN = 1 THEN CLF.TRNET ELSE -CLF.TRNET END) ELSE 0 END) AS NAVLUN_USD,
        
        -- EUR balances
        SUM(CASE WHEN (CL.CODE LIKE '320.01.%') AND CLF.TRCURR = 20 THEN (CASE WHEN CLF.SIGN = 1 THEN CLF.TRNET ELSE -CLF.TRNET END) ELSE 0 END) AS AKARYAKIT_EUR,
        SUM(CASE WHEN (CL.CODE LIKE '320.02.%') AND CLF.TRCURR = 20 THEN (CASE WHEN CLF.SIGN = 1 THEN CLF.TRNET ELSE -CLF.TRNET END) ELSE 0 END) AS SIGORTA_EUR,
        SUM(CASE WHEN (CL.CODE LIKE '320.03.%' OR CL.CODE LIKE '320.04.%') AND CLF.TRCURR = 20 THEN (CASE WHEN CLF.SIGN = 1 THEN CLF.TRNET ELSE -CLF.TRNET END) ELSE 0 END) AS SANAYI_EUR,
        SUM(CASE WHEN (CL.CODE LIKE '320.05.%' OR CL.CODE LIKE '320.06.%') AND CLF.TRCURR = 20 THEN (CASE WHEN CLF.SIGN = 1 THEN CLF.TRNET ELSE -CLF.TRNET END) ELSE 0 END) AS NAVLUN_EUR
    FROM LG_{Firma}_CLCARD CL WITH(NOLOCK)
    INNER JOIN LG_{Firma}_{Donem}_CLFLINE CLF WITH(NOLOCK) ON CLF.CLIENTREF = CL.LOGICALREF
    WHERE CLF.CANCELLED = 0
      AND CL.ACTIVE = 0
      AND (
           CL.CODE LIKE '320.01.%' OR 
           CL.CODE LIKE '320.02.%' OR 
           CL.CODE LIKE '320.03.%' OR 
           CL.CODE LIKE '320.04.%' OR 
           CL.CODE LIKE '320.05.%' OR 
           CL.CODE LIKE '320.06.%'
          )
    """
    try:
        with engine.connect() as conn:
            df = pd.read_sql(text(query), conn)
            if not df.empty:
                r = df.iloc[0]
                return {
                    'akaryakit': {
                        'TL': float(r['AKARYAKIT_TL'] or 0.0),
                        'USD': float(r['AKARYAKIT_USD'] or 0.0),
                        'EUR': float(r['AKARYAKIT_EUR'] or 0.0)
                    },
                    'sigorta': {
                        'TL': float(r['SIGORTA_TL'] or 0.0),
                        'USD': float(r['SIGORTA_USD'] or 0.0),
                        'EUR': float(r['SIGORTA_EUR'] or 0.0)
                    },
                    'sanayi': {
                        'TL': float(r['SANAYI_TL'] or 0.0),
                        'USD': float(r['SANAYI_USD'] or 0.0),
                        'EUR': float(r['SANAYI_EUR'] or 0.0)
                    },
                    'navlun': {
                        'TL': float(r['NAVLUN_TL'] or 0.0),
                        'USD': float(r['NAVLUN_USD'] or 0.0),
                        'EUR': float(r['NAVLUN_EUR'] or 0.0)
                    }
                }
    except Exception as e:
        print(f"Error getting open payables balances: {e}")
    
    empty_val = {'TL': 0.0, 'USD': 0.0, 'EUR': 0.0}
    return {
        'akaryakit': empty_val,
        'sigorta': empty_val,
        'sanayi': empty_val,
        'navlun': empty_val
    }

def get_virman_lists(target_date):
    """
    Logo Tiger Banka Virman Fişlerini (TRCODE=2, MODULENR=7) çeker.
    Giren bankaları (SIGN=0) sol liste (incoming_virman) için,
    Çıkan bankaları (SIGN=1) sağ liste (outgoing_virman) için hazırlar.
    Karşı banka adını (KARSI_BANKA) her iki tarafa da otomatik iliştirir.
    """
    query = f"""
    SELECT
        BNFLINE.LOGICALREF,
        BNFLINE.SOURCEFREF,
        BNFICHE.FICHENO,
        BNFLINE.DATE_ AS TARIH,
        BNFLINE.SIGN,
        BNFLINE.AMOUNT AS TL_TUTAR,
        BNFLINE.TRNET AS DOVIZLI_TUTAR,
        CASE BANKACC.CURRENCY WHEN 0 THEN 'TL' WHEN 1 THEN 'USD' WHEN 20 THEN 'EUR' ELSE ISNULL(L_CURRENCYLIST.CURCODE, '') END AS HESAP_DOVIZI_RAPOR,
        BANKACC.DEFINITION_ AS HESAP_ACIKLAMASI,
        BANKACC.CODE AS HESAP_KODU,
        BANKACC.ACCOUNTNO AS HESAP_NO,
        BNFLINE.LINEEXP AS SATIR_ACIKLAMASI,
        ISNULL(BNFICHE.GENEXP1, '') +' '+ ISNULL(BNFICHE.GENEXP2, '') AS FIS_ACIKLAMASI
    FROM LG_{Firma}_{Donem}_BNFLINE BNFLINE WITH(NOLOCK)
    LEFT JOIN LG_{Firma}_{Donem}_BNFICHE BNFICHE WITH(NOLOCK) ON BNFICHE.LOGICALREF = BNFLINE.SOURCEFREF
    LEFT JOIN LG_{Firma}_BANKACC BANKACC WITH(NOLOCK) ON BANKACC.LOGICALREF = BNFLINE.BNACCREF
    LEFT OUTER JOIN L_CURRENCYLIST WITH(NOLOCK) ON L_CURRENCYLIST.CURTYPE = BANKACC.CURRENCY
    WHERE BNFLINE.MODULENR = 7
      AND BNFLINE.TRCODE = 2
      AND NOT (BANKACC.CODE LIKE '50.%' OR BANKACC.CARDTYPE IN (5, 6))
      AND CONVERT(VARCHAR, BNFLINE.DATE_, 23) = '{target_date}'
    ORDER BY BNFLINE.SOURCEFREF, BNFLINE.SIGN
    """
    try:
        with engine.connect() as conn:
            df = pd.read_sql(text(query), conn)
            
        incoming_virman = []
        outgoing_virman = []
        if not df.empty:
            for sf_ref, group in df.groupby('SOURCEFREF'):
                inc_rows = group[group['SIGN'] == 0].to_dict('records')
                out_rows = group[group['SIGN'] == 1].to_dict('records')
                
                out_bank_names = ", ".join([clean_bank_name(r['HESAP_ACIKLAMASI']) for r in out_rows if r['HESAP_ACIKLAMASI']])
                inc_bank_names = ", ".join([clean_bank_name(r['HESAP_ACIKLAMASI']) for r in inc_rows if r['HESAP_ACIKLAMASI']])
                
                for r in inc_rows:
                    r_copy = dict(r)
                    r_copy['HESAP_ACIKLAMASI'] = clean_bank_name(r_copy['HESAP_ACIKLAMASI'])
                    r_copy['KARSI_BANKA'] = out_bank_names or '-'
                    r_copy['FIS_TURU'] = 'Banka Virman Girişi'
                    r_copy['FICHENO'] = str(r_copy.get('FICHENO') or '').strip()
                    if r_copy['FICHENO'] == 'nan':
                        r_copy['FICHENO'] = ''
                    incoming_virman.append(r_copy)
                    
                for r in out_rows:
                    r_copy = dict(r)
                    r_copy['HESAP_ACIKLAMASI'] = clean_bank_name(r_copy['HESAP_ACIKLAMASI'])
                    r_copy['KARSI_BANKA'] = inc_bank_names or '-'
                    r_copy['FIS_TURU'] = 'Banka Virman Çıkışı'
                    r_copy['FICHENO'] = str(r_copy.get('FICHENO') or '').strip()
                    if r_copy['FICHENO'] == 'nan':
                        r_copy['FICHENO'] = ''
                    outgoing_virman.append(r_copy)
                    
        return incoming_virman, outgoing_virman
    except Exception as e:
        print(f"get_virman_lists hata: {e}")
        return [], []

def get_incoming_transfers(target_date, filter_virman=False):
    virman_bank_filter = "AND BNFLINE.TRCODE NOT IN (2)" if filter_virman else ""
    virman_kasa_filter = "AND K.TRCODE NOT IN (61, 62, 63)" if filter_virman else ""

    query = f"""
    SELECT DISTINCT
        BNFLINE.LOGICALREF,
        CASE BANKACC.CARDTYPE
            WHEN 1 THEN 'Banka Ticari'
            WHEN 2 THEN 'Banka Kredi'
            WHEN 3 THEN 'Banka Dövizli Ticari'
            WHEN 4 THEN 'Banka Dövizli Kredi'
            WHEN 5 THEN 'Banka Kredi Kartı'
            WHEN 6 THEN 'Banka Dövizli Kredi Kartı'
            ELSE 'Banka'
        END AS HESAP_TURU_RAPOR,
        BANKACC.CODE AS HESAP_KODU,
        BANKACC.DEFINITION_ AS HESAP_ACIKLAMASI,
        BANKACC.ACCOUNTNO AS HESAP_NO,
        CASE BANKACC.CURRENCY WHEN 0 THEN 'TL' WHEN 1 THEN 'USD' WHEN 20 THEN 'EUR' ELSE ISNULL(L_CURRENCYLIST.CURCODE, '') END AS HESAP_DOVIZI_RAPOR,
        BNFICHE.FICHENO,
        BNFLINE.DATE_ AS TARIH,
        BNFLINE.LINEEXP AS SATIR_ACIKLAMASI,
        ISNULL(BNFICHE.GENEXP1, '') +' '+ ISNULL(BNFICHE.GENEXP2, '') +' '+ ISNULL(BNFICHE.GENEXP3, '') +' '+ ISNULL(BNFICHE.GENEXP4, '') AS FIS_ACIKLAMASI,
        ISNULL((CASE BNFLINE.SIGN WHEN 0 THEN BNFLINE.TRNET ELSE BNFLINE.TRNET * (-1) END),0) AS DOVIZLI_TUTAR,
        ISNULL((CASE BNFLINE.SIGN WHEN 0 THEN BNFLINE.AMOUNT ELSE BNFLINE.AMOUNT * (-1) END),0) AS TL_TUTAR,
        CL.CODE AS CARI_KOD,
        CL.DEFINITION_ AS CARI_UNVAN,
        CASE 
            WHEN BANKACC.CODE LIKE '50.%' OR BANKACC.CARDTYPE IN (5, 6) THEN
                CASE BNFLINE.TRCODE
                    WHEN 1 THEN 'Kredi Kartı Harcaması'
                    WHEN 2 THEN 'Kredi Kartı Ödemesi'
                    ELSE 'Kredi Kartı İşlemi'
                END
            ELSE
                ISNULL([dbo].[fn_trcode] ('Bnfiche', BNFLINE.TRCODE, '', ''), 'Banka İşlemi')
        END AS FIS_TURU
    FROM LG_{Firma}_{Donem}_BNFLINE BNFLINE WITH(NOLOCK)
    LEFT OUTER JOIN LG_{Firma}_{Donem}_BNFICHE BNFICHE WITH(NOLOCK) ON BNFICHE.LOGICALREF=BNFLINE.SOURCEFREF AND BNFLINE.MODULENR=7 AND BNFLINE.TRCODE=BNFICHE.TRCODE
    LEFT OUTER JOIN LG_{Firma}_BANKACC BANKACC WITH(NOLOCK) ON BNFLINE.BNACCREF=BANKACC.LOGICALREF
    LEFT JOIN LG_{Firma}_CLCARD CL WITH(NOLOCK) ON BNFLINE.CLIENTREF=CL.LOGICALREF
    LEFT OUTER JOIN L_CURRENCYLIST WITH(NOLOCK) ON L_CURRENCYLIST.CURTYPE=BANKACC.CURRENCY
    WHERE BNFLINE.SIGN = 0
      AND NOT (BANKACC.CODE LIKE '50.%' OR BANKACC.CARDTYPE IN (5, 6))
      {virman_bank_filter}
      AND CONVERT(VARCHAR, BNFLINE.DATE_, 23) = '{target_date}'

    UNION ALL

    SELECT
        K.LOGICALREF,
        'Kasa' AS HESAP_TURU_RAPOR,
        KS.CODE AS HESAP_KODU,
        KS.NAME AS HESAP_ACIKLAMASI,
        'Kasa' AS HESAP_NO,
        CASE K.TRCURR WHEN 0 THEN 'TL' WHEN 1 THEN 'USD' WHEN 20 THEN 'EUR' ELSE ISNULL(L_CURRENCYLIST.CURCODE, '') END AS HESAP_DOVIZI_RAPOR,
        K.FICHENO,
        K.DATE_ AS TARIH,
        K.LINEEXP AS SATIR_ACIKLAMASI,
        '' AS FIS_ACIKLAMASI,
        ISNULL(K.TRNET, 0) AS DOVIZLI_TUTAR,
        ISNULL(K.AMOUNT, 0) AS TL_TUTAR,
        CL.CODE AS CARI_KOD,
        ISNULL(NULLIF(CL.DEFINITION_, ''), ISNULL(NULLIF(K.CUSTTITLE, ''), '')) AS CARI_UNVAN,
        [dbo].[fn_trcode] ('Kslines', K.TRCODE, '', '') AS FIS_TURU
    FROM LG_{Firma}_{Donem}_KSLINES K WITH(NOLOCK)
    LEFT JOIN LG_{Firma}_KSCARD KS WITH(NOLOCK) ON KS.LOGICALREF = K.CARDREF
    LEFT JOIN LG_{Firma}_CLCARD CL WITH(NOLOCK) ON K.VCARDREF = CL.LOGICALREF
    LEFT OUTER JOIN L_CURRENCYLIST WITH(NOLOCK) ON L_CURRENCYLIST.CURTYPE = K.TRCURR
    WHERE K.SIGN = 0
      AND K.CANCELLED = 0
      {virman_kasa_filter}
      AND CONVERT(VARCHAR, K.DATE_, 23) = '{target_date}'
    ORDER BY LOGICALREF
    """
    try:
        with engine.connect() as conn:
            df = pd.read_sql(text(query), conn)
            
        records = []
        if not df.empty:
            for idx, r in df.iterrows():
                hesap_kodu_str = str(r['HESAP_KODU'] or '').strip()
                hesap_turu_str = str(r['HESAP_TURU_RAPOR'] or '').lower()
                fis_turu_str = str(r['FIS_TURU'] or '').lower()
                satir_str = str(r['SATIR_ACIKLAMASI'] or '').lower()
                fis_str = str(r['FIS_ACIKLAMASI'] or '').lower()
                
                # Firma kredi kartı ödemeleri/hareketleri gelen tahsilat/giriş olarak değerlendirilmemelidir
                if hesap_kodu_str.startswith('50.') or 'kredi kart' in fis_turu_str or 'kredi kart' in hesap_turu_str:
                    continue

                if filter_virman:
                    if 'virman' in fis_turu_str or 'virman' in satir_str or 'virman' in fis_str:
                        continue

                doviz = str(r['HESAP_DOVIZI_RAPOR']).strip() if pd.notna(r['HESAP_DOVIZI_RAPOR']) else 'TL'
                if not doviz or doviz.lower() == 'nan':
                    doviz = 'TL'
                records.append({
                    'LOGICALREF': r['LOGICALREF'],
                    'HESAP_TURU_RAPOR': r['HESAP_TURU_RAPOR'] if pd.notna(r['HESAP_TURU_RAPOR']) else '',
                    'HESAP_KODU': r['HESAP_KODU'] if pd.notna(r['HESAP_KODU']) else '',
                    'HESAP_ACIKLAMASI': r['HESAP_ACIKLAMASI'] if pd.notna(r['HESAP_ACIKLAMASI']) else '',
                    'HESAP_NO': r['HESAP_NO'] if pd.notna(r['HESAP_NO']) else '',
                    'HESAP_DOVIZI_RAPOR': doviz,
                    'FICHENO': r['FICHENO'] if pd.notna(r['FICHENO']) else '',
                    'TARIH': r['TARIH'],
                    'SATIR_ACIKLAMASI': r['SATIR_ACIKLAMASI'] or '',
                    'FIS_ACIKLAMASI': r['FIS_ACIKLAMASI'] or '',
                    'DOVIZLI_TUTAR': float(r['DOVIZLI_TUTAR'] or 0.0),
                    'TL_TUTAR': float(r['TL_TUTAR'] or 0.0),
                    'CARI_KOD': r['CARI_KOD'] or '',
                    'CARI_UNVAN': r['CARI_UNVAN'] or '',
                    'FIS_TURU': r['FIS_TURU'] or ''
                })
        return records
    except Exception as e:
        print(f"Error getting incoming transfers: {e}")
        return []

def aggregate_navlun_plate_transfers(records):
    if not records:
        return []
    try:
        plate_pattern = re.compile(r'/\s*([A-Za-z0-9\s\-]{3,14})\s*/')
        
        regular_records = []
        navlun_records = []
        
        for r in records:
            satir = str(r.get('SATIR_ACIKLAMASI', '') or '')
            fis = str(r.get('FIS_ACIKLAMASI', '') or '')
            cari = str(r.get('CARI_UNVAN', '') or '')
            hesap = str(r.get('HESAP_ACIKLAMASI', '') or '')
            combined = f"{satir} {fis} {cari} {hesap}"
            
            is_plate = False
            matches = plate_pattern.findall(combined)
            for m in matches:
                clean_m = m.strip().replace(' ', '')
                if len(clean_m) >= 4 and any(c.isdigit() for c in clean_m) and any(c.isalpha() for c in clean_m):
                    is_plate = True
                    break
                    
            if not is_plate and 'navlun' in combined.lower() and '/' in combined:
                is_plate = True
                
            if is_plate:
                navlun_records.append(r)
            else:
                regular_records.append(r)
                
        if navlun_records:
            tl_navlun = [r for r in navlun_records if r.get('HESAP_DOVIZI_RAPOR') == 'TL']
            usd_navlun = [r for r in navlun_records if r.get('HESAP_DOVIZI_RAPOR') == 'USD']
            eur_navlun = [r for r in navlun_records if r.get('HESAP_DOVIZI_RAPOR') == 'EUR']
            
            sample_date = navlun_records[0].get('TARIH')
            
            if tl_navlun:
                tl_sum = sum(float(r.get('TL_TUTAR', 0.0)) for r in tl_navlun)
                regular_records.append({
                    'LOGICALREF': 999901,
                    'HESAP_TURU_RAPOR': 'Banka Ticari',
                    'HESAP_KODU': 'NAVLUN',
                    'HESAP_ACIKLAMASI': 'NAVLUN ÖDEMELERİ',
                    'HESAP_NO': '-',
                    'HESAP_DOVIZI_RAPOR': 'TL',
                    'FICHENO': '-',
                    'TARIH': sample_date,
                    'SATIR_ACIKLAMASI': f"Toplu Navlun Ödemeleri ({len(tl_navlun)} Araç/Plaka)",
                    'FIS_ACIKLAMASI': 'Toplu Navlun Ödemeleri',
                    'DOVIZLI_TUTAR': 0.0,
                    'TL_TUTAR': tl_sum,
                    'CARI_KOD': '-',
                    'CARI_UNVAN': 'NAVLUN ÖDEMELERİ',
                    'FIS_TURU': 'Giden Havale'
                })
                
            if usd_navlun:
                usd_sum = sum(float(r.get('DOVIZLI_TUTAR', 0.0)) for r in usd_navlun)
                regular_records.append({
                    'LOGICALREF': 999902,
                    'HESAP_TURU_RAPOR': 'Banka Ticari',
                    'HESAP_KODU': 'NAVLUN',
                    'HESAP_ACIKLAMASI': 'NAVLUN ÖDEMELERİ',
                    'HESAP_NO': '-',
                    'HESAP_DOVIZI_RAPOR': 'USD',
                    'FICHENO': '-',
                    'TARIH': sample_date,
                    'SATIR_ACIKLAMASI': f"Toplu Navlun Ödemeleri ({len(usd_navlun)} Araç/Plaka)",
                    'FIS_ACIKLAMASI': 'Toplu Navlun Ödemeleri',
                    'DOVIZLI_TUTAR': usd_sum,
                    'TL_TUTAR': 0.0,
                    'CARI_KOD': '-',
                    'CARI_UNVAN': 'NAVLUN ÖDEMELERİ',
                    'FIS_TURU': 'Giden Havale'
                })
                
            if eur_navlun:
                eur_sum = sum(float(r.get('DOVIZLI_TUTAR', 0.0)) for r in eur_navlun)
                regular_records.append({
                    'LOGICALREF': 999903,
                    'HESAP_TURU_RAPOR': 'Banka Ticari',
                    'HESAP_KODU': 'NAVLUN',
                    'HESAP_ACIKLAMASI': 'NAVLUN ÖDEMELERİ',
                    'HESAP_NO': '-',
                    'HESAP_DOVIZI_RAPOR': 'EUR',
                    'FICHENO': '-',
                    'TARIH': sample_date,
                    'SATIR_ACIKLAMASI': f"Toplu Navlun Ödemeleri ({len(eur_navlun)} Araç/Plaka)",
                    'FIS_ACIKLAMASI': 'Toplu Navlun Ödemeleri',
                    'DOVIZLI_TUTAR': eur_sum,
                    'TL_TUTAR': 0.0,
                    'CARI_KOD': '-',
                    'CARI_UNVAN': 'NAVLUN ÖDEMELERİ',
                    'FIS_TURU': 'Giden Havale'
                })

        return regular_records
    except Exception as err:
        print(f"Error in aggregate_navlun_plate_transfers: {err}")
        return records

def aggregate_harcirah_transfers(records):
    if not records:
        return []
    try:
        regular_records = []
        harcirah_records = []
        
        for r in records:
            if r.get('HESAP_TURU_RAPOR') == 'Kasa':
                regular_records.append(r)
                continue
                
            satir = str(r.get('SATIR_ACIKLAMASI', '') or '').lower()
            fis = str(r.get('FIS_ACIKLAMASI', '') or '').lower()
            cari = str(r.get('CARI_UNVAN', '') or '').lower()
            hesap = str(r.get('HESAP_ACIKLAMASI', '') or '').lower()
            combined = f"{satir} {fis} {cari} {hesap}"
            
            is_harcirah = False
            if ('haftal' in combined or 'haked' in combined) and ('harc' in combined and 'rah' in combined):
                is_harcirah = True
            elif 'haftalık harcırah' in combined or 'haftalik harcirah' in combined or 'harcırah hakediş' in combined or 'harcirah hakedis' in combined:
                is_harcirah = True
                
            if is_harcirah:
                harcirah_records.append(r)
            else:
                regular_records.append(r)
                
        if harcirah_records:
            by_currency = {}
            for r in harcirah_records:
                dov = r.get('HESAP_DOVIZI_RAPOR', 'TL')
                if dov not in by_currency:
                    by_currency[dov] = []
                by_currency[dov].append(r)
                
            for dov, group in by_currency.items():
                sample_date = group[0].get('TARIH')
                tl_sum = sum(float(r.get('TL_TUTAR', 0.0)) for r in group)
                dov_sum = sum(float(r.get('DOVIZLI_TUTAR', 0.0)) for r in group)
                regular_records.append({
                    'LOGICALREF': 999951,
                    'HESAP_TURU_RAPOR': 'Banka Ticari',
                    'HESAP_KODU': 'HARCIRAH',
                    'HESAP_ACIKLAMASI': 'HAFTALIK HARCIRAH ÖDEMELERİ',
                    'HESAP_NO': '-',
                    'HESAP_DOVIZI_RAPOR': dov,
                    'FICHENO': '-',
                    'TARIH': sample_date,
                    'SATIR_ACIKLAMASI': f"Toplu Haftalık Harcırah Ödemeleri ({len(group)} Personel/Şoför)",
                    'FIS_ACIKLAMASI': 'Toplu Haftalık Harcırah Ödemeleri',
                    'DOVIZLI_TUTAR': dov_sum,
                    'TL_TUTAR': tl_sum,
                    'CARI_KOD': '-',
                    'CARI_UNVAN': 'HAFTALIK HARCIRAH ÖDEMELERİ',
                    'FIS_TURU': 'Giden Havale'
                })
        return regular_records
    except Exception as err:
        print(f"Error in aggregate_harcirah_transfers: {err}")
        return records

def get_outgoing_transfers(target_date, filter_virman=False, aggregate_navlun=False):
    virman_bank_filter = "AND BNFLINE.TRCODE NOT IN (2)" if filter_virman else ""
    virman_kasa_filter = "AND K.TRCODE NOT IN (61, 62, 63)" if filter_virman else ""

    query = f"""
    SELECT DISTINCT
        BNFLINE.LOGICALREF,
        CASE BANKACC.CARDTYPE
            WHEN 1 THEN 'Banka Ticari'
            WHEN 2 THEN 'Banka Kredi'
            WHEN 3 THEN 'Banka Dövizli Ticari'
            WHEN 4 THEN 'Banka Dövizli Kredi'
            WHEN 5 THEN 'Banka Kredi Kartı'
            WHEN 6 THEN 'Banka Dövizli Kredi Kartı'
            ELSE 'Banka'
        END AS HESAP_TURU_RAPOR,
        BANKACC.CODE AS HESAP_KODU,
        BANKACC.DEFINITION_ AS HESAP_ACIKLAMASI,
        BANKACC.ACCOUNTNO AS HESAP_NO,
        CASE BANKACC.CURRENCY WHEN 0 THEN 'TL' WHEN 1 THEN 'USD' WHEN 20 THEN 'EUR' ELSE ISNULL(L_CURRENCYLIST.CURCODE, '') END AS HESAP_DOVIZI_RAPOR,
        BNFICHE.FICHENO,
        BNFLINE.DATE_ AS TARIH,
        BNFLINE.LINEEXP AS SATIR_ACIKLAMASI,
        ISNULL(BNFICHE.GENEXP1, '') +' '+ ISNULL(BNFICHE.GENEXP2, '') +' '+ ISNULL(BNFICHE.GENEXP3, '') +' '+ ISNULL(BNFICHE.GENEXP4, '') AS FIS_ACIKLAMASI,
        ISNULL((CASE BNFLINE.SIGN WHEN 0 THEN BNFLINE.TRNET ELSE BNFLINE.TRNET * (-1) END),0) AS DOVIZLI_TUTAR,
        ISNULL((CASE BNFLINE.SIGN WHEN 0 THEN BNFLINE.AMOUNT ELSE BNFLINE.AMOUNT * (-1) END),0) AS TL_TUTAR,
        CL.CODE AS CARI_KOD,
        CL.DEFINITION_ AS CARI_UNVAN,
        CASE 
            WHEN BANKACC.CODE LIKE '50.%' OR BANKACC.CARDTYPE IN (5, 6) THEN
                CASE BNFLINE.TRCODE
                    WHEN 1 THEN 'Kredi Kartı Harcaması'
                    WHEN 2 THEN 'Kredi Kartı Ödemesi'
                    ELSE 'Kredi Kartı İşlemi'
                END
            ELSE
                ISNULL([dbo].[fn_trcode] ('Bnfiche', BNFLINE.TRCODE, '', ''), 'Banka İşlemi')
        END AS FIS_TURU
    FROM LG_{Firma}_{Donem}_BNFLINE BNFLINE WITH(NOLOCK)
    LEFT OUTER JOIN LG_{Firma}_{Donem}_BNFICHE BNFICHE WITH(NOLOCK) ON BNFICHE.LOGICALREF=BNFLINE.SOURCEFREF AND BNFLINE.MODULENR=7 AND BNFLINE.TRCODE=BNFICHE.TRCODE
    LEFT OUTER JOIN LG_{Firma}_BANKACC BANKACC WITH(NOLOCK) ON BNFLINE.BNACCREF=BANKACC.LOGICALREF
    LEFT JOIN LG_{Firma}_CLCARD CL WITH(NOLOCK) ON BNFLINE.CLIENTREF=CL.LOGICALREF
    LEFT OUTER JOIN L_CURRENCYLIST WITH(NOLOCK) ON L_CURRENCYLIST.CURTYPE=BANKACC.CURRENCY
    WHERE BNFLINE.SIGN = 1
      {virman_bank_filter}
      AND CONVERT(VARCHAR, BNFLINE.DATE_, 23) = '{target_date}'

    UNION ALL

    SELECT
        K.LOGICALREF,
        'Kasa' AS HESAP_TURU_RAPOR,
        KS.CODE AS HESAP_KODU,
        KS.NAME AS HESAP_ACIKLAMASI,
        'Kasa' AS HESAP_NO,
        CASE K.TRCURR WHEN 0 THEN 'TL' WHEN 1 THEN 'USD' WHEN 20 THEN 'EUR' ELSE ISNULL(L_CURRENCYLIST.CURCODE, '') END AS HESAP_DOVIZI_RAPOR,
        K.FICHENO,
        K.DATE_ AS TARIH,
        K.LINEEXP AS SATIR_ACIKLAMASI,
        '' AS FIS_ACIKLAMASI,
        ISNULL(K.TRNET, 0) * (-1) AS DOVIZLI_TUTAR,
        ISNULL(K.AMOUNT, 0) * (-1) AS TL_TUTAR,
        CL.CODE AS CARI_KOD,
        ISNULL(NULLIF(CL.DEFINITION_, ''), ISNULL(NULLIF(K.CUSTTITLE, ''), '')) AS CARI_UNVAN,
        [dbo].[fn_trcode] ('Kslines', K.TRCODE, '', '') AS FIS_TURU
    FROM LG_{Firma}_{Donem}_KSLINES K WITH(NOLOCK)
    LEFT JOIN LG_{Firma}_KSCARD KS WITH(NOLOCK) ON KS.LOGICALREF = K.CARDREF
    LEFT JOIN LG_{Firma}_CLCARD CL WITH(NOLOCK) ON K.VCARDREF = CL.LOGICALREF
    LEFT OUTER JOIN L_CURRENCYLIST WITH(NOLOCK) ON L_CURRENCYLIST.CURTYPE = K.TRCURR
    WHERE K.SIGN = 1
      AND K.CANCELLED = 0
      {virman_kasa_filter}
      AND CONVERT(VARCHAR, K.DATE_, 23) = '{target_date}'
    ORDER BY LOGICALREF
    """
    try:
        with engine.connect() as conn:
            df = pd.read_sql(text(query), conn)
            
        records = []
        if not df.empty:
            for idx, r in df.iterrows():
                if filter_virman:
                    fis_turu_str = str(r['FIS_TURU'] or '').lower()
                    satir_str = str(r['SATIR_ACIKLAMASI'] or '').lower()
                    fis_str = str(r['FIS_ACIKLAMASI'] or '').lower()
                    if 'virman' in fis_turu_str or 'virman' in satir_str or 'virman' in fis_str:
                        continue

                doviz = str(r['HESAP_DOVIZI_RAPOR']).strip() if pd.notna(r['HESAP_DOVIZI_RAPOR']) else 'TL'
                if not doviz or doviz.lower() == 'nan':
                    doviz = 'TL'
                records.append({
                    'LOGICALREF': r['LOGICALREF'],
                    'HESAP_TURU_RAPOR': r['HESAP_TURU_RAPOR'] if pd.notna(r['HESAP_TURU_RAPOR']) else '',
                    'HESAP_KODU': r['HESAP_KODU'] if pd.notna(r['HESAP_KODU']) else '',
                    'HESAP_ACIKLAMASI': r['HESAP_ACIKLAMASI'] if pd.notna(r['HESAP_ACIKLAMASI']) else '',
                    'HESAP_NO': r['HESAP_NO'] if pd.notna(r['HESAP_NO']) else '',
                    'HESAP_DOVIZI_RAPOR': doviz,
                    'FICHENO': r['FICHENO'] if pd.notna(r['FICHENO']) else '',
                    'TARIH': r['TARIH'],
                    'SATIR_ACIKLAMASI': r['SATIR_ACIKLAMASI'] or '',
                    'FIS_ACIKLAMASI': r['FIS_ACIKLAMASI'] or '',
                    'DOVIZLI_TUTAR': abs(float(r['DOVIZLI_TUTAR'] or 0.0)),
                    'TL_TUTAR': abs(float(r['TL_TUTAR'] or 0.0)),
                    'CARI_KOD': r['CARI_KOD'] or '',
                    'CARI_UNVAN': r['CARI_UNVAN'] or '',
                    'FIS_TURU': r['FIS_TURU'] or ''
                })
        if aggregate_navlun:
            records = aggregate_navlun_plate_transfers(records)
        return aggregate_harcirah_transfers(records)
    except Exception as e:
        print(f"Error getting outgoing transfers: {e}")
        return []

# --- TCMB DÖVİZ KURLARI CACHE VE ÇEKME ---
_TCMB_RATES_CACHE = {
    'rates': None,
    'last_updated': None
}

def get_tcmb_rates():
    global _TCMB_RATES_CACHE
    import time
    now = time.time()
    
    # 15 dakika cache
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

def get_unbilled_invoices_totals():
    query = """
    WITH LineTotals AS (
        SELECT 
            SHK.KapAdedi * SHK.KDVsizBirimFiyat AS NetAmount,
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
        WHERE 
            FT.FaturaTipiKodu = 2 
            AND CH.CariHesapKodu NOT LIKE '53590' 
            AND FT.ResmiMi = '1' 
            AND (FT.FaturaNo IS NULL OR FT.FaturaNo = '') 
            AND FT.FaturaTarihi >= '2026-01-01'
    ),
    LineCalculations AS (
        SELECT 
            DovizTipi,
            NetAmount + (NetAmount * KdvOrani / 100.0) - (NetAmount * KdvOrani / 100.0 * TevkifatOrani) AS LineTotal
        FROM LineTotals
    )
    SELECT 
        DovizTipi,
        SUM(LineTotal) AS ToplamTutar
    FROM LineCalculations
    GROUP BY DovizTipi
    """
    totals = {'TL': 0.0, 'USD': 0.0, 'EUR': 0.0}
    try:
        with engine.connect() as conn:
            df = pd.read_sql(text(query), conn)
            if not df.empty:
                for idx, r in df.iterrows():
                    doviz = r['DovizTipi']
                    tutar = float(r['ToplamTutar'] or 0.0)
                    if doviz in totals:
                        totals[doviz] = tutar
    except Exception as e:
        print(f"Error getting unbilled invoices: {e}")
    return totals

# --- MANUEL ÖDEMELER PERSISTENCE VE YÖNETİMİ ---

CUSTOM_PAYMENTS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'custom_payments.json')
if not os.path.exists(CUSTOM_PAYMENTS_FILE):
    alt_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'custom_payments.json')
    if os.path.exists(alt_file):
        CUSTOM_PAYMENTS_FILE = alt_file

def load_custom_payments():
    # 1. Önce JSON dosyasından oku (varsa)
    if os.path.exists(CUSTOM_PAYMENTS_FILE):
        try:
            with open(CUSTOM_PAYMENTS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            payments = []
            for idx, r in enumerate(data, 1):
                date_val = r.get('date') or r.get('tarih') or ''
                category_val = r.get('category') or r.get('kategori') or 'Diğer'
                exp_val = r.get('explanation') or r.get('aciklama') or ''
                amt_val = float(r.get('amount') or r.get('tutar') or 0.0)
                doviz_val = r.get('doviz') or 'TL'
                
                cc_pay = False
                if r.get('credit_card_pay') in (True, 1, '1', 'true', 'True'):
                    cc_pay = True
                elif r.get('kredi_karti') in (True, 1, '1', 'true', 'True'):
                    cc_pay = True
                
                odendi_val = 0
                if r.get('odendi') in (True, 1, '1', 'true', 'True'):
                    odendi_val = 1
                
                payments.append({
                    'id': r.get('id', idx),
                    'category': category_val,
                    'kategori': category_val,
                    'date': str(date_val),
                    'tarih': str(date_val),
                    'explanation': exp_val,
                    'aciklama': exp_val,
                    'amount': amt_val,
                    'tutar': amt_val,
                    'doviz': doviz_val,
                    'odendi': odendi_val,
                    'credit_card_pay': cc_pay
                })
            return payments
        except Exception as e:
            print(f"Error loading custom payments from JSON file: {e}")

    # 2. JSON dosyası yoksa veya okunamadıysa veritabanından dene (fallback)
    try:
        with engine_nexlog.connect() as conn:
            rows = conn.execute(text("SELECT id, tarih, kategori, aciklama, tutar, doviz, odendi, kredi_karti FROM dbo.manuel_veri_giris ORDER BY id")).fetchall()
            
            payments = []
            for r in rows:
                tarih_str = ''
                if r[1]:
                    if isinstance(r[1], str):
                        tarih_str = r[1]
                    else:
                        tarih_str = r[1].strftime('%Y-%m-%d')
                
                cc_pay = False
                if r[7]:
                    if str(r[7]).strip() in ('1', 'True', 'true'):
                        cc_pay = True
                
                cat_val = r[2] or 'Diğer'
                exp_val = r[3] or ''
                amt_val = float(r[4] or 0.0)
                doviz_val = r[5] or 'TL'
                od_val = 1 if r[6] == 1 or r[6] is True else 0

                payments.append({
                    'id': r[0],
                    'category': cat_val,
                    'kategori': cat_val,
                    'date': tarih_str,
                    'tarih': tarih_str,
                    'explanation': exp_val,
                    'aciklama': exp_val,
                    'amount': amt_val,
                    'tutar': amt_val,
                    'doviz': doviz_val,
                    'odendi': od_val,
                    'credit_card_pay': cc_pay
                })
            return payments
    except Exception as e:
        print(f"Error loading custom payments from DB: {e}")
        return []

def save_custom_payments_list(payments_list):
    # 1. JSON dosyasına kaydet
    try:
        with open(CUSTOM_PAYMENTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(payments_list, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving custom payments to JSON file: {e}")

    # 2. Veritabanına kaydet (senkronizasyon için)
    try:
        with engine_nexlog.begin() as conn:
            conn.execute(text("DELETE FROM dbo.manuel_veri_giris"))
            
            for item in payments_list:
                date_val = item.get('date') or item.get('tarih', None)
                if date_val:
                    if isinstance(date_val, str):
                        try:
                            datetime.strptime(date_val, '%Y-%m-%d')
                        except ValueError:
                            date_val = None
                    elif hasattr(date_val, 'strftime'):
                        date_val = date_val.strftime('%Y-%m-%d')
                    else:
                        date_val = str(date_val)
                
                cc_pay_val = '1' if item.get('credit_card_pay', False) else '0'
                odendi_val = 1 if item.get('odendi', False) in (True, 1, '1', 'true', 'on') else 0
                
                conn.execute(
                    text("""
                        INSERT INTO dbo.manuel_veri_giris (tarih, kategori, aciklama, tutar, doviz, odendi, kredi_karti)
                        VALUES (:tarih, :kategori, :aciklama, :tutar, :doviz, :odendi, :kredi_karti)
                    """),
                    {
                        "tarih": date_val,
                        "kategori": item.get('category') or item.get('kategori', 'Diğer'),
                        "aciklama": item.get('explanation') or item.get('aciklama', ''),
                        "tutar": float(item.get('amount') or item.get('tutar', 0.0)),
                        "doviz": item.get('doviz', 'TL'),
                        "odendi": odendi_val,
                        "kredi_karti": cc_pay_val
                    }
                )
        return True
    except Exception as e:
        print(f"Error saving custom payments to DB: {e}")
        return False

# --- VARLIKLAR PERSISTENCE VE YÖNETİMİ ---
ASSETS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets.json')

ASSET_KEYS = [
    ('cekiciler', '1. Çekiciler'),
    ('dorseler', '2. Dorseler'),
    ('binekler', '3. Binekler'),
    ('ismakinalari', '4. İş Makineleri'),
    ('binalar', '5. Binalar'),
    ('tekne', '6. Tekne'),
    ('findikpinari_ev', '7. Fındıkpınarı Ev'),
    ('bodrum_ev', '8. Bodrum Ev'),
    ('diger', '9. Diğer')
]

def load_assets():
    default_assets = {
        'cekiciler': {'amount': 0.0, 'description': ''},
        'dorseler': {'amount': 0.0, 'description': ''},
        'binekler': {'amount': 0.0, 'description': ''},
        'ismakinalari': {'amount': 0.0, 'description': ''},
        'binalar': {'amount': 0.0, 'description': ''},
        'tekne': {'amount': 0.0, 'description': ''},
        'findikpinari_ev': {'amount': 0.0, 'description': ''},
        'bodrum_ev': {'amount': 0.0, 'description': ''},
        'diger': {'amount': 0.0, 'description': ''}
    }
    if not os.path.exists(ASSETS_FILE):
        return default_assets
    try:
        with open(ASSETS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            normalized = {}
            for k in default_assets:
                val = data.get(k, 0.0)
                if isinstance(val, dict):
                    normalized[k] = {
                        'amount': float(val.get('amount', 0.0)),
                        'description': str(val.get('description', ''))
                    }
                else:
                    normalized[k] = {
                        'amount': float(val),
                        'description': ''
                    }
            return normalized
    except Exception:
        return default_assets

def save_assets(assets_dict):
    try:
        with open(ASSETS_FILE, 'w', encoding='utf-8') as f:
            json.dump(assets_dict, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False

# --- HIZLI TEMİZLİK VE AYARLAR ---
CUSTOM_SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'custom_payments_settings.json')

def load_custom_settings():
    if not os.path.exists(CUSTOM_SETTINGS_FILE):
        return {'auto_clean_past': False}
    try:
        with open(CUSTOM_SETTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'auto_clean_past': False}

def save_custom_settings(settings):
    try:
        with open(CUSTOM_SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False

# --- BÜTÇE AYARLARI VE YÖNETİMİ ---
BUDGET_SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'budget_settings.json')

def load_budget_settings():
    if not os.path.exists(BUDGET_SETTINGS_FILE):
        return {}
    try:
        with open(BUDGET_SETTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def save_budget_settings(budget_data):
    try:
        with open(BUDGET_SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(budget_data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False

# --- CASH FLOW BAŞLANGIÇ BAKİYESİ VE AYARLARI ---
CASH_FLOW_SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cash_flow_settings.json')

def load_starting_cash(year):
    if not os.path.exists(CASH_FLOW_SETTINGS_FILE):
        return 540000.0
    try:
        with open(CASH_FLOW_SETTINGS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return float(data.get(str(year), 540000.0))
    except Exception:
        return 540000.0

def save_starting_cash(year, amount):
    data = {}
    if os.path.exists(CASH_FLOW_SETTINGS_FILE):
        try:
            with open(CASH_FLOW_SETTINGS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            pass
    data[str(year)] = float(amount)
    try:
        with open(CASH_FLOW_SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False

def prune_expired_payments(force_all_expired=False):
    today = datetime.now().date()
    payments = load_custom_payments()
    new_payments = []
    pruned_count = 0
    for item in payments:
        try:
            item_date = datetime.strptime(item.get('date', ''), '%Y-%m-%d').date()
            is_paid = (item.get('odendi', 0) == 1)
            # Prune if the payment date has passed AND (it is marked as paid OR force_all_expired is enabled)
            if item_date < today and (is_paid or force_all_expired):
                pruned_count += 1
                continue
        except Exception:
            pass
        new_payments.append(item)
    if pruned_count > 0:
        save_custom_payments_list(new_payments)
    return pruned_count

def normalize_category(cat_str):
    if not cat_str:
        return 'Diğer'
    cat_str_clean = cat_str.strip()
    cat_lower = cat_str_clean.lower()
    
    if 'sgk' in cat_lower or 'geçici' in cat_lower or 'gecici' in cat_lower or 'vergi' in cat_lower or 'veri' in cat_lower:
        return 'Vergi'
    elif 'maaş' in cat_lower or 'maas' in cat_lower:
        return 'Maaş'
    elif '2' in cat_lower and 'kdv' in cat_lower:
        return '2 Nolu KDV'
    elif 'kdv' in cat_lower:
        return 'KDV'
    elif 'navlun' in cat_lower:
        return 'Navlun'
    elif 'kredi kart' in cat_lower or 'kredi' in cat_lower:
        return 'Kredi Kartı'
    elif 'leasing' in cat_lower:
        return 'Leasing'
    elif 'fatura' in cat_lower:
        return 'Fatura'
    elif 'yeni satış' in cat_lower or 'yeni satis' in cat_lower or 'satis tahsilat' in cat_lower or 'satış tahsilat' in cat_lower:
        return 'Yeni Satış Tahsilat'
    elif 'c/h' in cat_lower or 'cari' in cat_lower:
        return 'C/H Tahsilat'
        
    try:
        categories = get_nexlog_categories()
        for cat in categories:
            if cat['name'].lower() == cat_lower:
                return cat['name']
    except Exception:
        pass
        
    return cat_str_clean

def clean_bank_name(name):
    if not name:
        return '-'
    cleaned = name.split(' - ')[0].strip()
    if 'QNB' in cleaned.upper():
        return 'FİNANSBANK'
    return cleaned

def get_friendly_bank_name(name):
    if not name or not isinstance(name, str) or name.strip() in ('', '-'):
        return '-'
    name_upper = name.upper()
    if 'GARANT' in name_upper:
        return 'GARANTİ'
    elif 'VAKIF' in name_upper:
        return 'VAKIFBANK'
    elif 'ZİRAAT' in name_upper or 'ZIRAAT' in name_upper:
        return 'ZİRAAT'
    elif 'YAPI' in name_upper or 'YKB' in name_upper:
        return 'YAPI KREDİ'
    elif 'İŞ' in name_upper or 'IS' in name_upper:
        return 'İŞBANK'
    elif 'HALK' in name_upper:
        return 'HALK'
    elif 'AKBANK' in name_upper:
        return 'AKBANK'
    elif 'ALBARAKA' in name_upper:
        return 'ALBARAKA'
    elif 'TEB' in name_upper:
        return 'TEB'
    elif 'DENİZ' in name_upper or 'DENIZ' in name_upper:
        return 'DENİZBANK'
    elif 'KUVEYT' in name_upper:
        return 'KUVEYT TÜRK'
    elif 'QNB' in name_upper or 'FİNANS' in name_upper or 'FINANS' in name_upper:
        return 'FİNANSBANK'
    
    return name.split(' - ')[0].split(' * ')[0].split(' (')[0].strip()

@app.template_filter('friendly_bank')
def friendly_bank_filter(value):
    return get_friendly_bank_name(value)

# Bu anahtar Dashboard ile haberleşmeyi sağlar (Sabit kalmalıdır)
AUTH_TOKEN = "ufuk_rapor_portal_2026_secure_key"

@app.before_request
def check_dashboard_auth():
    # Yerel testler ve IP/localhost/LAN erişimleri için doğrulamayı bypass et (çerez domaini ufuklojistik.com olduğu için)
    if "ufuklojistik.com" not in request.host.lower():
        return
        
    # Local testleri engellememek için localhost istekleri bypass edilir
    if request.remote_addr in ['127.0.0.1', 'localhost', '::1']:
        return
        
    user_token = request.cookies.get('ufuk_auth')
    if user_token == AUTH_TOKEN:
        return
        
    # URL'den gelen token geçerliyse çereze kaydet ki sonraki istekler (static assets, API) hata almasın
    if request.args.get('auth_token') == AUTH_TOKEN:
        from flask import make_response, redirect
        from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
        
        url_parts = list(urlparse(request.url))
        query = dict(parse_qsl(url_parts[4]))
        query.pop('auth_token', None)
        url_parts[4] = urlencode(query)
        clean_url = urlunparse(url_parts)
        
        resp = make_response(redirect(clean_url))
        resp.set_cookie('ufuk_auth', AUTH_TOKEN, max_age=30*24*60*60, path='/')
        return resp
        
    abort(401, description="Lütfen önce Dashboard üzerinden giriş yapın.")


@app.route('/export_eski_sablon', methods=['GET'])
def export_eski_sablon_route():
    selected_date = request.args.get('target_date', datetime.now().strftime('%Y-%m-%d'))
    
    bankalar_hepsi = get_bank_balances()
    bank_records = bankalar_hepsi.to_dict('records') if not bankalar_hepsi.empty else []
    
    incoming_transfers = get_incoming_transfers(selected_date, filter_virman=True)
    
    df_cust = get_customer_check_details(selected_date, limit_date=True)
    customer_checks = df_cust.to_dict('records') if not df_cust.empty else []
    
    outgoing_transfers = get_outgoing_transfers(selected_date, filter_virman=True, aggregate_navlun=True)
    
    df_own = get_own_check_details(selected_date, limit_date=True)
    own_checks = df_own.to_dict('records') if not df_own.empty else []
    
    df_cred = get_credit_details(selected_date, limit_date=True)
    credits = df_cred.to_dict('records') if not df_cred.empty else []
    
    df_cc = get_credit_card_details(selected_date, limit_date=True)
    credit_cards = df_cc.to_dict('records') if not df_cc.empty else []
    
    all_custom = load_custom_payments()
    custom_payments = [p for p in all_custom if p['tarih'] == selected_date]
    
    # Calculate next business day payments
    try:
        dt_parsed = datetime.strptime(selected_date, '%Y-%m-%d')
        next_day = dt_parsed + timedelta(days=1)
        next_business_day = get_first_business_day(next_day).date()
        next_business_date_str = next_business_day.strftime('%Y-%m-%d')
        
        turkish_months = {
            1: 'Ocak', 2: 'Şubat', 3: 'Mart', 4: 'Nisan', 5: 'Mayıs', 6: 'Haziran',
            7: 'Temmuz', 8: 'Ağustos', 9: 'Eylül', 10: 'Ekim', 11: 'Kasım', 12: 'Aralık'
        }
        turkish_day_names = ['Pazartesi', 'Salı', 'Çarşamba', 'Perşembe', 'Cuma', 'Cumartesi', 'Pazar']
        next_business_date_formatted = f"{next_business_day.day} {turkish_months[next_business_day.month]} {next_business_day.year} {turkish_day_names[next_business_day.weekday()]}"

        next_credits_df = get_credit_details(next_business_date_str, limit_date=True)
        next_own_checks_df = get_own_check_details(next_business_date_str, limit_date=True)

        next_day_payments = []
        if not next_credits_df.empty:
            for _, row in next_credits_df.iterrows():
                row_vade = row.get('VADE')
                row_vade_str = str(row_vade)[:10] if row_vade else ""
                if row_vade_str and row_vade_str <= next_business_date_str:
                    next_day_payments.append({
                        'tur': 'Banka Kredisi',
                        'banka': row.get('BANKA_KREDI', '-'),
                        'aciklama': row.get('BANKA_KREDI', 'Banka Kredisi'),
                        'tutar': float(row.get('TUTAR', 0.0)),
                        'doviz': int(row.get('DOVIZ_TIPI', 0))
                    })
        if not next_own_checks_df.empty:
            for _, row in next_own_checks_df.iterrows():
                row_vade = row.get('VADE')
                row_vade_str = str(row_vade)[:10] if row_vade else ""
                if row_vade_str and row_vade_str <= next_business_date_str:
                    next_day_payments.append({
                        'tur': 'Kendi Çekimiz',
                        'banka': row.get('BANKA', '-'),
                        'aciklama': row.get('CH_UNVANI') or row.get('CARI_UNVAN') or '-',
                        'tutar': float(row.get('TUTAR', 0.0)),
                        'doviz': int(row.get('DOVIZ_TIPI', 0))
                    })
                    
        for p in all_custom:
            try:
                p_date = str(p.get('date', '')).strip()[:10]
                is_cc = p.get('credit_card_pay', False) or (str(p.get('category', '')).strip().lower() in ('kredi kartı', 'kredi karti', 'kredi kartı ödemesi'))
                if p_date and int(p.get('odendi', 0)) != 1 and not is_cc:
                    if p_date <= next_business_date_str:
                        next_day_payments.append({
                            'tur': p.get('category', 'Manuel Ödeme'),
                            'banka': '-',
                            'aciklama': p.get('explanation') or p.get('category', 'Manuel Ödeme'),
                            'tutar': float(p.get('amount', 0)),
                            'doviz': p.get('doviz', 'TL')
                        })
            except Exception:
                pass
    except Exception:
        next_day_payments = []
        next_business_date_formatted = ""

    tot_bank_tl = sum(float(r.get('BAKİYE', 0)) for r in bank_records if r.get('PARABİRİMİ') == 'TL' and r.get('HESAP_TURU') != 'Kasa')
    tot_bank_usd = sum(float(r.get('BAKİYE', 0)) for r in bank_records if r.get('PARABİRİMİ') == 'USD' and r.get('HESAP_TURU') != 'Kasa')
    tot_bank_eur = sum(float(r.get('BAKİYE', 0)) for r in bank_records if r.get('PARABİRİMİ') == 'EUR' and r.get('HESAP_TURU') != 'Kasa')

    excel_data = generate_eski_sablon_excel(
        selected_date,
        bank_records,
        incoming_transfers,
        customer_checks,
        outgoing_transfers,
        own_checks,
        credits,
        credit_cards,
        custom_payments,
        next_day_payments=next_day_payments,
        next_business_date_formatted=next_business_date_formatted,
        total_bank_balance_tl=tot_bank_tl,
        total_bank_balance_usd=tot_bank_usd,
        total_bank_balance_eur=tot_bank_eur
    )
    
    from flask import send_file
    from io import BytesIO
    return send_file(
        BytesIO(excel_data),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'Nakit_Akis_{selected_date}.xlsx'
    )

@app.route('/', methods=['GET', 'POST'])
def dashboard():
    selected_date = request.form.get('target_date') or request.args.get('target_date') or datetime.now().strftime('%Y-%m-%d')
    today = datetime.now().date()
    
    # Format the selected date in Turkish (e.g., "1 Haziran 2026 Pazartesi")
    selected_date_formatted = selected_date
    try:
        dt_parsed = datetime.strptime(selected_date, '%Y-%m-%d')
        turkish_months = {
            1: 'Ocak', 2: 'Şubat', 3: 'Mart', 4: 'Nisan', 5: 'Mayıs', 6: 'Haziran',
            7: 'Temmuz', 8: 'Ağustos', 9: 'Eylül', 10: 'Ekim', 11: 'Kasım', 12: 'Aralık'
        }
        turkish_day_names = ['Pazartesi', 'Salı', 'Çarşamba', 'Perşembe', 'Cuma', 'Cumartesi', 'Pazar']
        selected_date_formatted = f"{dt_parsed.day} {turkish_months[dt_parsed.month]} {dt_parsed.year} {turkish_day_names[dt_parsed.weekday()]}"
    except Exception as ex:
        print(f"Error formatting selected date: {ex}")
        
    nexlog_categories = get_nexlog_categories()
    
    try:
        # Prune expired payments (unconditionally for paid ones, and for all if auto_clean is enabled)
        settings = load_custom_settings()
        auto_clean = settings.get('auto_clean_past', False)
        prune_expired_payments(force_all_expired=auto_clean)

        ccards = get_credit_card_details(selected_date)
        if not ccards.empty:
            ccards['KARTI_ADI'] = ccards['KARTI_ADI'].apply(clean_bank_name)
        credits = get_credit_details(selected_date)
        own_checks = get_own_check_details(selected_date)
        customer_checks = get_customer_check_details(selected_date)
        
        # Bank virman transfers (Sol: Giren Bankalar, Sağ: Çıkan Bankalar)
        incoming_virman, outgoing_virman = get_virman_lists(selected_date)

        # Bank incoming transfers query (Tab 2: virman dışı gelenler)
        raw_incoming_transfers = get_incoming_transfers(selected_date, filter_virman=True)

        # Grouping incoming transfers for Tab 2 (Günlük Finans Hareketleri)
        grouped_incoming = {
            'havale': [],
            'virman': incoming_virman,
            'kredi_kart': [],
            'kasa': [],
            'diger': []
        }
        
        for r in raw_incoming_transfers:
            fis_turu_lower = r['FIS_TURU'].lower() if r['FIS_TURU'] else ''
            hesap_turu_lower = r['HESAP_TURU_RAPOR'].lower() if r['HESAP_TURU_RAPOR'] else ''
            hesap_aciklamasi_lower = r['HESAP_ACIKLAMASI'].lower() if r['HESAP_ACIKLAMASI'] else ''
            satir_exp_lower = r['SATIR_ACIKLAMASI'].lower() if r['SATIR_ACIKLAMASI'] else ''
            fis_exp_lower = r['FIS_ACIKLAMASI'].lower() if r['FIS_ACIKLAMASI'] else ''
            
            if 'kasa' in hesap_turu_lower or 'kasa' in hesap_aciklamasi_lower:
                grouped_incoming['kasa'].append(r)
            elif 'kredi kart' in fis_turu_lower or 'kredi kart' in hesap_turu_lower or ' kk' in hesap_aciklamasi_lower or '/ kk' in hesap_aciklamasi_lower:
                grouped_incoming['kredi_kart'].append(r)
            elif 'havale' in fis_turu_lower or 'eft' in fis_turu_lower:
                grouped_incoming['havale'].append(r)
            else:
                grouped_incoming['diger'].append(r)
                
        # Group totals
        incoming_totals = {}
        for key, items in grouped_incoming.items():
            incoming_totals[key] = {
                'TL': sum(r['TL_TUTAR'] for r in items if r['HESAP_DOVIZI_RAPOR'] == 'TL'),
                'USD': sum(r['DOVIZLI_TUTAR'] for r in items if r['HESAP_DOVIZI_RAPOR'] == 'USD'),
                'EUR': sum(r['DOVIZLI_TUTAR'] for r in items if r['HESAP_DOVIZI_RAPOR'] == 'EUR')
            }
            
        total_incoming_transfers_tl = sum(incoming_totals[k]['TL'] for k in incoming_totals)
        incoming_total_tl = sum(incoming_totals[k]['TL'] for k in incoming_totals)
        incoming_total_usd = sum(incoming_totals[k]['USD'] for k in incoming_totals)
        incoming_total_eur = sum(incoming_totals[k]['EUR'] for k in incoming_totals)
        
        # Bank outgoing transfers query (Tab 2: virman dışı çıkanlar)
        raw_outgoing_transfers = get_outgoing_transfers(selected_date, filter_virman=True, aggregate_navlun=False)

        # Grouping outgoing transfers for Tab 2 (Günlük Finans Hareketleri)
        grouped_outgoing = {
            'banka': [],
            'virman': outgoing_virman,
            'kredi_kart': [],
            'kasa': [],
            'diger': []
        }
        
        for r in raw_outgoing_transfers:
            fis_turu_lower = r['FIS_TURU'].lower() if r['FIS_TURU'] else ''
            hesap_turu_lower = r['HESAP_TURU_RAPOR'].lower() if r['HESAP_TURU_RAPOR'] else ''
            hesap_aciklamasi_lower = r['HESAP_ACIKLAMASI'].lower() if r['HESAP_ACIKLAMASI'] else ''
            satir_exp_lower = r['SATIR_ACIKLAMASI'].lower() if r['SATIR_ACIKLAMASI'] else ''
            fis_exp_lower = r['FIS_ACIKLAMASI'].lower() if r['FIS_ACIKLAMASI'] else ''
            
            if 'kasa' in hesap_turu_lower or 'kasa' in hesap_aciklamasi_lower:
                grouped_outgoing['kasa'].append(r)
            elif 'kredi kart' in fis_turu_lower or 'kredi kart' in hesap_turu_lower or ' kk' in hesap_aciklamasi_lower or '/ kk' in hesap_aciklamasi_lower:
                grouped_outgoing['kredi_kart'].append(r)
            elif ('havale' in fis_turu_lower or 'eft' in fis_turu_lower or 'giden' in fis_turu_lower
                  or 'çek öde' in fis_turu_lower or 'cek ode' in fis_turu_lower
                  or 'banka çek' in fis_turu_lower or 'banka cek' in fis_turu_lower):
                grouped_outgoing['banka'].append(r)
            else:
                grouped_outgoing['diger'].append(r)
                
        # Group totals
        outgoing_totals = {}
        for key, items in grouped_outgoing.items():
            outgoing_totals[key] = {
                'TL': sum(r['TL_TUTAR'] for r in items if r['HESAP_DOVIZI_RAPOR'] == 'TL'),
                'USD': sum(r['DOVIZLI_TUTAR'] for r in items if r['HESAP_DOVIZI_RAPOR'] == 'USD'),
                'EUR': sum(r['DOVIZLI_TUTAR'] for r in items if r['HESAP_DOVIZI_RAPOR'] == 'EUR')
            }
            
        total_outgoing_transfers_tl = sum(outgoing_totals[k]['TL'] for k in outgoing_totals)
        outgoing_total_tl = sum(outgoing_totals[k]['TL'] for k in outgoing_totals)
        outgoing_total_usd = sum(outgoing_totals[k]['USD'] for k in outgoing_totals)
        outgoing_total_eur = sum(outgoing_totals[k]['EUR'] for k in outgoing_totals)

        # Filtered & Aggregated transfers for Tab 3 (Şablon Görünümü)
        incoming_transfers = get_incoming_transfers(selected_date, filter_virman=True)
        outgoing_transfers = get_outgoing_transfers(selected_date, filter_virman=True, aggregate_navlun=True)
        
        # Bank balances query
        bank_balances_df = get_bank_balances()
        bankalar_hepsi = []
        total_bank_balance_tl = 0.0
        total_bank_balance_usd = 0.0
        total_bank_balance_eur = 0.0
        grouped_bank_balances_dict = {}
        
        if not bank_balances_df.empty:
            for idx, r in bank_balances_df.iterrows():
                try:
                    curr_type = int(r['DOVIZ_TIPI']) if r['DOVIZ_TIPI'] is not None else 0
                except (ValueError, TypeError):
                    curr_type = 0
                
                bakiye = round(float(r['BAKIYE']), 2)
                if abs(bakiye) == 0:
                    bakiye = 0.0
                record = {
                    'BANKA_ADI': r['BANKA_ADI'] or '-',
                    'HESAP_ADI': r['HESAP_ADI'] or '-',
                    'IBAN': r['IBAN'] or '-',
                    'BAKIYE': bakiye,
                    'DOVIZ_TIPI': curr_type
                }
                bankalar_hepsi.append(record)
                
                bname = str(r['BANKA_ADI'] or '').strip()
                if not bname:
                    bname = 'DİĞER BANKALAR'
                clean_bname = bname.replace(' A.Ş.', '').replace(' T.A.Ş.', '').replace(' T.A.O.', '').replace(' A.O.', '').strip()
                if clean_bname not in grouped_bank_balances_dict:
                    grouped_bank_balances_dict[clean_bname] = {'banka_adi': clean_bname, 'tl': 0.0, 'usd': 0.0, 'eur': 0.0}
                
                if curr_type == 0:
                    total_bank_balance_tl += bakiye
                    grouped_bank_balances_dict[clean_bname]['tl'] += bakiye
                elif curr_type == 1:
                    total_bank_balance_usd += bakiye
                    grouped_bank_balances_dict[clean_bname]['usd'] += bakiye
                elif curr_type == 20:
                    total_bank_balance_eur += bakiye
                    grouped_bank_balances_dict[clean_bname]['eur'] += bakiye
                    
        grouped_bank_balances_list = list(grouped_bank_balances_dict.values())
        grouped_bank_balances_list.sort(key=lambda x: x['tl'], reverse=True)
        
        # Kasa balances query (Filtered by Merkez Kasa and selected date)
        kasa_balances_df = get_kasa_balances(selected_date)
        total_kasa_balance_tl = 0.0
        total_kasa_balance_usd = 0.0
        total_kasa_balance_eur = 0.0
        
        if not kasa_balances_df.empty:
            for idx, r in kasa_balances_df.iterrows():
                try:
                    curr_type = int(r['DOVIZ_TIPI']) if r['DOVIZ_TIPI'] is not None else 0
                except (ValueError, TypeError):
                    curr_type = 0
                
                bakiye = round(float(r['BAKIYE']), 2)
                if abs(bakiye) == 0:
                    bakiye = 0.0
                if curr_type == 0:
                    total_kasa_balance_tl += bakiye
                elif curr_type == 1:
                    total_kasa_balance_usd += bakiye
                elif curr_type == 20:
                    total_kasa_balance_eur += bakiye
        
        # TCMB Döviz Kurları
        tcmb_rates = get_tcmb_rates()
        tcmb_usd = tcmb_rates.get('USD', 0.0)
        tcmb_eur = tcmb_rates.get('EUR', 0.0)

        # Load and process custom payments
        all_custom = load_custom_payments()
        filtered_custom = []
        total_custom_payments = 0.0
        total_credit_card_payments = 0.0
        total_custom_tl = 0.0
        total_custom_usd = 0.0
        total_custom_eur = 0.0
        
        db_categories = [c['name'] for c in nexlog_categories]
        custom_categories = []
        for cat in db_categories:
            if cat not in custom_categories:
                custom_categories.append(cat)
        for item in all_custom:
            cat_name = normalize_category(item.get('category') or item.get('kategori'))
            if cat_name and cat_name not in custom_categories:
                custom_categories.append(cat_name)
        if 'Diğer' not in custom_categories:
            custom_categories.append('Diğer')

        custom_by_category = {cat: [] for cat in custom_categories}
        custom_totals = {cat: 0.0 for cat in custom_categories}
        
        current_hour = datetime.now().hour
        for idx, item in enumerate(all_custom):
            try:
                item_date_str = item.get('date', '')
                item_date = datetime.strptime(item_date_str, '%Y-%m-%d').date()
                sel_date = datetime.strptime(selected_date, '%Y-%m-%d').date()
                if item_date <= sel_date:
                    kalan = (item_date - today).days
                    amt = float(item.get('amount', 0))
                    raw_cat = item.get('category', 'Diğer')
                    cat = normalize_category(raw_cat)
                    cc_pay = item.get('credit_card_pay', False)
                    odendi_val = item.get('odendi', 0)
                    
                    doviz_val = str(item.get('doviz', 'TL')).upper().strip()
                    amt_tl = amt
                    if doviz_val == 'USD' and tcmb_usd > 0:
                        amt_tl = amt * tcmb_usd
                    elif doviz_val == 'EUR' and tcmb_eur > 0:
                        amt_tl = amt * tcmb_eur

                    record = {
                        'index': idx,
                        'category': cat,
                        'kategori': cat,
                        'date': item_date,
                        'tarih': item_date,
                        'explanation': item.get('explanation', ''),
                        'aciklama': item.get('explanation', ''),
                        'amount': amt,
                        'tutar': amt,
                        'amount_tl': amt_tl,
                        'tutar_tl': amt_tl,
                        'doviz': doviz_val,
                        'kalan_gun': kalan,
                        'is_late': (item_date == today) and (current_hour >= 16),
                        'credit_card_pay': cc_pay,
                        'odendi': odendi_val
                    }
                    filtered_custom.append(record)
                    custom_by_category.setdefault(cat, []).append(record)
                    
                    # Sadece ÖDENDİ (odendi == 1) DEĞİLSE ve KREDİ KARTI (cc_pay == True) DEĞİLSE toplama dahil et
                    if odendi_val != 1 and not cc_pay:
                        custom_totals[cat] = custom_totals.get(cat, 0.0) + amt_tl
                        total_custom_payments += amt_tl
                        if doviz_val == 'USD':
                            total_custom_usd += amt
                        elif doviz_val == 'EUR':
                            total_custom_eur += amt
                        else:
                            total_custom_tl += amt
                    elif odendi_val != 1 and cc_pay:
                        total_credit_card_payments += amt_tl
            except Exception:
                pass

        total_custom_usd_tl = total_custom_usd * tcmb_usd
        total_custom_eur_tl = total_custom_eur * tcmb_eur
        
        # Sort custom payments by date
        filtered_custom.sort(key=lambda x: x['date'])
        for cat in custom_categories:
            custom_by_category[cat].sort(key=lambda x: x['date'])
            
        # --- NEXT BUSINESS DAY PAYMENTS LOGIC ---
        next_day = dt_parsed + timedelta(days=1)
        next_business_day = get_first_business_day(next_day).date()
        next_business_date_str = next_business_day.strftime('%Y-%m-%d')
        next_business_date_formatted = f"{next_business_day.day} {turkish_months[next_business_day.month]} {next_business_day.year} {turkish_day_names[next_business_day.weekday()]}"

        next_credits_df = get_credit_details(next_business_date_str, limit_date=True)
        next_own_checks_df = get_own_check_details(next_business_date_str, limit_date=True)

        next_day_payments = []
        if not next_credits_df.empty:
            for _, row in next_credits_df.iterrows():
                row_vade = row.get('VADE')
                row_vade_str = str(row_vade)[:10] if row_vade else ""
                if row_vade_str and row_vade_str <= next_business_date_str:
                    next_day_payments.append({
                        'tur': 'Banka Kredisi',
                        'banka': row.get('BANKA_KREDI', '-'),
                        'aciklama': row.get('BANKA_KREDI', 'Banka Kredisi'),
                        'tutar': float(row.get('TUTAR', 0.0)),
                        'doviz': int(row.get('DOVIZ_TIPI', 0))
                    })
        if not next_own_checks_df.empty:
            for _, row in next_own_checks_df.iterrows():
                row_vade = row.get('VADE')
                row_vade_str = str(row_vade)[:10] if row_vade else ""
                if row_vade_str and row_vade_str <= next_business_date_str:
                    next_day_payments.append({
                        'tur': 'Kendi Çekimiz',
                        'banka': row.get('BANKA', '-'),
                        'aciklama': row.get('CH_UNVANI') or row.get('CARI_UNVAN') or '-',
                        'tutar': float(row.get('TUTAR', 0.0)),
                        'doviz': int(row.get('DOVIZ_TIPI', 0))
                    })
                    
        for p in all_custom:
            try:
                p_date = str(p.get('date', '')).strip()[:10]
                is_cc = p.get('credit_card_pay', False) or (str(p.get('category', '')).strip().lower() in ('kredi kartı', 'kredi karti', 'kredi kartı ödemesi'))
                if p_date and int(p.get('odendi', 0)) != 1 and not is_cc:
                    if p_date <= next_business_date_str:
                        next_day_payments.append({
                            'tur': p.get('category', 'Manuel Ödeme'),
                            'banka': '-',
                            'aciklama': p.get('explanation') or p.get('category', 'Manuel Ödeme'),
                            'tutar': float(p.get('amount', 0)),
                            'doviz': p.get('doviz', 'TL')
                        })
            except Exception:
                pass
        # ----------------------------------------
        
        total_ccards = 0
        total_credits = credits['TUTAR'].sum() if not credits.empty else 0
        total_credit_anapara = credits['ANAPARA'].sum() if not credits.empty else 0
        total_credit_faiz = credits['FAIZ'].sum() if not credits.empty else 0
        
        total_own_checks_tl = 0.0
        total_own_checks_usd = 0.0
        total_own_checks_eur = 0.0
        
        if not own_checks.empty:
            for idx, r in own_checks.iterrows():
                try:
                    curr_type = int(r['DOVIZ_TIPI']) if r['DOVIZ_TIPI'] is not None else 0
                except (ValueError, TypeError):
                    curr_type = 0
                
                tutar = float(r['TUTAR'] or 0.0)
                if curr_type == 0:
                    total_own_checks_tl += tutar
                elif curr_type == 1:
                    total_own_checks_usd += tutar
                elif curr_type == 20:
                    total_own_checks_eur += tutar
                else:
                    total_own_checks_tl += tutar  # fallback
                    
        total_own_checks = total_own_checks_tl
        
        # Banka Tahsilatları için sadece bankaya tahsile verilen çekler (DURUM_KODU = 4) sum edilir
        total_customer_checks_tl = 0.0
        total_customer_checks_usd = 0.0
        total_customer_checks_eur = 0.0
        
        if not customer_checks.empty:
            active_checks = customer_checks[customer_checks['DURUM_KODU'].isin([4])]
            for idx, r in active_checks.iterrows():
                try:
                    curr_type = int(r['DOVIZ_TIPI']) if r['DOVIZ_TIPI'] is not None else 0
                except (ValueError, TypeError):
                    curr_type = 0
                
                tutar = float(r['TUTAR'])
                if curr_type == 0:
                    total_customer_checks_tl += tutar
                elif curr_type == 1:
                    total_customer_checks_usd += tutar
                elif curr_type == 20:
                    total_customer_checks_eur += tutar
                else:
                    total_customer_checks_tl += tutar  # fallback
                    
        total_customer_checks = total_customer_checks_tl

        # Alacakların toplamı
        receivables_balances = get_receivables_balances()
        total_receivables_tl = receivables_balances['TL']
        total_receivables_usd = receivables_balances['USD']
        total_receivables_eur = receivables_balances['EUR']

        # Kesilecek faturaların toplamı
        unbilled_balances = get_unbilled_invoices_totals()
        total_unbilled_tl = unbilled_balances['TL']
        total_unbilled_usd = unbilled_balances['USD']
        total_unbilled_eur = unbilled_balances['EUR']

        tcmb_usd_formatted = f"{tcmb_usd:.4f}".replace('.', ',')
        tcmb_eur_formatted = f"{tcmb_eur:.4f}".replace('.', ',')

        # Varlıklar modülü
        assets = load_assets()
        total_assets = sum(float(assets.get(k[0], {}).get('amount', 0.0)) for k in ASSET_KEYS)

        # Açık hesap borçlarımızın toplamı
        open_payables = get_open_payables_balances()
        total_payables_tl = sum(open_payables[k]['TL'] for k in open_payables)
        total_payables_usd = sum(open_payables[k]['USD'] for k in open_payables)
        total_payables_eur = sum(open_payables[k]['EUR'] for k in open_payables)

        # Genel mevcutların toplamı (Mevcutlar + Alacaklar)
        total_mevcut_tl = total_kasa_balance_tl + total_bank_balance_tl + total_customer_checks_tl + total_receivables_tl
        total_mevcut_usd = total_kasa_balance_usd + total_bank_balance_usd + total_customer_checks_usd + total_receivables_usd
        total_mevcut_eur = total_kasa_balance_eur + total_bank_balance_eur + total_customer_checks_eur + total_receivables_eur
            
        # Toplam Tahsilat = Müşteri Çekleri + Bankadaki TL (Dövizler hariç)
        total_tahsilat = total_customer_checks + total_bank_balance_tl
        
        total_payments = total_credits + total_own_checks + total_custom_payments
        net_cash_flow = total_tahsilat - total_payments

        # Yüzde hesaplama (Ödeme yükü dağılımı için)
        pct_ccards = (total_ccards / total_payments * 100) if total_payments > 0 else 0
        pct_credits = (total_credits / total_payments * 100) if total_payments > 0 else 0
        pct_own_checks = (total_own_checks / total_payments * 100) if total_payments > 0 else 0

        # Kredi kalan gün hesaplama
        credit_rows = []
        if not credits.empty:
            for i, r in credits.iterrows():
                vade = pd.to_datetime(r['VADE']).date()
                kalan = (vade - today).days
                credit_rows.append({
                    'BANKA_KREDI': clean_bank_name(r['BANKA_KREDI']),
                    'VADE': r['VADE'],
                    'TUTAR': r['TUTAR'],
                    'KALAN_GUN': kalan,
                    'IS_LATE': (vade == today) and (current_hour >= 16)
                })

        # Borç Çek kalan gün hesaplama
        own_check_rows = []
        if not own_checks.empty:
            for i, r in own_checks.iterrows():
                vade = pd.to_datetime(r['VADE']).date()
                kalan = (vade - today).days
                own_check_rows.append({
                    'CH_UNVANI': r['CH_UNVANI'] or '-',
                    'BANKA': clean_bank_name(r['BANKA']),
                    'VADE': r['VADE'],
                    'TUTAR': r['TUTAR'],
                    'KALAN_GUN': kalan,
                    'IS_LATE': (vade == today) and (current_hour >= 16)
                })

        # Müşteri Çek kalan gün hesaplama
        customer_check_rows = []
        if not customer_checks.empty:
            for i, r in customer_checks.iterrows():
                vade = pd.to_datetime(r['VADE']).date()
                kalan = (vade - today).days
                
                drawn_b = clean_bank_name(r['BANKA'])
                our_b = get_friendly_bank_name(r['OUR_BANK']) if r['OUR_BANK'] is not None else None
                cek_no = str(r.get('CEK_NO', '')).strip() if pd.notna(r.get('CEK_NO')) else ''
                orj_vade = str(r.get('ORJ_VADE', '')).strip()
                
                cek_info = f"Çek No: {cek_no} - {orj_vade}" if cek_no else f"Vade: {orj_vade}"
                
                if our_b and our_b != '-':
                    our_b = f"{our_b} - {cek_info}"
                    bank_display = f"{drawn_b} ({our_b})"
                else:
                    bank_display = f"{drawn_b} ({cek_info})"
                
                customer_check_rows.append({
                    'CH_UNVANI': r['CH_UNVANI'] or '-',
                    'BANKA': bank_display,
                    'VADE': r['VADE'],
                    'TUTAR': r['TUTAR'],
                    'KALAN_GUN': kalan,
                    'DURUM_KODU': int(r['DURUM_KODU']),
                    'DURUM': r['DURUM']
                })

        # --- SINIRSIZ (UNRESTRICTED) VERİLERİN SORGULANMASI VE HAZIRLANMASI ---
        credits_all = get_credit_details(selected_date, limit_date=False)
        
        # Yıllara göre kredi dağılımı (2026, 2027, 2028)
        credits_by_year = {
            2026: {'anapara': 0.0, 'faiz': 0.0, 'tl': 0.0, 'usd': 0.0, 'eur': 0.0},
            2027: {'anapara': 0.0, 'faiz': 0.0, 'tl': 0.0, 'usd': 0.0, 'eur': 0.0},
            2028: {'anapara': 0.0, 'faiz': 0.0, 'tl': 0.0, 'usd': 0.0, 'eur': 0.0}
        }
        
        if not credits_all.empty:
            for idx, r in credits_all.iterrows():
                if r['VADE'] is not None:
                    try:
                        v_year = pd.to_datetime(r['VADE']).year
                        if v_year in credits_by_year:
                            anapara = float(r['ANAPARA'] or 0.0)
                            faiz = float(r['FAIZ'] or 0.0)
                            tutar = float(r['TUTAR'] or 0.0)
                            
                            credits_by_year[v_year]['anapara'] += anapara
                            credits_by_year[v_year]['faiz'] += faiz
                            
                            try:
                                curr_type = int(r['DOVIZ_TIPI']) if r['DOVIZ_TIPI'] is not None else 0
                            except (ValueError, TypeError):
                                curr_type = 0
                                
                            if curr_type == 0:
                                credits_by_year[v_year]['tl'] += tutar
                            elif curr_type == 1:
                                credits_by_year[v_year]['usd'] += tutar
                            elif curr_type == 20:
                                credits_by_year[v_year]['eur'] += tutar
                            else:
                                credits_by_year[v_year]['tl'] += tutar
                    except Exception:
                        pass
        
        total_credit_anapara_all = sum(credits_by_year[y]['anapara'] for y in credits_by_year)
        total_credit_faiz_all = sum(credits_by_year[y]['faiz'] for y in credits_by_year)
        total_credit_tl_all = sum(credits_by_year[y]['tl'] for y in credits_by_year)
        total_credit_usd_all = sum(credits_by_year[y]['usd'] for y in credits_by_year)
        total_credit_eur_all = sum(credits_by_year[y]['eur'] for y in credits_by_year)
        own_checks_all = get_own_check_details(selected_date, limit_date=False)
        total_own_checks_tl_all = 0.0
        total_own_checks_usd_all = 0.0
        total_own_checks_eur_all = 0.0
        if not own_checks_all.empty:
            for idx, r in own_checks_all.iterrows():
                try:
                    curr_type = int(r['DOVIZ_TIPI']) if r['DOVIZ_TIPI'] is not None else 0
                except (ValueError, TypeError):
                    curr_type = 0
                tutar = float(r['TUTAR'] or 0.0)
                if curr_type == 0:
                    total_own_checks_tl_all += tutar
                elif curr_type == 1:
                    total_own_checks_usd_all += tutar
                elif curr_type == 20:
                    total_own_checks_eur_all += tutar
                else:
                    total_own_checks_tl_all += tutar

        customer_checks_all = get_customer_check_details(selected_date, limit_date=False)
        total_customer_checks_tl_all = 0.0
        total_customer_checks_usd_all = 0.0
        total_customer_checks_eur_all = 0.0
        if not customer_checks_all.empty:
            active_checks_all = customer_checks_all[customer_checks_all['DURUM_KODU'].isin([1, 3, 4])]
            for idx, r in active_checks_all.iterrows():
                try:
                    curr_type = int(r['DOVIZ_TIPI']) if r['DOVIZ_TIPI'] is not None else 0
                except (ValueError, TypeError):
                    curr_type = 0
                tutar = float(r['TUTAR'] or 0.0)
                if curr_type == 0:
                    total_customer_checks_tl_all += tutar
                elif curr_type == 1:
                    total_customer_checks_usd_all += tutar
                elif curr_type == 20:
                    total_customer_checks_eur_all += tutar
                else:
                    total_customer_checks_tl_all += tutar

        total_mevcut_tl_all = total_kasa_balance_tl + total_bank_balance_tl + total_customer_checks_tl_all + total_receivables_tl
        total_mevcut_usd_all = total_kasa_balance_usd + total_bank_balance_usd + total_customer_checks_usd_all + total_receivables_usd
        total_mevcut_eur_all = total_kasa_balance_eur + total_bank_balance_eur + total_customer_checks_eur_all + total_receivables_eur

        # SQL Verilerini Python Liste Nesnelerine Çevirme
        credit_rows_all = []
        if not credits_all.empty:
            for i, r in credits_all.iterrows():
                credit_rows_all.append({
                    'BANKA_KREDI': clean_bank_name(r['BANKA_KREDI']),
                    'VADE': pd.to_datetime(r['VADE']).date() if r['VADE'] is not None else None,
                    'TUTAR': float(r['TUTAR'])
                })

        own_check_rows_all = []
        if not own_checks_all.empty:
            for i, r in own_checks_all.iterrows():
                own_check_rows_all.append({
                    'CH_UNVANI': r['CH_UNVANI'] or '-',
                    'BANKA': clean_bank_name(r['BANKA']),
                    'VADE': pd.to_datetime(r['VADE']).date() if r['VADE'] is not None else None,
                    'TUTAR': float(r['TUTAR'])
                })

        customer_check_rows_all = []
        if not customer_checks_all.empty:
            for i, r in customer_checks_all.iterrows():
                drawn_b = clean_bank_name(r['BANKA'])
                our_b = get_friendly_bank_name(r['OUR_BANK']) if r['OUR_BANK'] is not None else None
                cek_no = str(r.get('CEK_NO', '')).strip() if pd.notna(r.get('CEK_NO')) else ''
                orj_vade = str(r.get('ORJ_VADE', '')).strip()
                
                cek_info = f"Çek No: {cek_no} - {orj_vade}" if cek_no else f"Vade: {orj_vade}"
                
                if our_b and our_b != '-':
                    our_b = f"{our_b} - {cek_info}"
                    bank_display = f"{drawn_b} ({our_b})"
                else:
                    bank_display = f"{drawn_b} ({cek_info})"
                
                customer_check_rows_all.append({
                    'CH_UNVANI': r['CH_UNVANI'] or '-',
                    'BANKA': bank_display,
                    'VADE': pd.to_datetime(r['VADE']).date() if r['VADE'] is not None else None,
                    'TUTAR': float(r['TUTAR']),
                    'DURUM_KODU': int(r['DURUM_KODU']),
                    'DURUM': r['DURUM']
                })

        custom_rows_all = []
        for idx, item in enumerate(all_custom):
            try:
                item_date_str = item.get('date', '')
                item_date = datetime.strptime(item_date_str, '%Y-%m-%d').date()
                amt = float(item.get('amount', 0))
                dov_val = str(item.get('doviz', 'TL')).upper().strip()
                amt_tl = amt
                if dov_val == 'USD' and tcmb_usd > 0:
                    amt_tl = amt * tcmb_usd
                elif dov_val == 'EUR' and tcmb_eur > 0:
                    amt_tl = amt * tcmb_eur

                raw_cat = item.get('category', 'Diğer')
                cat = normalize_category(raw_cat)
                cc_pay = item.get('credit_card_pay', False)
                odendi_val = item.get('odendi', 0)

                if odendi_val != 1 and not cc_pay:
                    custom_rows_all.append({
                        'category': cat,
                        'date': item_date,
                        'amount': amt_tl
                    })
            except Exception:
                pass

        # --- AYLIK VE YILLIK ÖZET HESAPLAMA (SINIRSIZ VERİ ÜZERİNDEN) ---
        monthly_data = {}  # key: (year, month), value: {'tahsilat': 0.0, 'odeme': 0.0}
        
        # 1. Alınan Müşteri Çekleri (Tahsilat)
        for r in customer_check_rows_all:
            if r['VADE'] is not None and r['DURUM_KODU'] in (1, 3, 4):
                key = (r['VADE'].year, r['VADE'].month)
                if key not in monthly_data:
                    monthly_data[key] = {'tahsilat': 0.0, 'odeme': 0.0}
                monthly_data[key]['tahsilat'] += r['TUTAR']
            
        # 2. Banka Kredileri (Ödeme)
        for r in credit_rows_all:
            if r['VADE'] is not None:
                key = (r['VADE'].year, r['VADE'].month)
                if key not in monthly_data:
                    monthly_data[key] = {'tahsilat': 0.0, 'odeme': 0.0}
                monthly_data[key]['odeme'] += r['TUTAR']
            
        # 3. Borç Çekleri (Ödeme)
        for r in own_check_rows_all:
            if r['VADE'] is not None:
                key = (r['VADE'].year, r['VADE'].month)
                if key not in monthly_data:
                    monthly_data[key] = {'tahsilat': 0.0, 'odeme': 0.0}
                monthly_data[key]['odeme'] += r['TUTAR']
            
        # 4. Manuel Ödemeler (Ödeme)
        for r in custom_rows_all:
            if r['date'] is not None:
                key = (r['date'].year, r['date'].month)
                if key not in monthly_data:
                    monthly_data[key] = {'tahsilat': 0.0, 'odeme': 0.0}
                monthly_data[key]['odeme'] += r['amount']
            
        # Düzgün bir liste haline getirip tarihe göre sıralayalım
        monthly_summary_list = []
        turkish_months = {
            1: 'Ocak', 2: 'Şubat', 3: 'Mart', 4: 'Nisan', 5: 'Mayıs', 6: 'Haziran',
            7: 'Temmuz', 8: 'Ağustos', 9: 'Eylül', 10: 'Ekim', 11: 'Kasım', 12: 'Aralık'
        }
        
        # Yıllık toplamlar için
        yearly_data = {}  # key: year, value: {'tahsilat': 0.0, 'odeme': 0.0}
        
        for (yr, mn), vals in monthly_data.items():
            tahsilat = vals['tahsilat']
            odeme = vals['odeme']
            net = tahsilat - odeme
            month_name = f"{turkish_months[mn]} {yr}"
            
            monthly_summary_list.append({
                'year': yr,
                'month': mn,
                'month_name': month_name,
                'tahsilat': tahsilat,
                'odeme': odeme,
                'net': net
            })
            
            # Yıllık biriktir
            if yr not in yearly_data:
                yearly_data[yr] = {'tahsilat': 0.0, 'odeme': 0.0}
            yearly_data[yr]['tahsilat'] += tahsilat
            yearly_data[yr]['odeme'] += odeme
            
        # Aylık listeyi kronolojik sırala
        monthly_summary_list.sort(key=lambda x: (x['year'], x['month']))
        
        yearly_summary_list = []
        for yr, vals in yearly_data.items():
            tahsilat = vals['tahsilat']
            odeme = vals['odeme']
            net = tahsilat - odeme
            yearly_summary_list.append({
                'year': yr,
                'tahsilat': tahsilat,
                'odeme': odeme,
                'net': net
            })
        yearly_summary_list.sort(key=lambda x: x['year'])

        # Aylık Ödeme Tipleri Matrisi
        matrix_data = {}  # key: (year, month), value: {col_name: float}
        
        income_only_cats = {'Yeni Satış Tahsilat', 'C/H Tahsilat', 'Müşteri Çekleri'}
        matrix_cols = ['Banka Kredisi', 'Borç Çeki']
        for cat in custom_categories:
            if cat not in matrix_cols and cat not in income_only_cats and cat != 'Diğer':
                matrix_cols.append(cat)
        if 'Diğer' not in matrix_cols:
            matrix_cols.append('Diğer')
        if 'Müşteri Çekleri' not in matrix_cols:
            matrix_cols.append('Müşteri Çekleri')
        
        def add_to_matrix(yr, mn, col, amt):
            key = (yr, mn)
            if key not in matrix_data:
                matrix_data[key] = {c: 0.0 for c in matrix_cols}
            if col in matrix_data[key]:
                matrix_data[key][col] += amt

        # 1. Alınan Müşteri Çekleri (Tahsilat)
        for r in customer_check_rows_all:
            if r['VADE'] is not None and r['DURUM_KODU'] in (1, 3, 4):
                add_to_matrix(r['VADE'].year, r['VADE'].month, 'Müşteri Çekleri', r['TUTAR'])
            
        # 2. Banka Kredileri (Ödeme)
        for r in credit_rows_all:
            if r['VADE'] is not None:
                add_to_matrix(r['VADE'].year, r['VADE'].month, 'Banka Kredisi', r['TUTAR'])
            
        # 3. Borç Çekleri (Ödeme)
        for r in own_check_rows_all:
            if r['VADE'] is not None:
                add_to_matrix(r['VADE'].year, r['VADE'].month, 'Borç Çeki', r['TUTAR'])
            
        # 4. Manuel Ödemeler (Ödeme)
        for r in custom_rows_all:
            if r['date'] is not None:
                cat = r['category']
                if cat in matrix_cols:
                    add_to_matrix(r['date'].year, r['date'].month, cat, r['amount'])
                else:
                    add_to_matrix(r['date'].year, r['date'].month, 'Diğer', r['amount'])
                    
        # Matris Satırlarını Kronolojik Sıralayalım
        matrix_rows = []
        for (yr, mn), cols in matrix_data.items():
            month_name = f"{turkish_months[mn]} {yr}"
            row_total_payment = sum(cols[c] for c in matrix_cols if c != 'Müşteri Çekleri')
            net_flow = cols['Müşteri Çekleri'] - row_total_payment
            
            row_record = {
                'year': yr,
                'month': mn,
                'month_name': month_name,
                'cols': cols,
                'total_payment': row_total_payment,
                'net_flow': net_flow
            }
            matrix_rows.append(row_record)
            
        matrix_rows.sort(key=lambda x: (x['year'], x['month']))
        
        # Sütun bazında dikey toplamlar
        matrix_col_totals = {c: 0.0 for c in matrix_cols}
        matrix_grand_total_payment = 0.0
        matrix_grand_net_flow = 0.0
        
        for r in matrix_rows:
            for c in matrix_cols:
                matrix_col_totals[c] += r['cols'][c]
            matrix_grand_total_payment += r['total_payment']
            matrix_grand_net_flow += r['net_flow']

        # --- DİNAMİK AYLARIN TOPLANMASI (AY SEÇİM HAPLARI İÇİN) ---
        available_months_set = set()
        # İçinde bulunduğumuz ayı ve seçilen ayı her ihtimale karşı ekleyelim
        available_months_set.add(datetime.now().strftime('%Y-%m'))
        try:
            sel_date_parsed = datetime.strptime(selected_date, '%Y-%m-%d')
            available_months_set.add(sel_date_parsed.strftime('%Y-%m'))
        except Exception:
            pass

        # Tüm veri kaynaklarındaki ayları ekle
        for r in credit_rows_all:
            if r['VADE'] is not None:
                available_months_set.add(r['VADE'].strftime('%Y-%m'))
        for r in own_check_rows_all:
            if r['VADE'] is not None:
                available_months_set.add(r['VADE'].strftime('%Y-%m'))
        for r in customer_check_rows_all:
            if r['VADE'] is not None:
                available_months_set.add(r['VADE'].strftime('%Y-%m'))
        for r in custom_rows_all:
            if r['date'] is not None:
                available_months_set.add(r['date'].strftime('%Y-%m'))

        available_months = sorted(list(available_months_set))

        # Seçilen ayların parametreden çözümlenmesi
        selected_months_arg = request.args.get('selected_months') or request.form.get('selected_months')
        if selected_months_arg:
            selected_months = [x.strip() for x in selected_months_arg.split(',') if x.strip() != '']
        else:
            # Varsayılan olarak içinde bulunduğumuz ay aktif
            selected_months = [datetime.now().strftime('%Y-%m')]

        # --- EXCEL FORMATINDA GÜNLÜK ÖDEMELER DAĞILIM MATRİSİ HESAPLAMA ---
        import calendar
        daily_breakdowns = []
        
        # Excel sütun sıralaması (dinamik kategori çekimi)
        nexlog_cats = [c["name"] for c in get_nexlog_categories()]
        income_only_cats = {'Yeni Satış Tahsilat', 'C/H Tahsilat', 'Müşteri Çekleri'}
        payment_cols = ['çek', 'kredi']
        cat_to_col = {}
        for cat in nexlog_cats:
            if cat in income_only_cats or cat == 'Diğer':
                continue
            cat_to_col[cat] = cat
            if cat not in payment_cols:
                payment_cols.append(cat)
                
        for r in custom_rows_all:
            cat = r.get('category')
            if cat and cat not in income_only_cats and cat != 'Diğer':
                cat_to_col[cat] = cat
                if cat not in payment_cols:
                    payment_cols.append(cat)
                    
        if 'Diğer' not in payment_cols:
            payment_cols.append('Diğer')
            cat_to_col['Diğer'] = 'Diğer'
        
        turkish_month_short = {
            1: 'Oca', 2: 'Şub', 3: 'Mar', 4: 'Nis', 5: 'May', 6: 'Haz',
            7: 'Tem', 8: 'Ağu', 9: 'Eyl', 10: 'Eki', 11: 'Kas', 12: 'Ara'
        }
        
        turkish_day_names = ['pazartesi', 'salı', 'çarşamba', 'perşembe', 'cuma', 'cumartesi', 'pazar']
        
        running_bank_balance = total_bank_balance_tl
        selected_months_sorted = sorted(selected_months)
        
        for m_str in selected_months_sorted:
            try:
                yr, mn = map(int, m_str.split('-'))
                month_title = f"{turkish_months[mn]} {yr}"
                num_days = calendar.monthrange(yr, mn)[1]
                
                days_data = []
                for day in range(1, num_days + 1):
                    day_date = datetime(yr, mn, day).date()
                    short_mon = turkish_month_short[mn].lower()
                    year_short = str(yr)[2:]
                    day_name = turkish_day_names[day_date.weekday()]
                    formatted_day_str = f"{day:02d}.{short_mon}.{year_short} {day_name}"

                    days_data.append({
                        'day_str': formatted_day_str,
                        'date': day_date,
                        'is_weekend': day_date.weekday() in (5, 6),
                        'is_holiday': is_resmi_tatil(day_date),
                        'cols': {c: 0.0 for c in payment_cols},
                        'total': 0.0,
                        'musteri_ceki': 0.0,
                        'banka_bakiye': 0.0,
                        'gun_ici_net': 0.0,
                        'net_durum': 0.0
                    })
                    
                # 1. Çek Ödemeleri (Borç Çekleri)
                for r in own_check_rows_all:
                    if r['VADE'] is not None and r['VADE'].year == yr and r['VADE'].month == mn:
                        days_data[r['VADE'].day - 1]['cols']['çek'] += r['TUTAR']
                            
                # 2. Kredi Ödemeleri (Banka Kredileri)
                for r in credit_rows_all:
                    if r['VADE'] is not None and r['VADE'].year == yr and r['VADE'].month == mn:
                        days_data[r['VADE'].day - 1]['cols']['kredi'] += r['TUTAR']
                            
                # 3. Ek/Manuel Ödemeler Dağıtımı
                for r in custom_rows_all:
                    v = r['date']
                    if v.year == yr and v.month == mn:
                        cat = r['category']
                        day_idx = v.day - 1
                        
                        col_name = cat_to_col.get(cat, cat)
                        if col_name not in days_data[day_idx]['cols']:
                            col_name = 'Diğer' if 'Diğer' in days_data[day_idx]['cols'] else 'diğer'
                            
                        days_data[day_idx]['cols'][col_name] += r['amount']

                # 4. Müşteri Çekleri Tahsilatı (Gelen Tahsilatlar)
                for r in customer_check_rows_all:
                    if r['VADE'] is not None and r['VADE'].year == yr and r['VADE'].month == mn and r['DURUM_KODU'] in (1, 3, 4):
                        days_data[r['VADE'].day - 1]['musteri_ceki'] += r['TUTAR']
                            
                # Kümülatif Kapanış Bakiyesi (Devreden Bakiye Yöntemi & Gün İçi Çarpıştırma)
                col_totals = {c: 0.0 for c in payment_cols}
                grand_total = 0.0
                total_musteri_ceki = 0.0
                initial_month_bank = None
                
                for d in days_data:
                    row_tot = sum(d['cols'][c] for c in payment_cols)
                    d['total'] = row_tot
                    grand_total += row_tot
                    total_musteri_ceki += d['musteri_ceki']
                    
                    # Gün içi çarpıştırma (devreden bakiyesiz saf o günün net nakit akışı)
                    d['gun_ici_net'] = d['musteri_ceki'] - row_tot
                    
                    if d['date'] < today:
                        # Geçmiş günler için banka bakiyesi devretmez
                        d['banka_bakiye'] = 0.0
                        d['net_durum'] = d['gun_ici_net']
                    else:
                        # Bugün ve Gelecek Günler: Mevcut paraya çekleri ekle, ödemeleri düş, kalan bakiyeyi devret
                        if initial_month_bank is None:
                            initial_month_bank = running_bank_balance
                        d['banka_bakiye'] = running_bank_balance
                        running_bank_balance = (running_bank_balance + d['musteri_ceki']) - row_tot
                        d['net_durum'] = running_bank_balance
                    
                    for c in payment_cols:
                        col_totals[c] += d['cols'][c]
                        
                display_month_bank = initial_month_bank if initial_month_bank is not None else 0.0
                total_gun_ici_net = total_musteri_ceki - grand_total
                total_net_durum = running_bank_balance if initial_month_bank is not None else total_gun_ici_net

                daily_breakdowns.append({
                    'month_str': m_str,
                    'month_title': month_title,
                    'days_data': days_data,
                    'col_totals': col_totals,
                    'grand_total': grand_total,
                    'total_musteri_ceki': total_musteri_ceki,
                    'total_banka_bakiye': display_month_bank,
                    'total_gun_ici_net': total_gun_ici_net,
                    'total_net_durum': total_net_durum
                })
            except Exception as ex:
                print(f"Error compiling daily breakdown for {m_str}: {ex}")

        # --- BÜTÇE HESAPLAMA VE KARŞILAŞTIRMA (DİNAMİK) ---
        selected_budget_month = request.values.get('budget_month') or request.values.get('selected_budget_month')
        if not selected_budget_month:
            try:
                selected_budget_month = datetime.strptime(selected_date, '%Y-%m-%d').strftime('%Y-%m')
            except Exception:
                selected_budget_month = datetime.now().strftime('%Y-%m')

        # Load all budgets
        budgets = load_budget_settings()
        budget_for_month = budgets.get(selected_budget_month, {})
        budget_income = budget_for_month.get('income', {})
        budget_expense = budget_for_month.get('expense', {})

        # Parse selected budget month
        try:
            b_yr, b_mn = map(int, selected_budget_month.split('-'))
        except Exception:
            b_yr, b_mn = today.year, today.month

        # Calculate Income Actuals
        actual_income_customer = 0.0
        for r in customer_check_rows_all:
            if r['VADE'] is not None and r['VADE'].year == b_yr and r['VADE'].month == b_mn and r['DURUM_KODU'] in (1, 3, 4):
                actual_income_customer += r['TUTAR']

        actual_income_other = 0.0

        # Incomes list
        income_categories = ["Müşteri Çekleri", "Diğer Gelirler"]
        budget_income_items = []
        total_budget_income = 0.0
        total_actual_income = 0.0

        for cat in income_categories:
            target = float(budget_income.get(cat, 0.0))
            actual = actual_income_customer if cat == "Müşteri Çekleri" else actual_income_other
            diff = actual - target
            pct = (actual / target * 100.0) if target > 0 else (100.0 if actual > 0 else 0.0)
            
            total_budget_income += target
            total_actual_income += actual
            
            budget_income_items.append({
                'category': cat,
                'target': target,
                'actual': actual,
                'diff': diff,
                'pct': pct
            })

        # Calculate Expense Actuals
        actual_expense_credit = 0.0
        for r in credit_rows_all:
            if r['VADE'] is not None and r['VADE'].year == b_yr and r['VADE'].month == b_mn:
                actual_expense_credit += r['TUTAR']

        actual_expense_own_check = 0.0
        for r in own_check_rows_all:
            if r['VADE'] is not None and r['VADE'].year == b_yr and r['VADE'].month == b_mn:
                actual_expense_own_check += r['TUTAR']

        # Custom category actuals
        custom_actuals = {cat: 0.0 for cat in custom_categories}
        for r in custom_rows_all:
            if r['date'] is not None and r['date'].year == b_yr and r['date'].month == b_mn:
                cat = r['category']
                if cat in custom_actuals:
                    custom_actuals[cat] += r['amount']
                else:
                    custom_actuals['Diğer'] += r['amount']

        # Expense categories list (dynamically generated)
        expense_categories = ['Banka Kredisi', 'Borç Çeki']
        for cat in custom_categories:
            if cat not in expense_categories and cat not in income_only_cats and cat != 'Diğer':
                expense_categories.append(cat)
        if 'Diğer' not in expense_categories:
            expense_categories.append('Diğer')
        budget_expense_items = []
        total_budget_expense = 0.0
        total_actual_expense = 0.0

        for cat in expense_categories:
            target = float(budget_expense.get(cat, 0.0))
            if cat == "Banka Kredisi":
                actual = actual_expense_credit
            elif cat == "Borç Çeki":
                actual = actual_expense_own_check
            else:
                actual = custom_actuals.get(cat, 0.0)
                
            diff = actual - target
            pct = (actual / target * 100.0) if target > 0 else (100.0 if actual > 0 else 0.0)
            
            total_budget_expense += target
            total_actual_expense += actual
            
            budget_expense_items.append({
                'category': cat,
                'target': target,
                'actual': actual,
                'diff': diff,
                'pct': pct
            })

        # Calculate Net Budget
        total_budget_net = total_budget_income - total_budget_expense
        total_actual_net = total_actual_income - total_actual_expense
        total_net_diff = total_actual_net - total_budget_net
        total_net_pct = (total_actual_net / total_budget_net * 100.0) if total_budget_net != 0 else (100.0 if total_actual_net > 0 else 0.0)

        # --- NAKİT AKIŞ SİMÜLASYON MATRİSİ HESAPLAMA ---
        try:
            sel_year = datetime.strptime(selected_date, '%Y-%m-%d').year
        except Exception:
            sel_year = datetime.now().year

        starting_cash = load_starting_cash(sel_year)

        cf_months_data = []
        current_mevcut = starting_cash
        
        tr_months_full = {
            1: 'Ocak', 2: 'Şubat', 3: 'Mart', 4: 'Nisan', 5: 'Mayıs', 6: 'Haziran',
            7: 'Temmuz', 8: 'Ağustos', 9: 'Eylül', 10: 'Ekim', 11: 'Kasım', 12: 'Aralık'
        }
        
        for m in range(1, 13):
            # 1. Inflows
            cek_tahsilat = sum(float(r['TUTAR']) for r in customer_check_rows_all if r['VADE'] is not None and r['VADE'].year == sel_year and r['VADE'].month == m)
            
            yeni_satis = sum(float(r['amount']) for r in custom_rows_all if r['date'] is not None and r['date'].year == sel_year and r['date'].month == m and r['category'] == 'Yeni Satış Tahsilat')
            if yeni_satis == 0.0:
                yeni_satis = float(budgets.get(f"{sel_year}-{m:02d}", {}).get('income', {}).get('Diğer Gelirler', 0.0))
                
            ch_tahsilat = sum(float(r['amount']) for r in custom_rows_all if r['date'] is not None and r['date'].year == sel_year and r['date'].month == m and r['category'] == 'C/H Tahsilat')
            
            girisler_toplam = cek_tahsilat + yeni_satis + ch_tahsilat
            mevcut_plus_girisler = current_mevcut + girisler_toplam
            
            # 2. Outflows
            satinalma = sum(float(r['amount']) for r in custom_rows_all if r['date'] is not None and r['date'].year == sel_year and r['date'].month == m and r['category'] == 'Navlun')
            cek_senet = sum(float(r['TUTAR']) for r in own_check_rows_all if r['VADE'] is not None and r['VADE'].year == sel_year and r['VADE'].month == m)
            maas = sum(float(r['amount']) for r in custom_rows_all if r['date'] is not None and r['date'].year == sel_year and r['date'].month == m and r['category'] in ('Maaş', 'Maaş Banka', 'Maaş II'))
            gider = sum(float(r['amount']) for r in custom_rows_all if r['date'] is not None and r['date'].year == sel_year and r['date'].month == m and r['category'] == 'Diğer')
            
            vergi_sgk = sum(float(r['amount']) for r in custom_rows_all if r['date'] is not None and r['date'].year == sel_year and r['date'].month == m and r['category'] in ('Vergi', 'SGK', 'KDV', '2 Nolu KDV', 'Vergiler'))
            
            kredi_leasing = sum(float(r['TUTAR']) for r in credit_rows_all if r['VADE'] is not None and r['VADE'].year == sel_year and r['VADE'].month == m)
            kredi_leasing += sum(float(r['amount']) for r in custom_rows_all if r['date'] is not None and r['date'].year == sel_year and r['date'].month == m and r['category'] == 'Kredi Kartı')
            
            cikislar_toplam = satinalma + cek_senet + maas + gider + vergi_sgk + kredi_leasing
            ending_balance = mevcut_plus_girisler - cikislar_toplam
            
            cf_months_data.append({
                'month_num': m,
                'month_name': tr_months_full[m],
                'mevcut': current_mevcut,
                'cek_tahsilat': cek_tahsilat,
                'yeni_satis': yeni_satis,
                'ch_tahsilat': ch_tahsilat,
                'girisler_toplam': girisler_toplam,
                'mevcut_plus_girisler': mevcut_plus_girisler,
                'satinalma': satinalma,
                'cek_senet': cek_senet,
                'maas': maas,
                'gider': gider,
                'vergi_sgk': vergi_sgk,
                'kredi_leasing': kredi_leasing,
                'cikislar_toplam': cikislar_toplam,
                'ending_balance': ending_balance
            })
            current_mevcut = ending_balance

        # JSON representation for raw view
        raw_json_str = json.dumps(all_custom, ensure_ascii=False, indent=2)

        # Net reconciliation (varlıklar hariç sol toplamlar ve sağ toplamlar çarpıştırması)
        sol_toplam_tl = total_mevcut_tl_all + total_unbilled_tl
        sol_toplam_usd = total_mevcut_usd_all + total_unbilled_usd
        sol_toplam_eur = total_mevcut_eur_all + total_unbilled_eur

        sag_toplam_tl = total_credit_tl_all + total_own_checks_tl_all + total_payables_tl
        sag_toplam_usd = total_credit_usd_all + total_own_checks_usd_all + total_payables_usd
        sag_toplam_eur = total_credit_eur_all + total_own_checks_eur_all + total_payables_eur

        recon_diff_tl = sol_toplam_tl - sag_toplam_tl
        recon_diff_usd = sol_toplam_usd - sag_toplam_usd
        recon_diff_eur = sol_toplam_eur - sag_toplam_eur

        ccards_list = ccards.to_dict('records') if (hasattr(ccards, 'to_dict') and not ccards.empty) else []
        credits_list = credits.to_dict('records') if (hasattr(credits, 'to_dict') and not credits.empty) else []
        own_checks_dict = own_checks.to_dict('records') if (hasattr(own_checks, 'to_dict') and not own_checks.empty) else []
        customer_checks_dict = customer_checks.to_dict('records') if (hasattr(customer_checks, 'to_dict') and not customer_checks.empty) else []

        return render_template("gunluk_nakit_akıs.html", 
                               aktif_sayfa='nakit_akis',
                               next_day_payments=next_day_payments,
                               next_business_date_formatted=next_business_date_formatted,
                               sol_toplam_tl=sol_toplam_tl,
                               sol_toplam_usd=sol_toplam_usd,
                               sol_toplam_eur=sol_toplam_eur,
                               sag_toplam_tl=sag_toplam_tl,
                               sag_toplam_usd=sag_toplam_usd,
                               sag_toplam_eur=sag_toplam_eur,
                               recon_diff_tl=recon_diff_tl,
                               recon_diff_usd=recon_diff_usd,
                               recon_diff_eur=recon_diff_eur,
                               incoming_transfers=incoming_transfers,
                               total_incoming_transfers_tl=total_incoming_transfers_tl,
                               incoming_total_tl=incoming_total_tl,
                               incoming_total_usd=incoming_total_usd,
                               incoming_total_eur=incoming_total_eur,
                               grouped_incoming=grouped_incoming,
                               incoming_totals=incoming_totals,
                               outgoing_transfers=outgoing_transfers,
                               total_outgoing_transfers_tl=total_outgoing_transfers_tl,
                               outgoing_total_tl=outgoing_total_tl,
                               outgoing_total_usd=outgoing_total_usd,
                               outgoing_total_eur=outgoing_total_eur,
                               grouped_outgoing=grouped_outgoing,
                               outgoing_totals=outgoing_totals,
                               bankalar_hepsi=bankalar_hepsi,
                               grouped_bank_balances_list=grouped_bank_balances_list,
                               total_bank_balance_tl=total_bank_balance_tl,
                               total_bank_balance_usd=total_bank_balance_usd,
                               total_bank_balance_eur=total_bank_balance_eur,
                               total_kasa_balance_tl=total_kasa_balance_tl,
                               total_kasa_balance_usd=total_kasa_balance_usd,
                               total_kasa_balance_eur=total_kasa_balance_eur,
                               total_customer_checks_tl=total_customer_checks_tl,
                               total_customer_checks_usd=total_customer_checks_usd,
                               total_customer_checks_eur=total_customer_checks_eur,
                               total_mevcut_tl=total_mevcut_tl,
                               total_mevcut_usd=total_mevcut_usd,
                               total_mevcut_eur=total_mevcut_eur,
                               credits_by_year=credits_by_year,
                               total_credit_anapara_all=total_credit_anapara_all,
                               total_credit_faiz_all=total_credit_faiz_all,
                               total_credit_tl_all=total_credit_tl_all,
                               total_credit_usd_all=total_credit_usd_all,
                               total_credit_eur_all=total_credit_eur_all,
                               total_receivables_tl=total_receivables_tl,
                               total_receivables_usd=total_receivables_usd,
                               total_receivables_eur=total_receivables_eur,
                               total_unbilled_tl=total_unbilled_tl,
                               total_unbilled_usd=total_unbilled_usd,
                               total_unbilled_eur=total_unbilled_eur,
                               tcmb_usd=tcmb_usd_formatted,
                               tcmb_eur=tcmb_eur_formatted,
                               assets=assets,
                               total_assets=total_assets,
                               asset_keys=ASSET_KEYS,
                               open_payables=open_payables,
                               total_payables_tl=total_payables_tl,
                               total_payables_usd=total_payables_usd,
                               total_payables_eur=total_payables_eur,
                               total_own_checks_tl=total_own_checks_tl,
                               total_own_checks_usd=total_own_checks_usd,
                               total_own_checks_eur=total_own_checks_eur,
                               total_own_checks_tl_all=total_own_checks_tl_all,
                               total_own_checks_usd_all=total_own_checks_usd_all,
                               total_own_checks_eur_all=total_own_checks_eur_all,
                               total_customer_checks_tl_all=total_customer_checks_tl_all,
                               total_customer_checks_usd_all=total_customer_checks_usd_all,
                               total_customer_checks_eur_all=total_customer_checks_eur_all,
                               total_mevcut_tl_all=total_mevcut_tl_all,
                               total_mevcut_usd_all=total_mevcut_usd_all,
                                total_mevcut_eur_all=total_mevcut_eur_all,
                               total_custom_payments=total_custom_payments,
                               total_custom_tl=total_custom_tl,
                               total_custom_usd=total_custom_usd,
                               total_custom_eur=total_custom_eur,
                               total_custom_usd_tl=total_custom_usd_tl,
                               total_custom_eur_tl=total_custom_eur_tl,
                               total_credit_card_payments=total_credit_card_payments,
                               raw_json_str=raw_json_str,
                               ccards=ccards_list, 
                               credits=credits_list, 
                               own_checks=own_checks_dict, 
                               customer_checks=customer_checks_dict,
                               credit_rows=credit_rows,
                               own_check_rows=own_check_rows,
                               customer_check_rows=customer_check_rows,
                               filtered_custom=filtered_custom,
                               selected_date=selected_date,
                               selected_date_formatted=selected_date_formatted,
                               total_ccards=total_ccards,
                               total_credits=total_credits,
                               total_credit_anapara=total_credit_anapara,
                               total_credit_faiz=total_credit_faiz,
                               total_own_checks=total_own_checks,
                               total_customer_checks=total_customer_checks,
                               total_tahsilat=total_tahsilat,
                               total_payments=total_payments,
                               net_cash_flow=net_cash_flow,
                               pct_ccards=pct_ccards,
                               pct_credits=pct_credits,
                               pct_own_checks=pct_own_checks,
                               custom_by_category=custom_by_category,
                               custom_totals=custom_totals,
                               custom_categories=custom_categories,
                               auto_clean=auto_clean,
                               monthly_summary_list=monthly_summary_list,
                               yearly_summary_list=yearly_summary_list,
                               matrix_rows=matrix_rows,
                               matrix_cols=matrix_cols,
                               matrix_col_totals=matrix_col_totals,
                               matrix_grand_total_payment=matrix_grand_total_payment,
                               matrix_grand_net_flow=matrix_grand_net_flow,
                               available_months=available_months,
                               selected_months=selected_months,
                               daily_breakdowns=daily_breakdowns,
                               payment_cols=payment_cols,
                               selected_budget_month=selected_budget_month,
                               budget_income_items=budget_income_items,
                               budget_expense_items=budget_expense_items,
                               total_budget_income=total_budget_income,
                               total_actual_income=total_actual_income,
                               total_budget_expense=total_budget_expense,
                               total_actual_expense=total_actual_expense,
                               total_budget_net=total_budget_net,
                               total_actual_net=total_actual_net,
                               total_net_diff=total_net_diff,
                               total_net_pct=total_net_pct,
                               budget_income=budget_income,
                               budget_expense=budget_expense,
                               income_categories=income_categories,
                               expense_categories=expense_categories,
                               cf_months_data=cf_months_data,
                               starting_cash=starting_cash,
                               sel_year=sel_year,
                               nexlog_categories=nexlog_categories,
                               now=datetime.now().strftime('%d.%m.%Y %H:%M'))
    except Exception as e:
        return f"Hata oluştu: {str(e)}"

@app.route('/save_custom_payments', methods=['POST'])
def save_custom_payments_route():
    idx_str = request.form.get('idx')
    category = request.form.get('category')
    date_str = request.form.get('date')
    explanation = request.form.get('explanation')
    amount_str = request.form.get('amount')
    doviz_str = request.form.get('doviz', 'TL').upper().strip()
    if doviz_str not in ('TL', 'USD', 'EUR'):
        doviz_str = 'TL'
    credit_card_pay_str = request.form.get('credit_card_pay')
    odendi_str = request.form.get('odendi')
    target_date = request.form.get('target_date', date_str)
    
    try:
        clean_amount = amount_str.replace('.', '').replace(',', '.') if amount_str else '0.0'
        amount = float(clean_amount)
    except ValueError:
        amount = 0.0
        
    payments = load_custom_payments()
    normalized_cat = normalize_category(category)
    cc_pay = credit_card_pay_str in ('true', 'on', True)
    od_val = 1 if odendi_str in ('true', 'on', True, '1') else 0
    
    new_payment = {
        'category': normalized_cat,
        'date': date_str,
        'explanation': explanation,
        'amount': amount,
        'doviz': doviz_str,
        'credit_card_pay': cc_pay,
        'odendi': od_val
    }
    
    if idx_str and idx_str.strip() != "":
        try:
            idx = int(idx_str)
            if 0 <= idx < len(payments):
                payments[idx] = new_payment
        except ValueError:
            pass
    else:
        payments.append(new_payment)
        
    save_custom_payments_list(payments)
    return redirect(url_for('dashboard', target_date=target_date))

@app.route('/toggle_payment_paid/<int:idx>', methods=['POST', 'GET'])
def toggle_payment_paid_route(idx):
    payments = load_custom_payments()
    if 0 <= idx < len(payments):
        current_status = payments[idx].get('odendi', 0)
        payments[idx]['odendi'] = 0 if current_status == 1 else 1
        save_custom_payments_list(payments)
        return jsonify({"success": True, "new_status": payments[idx]['odendi']})
    return jsonify({"success": False, "error": "Index out of range"}), 400

@app.route('/save_custom_payments_json', methods=['POST'])
def save_custom_payments_json_route():
    json_data_str = request.form.get('json_data')
    target_date = request.form.get('target_date', '')
    try:
        parsed_data = json.loads(json_data_str)
        if not isinstance(parsed_data, list):
            raise ValueError("JSON verisi bir liste (array) olmalıdır.")
        
        # Validation & Normalization
        normalized_data = []
        for idx, item in enumerate(parsed_data):
            if not isinstance(item, dict):
                raise ValueError(f"{idx}. eleman bir obje olmalıdır.")
            if 'category' not in item or 'date' not in item or 'amount' not in item:
                raise ValueError(f"{idx}. eleman eksik alan içeriyor (category, date veya amount gerekli).")
            
            normalized_data.append({
                'category': normalize_category(item.get('category')),
                'date': item.get('date'),
                'explanation': item.get('explanation', ''),
                'amount': float(item.get('amount', 0)),
                'credit_card_pay': bool(item.get('credit_card_pay', False)),
                'odendi': 1 if item.get('odendi', False) in (True, 1, '1', 'true') else 0
            })
            
        save_custom_payments_list(normalized_data)
    except Exception as e:
        return f"""
        <script>
            alert("HATA: JSON formatı geçersiz!\\n{str(e)}");
            window.history.back();
        </script>
        """
    if target_date:
        return redirect(url_for('dashboard', target_date=target_date))
    return redirect(url_for('dashboard'))

@app.route('/toggle_credit_card_payment/<int:idx>', methods=['POST', 'GET'])
def toggle_credit_card_payment_route(idx):
    payments = load_custom_payments()
    if 0 <= idx < len(payments):
        payments[idx]['credit_card_pay'] = not payments[idx].get('credit_card_pay', False)
        save_custom_payments_list(payments)
    
    target_date = request.values.get('target_date', '')
    if target_date:
        return redirect(url_for('dashboard', target_date=target_date))
    return redirect(url_for('dashboard'))

@app.route('/delete_custom_payment/<int:idx>', methods=['POST', 'GET'])
def delete_custom_payment_route(idx):
    payments = load_custom_payments()
    if 0 <= idx < len(payments):
        payments.pop(idx)
        save_custom_payments_list(payments)
    
    target_date = request.values.get('target_date', '')
    if target_date:
        return redirect(url_for('dashboard', target_date=target_date))
    return redirect(url_for('dashboard'))

@app.route('/delete_multiple_payments', methods=['POST'])
def delete_multiple_payments_route():
    target_date = request.form.get('target_date', '')
    indices_str = request.form.get('indices', '')
    
    if indices_str:
        try:
            # Parse and sort in descending order to avoid shift during popping
            indices = [int(x) for x in indices_str.split(',') if x.strip() != '']
            indices.sort(reverse=True)
            
            payments = load_custom_payments()
            for idx in indices:
                if 0 <= idx < len(payments):
                    payments.pop(idx)
            save_custom_payments_list(payments)
        except Exception:
            pass
            
    if target_date:
        return redirect(url_for('dashboard', target_date=target_date))
    return redirect(url_for('dashboard'))

@app.route('/toggle_auto_clean', methods=['GET', 'POST'])
def toggle_auto_clean_route():
    settings = load_custom_settings()
    settings['auto_clean_past'] = not settings.get('auto_clean_past', False)
    save_custom_settings(settings)
    
    # Run pruning immediately
    prune_expired_payments(force_all_expired=settings['auto_clean_past'])
        
    target_date = request.values.get('target_date', '')
    if target_date:
        return redirect(url_for('dashboard', target_date=target_date))
    return redirect(url_for('dashboard'))

@app.route('/clear_past_payments', methods=['GET', 'POST'])
def clear_past_payments_route():
    pruned = prune_expired_payments(force_all_expired=True)
    target_date = request.values.get('target_date', '')
    if target_date:
        return redirect(url_for('dashboard', target_date=target_date))
    return redirect(url_for('dashboard'))

@app.route('/clear_all_payments', methods=['POST'])
def clear_all_payments_route():
    save_custom_payments_list([])
    target_date = request.form.get('target_date', '')
    if target_date:
        return redirect(url_for('dashboard', target_date=target_date))
    return redirect(url_for('dashboard'))

@app.route('/save_budget', methods=['POST'])
def save_budget_route():
    target_date = request.form.get('target_date', '')
    budget_month = request.form.get('budget_month', '')
    
    if not budget_month:
        budget_month = datetime.now().strftime('%Y-%m')
        
    budgets = load_budget_settings()
    
    if budget_month not in budgets:
        budgets[budget_month] = {'income': {}, 'expense': {}}
        
    income_categories = ["Müşteri Çekleri", "Diğer Gelirler"]
    db_cats = [c['name'] for c in get_nexlog_categories()]
    income_only_cats = {'Yeni Satış Tahsilat', 'C/H Tahsilat', 'Müşteri Çekleri'}
    expense_categories = ['Banka Kredisi', 'Borç Çeki']
    for cat in db_cats:
        if cat not in expense_categories and cat not in income_only_cats and cat != 'Diğer':
            expense_categories.append(cat)
    if 'Diğer' not in expense_categories:
        expense_categories.append('Diğer')
    
    budgets[budget_month]['income'] = {}
    for cat in income_categories:
        val_str = request.form.get(f'inc_{cat}') or '0.0'
        try:
            budgets[budget_month]['income'][cat] = float(val_str)
        except ValueError:
            budgets[budget_month]['income'][cat] = 0.0
            
    budgets[budget_month]['expense'] = {}
    for cat in expense_categories:
        val_str = request.form.get(f'exp_{cat}') or '0.0'
        try:
            budgets[budget_month]['expense'][cat] = float(val_str)
        except ValueError:
            budgets[budget_month]['expense'][cat] = 0.0
            
    save_budget_settings(budgets)
    return redirect(url_for('dashboard', target_date=target_date, budget_month=budget_month, active_tab='budget-tab'))

@app.route('/save_starting_cash', methods=['POST'])
def save_starting_cash_route():
    target_date = request.form.get('target_date', '')
    year_str = request.form.get('year', '')
    amount_str = request.form.get('starting_cash', '540000.0')
    
    try:
        year = int(year_str)
    except ValueError:
        year = datetime.now().year
        
    try:
        # Remove thousands separators if any, and convert Turkish decimal comma to dot
        clean_amount = amount_str.replace('.', '').replace(',', '.')
        amount = float(clean_amount)
    except ValueError:
        amount = 540000.0
        
    save_starting_cash(year, amount)
    return redirect(url_for('dashboard', target_date=target_date, active_tab='flow-tab'))

@app.route('/save_assets', methods=['POST'])
def save_assets_route():
    target_date = request.form.get('target_date', '')
    active_tab = request.form.get('active_tab', 'ozet-tab')
    
    assets = load_assets()
    for key, label in ASSET_KEYS:
        val_str = request.form.get(f'asset_{key}_amount', '0.0')
        desc_str = request.form.get(f'asset_{key}_desc', '')
        try:
            # Handle standard numbers and browser numeric input
            clean_val = val_str.replace('.', '').replace(',', '.')
            amount = float(clean_val)
        except ValueError:
            amount = 0.0
            
        assets[key] = {
            'amount': amount,
            'description': desc_str
        }
            
    save_assets(assets)
    return redirect(url_for('dashboard', target_date=target_date, active_tab=active_tab))
@app.route('/api/get_categories', methods=['GET'])
def api_get_categories():
    categories = get_nexlog_categories()
    return jsonify(categories)

@app.route('/api/add_category', methods=['POST'])
def api_add_category():
    category_name = None
    if request.is_json:
        category_name = request.json.get('name', '').strip()
    else:
        category_name = request.form.get('name', '').strip()
        
    if not category_name:
        return jsonify({"success": False, "error": "Kategori adı boş olamaz"}), 400
    try:
        with engine_nexlog.begin() as conn:
            # Check duplicate
            dup = conn.execute(
                text("SELECT COUNT(*) FROM dbo.manuel_giris_kaotgeri WHERE LOWER(katogeri) = LOWER(:name)"),
                {"name": category_name}
            ).scalar()
            if dup > 0:
                return jsonify({"success": False, "error": "Bu kategori zaten mevcut"}), 400
                
            next_id = conn.execute(text("SELECT COALESCE(MAX(id), 0) + 1 FROM dbo.manuel_giris_kaotgeri")).scalar()
            conn.execute(
                text("INSERT INTO dbo.manuel_giris_kaotgeri (id, katogeri) VALUES (:id, :katogeri)"),
                {"id": next_id, "katogeri": category_name}
            )
        clear_nexlog_categories_cache()
        return jsonify({"success": True, "category": {"id": next_id, "name": category_name}})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/delete_category/<int:cat_id>', methods=['POST', 'DELETE'])
def api_delete_category(cat_id):
    try:
        with engine_nexlog.begin() as conn:
            conn.execute(
                text("DELETE FROM dbo.manuel_giris_kaotgeri WHERE id = :id"),
                {"id": cat_id}
            )
        clear_nexlog_categories_cache()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5046, debug=True)
