@echo off
chcp 65001 > nul
title SQL Köprüsü ve Canlı Tünel
cd /d "%~dp0"

REM Ortam Değişkeni (Kod içinde değil, ortamda tutulur)
if "%BRIDGE_API_KEY%"=="" set BRIDGE_API_KEY=nexlog_bridge_2026_secure_xKj9

REM Python yolunu belirle (.venv öncelikli)
set PYTHON_EXE=python
if exist "%~dp0.venv\Scripts\python.exe" (
    set PYTHON_EXE="%~dp0.venv\Scripts\python.exe"
)

%PYTHON_EXE% bridge_runner.py

pause
