"""Generate training examples for the OWN brain with the teacher model.

The teacher (a downloaded local model, see config/alontito_brain.json) helps
produce question/answer pairs for the future own brain. The pipeline rule of
this project: an example reaches the fine-tuning dataset (curated_training)
ONLY after approval (`LearningEngine.approve`) - by a human or an objective
measurement. The teacher can NEVER approve its own output: this script stores
everything as pending (approved=0) and exits.

Usage:
    python tools/teacher_generate.py --topics "что такое честный замер, ..." --per-topic 2
    python tools/teacher_generate.py --from-questions 5     # seeds from open questions
    python tools/teacher_generate.py --style 3              # style anchors from approved examples

Dry run (no teacher call):   --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from alontito.model import BrainConfig, ModelUnavailable, Teacher  # noqa: E402
from brain.learning_engine import LearningEngine  # noqa: E402

SYSTEM = """Ты помогаешь готовить обучающие данные для локального ИИ-агента Алонтито.
Правила проекта:
- отвечай на русском;
- честность важнее полноты: если данных нет, ответ говорит «не подтверждено», а не выдумывает;
- никакой автоматизации игр, никаких покупок и трейдов;
- верни СТРОГО JSON-массив объектов вида {"prompt": "...", "answer": "...", "lesson": "..."} без пояснений."""


def topics_from_questions(limit: int) -> list:
    """Open questions of the agent are honest knowledge gaps."""
    try:
        from alontito.memory import Memory
        mem = Memory()
        return [q["question"] for q in mem.open_questions()[:max(1, limit)]]
    except Exception:
        return []


def style_anchors(engine: LearningEngine, limit: int) -> list:
    """Approved examples show the agent the tone it should learn."""
    rows = []
    try:
        with engine._connect() as c:
            rows = c.execute(
                "SELECT prompt, answer FROM learning_examples "
                "WHERE approved=1 ORDER BY id DESC LIMIT ?",
                (max(1, limit),)).fetchall()
    except Exception:
        return []
    return [{"prompt": r["prompt"], "answer": r["answer"]} for r in rows]


def _extract_pairs(raw: str) -> list:
    """Parse the teacher output tolerantly: JSON array, or JSON lines."""
    text = (raw or "").strip()
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(text[start:end + 1])
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]
        except ValueError:
            pass
    out = []
    for line in text.splitlines():
        line = line.strip().rstrip(",")
        if not line.startswith("{"):
            continue
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                out.append(item)
        except ValueError:
            continue
    return out


def generate(teacher, engine: LearningEngine, topics: list,
             per_topic: int, dry_run: bool = False, n_style: int = 3) -> dict:
    """teacher: Teacher or any stub with .complete() and .model (tests)."""
    anchors = style_anchors(engine, n_style)
    anchor_block = ""
    if anchors:
        sample = json.dumps(anchors[:2], ensure_ascii=False)
        anchor_block = (f"\nПримеры нужного тона (уже одобрены человеком):\n{sample}")
    stats = {"topics": 0, "pairs": 0, "stored": 0, "skipped": 0}
    for topic in topics:
        stats["topics"] += 1
        prompt = (
            f"Тема: {topic}\n"
            f"Составь {per_topic} пар(ы) вопрос→ответ для обучения агента "
            f"по этой теме.{anchor_block}"
        )
        if dry_run:
            print(f"[dry-run] тема: {topic}")
            continue
        raw = teacher.complete(prompt, system=SYSTEM)
        pairs = _extract_pairs(raw)
        if not pairs:
            print(f"[warn] учитель вернул не JSON для темы: {topic}")
            continue
        for item in pairs[:per_topic]:
            q = str(item.get("prompt") or "").strip()
            a = str(item.get("answer") or "").strip()
            lesson = str(item.get("lesson") or "").strip()
            if not q or not a:
                stats["skipped"] += 1
                continue
            stats["pairs"] += 1
            stored = engine.add_example(
                q, a, lesson=lesson,
                source=f"teacher:{teacher.model}",
                evidence="НЕ ПОДТВЕРЖДЕНО (синтетика учителя, ждёт одобрения)",
                approved=False)
            if stored:
                stats["stored"] += 1
            else:
                stats["skipped"] += 1   # duplicate fingerprint
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--topics", default="", help="темы через запятую")
    ap.add_argument("--from-questions", type=int, default=0,
                    help="взять N тем из открытых вопросов агента")
    ap.add_argument("--per-topic", type=int, default=2)
    ap.add_argument("--style", type=int, default=3,
                    help="сколько одобренных примеров показать как образец тона")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = BrainConfig.load()
    if not args.dry_run:
        try:
            teacher = Teacher(cfg)
        except ModelUnavailable as exc:
            print(f"Учитель недоступен: {exc}")
            print("Настрой teacher в config/alontito_brain.json "
                  "(скопируйте из config/alontito_brain.json.example).")
            return 2
        st = teacher.status()
        if not st.get("reachable"):
            print(f"Учитель недоступен: {st.get('detail')}")
            return 2
        if not st.get("model_present"):
            print(f"Модели «{st.get('model')}» нет на сервере. Скачай её: "
                  f"ollama pull {st.get('model')}")
            return 2
    else:
        teacher = None

    topics = [t.strip() for t in args.topics.split(",") if t.strip()]
    if args.from_questions:
        topics += topics_from_questions(args.from_questions)
    if not topics:
        print("Нет тем: задай --topics или --from-questions N.")
        return 2

    engine = LearningEngine()
    stats = generate(teacher, engine, topics, max(1, args.per_topic),
                     dry_run=args.dry_run, n_style=max(0, args.style))
    print("Итог:", json.dumps(stats, ensure_ascii=False))
    print("Все примеры сохранены как НЕ одобренные: одобри их на устройстве "
          "(вкладка «Обучение»), только после этого они попадут в датасет "
          "(LearningEngine.export_approved). Учитель не может одобрить сам себя.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
