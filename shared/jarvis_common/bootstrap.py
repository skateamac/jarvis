from shared.jarvis_common.clients import connector_client, scheduler_client
from shared.jarvis_common.orchestrator import CommandOrchestrator
from shared.jarvis_common.policy_engine import evaluate_policy
from shared.jarvis_common.stores import approval_store, audit_store

orchestrator = CommandOrchestrator(
    policy_evaluate=evaluate_policy,
    approval_store=approval_store,
    audit_store=audit_store,
    connector_client=connector_client(),
    scheduler_client=scheduler_client(),
)
