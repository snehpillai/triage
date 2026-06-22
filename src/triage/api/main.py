"""FastAPI application.

Start with:
    uvicorn triage.api.main:app --reload --port 8000
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from triage.api.routes.tickets import router as tickets_router
from triage.config import settings
from triage.observability import setup_tracing

setup_tracing()

app = FastAPI(
    title="Triage API",
    description="Multi-agent customer support ticket ingress and status.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tickets_router)


@app.get("/health", tags=["meta"], summary="Health check")
async def health() -> dict[str, str]:
    return {"status": "ok"}
