"""
SQL Köprü ve Güvenli Tünel Yöneticisi
sql_bridge.py servisini ve Cloudflare Tunnel (veya Ngrok) tünelini tek tıkla çalıştırır.
"""
import os
import sys
import subprocess
import time
import re
import signal

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_EXE = sys.executable

def main():
    print("=" * 60)
    print("   PROTUR - SQL KÖPRÜSÜ VE CANLI İNTERNET TÜNELİ")
    print("=" * 60)
    print()

    # 1. sql_bridge.py başlat
    print("[1/3] SQL Köprü API servisi başlatılıyor (Port 5001)...")
    bridge_script = os.path.join(BASE_DIR, "sql_bridge.py")
    env = os.environ.copy()
    if not env.get('BRIDGE_API_KEY'):
        env['BRIDGE_API_KEY'] = 'nexlog_bridge_2026_secure_xKj9'
    bridge_proc = subprocess.Popen([PYTHON_EXE, bridge_script], env=env)
    time.sleep(2)

    # 2. Cloudflare Tunnel başlat
    cf_exe = os.path.join(BASE_DIR, "cloudflared.exe")
    use_cf = os.path.exists(cf_exe)

    tunnel_proc = None
    tunnel_url = None

    if use_cf:
        print("[2/3] Cloudflare Güvenli Tüneli başlatılıyor (Üyelik/Token gerektirmez)...")
        tunnel_proc = subprocess.Popen(
            [cf_exe, "tunnel", "--url", "http://127.0.0.1:5001"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        print("[3/3] Canlı köprü internet adresi oluşturuluyor...")
        start_t = time.time()
        for line in tunnel_proc.stderr:
            match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
            if match:
                tunnel_url = match.group(0)
                break
            if time.time() - start_t > 15:
                break
    else:
        # Fallback to ngrok
        print("[2/3] Ngrok HTTP tüneli başlatılıyor (Port 5001)...")
        ngrok_exe = os.path.join(BASE_DIR, "ngrok.exe")
        tunnel_proc = subprocess.Popen(
            [ngrok_exe if os.path.exists(ngrok_exe) else "ngrok", "http", "5001"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        time.sleep(4)
        print("[3/3] Ngrok adresi alınıyor...")
        try:
            import urllib.request
            import json
            req = urllib.request.Request("http://127.0.0.1:4040/api/tunnels")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                for tun in data.get("tunnels", []):
                    if tun.get("proto") == "https":
                        tunnel_url = tun.get("public_url")
                        break
        except Exception:
            pass

    # Clipboard copy
    if tunnel_url:
        try:
            if sys.platform == "win32":
                subprocess.run("clip", input=tunnel_url.strip().encode("utf-8"), check=False)
                copied_msg = "  (Adres otomatik olarak panonuza kopyalandı! Ctrl+V yapabilirsiniz)"
            else:
                copied_msg = ""
        except Exception:
            copied_msg = ""

        print()
        print("=" * 60)
        print("  🎉 KÖPRÜ BAŞARIYLA OLUŞTURULDU!")
        print("=" * 60)
        print(f"  KÖPRÜ ADRESİ (BRIDGE_URL) : {tunnel_url}")
        if copied_msg:
            print(copied_msg)
        print()
        print("  👉 Render.com Dashboard -> Environment Sekmesine Ekleyiniz:")
        print(f"     BRIDGE_URL     = {tunnel_url}")
        print(f"     BRIDGE_API_KEY = nexlog_bridge_2026_secure_xKj9")
        print("=" * 60)
        print()
        print("  ⚠️  NOT:")
        print("  - Bu siyah pencere açık kaldığı sürece canlı sistem SQL'e bağlanabilir.")
        print("  - Kapatmak için bu pencereyi kapatabilir veya CTRL+C yapabilirsiniz.")
        print("=" * 60)
    else:
        print()
        print("=" * 60)
        print("  ❌ Tünel adresi otomatik alınamadı.")
        print("  Lütfen internet bağlantınızı kontrol edip tekrar deneyin.")
        print("=" * 60)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nKöprü kapatılıyor...")
    finally:
        if bridge_proc:
            bridge_proc.terminate()
        if tunnel_proc:
            tunnel_proc.terminate()

if __name__ == "__main__":
    main()
