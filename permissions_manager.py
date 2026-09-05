"""
permissions_manager.py - Genişletilebilir Boyut ve Veri Tabanlı Yetkilendirme Yöneticisi (DBAC)

Bu modül, kullanıcıların sayfa yetkilerinin yanı sıra belirli veri boyutlarına
(Kasalar, Bankalar, Depolar, Şubeler vb.) erişimlerini kısıtlamak ve filtrelemek için
merkezi ve standart bir yapı sağlar.
"""
from flask import session

def get_current_user():
    """Flask session'ındaki aktif kullanıcı sözlüğünü döner."""
    try:
        return session.get('user')
    except Exception:
        return None

def is_admin(user=None):
    """Kullanıcının admin olup olmadığını kontrol eder."""
    u = user if user is not None else get_current_user()
    return bool(u and u.get('role') == 'admin')

def get_user_allowed_items(dimension, user=None):
    """
    Belirli bir boyut için kullanıcının izinli olduğu kod listesini döner.
    Admin ise veya kısıtlama yoksa ['*'] döner (her şeye izinli).
    
    Örnek dimension anahtarları:
      - 'kasalar' -> user.get('allowed_kasalar')
      - 'bankalar' -> user.get('restrictions', {}).get('bankalar')
      - 'depolar'  -> user.get('restrictions', {}).get('depolar')
    """
    u = user if user is not None else get_current_user()
    if not u:
        return []
    
    if u.get('role') == 'admin':
        return ['*']
    
    # Doğrudan tanımlı alan (örn: allowed_kasalar, allowed_pages)
    direct_key = f"allowed_{dimension}"
    if direct_key in u:
        val = u.get(direct_key)
        if val is None or '*' in val:
            return ['*']
        return [str(x).strip() for x in val if str(x).strip()]
    
    # Genişletilebilir 'restrictions' nesnesi içinden oku
    restrictions = u.get('restrictions') or {}
    if dimension in restrictions:
        val = restrictions.get(dimension)
        if val is None or '*' in val:
            return ['*']
        return [str(x).strip() for x in val if str(x).strip()]
    
    # Kısıtlama tanımlanmamışsa varsayılan olarak serbest ('*')
    return ['*']

def is_item_allowed(dimension, item_code, user=None):
    """Tek bir öğe kodunun (örn: Kasa Kodu '01') izinli olup olmadığını doğrular."""
    allowed = get_user_allowed_items(dimension, user)
    if '*' in allowed:
        return True
    return str(item_code).strip() in allowed

def filter_items_by_permission(items, dimension, code_key='CODE', user=None):
    """
    Sözlük listesini (dict list) yetkili kodlara göre süzer.
    Örnek: filter_items_by_permission(kasalar, 'kasalar', code_key='CODE')
    """
    allowed = get_user_allowed_items(dimension, user)
    if '*' in allowed:
        return items
    
    allowed_set = set(str(x).strip() for x in allowed)
    filtered = []
    for item in items:
        if isinstance(item, dict):
            val = str(item.get(code_key, '')).strip()
            if val in allowed_set:
                filtered.append(item)
    return filtered

def build_sql_dimension_filter(dimension, column_sql, user=None, allowed_items=None):
    """
    SQL sorgusuna doğrudan eklenebilecek WHERE koşulunu döner.
    
    Örnek Kullanım:
      filter_sql = build_sql_dimension_filter('kasalar', 'KS.CODE', user=user)
      if filter_sql:
          where_clauses.append(filter_sql)
          
    Dönüş Değeri:
      - Sınırlama yoksa ('*' varsa): None (ekstra WHERE şartı gerekmez)
      - Sınırlama varsa: "KS.CODE IN ('01', '02')"
      - Hiç yetki yoksa (boş liste): "1 = 0"
    """
    if allowed_items is None:
        allowed = get_user_allowed_items(dimension, user)
    else:
        allowed = allowed_items

    if '*' in allowed:
        return None
    
    if not allowed:
        return "1 = 0"
    
    # Güvenli tırnak içine alma (SQL injection önlemi)
    escaped = [str(x).replace("'", "''").strip() for x in allowed if str(x).strip()]
    if not escaped:
        return "1 = 0"
        
    in_clause = ", ".join(f"'{x}'" for x in escaped)
    return f"{column_sql} IN ({in_clause})"
