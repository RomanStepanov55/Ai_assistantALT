@echo off
chcp 65001 >nul
title Alontito MCP Server
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python не найден в PATH.
  pause
  exit /b 1
)

python -m alontito.mcp_server
