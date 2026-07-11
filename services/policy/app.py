from fastapi import FastAPI

from shared.jarvis_common.models import PolicyDecision, PolicyEvaluationRequest
from shared.jarvis_common.policy_engine import evaluate_policy

app = FastAPI(title="jarvis-policy")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/internal/policy/evaluate", response_model=PolicyDecision)
def evaluate_policy_endpoint(payload: PolicyEvaluationRequest) -> PolicyDecision:
    return evaluate_policy(payload)
