from contextlib import asynccontextmanager

from fastapi import FastAPI

from shared.jarvis_common.bootstrap import orchestrator
from shared.jarvis_common.config import settings
from shared.jarvis_common.db.connection import db_connection
from shared.jarvis_common.db.migrate import apply_migrations
from shared.jarvis_common.models import CommandEnvelope, CommandResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.database_url:
        with db_connection() as conn:
            apply_migrations(conn)
    yield


app = FastAPI(title="jarvis-core", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "database": bool(settings.database_url)}


@app.post("/v1/commands", response_model=CommandResponse)
def receive_command(envelope: CommandEnvelope) -> CommandResponse:
    return orchestrator.handle(envelope)


@app.post("/internal/approvals/{approval_id}/execute", response_model=CommandResponse)
def execute_approval(approval_id: str) -> CommandResponse:
    return orchestrator.execute_approval(approval_id)
