@echo off
chcp 65001 >nul
title Alontito Omni Opus 5 System
echo [Alontito] Запуск автономного ИИ-ядра, Дашборда и 3D-ассистента...
if exist .venv\Scripts\activate.bat (
    call .venv\Scripts\activate.bat
)
python alontito_omni_launcher.py
pause
