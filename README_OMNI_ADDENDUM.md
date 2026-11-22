# Alontito Omni — continual learning architecture

## What this system can and cannot do

Alontito cannot become Claude Opus 5 by changing prompts or code. Its quality is bounded by the selected local model, context window, tools, compute and data. This update builds a disciplined improvement loop around that model; it does not claim equivalent weights or benchmark performance.

## The improvement loop

1. **Plan:** decompose the task, list constraints and unknowns.
2. **Draft:** produce a candidate answer or action plan.
3. **Critique:** a separately configured reviewer model checks completeness, logic, uncertainty and safety.
4. **Revise:** apply concrete findings for a bounded number of rounds.
5. **Verify:** report the scope honestly. Model review is not external fact-checking; objective checks require independent evidence.
6. **Remember:** store structured lessons and compact visual descriptors in SQLite.
7. **Learn:** retrieve relevant approved experience on later tasks; keep rejected/uncertain examples for analysis, not positive fine-tuning.
8. **Evaluate:** run regression suites on a held-out set and promote candidate adapters only if they beat the baseline without unacceptable regressions.

## Memory and screenshots

`vision/omni_eye.py` processes screenshots transiently. It stores only a perceptual hash, OCR text, coordinates, confidence values, metrics and window metadata. It does not write screenshot image files to the learning database. Telegram `/screen` may transmit an ephemeral screenshot to the authorized chat; it is not archived by the memory module.

## Learning levels

- Retrieval/experience learning works continuously and does not change model weights.
- Curated fine-tuning examples are exported only after explicit user approval or objective repeatable environment evidence. A model's own `PASS` is never sufficient approval.
- Actual LoRA adapter training is optional and manual: see `training/TRAINING_GUIDE.md`. Adapters are not installed automatically. Evaluate against the unchanged baseline before deployment.

## Verification

Run `python -m unittest discover -s tests`. The tests cover memory deduplication, evidence-gated export, review-loop behavior, HUD parsing and regression contracts. Passing unit tests does not establish that the local model is as capable as Opus, nor does it replace Windows/Ollama integration tests.
