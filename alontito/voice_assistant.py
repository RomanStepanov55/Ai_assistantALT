import threading
import time
from typing import Optional
from brain.opus_local_core import OpusLocalCore, emit_pet_command


class OfflineVoiceAssistant:
    WAKE_WORDS = ("алонтито", "alontito")

    def __init__(self, brain: OpusLocalCore):
        self.brain = brain
        self.running = False
        self._thread: Optional[threading.Thread] = None

    def process_transcript(self, text: str) -> Optional[str]:
        low = text.lower().strip()
        if any(w in low for w in self.WAKE_WORDS):
            emit_pet_command("wave", emotion="excited")
            cleaned = low
            for w in self.WAKE_WORDS:
                cleaned = cleaned.replace(w, "").strip(" ,.!?")
            query = cleaned or "Привет! Доложи статус."
            res = self.brain.think_and_act(query)
            return res["answer"]
        return None

    def start(self) -> None:
        if self.running:
            return
        self.running = True

        def _worker():
            while self.running:
                time.sleep(1.0)

        self._thread = threading.Thread(target=_worker, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.running = False
