import math
import time
import json
import threading
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Any, Callable, Dict, List, Optional

from brain.opus_local_core import OpusLocalCore, emit_pet_command
from vision.omni_eye import OmniEye


ALLOWED_SAFE_ACTIONS: Dict[str, Dict[str, Any]] = {
    "click_train_center": {"type": "click_ratio", "rx": 0.50, "ry": 0.55, "desc": "Клик тренировки в центре"},
    "press_e_interact": {"type": "key", "key": "e", "desc": "Взаимодействие с тренажёром (E)"},
    "jump_space": {"type": "key", "key": "space", "desc": "Прыжок / выход из застревания"},
    "step_forward": {"type": "hold_key", "key": "w", "duration": 0.6, "desc": "Короткий шаг вперёд"},
    "close_modal_x": {"type": "click_ratio", "rx": 0.78, "ry": 0.22, "desc": "Закрытие всплывающего окна"}
}


@dataclass
class ArmStats:
    action_name: str
    attempts: int = 0
    successes: int = 0
    total_gain_per_min: float = 0.0
    baseline_rate: float = 0.0

    @property
    def success_rate(self) -> float:
        return (self.successes / self.attempts) if self.attempts > 0 else 0.5

    @property
    def exploit_rate(self) -> float:
        return (self.total_gain_per_min / self.attempts) if self.attempts > 0 else 0.0


class UniversalExplorer:
    NOISE_BAND = 0.10

    def __init__(
        self,
        brain: OpusLocalCore,
        eye: OmniEye,
        input_executor: Optional[Callable[[Dict[str, Any]], None]] = None,
        state_path: str = "data/ucb1_explorer_state.json"
    ):
        self.brain = brain
        self.eye = eye
        self.executor = input_executor or self._default_noop_executor
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

        self.arms: Dict[str, ArmStats] = {
            name: ArmStats(action_name=name) for name in ALLOWED_SAFE_ACTIONS
        }
        self.total_attempts: int = 0
        self.running: bool = False
        self._thread: Optional[threading.Thread] = None
        self._load_state()

    @staticmethod
    def _default_noop_executor(action_spec: Dict[str, Any]) -> None:
        time.sleep(0.15)

    def _load_state(self) -> None:
        if self.state_path.exists():
            try:
                raw = json.loads(self.state_path.read_text(encoding="utf-8"))
                self.total_attempts = raw.get("total_attempts", 0)
                for k, v in raw.get("arms", {}).items():
                    if k in self.arms:
                        self.arms[k] = ArmStats(**v)
            except Exception:
                pass

    def save_state(self) -> None:
        payload = {
            "total_attempts": self.total_attempts,
            "arms": {k: asdict(v) for k, v in self.arms.items()}
        }
        self.state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def select_ucb1_action(self) -> str:
        best_action = next(iter(self.arms))
        best_score = -1.0
        for name, arm in self.arms.items():
            exploit = arm.exploit_rate * arm.success_rate
            explore = math.sqrt(math.log(self.total_attempts + 2) / (arm.attempts + 1))
            score = exploit + explore * max(exploit, 1.0) * 0.15
            if score > best_score:
                best_score = score
                best_action = name
        return best_action

    def run_single_cycle(self, duration_sec: float = 3.0) -> Dict[str, Any]:
        before_obs = self.eye.observe_verified(delay_sec=0.2)
        if before_obs.get("reflex_action"):
            reflex = before_obs["reflex_action"]
            self.executor({"type": "click_xy", "xy": reflex["click_xy"]})
            emit_pet_command("shake_head", emotion="scared")
            return {"status": "reflex_triggered", "reflex": reflex}

        action_name = self.select_ucb1_action()
        spec = ALLOWED_SAFE_ACTIONS[action_name]
        prev_arm = ArmStats(**asdict(self.arms[action_name]))

        t0 = time.time()
        self.executor(spec)
        time.sleep(max(0.5, duration_sec))
        after_obs = self.eye.observe_verified(delay_sec=0.25)
        confirm_obs = self.eye.observe_verified(delay_sec=0.25)
        elapsed = max(0.5, time.time() - t0)

        m_before = before_obs.get("metrics", {})
        m_after = after_obs.get("metrics", {})
        confirm_metrics = confirm_obs.get("metrics", {})
        # Reject unstable OCR/HUD readings instead of learning from a single noisy frame.
        common = set(m_after) & set(confirm_metrics)
        stable = bool(common) and all(
            abs(float(m_after[k]) - float(confirm_metrics[k])) / max(abs(float(m_after[k])), 1.0) <= 0.10
            for k in common
        )
        if not stable:
            m_after = {}
        total_delta = 0.0
        for k, v_after in m_after.items():
            v_before = m_before.get(k, v_after)
            if v_after > v_before:
                total_delta += (v_after - v_before)

        rate_per_min = (total_delta / elapsed) * 60.0
        arm = self.arms[action_name]
        self.total_attempts += 1
        arm.attempts += 1

        if arm.baseline_rate <= 0:
            verdict = "better" if rate_per_min > 0 else "inconclusive"
        else:
            rel_change = (rate_per_min - arm.baseline_rate) / arm.baseline_rate
            if rel_change > self.NOISE_BAND:
                verdict = "better"
            elif rel_change < -self.NOISE_BAND:
                verdict = "worse"
            else:
                verdict = "inconclusive"

        if not stable:
            verdict = "inconclusive"

        if verdict == "better":
            arm.successes += 1
            arm.total_gain_per_min += rate_per_min
            arm.baseline_rate = rate_per_min
            emit_pet_command("dance", emotion="happy")
            self.brain.record_training_sample(
                prompt=f"Выбери действие для прироста HUD. Метрики ДО: {m_before}",
                thinking=f"Действие {action_name} дало прирост {rate_per_min:.2f}/мин (> +10% к базе).",
                response=f"Выполняю безопасное действие `{action_name}` ({spec['desc']}).",
                source="ucb1_win",
                reward=1.0,
                verified=True,
                evidence="objective_stable_hud_measurement"
            )
            self.brain.export_lesson_to_knowledge_md(
                f"Победа UCB1: {action_name}",
                f"Подтверждён темп {rate_per_min:.2f}/мин (попыток: {arm.attempts})."
            )
        elif verdict == "worse":
            # Автоматический откат параметров к стабильной базовой линии
            self.arms[action_name] = prev_arm
            self.arms[action_name].attempts += 1
            emit_pet_command("sad", emotion="sad")

        self.save_state()
        lesson = (f"Действие {action_name}: {verdict}; темп {rate_per_min:.2f}/мин; "
                  f"стабильный повторный замер: {stable}.")
        self.brain.memory.add_experience(
            prompt=f"Проверка действия {action_name}; метрики до: {m_before}",
            outcome=f"Вердикт {verdict}; метрики после: {m_after}",
            lesson=lesson, reward=(1.0 if verdict == "better" else -0.25 if verdict == "worse" else 0.0),
            source="ucb1_verified_cycle",
            state_hash=(confirm_obs.get("visual_memory") or {}).get("state_hash")
        )
        report = {
            "action": action_name,
            "verdict": verdict,
            "rate_per_min": round(rate_per_min, 2),
            "stable_confirmation": stable,
            "before_state": (before_obs.get("visual_memory") or {}).get("state_id"),
            "after_state": (confirm_obs.get("visual_memory") or {}).get("state_id"),
            "metrics_after": m_after
        }
        self.brain.bus.publish("ucb1_cycle", report)
        return report

    def start_background(self) -> None:
        if self.running:
            return
        self.running = True
        emit_pet_command("roblox_watch_back", emotion="excited")

        def _loop():
            while self.running:
                try:
                    self.run_single_cycle(duration_sec=2.5)
                except Exception as exc:
                    self.brain.bus.publish("error", {"message": f"Explorer error: {exc}"})
                    time.sleep(2.0)

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.running = False
        emit_pet_command("idle", emotion="happy")
