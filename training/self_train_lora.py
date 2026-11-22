"""Compatibility entry point: prepares approved examples; does not train weights."""
from pathlib import Path
from brain.learning_engine import LearningEngine


def prepare_and_register_ollama_model(dataset_path: str = "data/curated_training.jsonl", **_kwargs) -> str:
    """Export evidence-approved records. Kept under the old name for compatibility."""
    engine = LearningEngine(dataset_path=dataset_path)
    result = engine.export_approved()
    if result["exported"]:
        return f"Экспортировано одобренных примеров: {result['exported']}. Это не LoRA-обучение; см. training/TRAINING_GUIDE.md."
    if not Path(dataset_path).exists():
        return "Одобренных примеров пока нет. Сначала соберите подтверждённую обратную связь."
    return "Новых одобренных примеров нет. Веса модели не изменялись."

if __name__ == "__main__":
    print(prepare_and_register_ollama_model())
