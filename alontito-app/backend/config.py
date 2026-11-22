"""Конфигурация бэкенда Alontito Control Center."""
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
APP_DIR = BACKEND_DIR.parent
PROJECT_ROOT = APP_DIR.parent

HOST = "127.0.0.1"
PORT = 8800

AGENT_ENTRY = PROJECT_ROOT / "main.py"
LOG_BUFFER_SIZE = 500

DATA_DIR = PROJECT_ROOT / "data"
TELEMETRY_FILE = DATA_DIR / "telemetry.log"
MEMORY_FILE = DATA_DIR / "memory.json"
CAPS_FILE = DATA_DIR / "capabilities.json"