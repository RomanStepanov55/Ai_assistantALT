"""Skill synthesis: the agent writes its own tools, guarded by construction.

This is how Alontito grows abilities WITHOUT a human writing code and
WITHOUT a third-party runtime model: the OWN brain drafts a tool, and the
draft is only allowed to exist after three independent gates:

  1. AST gate     - SafeToolValidator allows ONLY pure computation modules
                    (math, json, re, ...) and rejects dangerous calls
                    (open, eval, exec, system, ...) before any execution.
  2. Runtime gate - the module must expose run(params) -> dict and survive a
                    smoke call with a timeout watchdog; a hung or crashing
                    tool is rejected, not registered.
  3. Registry     - every accepted tool is recorded with provenance (task,
                    test result) in manifest.json; unregistered files are
                    never auto-loaded.

The validator here is canonical; brain/opus_local_core.py re-exports it.
Generated tools run in-process AFTER the AST gate - the same trust level the
Omni lab already had, minus the third-party requests dependency.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


class ToolSecurityViolation(Exception):
    """Generated code tried something the AST gate forbids."""


class SafeToolValidator(ast.NodeVisitor):
    """Whitelist gate: only pure computation modules may be imported, and a
    fixed set of dangerous calls is refused. Everything else is rejected -
    a generated tool has no business touching the network, files or processes."""

    ALLOWED_MODULES = {
        "math", "json", "time", "re", "statistics", "datetime",
        "collections", "itertools", "string", "random", "decimal",
        "fractions", "typing", "functools",
    }
    FORBIDDEN_CALLS = {
        "eval", "exec", "compile", "__import__", "open", "system", "popen",
        "ReadProcessMemory", "WriteProcessMemory", "VirtualAllocEx",
        "remove", "unlink", "rmtree", "input",
    }

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root_mod = alias.name.split(".")[0]
            if root_mod not in self.ALLOWED_MODULES:
                raise ToolSecurityViolation(
                    f"Запрещённый импорт модуля: {alias.name} (разрешены только "
                    f"{', '.join(sorted(self.ALLOWED_MODULES))})")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            root_mod = node.module.split(".")[0]
            if root_mod not in self.ALLOWED_MODULES:
                raise ToolSecurityViolation(f"Запрещённый импорт из модуля: {node.module}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr
        if func_name in self.FORBIDDEN_CALLS:
            raise ToolSecurityViolation(f"Запрещённый вызов функции: {func_name}")
        self.generic_visit(node)


def validate_code(code: str) -> ast.Module:
    """Parse + AST check + entry-point check. Raises on any violation."""
    tree = ast.parse(code)
    SafeToolValidator().visit(tree)
    if not any(isinstance(n, ast.FunctionDef) and n.name == "run" for n in tree.body):
        raise ToolSecurityViolation(
            "модуль не содержит обязательную точку входа run(params)")
    return tree


def extract_code(raw: str) -> str:
    """Pull the python block out of a model reply. No block = the reply itself."""
    raw = raw or ""
    if "```python" in raw:
        return raw.split("```python", 1)[1].split("```", 1)[0].strip()
    if "```" in raw:
        return raw.split("```", 1)[1].split("```", 1)[0].strip()
    return raw.strip()


class SkillSynthesizer:
    """Create, test, register and load self-made tools."""

    def __init__(self, tools_dir: Optional[str] = None,
                 test_timeout: float = 5.0):
        default_dir = Path(__file__).resolve().parent.parent / "tools" / "auto_generated"
        self.dir = Path(tools_dir) if tools_dir else default_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.manifest = self.dir / "manifest.json"
        self.test_timeout = test_timeout
        self.registered: Dict[str, Callable] = {}
        self._load_existing()

    # ------------------------------------------------------------- registry
    def _load_existing(self) -> None:
        for file_path in sorted(self.dir.glob("*.py")):
            self._load_file(file_path)

    def _load_file(self, path: Path) -> bool:
        """Load a file only if the AST gate passes it right now."""
        try:
            code = path.read_text(encoding="utf-8")
            validate_code(code)
            spec = importlib.util.spec_from_file_location(path.stem, str(path))
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "run") and callable(mod.run):
                    self.registered[path.stem] = mod.run
                    return True
        except Exception:
            pass
        return False

    def _read_manifest(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.manifest.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def list(self) -> List[Dict[str, Any]]:
        """Registered tools with provenance. Unregistered files are not listed."""
        man = self._read_manifest()
        out = []
        for name, fn in sorted(self.registered.items()):
            info = man.get(name) or {}
            out.append({"name": name, "task": info.get("task", ""),
                        "created": info.get("created", ""),
                        "tested": info.get("tested", False)})
        return out

    # ------------------------------------------------------------- synthesis
    def synthesize(self, name: str, task: str,
                   chat_fn: Optional[Callable[..., str]] = None) -> Tuple[bool, str]:
        """Own brain drafts -> AST gate -> smoke test -> register.

        chat_fn comes from the own brain only (same door as the runtime
        chat); a missing brain is an honest refusal, not an empty tool.
        """
        safe_name = "".join(c for c in name if c.isalnum() or c == "_").lower()
        if not safe_name:
            return False, "Некорректное имя навыка."
        if chat_fn is None:
            return False, "свой мозг не подключён: синтез навыков требует модель"
        if safe_name in self.registered:
            return False, f"навык `{safe_name}` уже существует"

        prompt = (
            f"Напиши чистый Python-модуль для навыка `{safe_name}`.\n"
            f"Задача: {task}\n"
            "Требования:\n"
            "1. Модуль содержит функцию `def run(params: dict) -> dict:`.\n"
            "2. Только стандартная библиотека: math, json, time, re, statistics, "
            "datetime, collections, itertools, string, random.\n"
            "3. ЗАПРЕЩЕНО: os.system, subprocess, ctypes, socket, open, "
            "eval, exec, сеть, файлы.\n"
            "4. Функция чистая: считает результат из params и возвращает dict.\n"
            "5. Верни ТОЛЬКО код внутри блока ```python ... ```.")
        raw = chat_fn([{"role": "user", "content": prompt}])
        code = extract_code(raw)
        try:
            validate_code(code)
        except ToolSecurityViolation as exc:
            return False, f"навык отклонён стражем AST: {exc}"
        except SyntaxError as exc:
            return False, f"навык отклонён: синтаксическая ошибка ({exc})"

        path = self.dir / f"{safe_name}.py"
        path.write_text(code, encoding="utf-8")
        ok, detail = self._smoke_test(path)
        if not ok:
            path.unlink(missing_ok=True)
            return False, f"навык отклонён автотестом: {detail}"

        if self._load_file(path):
            manifest = self._read_manifest()
            manifest[safe_name] = {
                "task": task[:300], "created": time.strftime("%Y-%m-%d %H:%M"),
                "tested": True,
            }
            self._write_manifest(manifest)
            return True, f"навык `{safe_name}` создан, проверен и подключён."
        path.unlink(missing_ok=True)
        return False, "загрузка после проверки не удалась"

    def _write_manifest(self, data: Dict[str, Any]) -> None:
        tmp = self.manifest.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, self.manifest)

    def remove(self, name: str) -> bool:
        """Rollback of a synthesized tool: file, manifest entry, registration."""
        if name not in self.registered:
            return False
        self.registered.pop(name, None)
        path = self.dir / f"{name}.py"
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        manifest = self._read_manifest()
        if name in manifest:
            del manifest[name]
            self._write_manifest(manifest)
        return True

    # ------------------------------------------------------------- smoke test
    def _smoke_test(self, path: Path) -> Tuple[bool, str]:
        """run({}) must finish inside the timeout and return a dict."""
        box: Dict[str, Any] = {}

        def target():
            try:
                code = path.read_text(encoding="utf-8")
                validate_code(code)
                spec = importlib.util.spec_from_file_location(path.stem, str(path))
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                box["result"] = mod.run({})
            except Exception as exc:                        # noqa: BLE001
                box["error"] = str(exc)

        worker = threading.Thread(target=target, daemon=True)
        worker.start()
        worker.join(self.test_timeout)
        if worker.is_alive():
            return False, "завис (превышен таймаут)"
        if "error" in box:
            return False, box["error"][:200]
        if not isinstance(box.get("result"), dict):
            return False, "run({}) вернул не dict"
        return True, ""
