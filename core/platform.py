"""
Platform abstraction (Fix 01).

The cognitive core of Alontito (UCB1 math, memory, learning, pult, MCP) is
platform-independent. Only three drivers are Windows-only: window management
(user32), capture (GDI/win32gui) and input (DirectInput scan codes).

This module is the single place that knows about the OS. Drivers should call
require_windows() at build time and raise PlatformDriverError with an
actionable message instead of crashing with a bare ImportError on Linux/macOS.
"""
from __future__ import annotations

import sys

IS_WINDOWS = sys.platform == "win32"


class PlatformDriverError(RuntimeError):
    """Raised when a Windows-only driver is used on another OS."""


def require_windows(feature: str) -> None:
    """Guard for capture/window/input drivers. Call at the TOP of __init__."""
    if not IS_WINDOWS:
        raise PlatformDriverError(
            f"'{feature}' требует Windows (win32 API). "
            "Когнитивное ядро, память, обучение, пульт и MCP работают на любой ОС — "
            "запустите их, а драйверы экрана/ввода доступны только в Windows."
        )


def backend_name() -> str:
    """Human-readable backend label for status lines and the pult."""
    if IS_WINDOWS:
        return "win32 (DPI-aware capture + DirectInput)"
    return f"{sys.platform} (headless: no capture/input drivers)"
