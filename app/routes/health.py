"""Health endpoints."""

from fastapi import APIRouter

from app import __version__
from app.database import health_check

router = APIRouter(tags=["Health"])


@router.get("/")
def home():
    return {
        "message": "Munim Ji AI is running! 🚀",
        "version": __version__,
        "database": "ok" if health_check() else "unreachable",
    }


@router.get("/healthz")
def healthz():
    return {"status": "ok" if health_check() else "degraded"}
