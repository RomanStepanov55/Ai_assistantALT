"""Конфигурация бэкенда Alontito Control Center."""
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
APP_DIR = BACKEND_DIR.parent            # alontito-app
PROJECT_ROOT = APP_DIR.parent           # корень проекта

HOST = "127.0.0.1"
PORT = 8800

AGENT_ENTRY = PROJECT_ROOT / "main.py"
LOG_BUFFER_SIZE = 500