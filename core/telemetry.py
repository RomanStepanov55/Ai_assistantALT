"""
Telemetry & Stat Tracker
Tracks progression rates (Strength/sec, Gems/sec), session history,
and exports clean metrics for the AI Decision Maker and logs.
"""
import time
from typing import Dict, List, Optional
from pathlib import Path
from config import TELEMETRY_FILE


class TelemetryTracker:
    def __init__(self):
        self.start_time = time.time()
        self.samples: List[Dict] = []
        self.rebirths_count = 0
        self.deaths_count = 0
        self.last_log_time = 0.0

    def record_sample(self, strength: int, gems: int, rebirths: int, health_pct: float, current_action: str):
        """Records a point-in-time state sample."""
        now = time.time()
        sample = {
            "timestamp": now,
            "elapsed_sec": round(now - self.start_time, 1),
            "strength": strength,
            "gems": gems,
            "rebirths": rebirths,
            "health_pct": round(health_pct, 2),
            "action": current_action
        }
        self.samples.append(sample)
        
        # Keep recent 200 samples in memory
        if len(self.samples) > 200:
            self.samples.pop(0)

        # Append to log file periodically
        if now - self.last_log_time > 10.0:
            self.save_log_line(sample)
            self.last_log_time = now

    def calculate_rates(self) -> Dict[str, float]:
        """Calculates current Strength/min and Gems/min over the last 60 seconds."""
        if len(self.samples) < 2:
            return {"strength_per_min": 0.0, "gems_per_min": 0.0}

        now = time.time()
        # Find sample from ~60s ago
        recent_samples = [s for s in self.samples if now - s["timestamp"] <= 60.0]
        if len(recent_samples) < 2:
            recent_samples = self.samples

        first = recent_samples[0]
        last = recent_samples[-1]
        dt_min = (last["timestamp"] - first["timestamp"]) / 60.0

        if dt_min <= 0.001:
            return {"strength_per_min": 0.0, "gems_per_min": 0.0}

        d_strength = max(0, last["strength"] - first["strength"])
        d_gems = max(0, last["gems"] - first["gems"])

        return {
            "strength_per_min": round(d_strength / dt_min, 1),
            "gems_per_min": round(d_gems / dt_min, 1)
        }

    def save_log_line(self, sample: Dict):
        """Appends a structured log entry."""
        try:
            rates = self.calculate_rates()
            line = (
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                f"Elapsed: {sample['elapsed_sec']}s | "
                f"Str: {sample['strength']:,} (+{rates['strength_per_min']}/min) | "
                f"Gems: {sample['gems']:,} | "
                f"Rebirths: {sample['rebirths']} | "
                f"HP: {int(sample['health_pct'] * 100)}% | "
                f"Action: {sample['action']}\n"
            )
            with open(TELEMETRY_FILE, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass

    def get_summary_text(self) -> str:
        """Returns human and AI-readable status string."""
        if not self.samples:
            return "No telemetry samples recorded yet."
        
        last = self.samples[-1]
        rates = self.calculate_rates()
        return (
            f"=== Muscle Legends AI Telemetry ===\n"
            f"Session Duration: {int(time.time() - self.start_time)}s\n"
            f"Current Strength: {last['strength']:,}\n"
            f"Growth Rate:      {rates['strength_per_min']:,} Str/min\n"
            f"Current Gems:     {last['gems']:,}\n"
            f"Rebirths:         {last['rebirths']}\n"
            f"Health:           {int(last['health_pct'] * 100)}%\n"
            f"Active State:     {last['action']}\n"
        )
