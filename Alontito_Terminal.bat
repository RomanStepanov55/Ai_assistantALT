@echo off
chcp 65001 >nul
title Alontito Terminal
cd /d "%~dp0"
python alontito.py
if errorlevel 1 pause
