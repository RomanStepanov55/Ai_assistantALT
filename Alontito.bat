@echo off
chcp 65001 >nul
title Alontito - personal AI agent
cd /d "%~dp0"
python alontito_launcher.py
if errorlevel 1 pause
