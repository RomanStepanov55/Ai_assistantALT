"""The agent's inbox: text, voice, images and files sent from any device.

Storage: data/alontito/inbox/ holds payload files; the index is an
append-only JSONL (data/alontito/inbox/index.jsonl) so the journal survives
crashes. Text items carry their content in the index; binary items are
written to files with a size cap. Nothing here interprets the content -
transcription (voice) and understanding (images) are separate stages.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = ROOT / "data" / "alontito" / "inbox"

MAX_BYTES = 20 * 1024 * 1024          # 20 MB per binary item
TEXT_KINDS = ("text",)
BINARY_KINDS = ("voice", "image", "file")
KINDS = TEXT_KINDS + BINARY_KINDS
STATES = ("new", "read", "processed", "dismissed")

EXT_FOR_KIND = {
    "voice": {".wav", ".m4a", ".ogg", ".oga", ".webm", ".mp3"},
    "image": {".png", ".jpg", ".jpeg", ".webp", ".gif"},
    "file":  None,                     # any extension, size-capped
}


class InboxError(ValueError):
    """Bad item: unknown kind, empty content or too big."""


class Inbox:
    def __init__(self, dir_path: Optional[Path] = None):
        self.dir = Path(dir_path) if dir_path else DEFAULT_DIR
        self.index = self.dir / "index.jsonl"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._last_id = 0
        for item in self._read():                              # ids must stay unique across restarts
            try:
                self._last_id = max(self._last_id, int(item.get("id") or 0))
            except (TypeError, ValueError):
                continue

    # ---------------------------------------------------------------- io

    def _append(self, item: dict) -> None:
        with self.index.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    def _rewrite(self, items: List[dict]) -> None:
        tmp = self.index.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n"
                               for i in items), encoding="utf-8")
        os.replace(tmp, self.index)

    def _read(self) -> List[dict]:
        if not self.index.exists():
            return []
        out = []
        for line in self.index.read_text(encoding="utf-8",
                                         errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    # ---------------------------------------------------------------- api

    def add(self, kind: str, content: bytes = b"", *,
            filename: str = "", note: str = "", text: str = "",
            now: Optional[float] = None) -> dict:
        if kind not in KINDS:
            raise InboxError(f"unknown inbox kind: {kind}")
        now = now if now is not None else time.time()
        self._last_id = max(int(now * 1000), self._last_id + 1)
        item = {
            "id": f"{self._last_id:d}",
            "ts": now,
            "at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "kind": kind,
            "state": "new",
            "note": (note or "")[:500],
            "filename": "",
            "size": 0,
            "transcript": None,
        }
        if kind == "text":
            if not (text or "").strip():
                raise InboxError("text item is empty")
            item["text"] = text[:20000]
            item["size"] = len(item["text"].encode("utf-8"))
        else:
            if not content:
                raise InboxError("binary item is empty")
            if len(content) > MAX_BYTES:
                raise InboxError(f"item is too big: {len(content)} > {MAX_BYTES}")
            ext = Path(filename or "").suffix.lower()
            allowed = EXT_FOR_KIND.get(kind)
            if allowed is not None and ext not in allowed:
                raise InboxError(f"bad extension for {kind}: {ext or '(none)'}")
            safe_name = f"{item['id']}{ext or '.bin'}"
            (self.dir / safe_name).write_bytes(content)
            item["filename"] = safe_name
            item["size"] = len(content)
        self._append(item)
        return item

    def list(self, state: Optional[str] = None,
             limit: int = 100) -> List[dict]:
        items = self._read()
        if state:
            items = [i for i in items if i.get("state") == state]
        return items[-max(1, limit):]

    def get(self, item_id: str) -> Optional[dict]:
        for item in reversed(self._read()):
            if item.get("id") == item_id:
                return item
        return None

    def read_file(self, item_id: str) -> Optional[bytes]:
        item = self.get(item_id)
        if not item or not item.get("filename"):
            return None
        try:
            return (self.dir / item["filename"]).read_bytes()
        except OSError:
            return None

    def set_state(self, item_id: str, state: str,
                  transcript: Optional[str] = None) -> bool:
        if state not in STATES:
            raise InboxError(f"unknown state: {state}")
        items = self._read()
        hit = False
        for item in items:
            if item.get("id") == item_id:
                item["state"] = state
                if transcript is not None:
                    item["transcript"] = transcript[:20000]
                hit = True
        if hit:
            self._rewrite(items)
        return hit
