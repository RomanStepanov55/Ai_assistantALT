import ast
import json
import time
import queue
import threading
import importlib.util
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple
import requests
from brain.experience_memory import ExperienceMemory
from brain.learning_engine import LearningEngine


class OmniEventBus:
    def __init__(self):
        self._subscribers: List[queue.Queue] = []
        self._lock = threading.Lock()
        self.latest_thoughts: List[str] = []

    def subscribe(self) -> queue.Queue:
        q = queue.Queue(maxsize=200)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def publish(self, event_type: str, payload: Dict[str, Any]) -> None:
        event = {"ts": time.time(), "type": event_type, "data": payload}
        if event_type == "thinking_chunk":
            self.latest_thoughts.append(payload.get("text", ""))
            if len(self.latest_thoughts) > 500:
                self.latest_thoughts = self.latest_thoughts[-250:]
        with self._lock:
            for q in list(self._subscribers):
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass


def emit_pet_command(action: str, prop: Optional[str] = None, emotion: Optional[str] = None) -> None:
    packet = {"action": action}
    if prop:
        packet["prop"] = prop
    if emotion:
        packet["emotion"] = emotion
    print(f"@@PET@@ {json.dumps(packet, ensure_ascii=False)}", flush=True)


class ToolSecurityViolation(Exception):
    pass


class SafeToolValidator(ast.NodeVisitor):
    FORBIDDEN_MODULES = {
        "ctypes", "pymem", "subprocess", "socket", "win32process",
        "win32security", "shutil", "pickle", "marshal", "pty", "telnetlib"
    }
    FORBIDDEN_CALLS = {
        "eval", "exec", "compile", "__import__", "open", "system", "popen",
        "ReadProcessMemory", "WriteProcessMemory", "VirtualAllocEx"
    }

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root_mod = alias.name.split(".")[0]
            if root_mod in self.FORBIDDEN_MODULES:
                raise ToolSecurityViolation(f"Запрещённый импорт модуля: {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            root_mod = node.module.split(".")[0]
            if root_mod in self.FORBIDDEN_MODULES:
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


@dataclass
class OpusConfig:
    ollama_url: str = "http://127.0.0.1:11434"
    fast_model: str = "qwen2.5:7b-instruct"
    deep_model: str = "alontito-opus:latest"
    fallback_deep_model: str = "deepseek-r1:14b"
    vision_model: str = "qwen2.5vl:7b"
    max_context_messages: int = 16
    tools_dir: str = "tools/auto_generated"
    dataset_path: str = "data/self_learning_dataset.jsonl"
    knowledge_path: str = "KNOWLEDGE.md"
    memory_db_path: str = "data/alontito_memory.sqlite3"
    learning_db_path: str = "data/alontito_learning.sqlite3"
    reviewer_model: str = "qwen2.5:7b-instruct"
    max_review_rounds: int = 2
    request_timeout: int = 120

    @classmethod
    def from_json(cls, path: str = "config_omni.json") -> "OpusConfig":
        p = Path(path)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            return cls(
                ollama_url=data.get("ollama_url", cls.ollama_url),
                fast_model=data.get("fast_model", cls.fast_model),
                deep_model=data.get("deep_model", cls.deep_model),
                fallback_deep_model=data.get("fallback_deep_model", cls.fallback_deep_model),
                vision_model=data.get("vision_model", cls.vision_model),
                max_context_messages=int(data.get("max_context_messages", 16)),
                tools_dir=data.get("tools_dir", "tools/auto_generated"),
                dataset_path=data.get("dataset_path", "data/self_learning_dataset.jsonl"),
                knowledge_path=data.get("knowledge_path", "KNOWLEDGE.md"),
                memory_db_path=data.get("memory_db_path", "data/alontito_memory.sqlite3"),
                learning_db_path=data.get("learning_db_path", "data/alontito_learning.sqlite3"),
                reviewer_model=data.get("reviewer_model", data.get("fast_model", "qwen2.5:7b-instruct")),
                max_review_rounds=max(1, min(3, int(data.get("max_review_rounds", 2)))),
                request_timeout=int(data.get("request_timeout", 120)),
            )
        return cls()


class OpusLocalCore:
    SYSTEM_PROMPT = (
        "Ты — Алонтито (Alontito), автономный саморазвивающийся AI-агент и 3D-ассистент рабочего стола. "
        "Всегда рассуждай строго внутри тегов <thinking>...</thinking> перед финальным ответом. "
        "СТРОГИЕ ПРАВИЛА БЕЗОПАСНОСТИ: запрещены любые DLL/Lua-инъекции, чтение памяти процессов, "
        "эксплойты, трейды в играх и действия с реальными деньгами/Robux. "
        "Разрешена только честная визуальная навигация и безопасная эмуляция ввода."
    )

    def __init__(self, config: Optional[OpusConfig] = None, event_bus: Optional[OmniEventBus] = None):
        self.cfg = config or OpusConfig.from_json()
        self.bus = event_bus or OmniEventBus()
        self.history: List[Dict[str, str]] = []
        self.compacted_summary: str = ""
        self.registered_tools: Dict[str, Callable] = {}
        self.last_interaction: Optional[Dict[str, Any]] = None
        self.memory = ExperienceMemory(self.cfg.memory_db_path)
        self.learning = LearningEngine(self.cfg.learning_db_path, self.cfg.dataset_path)
        self._think_lock = threading.RLock()

        Path(self.cfg.tools_dir).mkdir(parents=True, exist_ok=True)
        Path(self.cfg.dataset_path).parent.mkdir(parents=True, exist_ok=True)
        self._load_existing_tools()

    def _resolve_deep_model(self) -> str:
        try:
            resp = requests.get(f"{self.cfg.ollama_url}/api/tags", timeout=2.5)
            if resp.status_code == 200:
                names = [m["name"] for m in resp.json().get("models", [])]
                if any(self.cfg.deep_model in n for n in names):
                    return self.cfg.deep_model
        except Exception:
            pass
        return self.cfg.fallback_deep_model

    def route_effort(self, query: str, has_vision: bool = False) -> str:
        q_lower = query.lower()
        if any(w in q_lower for w in ["напиши инструмент", "создай модуль", "архитектур", "стратег", "почему упал"]):
            return "max"
        if has_vision or any(w in q_lower for w in ["проанализируй", "план", "исследуй", "ucb", "оптимиз"]):
            return "high"
        if len(query) > 80:
            return "medium"
        return "low"

    def compact_context_if_needed(self) -> None:
        if len(self.history) <= self.cfg.max_context_messages:
            return
        old_chunk = self.history[:-6]
        self.history = self.history[-6:]
        digest = "\n".join(f"{m['role']}: {m['content'][:250]}" for m in old_chunk)
        prompt = (
            "Сожми историю диалога в краткую сводку фактов, достигнутых результатов UCB1 и настроек (до 120 слов):\n"
            f"Предыдущая сводка: {self.compacted_summary}\nНовые сообщения:\n{digest}"
        )
        summary = self._call_ollama(self.cfg.fast_model, [{"role": "user", "content": prompt}], stream=False)
        if summary:
            self.compacted_summary = summary.strip()
            self.bus.publish("context_compacted", {"summary": self.compacted_summary})

    def _call_ollama(
        self,
        model: str,
        messages: List[Dict[str, str]],
        stream: bool = True,
        images_b64: Optional[List[str]] = None
    ) -> str:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "options": {"temperature": 0.35, "num_ctx": 8192}
        }
        if images_b64 and messages:
            messages[-1]["images"] = images_b64

        full_text = ""
        try:
            with requests.post(
                f"{self.cfg.ollama_url}/api/chat",
                json=payload,
                stream=stream,
                timeout=self.cfg.request_timeout
            ) as resp:
                resp.raise_for_status()
                if not stream:
                    return resp.json().get("message", {}).get("content", "")
                for line in resp.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    chunk = json.loads(line)
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        full_text += token
                        self.bus.publish("thinking_chunk", {"text": token})
                    if chunk.get("done"):
                        break
        except Exception as exc:
            err_msg = f"[Ollama Error: {exc}]"
            self.bus.publish("error", {"message": err_msg})
            return err_msg
        return full_text

    @staticmethod
    def split_thinking(raw_output: str) -> Tuple[str, str]:
        if "<thinking>" in raw_output and "</thinking>" in raw_output:
            start = raw_output.index("<thinking>") + len("<thinking>")
            end = raw_output.index("</thinking>")
            thought = raw_output[start:end].strip()
            answer = raw_output[end + len("</thinking>"):].strip()
            return thought, answer
        return "", raw_output.strip()

    def think_and_act(
        self,
        user_query: str,
        telemetry_context: Optional[Dict[str, Any]] = None,
        images_b64: Optional[List[str]] = None,
        force_effort: Optional[str] = None
    ) -> Dict[str, Any]:
        """Plan -> draft -> independent critique -> revise -> final verification.

        This is an iterative controller around the local model, not a claim that
        the underlying weights are Claude Opus. Private scratch reasoning is not
        returned; only a concise decision/review summary is exposed.
        """
        with self._think_lock:
            self.compact_context_if_needed()
            effort = force_effort or self.route_effort(user_query, has_vision=bool(images_b64))
            review_rounds = self.cfg.max_review_rounds if effort in ("high", "max") else 1
            emit_pet_command("activity_coding" if effort in ("high", "max") else "activity_rubik",
                             prop="laptop" if effort in ("high", "max") else "rubik", emotion="curious")

            model = self.cfg.vision_model if images_b64 else (
                self.cfg.fast_model if effort == "low" else self._resolve_deep_model()
            )
            recalled = self.memory.recall(user_query, limit=4)
            sys_prompt = self.SYSTEM_PROMPT
            if self.compacted_summary:
                sys_prompt += f"\n\n[Сводка контекста]: {self.compacted_summary}"
            if telemetry_context:
                sys_prompt += f"\n\n[Проверенная телеметрия]: {json.dumps(telemetry_context, ensure_ascii=False, default=str)}"
            if recalled:
                memory_text = "\n".join(
                    f"- Задача: {x['prompt']}\n  Итог: {x['outcome']}\n  Урок: {x['lesson']} (reward={x['reward']})"
                    for x in recalled
                )
                sys_prompt += "\n\n[Релевантный опыт из долговременной памяти — использовать как подсказки, не как факты]:\n" + memory_text

            planning_prompt = (
                "Выполни задачу методично. Сначала сформируй краткий план проверки (3-6 пунктов), "
                "отметь неизвестные данные и риски. Не выдумывай выполненные действия. "
                "Затем дай рабочий ответ. Внутренние рассуждения не раскрывай; верни только "
                "краткий <thinking>итог подхода и проверок</thinking> и затем ответ.\n\nЗадача: " + user_query
            )
            messages = [{"role": "system", "content": sys_prompt}] + self.history + [
                {"role": "user", "content": planning_prompt}
            ]
            raw = self._call_ollama(model, messages, stream=True, images_b64=images_b64)
            thought, answer = self.split_thinking(raw)
            if not answer or answer.startswith("[Ollama Error"):
                result = {"effort": effort, "model": model, "thinking": thought,
                          "answer": answer or "Не удалось получить ответ от локальной модели.",
                          "reviews": [], "verified": False, "timestamp": time.time()}
                self.bus.publish("response_ready", result)
                return result

            reviews: List[Dict[str, Any]] = []
            review_summary = ""
            for round_no in range(1, review_rounds + 1):
                self.bus.publish("deliberation_step", {"step": "review", "round": round_no,
                                                        "total": review_rounds})
                critique_prompt = (
                    f"Независимая проверка ответа (проход {round_no}/{review_rounds}).\n"
                    f"Задача пользователя: {user_query}\nЧерновик: {answer}\n"
                    f"Проверь по критериям: соответствие задаче; фактическая опора; логика; "
                    f"полнота; противоречия; безопасность; выполнимость; явное обозначение "
                    f"неизвестного. Найди конкретные дефекты. Если дефектов нет — скажи PASS. "
                    f"Если есть — верни исправленный ответ целиком. Не добавляй выдуманных фактов. "
                    f"В начале ответа используй ровно REVIEW: PASS или REVIEW: FIXED, затем краткое "
                    f"объяснение проверки и итоговый текст после маркера FINAL:."
                )
                # Use a separate, usually smaller model as critic to reduce correlated errors.
                critic_model = self.cfg.reviewer_model or self.cfg.fast_model
                critique_raw = self._call_ollama(
                    critic_model,
                    [{"role": "system", "content": sys_prompt},
                     {"role": "user", "content": critique_prompt}],
                    stream=False
                )
                if not critique_raw or critique_raw.startswith("[Ollama Error"):
                    reviews.append({"round": round_no, "status": "error"})
                    break
                fixed = "REVIEW: FIXED" in critique_raw.upper()
                passed = "REVIEW: PASS" in critique_raw.upper()
                final_marker = "FINAL:"
                if fixed and final_marker in critique_raw:
                    revised = critique_raw.split(final_marker, 1)[1].strip()
                    if revised:
                        answer = revised
                elif not (passed or fixed):
                    # Malformed critic output must not overwrite a usable draft.
                    reviews.append({"round": round_no, "status": "invalid_review_format"})
                    break
                status = "fixed" if fixed else "passed"
                reviews.append({"round": round_no, "status": status})
                review_summary = "Проверка: " + ("обнаруженные замечания исправлены" if fixed else "критические замечания не обнаружены")
                self.bus.publish("deliberation_step", {"step": status, "round": round_no})
                if passed:
                    break

            thought_summary = "; ".join(x["status"] for x in reviews) or "проверка не выполнена"
            if review_summary:
                thought = (thought + "\n" + review_summary).strip()
            self.history.extend([
                {"role": "user", "content": user_query},
                {"role": "assistant", "content": answer}
            ])
            self.compact_context_if_needed()
            emit_pet_command("nod", emotion="happy")
            result = {
                "effort": effort, "model": model, "reviewer_model": (self.cfg.reviewer_model or self.cfg.fast_model),
                "thinking": thought, "answer": answer, "reviews": reviews,
                "verified": bool(reviews and reviews[-1]["status"] == "passed"),
                "verification_scope": "model_review_only_not_external_fact_check",
                "review_summary": review_summary, "timestamp": time.time()
            }
            self.last_interaction = {"prompt": user_query, "thinking": thought,
                                     "response": answer, "reviews": reviews}
            self.bus.publish("response_ready", result)
            return result

    def create_and_register_tool(self, tool_name: str, task_description: str) -> Tuple[bool, str]:
        safe_name = "".join(c for c in tool_name if c.isalnum() or c == "_").lower()
        if not safe_name:
            return False, "Некорректное имя инструмента."

        prompt = (
            f"Напиши чистый Python-модуль для инструмента `{safe_name}`.\n"
            f"Задача: {task_description}\n"
            "Требования:\n"
            "1. Модуль должен содержать функцию `def run(params: dict) -> dict:`.\n"
            "2. Используй только стандартную библиотеку (math, json, time, re, statistics) или разрешённые модули.\n"
            "3. ЗАПРЕЩЕНО использовать os.system, subprocess, ctypes, pymem, socket, open.\n"
            "4. Верни ТОЛЬКО код внутри блока ```python ... ```."
        )
        raw = self._call_ollama(self._resolve_deep_model(), [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ], stream=False)

        code = raw
        if "```python" in raw:
            code = raw.split("```python", 1)[1].split("```", 1)[0].strip()
        elif "```" in raw:
            code = raw.split("```", 1)[1].split("```", 1)[0].strip()

        try:
            tree = ast.parse(code)
            SafeToolValidator().visit(tree)
            if not any(isinstance(n, ast.FunctionDef) and n.name == "run" for n in tree.body):
                return False, "Сгенерированный код не содержит обязательную точку входа `run(params)`."
        except (SyntaxError, ToolSecurityViolation) as sec_err:
            emit_pet_command("shake_head", emotion="angry")
            return False, f"Инструмент отклонён защитой AST: {sec_err}"

        tool_file = Path(self.cfg.tools_dir) / f"{safe_name}.py"
        tool_file.write_text(code, encoding="utf-8")

        loaded = self._load_tool_file(tool_file)
        if loaded:
            emit_pet_command("dance", emotion="excited")
            self.bus.publish("tool_created", {"tool": safe_name, "path": str(tool_file)})
            return True, f"Инструмент `{safe_name}` успешно создан, проверен и подключён."
        return False, "Ошибка загрузки модуля после проверки AST."

    def _load_tool_file(self, path: Path) -> bool:
        try:
            code = path.read_text(encoding="utf-8")
            tree = ast.parse(code)
            SafeToolValidator().visit(tree)
            spec = importlib.util.spec_from_file_location(path.stem, str(path))
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "run") and callable(mod.run):
                    self.registered_tools[path.stem] = mod.run
                    return True
        except Exception:
            pass
        return False

    def _load_existing_tools(self) -> None:
        for file_path in Path(self.cfg.tools_dir).glob("*.py"):
            self._load_tool_file(file_path)

    def export_lesson_to_knowledge_md(self, title: str, summary: str) -> None:
        kp = Path(self.cfg.knowledge_path)
        ts_str = time.strftime("%Y-%m-%d %H:%M:%S")
        block = f"\n### [{ts_str}] {title}\n- {summary}\n"
        with open(kp, "a", encoding="utf-8") as f:
            f.write(block)

    def record_training_sample(
        self,
        prompt: str,
        thinking: str,
        response: str,
        source: str = "unknown",
        reward: float = 0.0,
        verified: bool = False,
        evidence: str = "unverified"
    ) -> None:
        """Store an experience; export to training data only with explicit evidence.

        `verified=True` is reserved for explicit human approval or objective
        environment checks (e.g. stable telemetry), never the model's own vote.
        """
        self.memory.add_experience(prompt, response, thinking, reward, source)
        self.learning.add_example(
            prompt=prompt, answer=response, lesson=thinking, source=source,
            reward=reward, evidence=evidence, approved=bool(verified)
        )
        export = self.learning.export_approved()
        self.bus.publish("dataset_updated", {
            "source": source, "reward": reward, "approved": bool(verified),
            "export": export, "learning": self.learning.stats()
        })

