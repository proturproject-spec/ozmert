@echo off
chcp 65001 > nul
title GitHub'a Yükle (Push)
cd /d "%~dp0"

echo ========================================================
echo        PROTUR YÖNETİM PANELİ - GITHUB YAYINLAMA
echo ========================================================
echo.

echo [1/3] Değişiklikler taranıyor...
git status -s
echo.

echo [2/3] Değişiklikler pakete ekleniyor (git add)...
git add -A

echo.
set /p commit_msg="Commit mesajı girin (Boş bırakmak için Enter'a basın): "
if "%commit_msg%"=="" (
    set commit_msg=Güncelleme: %date% %time%
)

git commit -m "%commit_msg%"

echo.
echo [3/3] GitHub'a gönderiliyor (git push origin main)...
echo.
git push -u origin main --force

echo.
if %errorlevel% equ 0 (
    echo ========================================================
    echo  [BAŞARILI] Değişiklikler GitHub'a başarıyla yüklendi!
    echo ========================================================
) else (
    echo ========================================================
    echo  [HATA] Gönderim tamamlanamadı. 
    echo  Lütfen GitHub giriş penceresini kontrol ediniz.
    echo ========================================================
)

echo.
pause
