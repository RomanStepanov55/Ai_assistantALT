"""
Learning Memory & Experience Optimization
Stores persistent knowledge in data/memory.json, analyzes historical runs,
identifies the fastest leveling strategies, and adapts based on past mistakes.
"""
import json
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
from config import MEMORY_FILE


class LearningMemory:
    def __init__(self):
        self.file_path = Path(MEMORY_FILE)
        self.data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if not self.file_path.exists():
            default_data = {
                "version": 1,
                "total_sessions": 0,
                "total_playtime_sec": 0,
                "rebirth_history": [],
                "death_history": [],
                "exercise_efficiency": {
                    "tool_1_hand_weights": {"samples": 0, "avg_strength_per_min": 0.0},
                    "pushups": {"samples": 0, "avg_strength_per_min": 0.0},
                    "situps": {"samples": 0, "avg_strength_per_min": 0.0},
                    "treadmill": {"samples": 0, "avg_agility_per_min": 0.0},
                },
                "learned_insights": [
                    "Rebirthing as soon as requirement is met maximizes exponential growth.",
                    "Safe zones prevent high-level players from interrupting the training cycle."
                ]
            }
            self._save(default_data)
            return default_data

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self, data: Optional[Dict[str, Any]] = None):
        if data is None:
            data = self.data
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def note_session(self):
        """Marks that a new autonomous session started (for playtime stats)."""
        self.data["total_sessions"] = self.data.get("total_sessions", 0) + 1
        self.data["last_session_started"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._save()

    def get_rebirths_known(self) -> int:
        """Bot-side rebirth counter (OCR of the in-game counter is unreliable)."""
        return int(self.data.get("rebirths_known", 0))

    def set_rebirths_known(self, count: int):
        self.data["rebirths_known"] = int(count)
        self._save()

    def record_rebirth(self, rebirth_number: int, final_strength: int, duration_sec: float):
        """Records a successful rebirth event to analyze time-to-rebirth curves."""
        entry = {
            "timestamp": time.time(),
            "rebirth_number": rebirth_number,
            "final_strength": final_strength,
            "time_taken_sec": round(duration_sec, 1)
        }
        self.data.setdefault("rebirth_history", []).append(entry)
        self._add_insight(f"Rebirth #{rebirth_number} completed in {int(duration_sec/60)} minutes.")
        self._save()

    def record_death(self, location_tag: str = "open_arena"):
        """Records an accidental death/respawn to avoid that area/pattern."""
        entry = {
            "timestamp": time.time(),
            "location": location_tag,
        }
        self.data.setdefault("death_history", []).append(entry)
        self._add_insight(f"Died in '{location_tag}'. Strategy updated: stay closer to safe zones.")
        self._save()

    def update_efficiency(self, activity_name: str, measured_rate_per_min: float):
        """Updates moving average of training efficiency for a given exercise."""
        eff = self.data.setdefault("exercise_efficiency", {})
        item = eff.setdefault(activity_name, {"samples": 0, "avg_strength_per_min": 0.0})
        
        samples = item.get("samples", 0)
        current_avg = item.get("avg_strength_per_min", 0.0)
        
        # Incremental moving average
        new_samples = samples + 1
        new_avg = ((current_avg * samples) + measured_rate_per_min) / new_samples

        item["samples"] = new_samples
        item["avg_strength_per_min"] = round(new_avg, 1)
        self._save()

    def _add_insight(self, text: str):
        insights = self.data.setdefault("learned_insights", [])
        if text not in insights:
            insights.append(text)
            if len(insights) > 20:
                insights.pop(0)

    def add_insight(self, text: str):
        """Public entry point: record a verified lesson (deduplicated)."""
        self._add_insight(text)
        self._save()

    def get_best_exercise(self) -> str:
        """Returns the exercise name that produced the highest measured rate."""
        eff = self.data.get("exercise_efficiency", {})
        best_name = "tool_1_hand_weights"
        best_val = 0.0

        for name, stats in eff.items():
            avg = stats.get("avg_strength_per_min", 0.0)
            if avg > best_val:
                best_val = avg
                best_name = name

        return best_name
