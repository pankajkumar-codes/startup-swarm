#!/usr/bin/env python3
"""CDK app. Select the stage with `-c stage=beta|prod`.

cdk deploy StartupOrchestratorStack-Beta -c stage=beta --outputs-file cdk-outputs-beta.json
cdk deploy GitHubDeployStack-Beta -c stage=beta -c github_repo=<owner>/<repo>   # optional
"""

import os

import aws_cdk as cdk

from stacks.github_deploy_stack import GitHubDeployStack
from stacks.orchestrator_stack import StartupOrchestratorStack

app = cdk.App()
stage = (app.node.try_get_context("stage") or os.environ.get("STAGE", "beta")).lower()
if stage not in ("beta", "prod"):
    raise ValueError("stage must be beta or prod")

env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
)
suffix = stage.capitalize()

StartupOrchestratorStack(
    app,
    f"StartupOrchestratorStack-{suffix}",
    stage=stage,
    env=env,
    description=f"Startup agent orchestrator data plane and IAM ({stage})",
)

github_repo = app.node.try_get_context("github_repo")
if github_repo:
    GitHubDeployStack(
        app,
        f"GitHubDeployStack-{suffix}",
        stage=stage,
        github_repo=github_repo,
        oidc_provider_arn=app.node.try_get_context("github_oidc_provider_arn"),
        env=env,
        description=f"GitHub Actions OIDC deploy role ({stage})",
    )

cdk.Tags.of(app).add("project", "startup-swarm")
cdk.Tags.of(app).add("stage", stage)
app.synth()
