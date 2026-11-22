"""Opt-in LoRA fine-tuning from human/objectively approved examples only.

This changes adapter weights, not the base model. It never runs automatically.
See TRAINING_GUIDE.md and install the optional training dependencies first.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path


def load_examples(path: Path):
    rows = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not row.get("id") or not isinstance(row.get("messages"), list):
            continue
        msgs = row["messages"]
        user = next((m.get("content", "") for m in msgs if m.get("role") == "user"), "")
        assistant = next((m.get("content", "") for m in msgs if m.get("role") == "assistant"), "")
        if user.strip() and assistant.strip():
            rows.append((user.strip(), assistant.strip()))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-model", required=True, help="HF model ID or local Transformers directory")
    ap.add_argument("--dataset", default="data/curated_training.jsonl")
    ap.add_argument("--output", default="artifacts/alontito-lora")
    ap.add_argument("--epochs", type=float, default=1.0)
    ap.add_argument("--max-length", type=int, default=1024)
    ap.add_argument("--min-examples", type=int, default=30)
    args = ap.parse_args()

    rows = load_examples(Path(args.dataset))
    if len(rows) < args.min_examples:
        raise SystemExit(f"Need at least {args.min_examples} curated examples; found {len(rows)}. No training performed.")

    try:
        import torch
        from transformers import (AutoTokenizer, AutoModelForCausalLM, Trainer,
                                  TrainingArguments, DataCollatorForLanguageModeling)
        from peft import LoraConfig, get_peft_model, TaskType
    except ImportError as exc:
        raise SystemExit("Install optional packages: pip install -r requirements-training.txt") from exc

    if not torch.cuda.is_available():
        raise SystemExit("CUDA GPU not detected. Training is stopped to avoid unexpectedly exhausting system RAM.")

    tokenizer = AutoTokenizer.from_pretrained(args.base_model, use_fast=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(args.base_model, torch_dtype=torch.float16,
                                                 device_map="auto")
    lora = LoraConfig(task_type=TaskType.CAUSAL_LM, r=16, lora_alpha=32,
                      lora_dropout=0.05, target_modules="all-linear")
    model = get_peft_model(model, lora)

    texts = []
    for user, assistant in rows:
        messages = [{"role": "user", "content": user}, {"role": "assistant", "content": assistant}]
        if hasattr(tokenizer, "apply_chat_template") and tokenizer.chat_template:
            text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)
        else:
            text = f"User:\n{user}\n\nAssistant:\n{assistant}{tokenizer.eos_token}"
        texts.append(text)

    class TokenDataset(torch.utils.data.Dataset):
        def __len__(self): return len(texts)
        def __getitem__(self, idx):
            enc = tokenizer(texts[idx], truncation=True, max_length=args.max_length,
                            padding="max_length", return_tensors="pt")
            return {k: v.squeeze(0) for k, v in enc.items()}

    out = Path(args.output); out.mkdir(parents=True, exist_ok=True)
    train_args = TrainingArguments(
        output_dir=str(out / "checkpoints"), num_train_epochs=args.epochs,
        per_device_train_batch_size=1, gradient_accumulation_steps=8,
        learning_rate=2e-4, logging_steps=5, save_strategy="epoch",
        fp16=True, report_to="none", remove_unused_columns=False
    )
    trainer = Trainer(model=model, args=train_args, train_dataset=TokenDataset(),
                      data_collator=DataCollatorForLanguageModeling(tokenizer, mlm=False))
    trainer.train()
    model.save_pretrained(out); tokenizer.save_pretrained(out)
    (out / "TRAINING_MANIFEST.json").write_text(json.dumps({
        "base_model": args.base_model, "examples": len(rows), "epochs": args.epochs,
        "adapter": "LoRA", "automatic_promotion": False,
        "note": "Evaluate this adapter against the unchanged baseline before deployment."
    }, indent=2), encoding="utf-8")
    print(f"LoRA adapter saved to {out}. It has NOT been promoted or installed automatically.")

if __name__ == "__main__":
    main()
