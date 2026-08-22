@echo off
title SQL Kopru + Ngrok Baslaniyor...
cd /d "C:\Users\AliYokus\Desktop\github_project"

echo ============================================
echo   SQL Kopru ve Ngrok Baslatiliyor...
echo ============================================
echo.

REM Ortam Değişkeni (Kod içinde değil, ortamda tutulur)
if "%BRIDGE_API_KEY%"=="" set BRIDGE_API_KEY=nexlog_bridge_2026_secure_xKj9

REM sql_bridge.py'yi port 5001'de baslat
echo [1/3] SQL Kopru baslatiliyor (port 5001)...
start "" /B "C:\Users\AliYokus\AppData\Local\Programs\Python\Python314\python.exe" sql_bridge.py

REM Koprunun hazir olmasini bekle
timeout /t 4 /nobreak > nul

REM ngrok ile 5001 portunu internete ac (HTTP tunel)
echo [2/3] Ngrok HTTP tuneli olusturuluyor (5001)...
start "" /B ngrok http 5001 --log=stdout > "%TEMP%\ngrok_bridge_log.txt" 2>&1

REM URL'nin olusmasini bekle
timeout /t 5 /nobreak > nul

echo [3/3] Ngrok URL'si aliniyor...
echo.
echo ============================================
for /f "tokens=*" %%a in ('powershell -Command "try { (Invoke-WebRequest -Uri http://127.0.0.1:4040/api/tunnels -UseBasicParsing).Content | ConvertFrom-Json | Select-Object -ExpandProperty tunnels | Where-Object {$_.proto -eq 'https'} | Select-Object -ExpandProperty public_url } catch { 'URL henuz hazir degil - http://127.0.0.1:4040 adresine bakin' }"') do (
    echo   KOPRU ADRESI: %%a
    echo.
    echo   Render'da su ayari yapiniz:
    echo   BRIDGE_URL = %%a
)
echo ============================================
echo.
echo NOT: Bu pencereyi KAPATIRSANIZ baglanti kesilir!
echo NOT: Ngrok URL her yeniden baslatmada degisir.
echo NOT: Degisen URL'yi Render'a tekrar girmeniz gerekir.
echo.
pause
