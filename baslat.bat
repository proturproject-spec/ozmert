@echo off
title Finans Muhasebe Paneli - Baslaniyor...
cd /d "%~dp0"

echo ============================================
echo   Finans Muhasebe Paneli Baslatiliyor...
echo ============================================
echo.

REM Ortam Değişkenlerini Tanımla (Kod içinde değil, ortamda tutulur)
if "%SECRET_KEY%"=="" set SECRET_KEY=nexlog_finans_secret_key_2026_local
if "%BRIDGE_API_KEY%"=="" set BRIDGE_API_KEY=nexlog_bridge_2026_secure_xKj9

REM Python yolunu belirle (.venv öncelikli)
set PYTHON_EXE=python
if exist "%~dp0.venv\Scripts\python.exe" (
    set PYTHON_EXE="%~dp0.venv\Scripts\python.exe"
)

REM Flask'i arka planda baslat
start "" /B %PYTHON_EXE% app.py

REM Flask'in hazir olmasini bekle
echo Flask baslatiliyor, bekleyin...
timeout /t 3 /nobreak > nul

REM ngrok ile internete ac
echo Ngrok tunel olusturuluyor...
start "" /B ngrok http 5000 --log=stdout > "%TEMP%\ngrok_log.txt" 2>&1

REM URL'nin olusmasini bekle
timeout /t 4 /nobreak > nul

REM ngrok API'den URL'yi al ve goster
echo.
echo ============================================
for /f "tokens=*" %%a in ('powershell -Command "try { (Invoke-WebRequest -Uri http://127.0.0.1:4040/api/tunnels -UseBasicParsing).Content | ConvertFrom-Json | Select-Object -ExpandProperty tunnels | Where-Object {$_.proto -eq 'https'} | Select-Object -ExpandProperty public_url } catch { 'URL henuz hazir degil - tarayicida http://127.0.0.1:4040 adresine bakin' }"') do (
    echo   INTERNET ADRESI: %%a
)
echo   LOKAL ADRES    : http://127.0.0.1:5000
echo ============================================
echo.
echo Kapatmak icin bu pencereyi kapatin.
echo Not: ngrok penceresi kapanirsa internet erisimi kesilir.
pause
