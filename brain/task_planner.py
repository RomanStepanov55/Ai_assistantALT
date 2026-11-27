#!/usr/bin/env python3
"""
TaskPlanner v5 — универсальный планировщик задач для Alontito.

Один и тот же мозг (NeuroLab + SkillAutonomy) работает с любой задачей
агента: саморазвитие, память, навыки, автоматизации, знания, рутина.
Планировщик:
  - разбивает цель на шаги;
  - переиспользует уроки из LessonRegistry (параметры прошлых успешных
    экспериментов переносятся на похожие задачи — это и есть "кругозор");
  - каждый шаг прогоняется через ту же петлю: выбрать -> выполнить -> замерить -> записать.

С пивота 2026-11-27 игровых доменов нет: агент не играет. Словарь доменов —
только агентская работа.

Пример:
    tp = TaskPlanner()
    task = tp.plan("навести порядок в памяти")
    while not task.done:
        step = tp.next_step(task)
        ... выполнить step ...
        tp.finish_step(task, success=True, reward=0.8)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Step:
    name: str
    params: Dict = field(default_factory=dict)
    done: bool = False
    success: Optional[bool] = None


@dataclass
class Task:
    goal: str
    steps: List[Step]
    created_at: float = field(default_factory=time.time)

    @property
    def done(self) -> bool:
        return all(s.done for s in self.steps)

    @property
    def progress(self) -> float:
        return sum(1 for s in self.steps if s.done) / max(len(self.steps), 1)


class TaskPlanner:
    KEYWORDS = {
        # агентская работа (пивот 2026-11-27)
        ("памят", "факт", "знан"):      ["collect", "consolidate", "verify", "prune"],
        ("напомин", "автомат", "рутин"): ["observe", "propose", "measure", "adopt"],
        ("навык", "skill", "инструмент"): ["measure", "synthesize", "test", "register"],
        ("ответ", " чат", "качеств", "одобрен"): ["collect", "review", "revise", "measure"],
        ("саморазвит", "эксперимент", "улучш"): ["measure", "propose", "test", "adopt"],
        ("учеб", "learn", "обучен", "датасет"): ["warmup", "train", "test", "reflect"],
        ("файл", "file", "папк", "документ"): ["scan", "plan_changes", "apply", "verify"],
        # общая дисциплина «тренировка» (саморазвитие как упражнение)
        ("сил", "strength", "кач"):     ["observe", "train", "measure", "reflect"],
        ("тренир", "практ"):            ["warmup", "train", "test", "reflect"],
    }
    DEFAULT_STEPS = ["observe", "act", "measure", "reflect"]

    def __init__(self, lesson_registry: Optional[List[Dict]] = None):
        self.lessons = lesson_registry or []

    def _match_steps(self, goal: str) -> List[str]:
        g = goal.lower()
        for keys, steps in self.KEYWORDS.items():
            if any(k in g for k in keys):
                return list(steps)
        return list(self.DEFAULT_STEPS)

    def _reuse_params(self, goal: str) -> Dict:
        """Перенос параметров лучшего похожего урока с вердиктом 'better'."""
        g = goal.lower()
        best, best_key = {}, 0
        for lesson in self.lessons:
            if lesson.get("verdict") != "better":
                continue
            params = lesson.get("params") or {}
            if not params:
                continue
            hits = sum(1 for k in params if str(k).lower() in g)
            if hits > best_key:
                best, best_key = params, hits
        return dict(best)

    def plan(self, goal: str) -> Task:
        steps = [Step(name=s) for s in self._match_steps(goal)]
        reused = self._reuse_params(goal)
        if reused:
            steps[0].params["reused_lesson_params"] = reused
        return Task(goal=goal, steps=steps)

    def next_step(self, task: Task) -> Optional[Step]:
        for s in task.steps:
            if not s.done:
                return s
        return None

    def finish_step(self, task: Task, success: bool, reward: float = 0.5) -> Step:
        step = self.next_step(task)
        if step is None:
            raise RuntimeError("Task is already done")
        step.done = True
        step.success = success
        return step

    def summary(self, task: Task) -> str:
        mark = lambda s: "✔" if s.done and s.success else ("✖" if s.done else "…")
        return f"Задача: {task.goal} ({task.progress:.0%})\n" + "\n".join(
            f" {mark(s)} {s.name}" for s in task.steps)
