"""PC actions: a tiny, whitelisted surface - OFF by default.

The agent may propose an action («открой папку X»), every paired device
receives a confirm request, and the action executes only after an explicit
✓ from any device. There is no arbitrary shell, no arbitrary process launch,
no network. The whole thing is dormant until the user enables it in the app
settings ("pc_actions": true in devices.json - a decision made from the PC).

Allowed actions and their honest limits:
  open_url   - opens a http(s) link in the default browser
  open_path  - opens a file or folder, ONLY inside the allowed roots
  list_dir   - lists a folder, ONLY inside the allowed roots
  read_text  - reads a text file (≤ 256 KB), ONLY inside the allowed roots
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "data" / "alontito" / "pc_actions.json"
MAX_TEXT_BYTES = 256 * 1024
CONFIRM_TTL = 120                       # seconds to confirm a request


class PcActionsDisabled(RuntimeError):
    """pc_actions is off: nothing executes, not even a "harmless" listing."""


class ActionDenied(ValueError):
    """The action is outside the whitelist or outside the allowed roots."""


class PcActions:
    def __init__(self, path: Optional[Path] = None,
                 clock: Optional[Callable[[], float]] = None):
        self.path = Path(path) if path else DEFAULT_PATH
        self.clock = clock or time.time
        self._lock = threading.Lock()
        self._requests: Dict[str, dict] = {}
        self.allowed_roots: List[Path] = [Path.home() / "Desktop", ROOT]
        self._load()

    # ------------------------------------------------------------ config

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        self.enabled = bool(raw.get("enabled", False))
        roots = raw.get("allowed_roots") or []
        self.allowed_roots = [Path(r).expanduser() for r in roots] or \
            [Path.home() / "Desktop", ROOT]

    def set_enabled(self, enabled: bool) -> dict:
        self.enabled = bool(enabled)
        self._save()
        return self.state()

    def set_roots(self, roots: List[str]) -> dict:
        self.allowed_roots = [Path(r).expanduser() for r in roots if r]
        self._save()
        return self.state()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "enabled": self.enabled,
            "allowed_roots": [str(r) for r in self.allowed_roots],
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def state(self) -> dict:
        return {"enabled": self.enabled,
                "allowed_roots": [str(r) for r in self.allowed_roots]}

    # ------------------------------------------------------------ confirm

    def propose(self, action: str, **params: Any) -> dict:
        """Stage one: the agent proposes; nothing happens yet."""
        if action not in ("open_url", "open_path", "list_dir", "read_text"):
            raise ActionDenied(f"действие «{action}» не в белом списке")
        request_id = f"{int(self.clock() * 1000):d}"
        with self._lock:
            now = self.clock()
            for rid in [r for r, v in self._requests.items()
                        if v["expires"] < now]:
                del self._requests[r]
            self._requests[request_id] = {
                "action": action, "params": params, "expires": now + CONFIRM_TTL,
            }
        return {"request_id": request_id, "action": action,
                "params": params, "expires_in": CONFIRM_TTL}

    def confirm(self, request_id: str) -> dict:
        """Stage two: a device said ✓; executes ONLY now, ONLY if enabled."""
        if not self.enabled:
            raise PcActionsDisabled(
                "действия на ПК выключены: включите их в Настройках с этого ПК")
        with self._lock:
            request = self._requests.pop(request_id, None)
        if request is None or request["expires"] < self.clock():
            raise ActionDenied("запрос не найден или истёк — попросите агента повторить")
        action, params = request["action"], request["params"]
        result = {"action": action, "ok": False}
        if action == "open_url":
            url = str(params.get("url") or "")
            if not url.lower().startswith(("http://", "https://")):
                raise ActionDenied("открывать можно только http(s)-ссылки")
            webbrowser.open(url)
            result["ok"] = True
            result["detail"] = f"открыл ссылку в браузере: {url}"
        elif action == "open_path":
            p = self._inside_roots(str(params.get("path") or ""))
            os.startfile(str(p))            # noqa: S606 - whitelist-gated above
            result["ok"] = True
            result["detail"] = f"открыл: {p}"
        elif action == "list_dir":
            p = self._inside_roots(str(params.get("path") or ""))
            entries = sorted(e.name + ("/" if e.is_dir() else "")
                             for e in p.iterdir())[:200]
            result["ok"] = True
            result["detail"] = f"{p}:\n" + "\n".join(entries)
        elif action == "read_text":
            p = self._inside_roots(str(params.get("path") or ""))
            if p.stat().st_size > MAX_TEXT_BYTES:
                raise ActionDenied("файл больше 256 КБ — читайте частями")
            text = p.read_text(encoding="utf-8", errors="replace")
            result["ok"] = True
            result["detail"] = text[:MAX_TEXT_BYTES]
        return result

    def pending(self) -> List[dict]:
        with self._lock:
            now = self.clock()
            return [{"request_id": rid, "action": v["action"],
                     "params": v["params"],
                     "expires_in": round(v["expires"] - now, 1)}
                    for rid, v in self._requests.items() if v["expires"] >= now]

    def _inside_roots(self, raw: str) -> Path:
        p = Path(raw).expanduser().resolve()
        if not any(_is_relative_to(p, root.resolve()) for root in self.allowed_roots):
            raise ActionDenied(
                f"путь {p} вне разрешённых папок: "
                + "; ".join(str(r) for r in self.allowed_roots))
        return p


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        return child.is_relative_to(parent)
    except AttributeError:                  # Python < 3.9
        return str(child).startswith(str(parent))
