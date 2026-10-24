"""Optional: keyless GitHub Actions deploys via OIDC (no long-lived AWS keys).

Creates an IAM role that only workflows in your repository, running in the GitHub
environment named after the stage ("beta" or "prod"), can assume. The role may only
assume the CDK bootstrap roles, which is what `cdk deploy` needs.

Deploy once per stage from your machine:
  cdk deploy GitHubDeployStack-Beta -c stage=beta -c github_repo=<owner>/<repo>
If your account already has the GitHub OIDC provider, also pass
  -c github_oidc_provider_arn=arn:aws:iam::<account>:oidc-provider/token.actions.githubusercontent.com
"""

from typing import Optional

from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_iam as iam
from constructs import Construct

GITHUB_OIDC_URL = "https://token.actions.githubusercontent.com"


class GitHubDeployStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        stage: str,
        github_repo: str,
        oidc_provider_arn: Optional[str] = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        if oidc_provider_arn:
            provider = iam.OpenIdConnectProvider.from_open_id_connect_provider_arn(
                self, "GitHubOidc", oidc_provider_arn
            )
        else:
            provider = iam.OpenIdConnectProvider(
                self, "GitHubOidc", url=GITHUB_OIDC_URL, client_ids=["sts.amazonaws.com"]
            )

        role = iam.Role(
            self,
            "DeployRole",
            role_name=f"startup-swarm-github-deploy-{stage}",
            assumed_by=iam.WebIdentityPrincipal(
                provider.open_id_connect_provider_arn,
                conditions={
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                        "token.actions.githubusercontent.com:sub": f"repo:{github_repo}:environment:{stage}",
                    }
                },
            ),
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="AssumeCdkBootstrapRoles",
                actions=["sts:AssumeRole"],
                resources=[f"arn:aws:iam::{self.account}:role/cdk-*"],
            )
        )
        CfnOutput(self, "DeployRoleArn", value=role.role_arn)
