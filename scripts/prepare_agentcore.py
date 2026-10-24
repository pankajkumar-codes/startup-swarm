#!/usr/bin/env python3
"""Stage this repo into an AgentCore CLI project for one stage (beta or prod).

What it does:
1. Reads the CDK outputs file (`cdk deploy --outputs-file cdk-outputs.json`) to get the
   stage's S3 bucket, DynamoDB table and AgentCore execution role ARN.
2. Copies the runtime code into <project-dir>/app/startup_swarm_<stage>/ with a
   pyproject.toml listing its dependencies.
3. Adds or updates the runtime entry "startup_swarm_<stage>" in
   <project-dir>/agentcore/agentcore.json, pointing at that code, using the CDK role
   (executionRoleArn) and passing the stage settings as envVars.

Then run `agentcore deploy` inside <project-dir>. See README "Hosted mode on AgentCore".

Usage:
  python scripts/prepare_agentcore.py --stage beta --project-dir ../SwarmRuntime \
      --outputs infrastructure/cdk-outputs-beta.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COPY_DIRS = ["orchestrator", "agents"]
PYPROJECT = """\
[project]
name = "startup-swarm-runtime"
version = "0.2.0"
requires-python = ">=3.11"
dependencies = [
    "bedrock-agentcore",
    "boto3>=1.40",
    "pydantic>=2.6",
    "pyyaml>=6.0",
]
"""


def read_outputs(path: str, stage: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    stack = f"StartupOrchestratorStack-{stage.capitalize()}"
    if stack not in data:
        raise SystemExit(f"{stack} not found in {path}. Deploy it with cdk first.")
    out = data[stack]
    for key in ("ArtifactBucketName", "StateTableName", "AgentRuntimeRoleArn"):
        if key not in out:
            raise SystemExit(f"Output {key} missing from {stack} in {path}")
    return out


def stage_code(dest: str) -> None:
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    for d in COPY_DIRS:
        shutil.copytree(os.path.join(REPO, d), os.path.join(dest, d), ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(os.path.join(REPO, "config.yaml"), os.path.join(dest, "config.yaml"))
    shutil.copy(os.path.join(REPO, "agentcore_app", "main.py"), os.path.join(dest, "main.py"))
    with open(os.path.join(dest, "pyproject.toml"), "w", encoding="utf-8") as fh:
        fh.write(PYPROJECT)


def upsert_runtime(spec_path: str, entry: dict) -> None:
    with open(spec_path, "r", encoding="utf-8") as fh:
        spec = json.load(fh)
    runtimes = spec.setdefault("runtimes", [])
    for i, rt in enumerate(runtimes):
        if rt.get("name") == entry["name"]:
            runtimes[i] = {**rt, **entry}
            break
    else:
        runtimes.append(entry)
    with open(spec_path, "w", encoding="utf-8") as fh:
        json.dump(spec, fh, indent=2)
        fh.write("\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", choices=["beta", "prod"], required=True)
    ap.add_argument("--project-dir", required=True, help="Folder created by `agentcore create`")
    ap.add_argument("--outputs", required=True, help="CDK outputs JSON file")
    ap.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    args = ap.parse_args()

    spec_path = os.path.join(args.project_dir, "agentcore", "agentcore.json")
    if not os.path.exists(spec_path):
        print(f"{spec_path} not found. Create the project first: agentcore create --name SwarmRuntime", file=sys.stderr)
        return 1

    outputs = read_outputs(args.outputs, args.stage)
    name = f"startup_swarm_{args.stage}"
    code_rel = f"app/{name}/"
    stage_code(os.path.join(args.project_dir, code_rel))
    upsert_runtime(
        spec_path,
        {
            "name": name,
            "build": "CodeZip",
            "entrypoint": "main.py",
            "codeLocation": code_rel,
            "runtimeVersion": "PYTHON_3_12",
            "networkMode": "PUBLIC",
            "executionRoleArn": outputs["AgentRuntimeRoleArn"],
            "envVars": [
                {"name": "STAGE", "value": args.stage},
                {"name": "AWS_REGION", "value": args.region},
                {"name": "S3_ARTIFACT_BUCKET", "value": outputs["ArtifactBucketName"]},
                {"name": "DYNAMODB_STATE_TABLE", "value": outputs["StateTableName"]},
            ],
        },
    )
    print(f"Runtime '{name}' staged in {args.project_dir}. Next:")
    print(f"  cd {args.project_dir} && agentcore validate && agentcore deploy")
    print(f'  agentcore invoke --runtime {name} --payload \'{{"action": "status", "project": "demo"}}\'')
    return 0


if __name__ == "__main__":
    sys.exit(main())
