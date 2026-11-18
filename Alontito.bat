@echo off
chcp 65001 >nul
title Alontito - Open Source Bot
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python не найден в PATH. Установи Python 3.10+ и повтори.
  pause
  exit /b 1
)

python alontito.py
set CODE=%ERRORLEVEL%
if not "%CODE%"=="0" (
  echo.
  echo Алонтито завершился с кодом %CODE%.
  pause
)
