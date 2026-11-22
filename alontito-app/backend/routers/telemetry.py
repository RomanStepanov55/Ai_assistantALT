"""REST: телеметрия и обучение."""
from fastapi import APIRouter
from backend.services import telemetry_reader as tr

router = APIRouter(prefix="/api", tags=["telemetry"])


@router.get("/telemetry/samples")
def samples(limit: int = 300):
    return {"samples": tr.read_samples(limit)}


@router.get("/telemetry/summary")
def summary():
    return {"last": tr.last_sample()}


@router.get("/learning")
def learning():
    return tr.learning()