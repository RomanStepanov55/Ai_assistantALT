@echo off
chcp 65001 >nul
title Alontito - Open Source Bot
cd /d "%~dp0"
python alontito.py
if errorlevel 1 pause
