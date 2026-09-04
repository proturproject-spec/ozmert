@echo off
chcp 65001 > nul
title GitHub'a Yükle (Push)
cd /d "%~dp0"

echo ========================================================
echo        PROTUR YÖNETİM PANELİ - GITHUB YAYINLAMA
echo ========================================================
echo.

echo [1/2] Dosyalar pakete ekleniyor (requirements.txt, sayfalar, kodlar)...
git add -A
git commit -m "feat: Finans Paneli guncellemesi %date% %time%" >nul 2>&1

echo.
echo [2/2] GitHub'a gonderiliyor (git push)...
echo.
git push -u origin main --force

echo.
if %errorlevel% equ 0 (
    echo ========================================================
    echo  [BASARILI] Tum dosyalar GitHub'a yuklendi!
    echo  Simdi Render.com sayfasina donup 'Deploy' yapabilirsiniz.
    echo ========================================================
) else (
    echo ========================================================
    echo  [HATA] Yukleme sirasinda hata olustu.
    echo  Ekranda GitHub giris penceresi ciktiysa onaylayin.
    echo ========================================================
)

echo.
pause
