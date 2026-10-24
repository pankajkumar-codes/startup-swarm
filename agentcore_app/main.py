"""Amazon Bedrock AgentCore Runtime entrypoint.

`scripts/prepare_agentcore.py` copies this file (as main.py) together with the
`orchestrator/` and `agents/` packages into an AgentCore CLI project.

Invoke payloads (JSON):
  {"action": "run",    "project": "my-app", "idea": "..."}      # run or resume
  {"action": "task",   "project": "my-app", "task": "..."}      # direct engineering task
  {"action": "status", "project": "my-app"}
  {"action": "approve_gate", "project": "my-app", "gate": "founder", "note": "..."}

State lives in DynamoDB and documents in S3 (names come from env vars set by the
deploy script), so every invocation picks up where the last one stopped. If one
invocation times out mid-run, invoking "run" again resumes from the last finished phase.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any, Dict

from orchestrator import status as status_mod
from orchestrator.pipeline import StartupPipeline

HERE = os.path.dirname(os.path.abspath(__file__))
_candidates = [os.path.join(HERE, "config.yaml"), os.path.join(os.path.dirname(HERE), "config.yaml")]
CONFIG_PATH = os.environ.get("SWARM_CONFIG") or next((c for c in _candidates if os.path.exists(c)), _candidates[0])


def handle(payload: Dict[str, Any]) -> Dict[str, Any]:
    action = (payload or {}).get("action", "status")
    project = payload.get("project")
    stage = os.environ.get("STAGE", "beta")
    workspace = os.path.join(tempfile.gettempdir(), "swarm", stage, project or "default")
    pipeline = StartupPipeline.build(
        stage=stage,
        config_path=CONFIG_PATH,
        mock=os.environ.get("AWS_MOCK_MODE") == "1",
        project=project,
        workspace=workspace,
    )

    if action == "status":
        return status_mod.collect(pipeline)
    if action == "run":
        outcome = pipeline.run(payload.get("idea"))
    elif action == "task":
        if not payload.get("task"):
            return {"status": "FAILED", "message": "payload.task is required"}
        outcome = pipeline.run_task(payload["task"])
    elif action == "approve_gate":
        outcome = pipeline.approve_gate(payload.get("gate", ""), payload.get("note", ""))
    else:
        return {"status": "FAILED", "message": f"unknown action {action!r}"}

    result = outcome.as_dict()
    result["usage"] = pipeline.client.usage.as_dict()
    result["s3_prefix"] = (
        f"s3://{pipeline.bus.mirror.bucket}/{pipeline.bus.mirror.prefix}/" if pipeline.bus.mirror else ""
    )
    return result


try:
    from bedrock_agentcore import BedrockAgentCoreApp
except ImportError:  # local tests without the AgentCore SDK installed
    BedrockAgentCoreApp = None

if BedrockAgentCoreApp is not None:
    app = BedrockAgentCoreApp()

    @app.entrypoint
    def invoke(payload):
        return handle(payload)


if __name__ == "__main__":
    if BedrockAgentCoreApp is None:
        raise SystemExit("pip install bedrock-agentcore to serve this entrypoint locally")
    app.run()
