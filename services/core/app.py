from fastapi import FastAPI

from shared.jarvis_common.bootstrap import orchestrator
from shared.jarvis_common.models import CommandEnvelope, CommandResponse

app = FastAPI(title="jarvis-core")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/v1/commands", response_model=CommandResponse)
def receive_command(envelope: CommandEnvelope) -> CommandResponse:
    return orchestrator.handle(envelope)


@app.post("/internal/approvals/{approval_id}/execute", response_model=CommandResponse)
def execute_approval(approval_id: str) -> CommandResponse:
    return orchestrator.execute_approval(approval_id)
