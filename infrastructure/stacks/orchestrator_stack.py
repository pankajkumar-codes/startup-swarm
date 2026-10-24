"""Per-stage data plane and IAM for the startup swarm.

Creates, for one stage (beta or prod):
- S3 artifact bucket (versioned, encrypted, TLS-only, no public access)
- DynamoDB state table (PK/SK; point-in-time recovery on prod)
- AgentCore Runtime execution role (trusted by bedrock-agentcore.amazonaws.com only from
  this account) for hosted mode
- "Runner" managed policy to attach to the IAM user/role that runs the CLI from a laptop
  or CI against real AWS

Beta is disposable (bucket and table are deleted with the stack). Prod is retained.
"""

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from constructs import Construct


class StartupOrchestratorStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, stage: str = "beta", **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        stage = stage.lower()
        is_prod = stage == "prod"
        removal = RemovalPolicy.RETAIN if is_prod else RemovalPolicy.DESTROY

        bucket = s3.Bucket(
            self,
            "ArtifactBucket",
            bucket_name=f"startup-agent-artifacts-{stage}-{self.account}-{self.region}",
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=removal,
            auto_delete_objects=not is_prod,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="ExpireOldVersions",
                    noncurrent_version_expiration=Duration.days(90 if is_prod else 14),
                )
            ],
        )

        table = dynamodb.Table(
            self,
            "StateTable",
            table_name=f"startup-agent-state-{stage}",
            partition_key=dynamodb.Attribute(name="PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="SK", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=is_prod
            ),
            removal_policy=removal,
        )

        # Bedrock model access: foundation models in any region (cross-region inference
        # profiles route there) plus this account's inference profiles.
        bedrock_invoke = iam.PolicyStatement(
            sid="BedrockModelInvocation",
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=[
                "arn:aws:bedrock:*::foundation-model/*",
                f"arn:aws:bedrock:*:{self.account}:inference-profile/*",
            ],
        )

        # ---- AgentCore Runtime execution role (hosted mode) --------------------
        runtime_role = iam.Role(
            self,
            "AgentRuntimeRole",
            role_name=f"startup-swarm-agentcore-{stage}",
            description=f"AgentCore Runtime execution role for startup swarm ({stage})",
            assumed_by=iam.ServicePrincipal(
                "bedrock-agentcore.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": self.account},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:*"},
                },
            ),
        )
        runtime_role.add_to_policy(bedrock_invoke)
        runtime_logs = f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/bedrock-agentcore/runtimes/*"
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                sid="RuntimeLogs",
                actions=["logs:CreateLogGroup", "logs:DescribeLogStreams", "logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[runtime_logs, f"{runtime_logs}:log-stream:*"],
            )
        )
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                sid="DescribeLogGroups",
                actions=["logs:DescribeLogGroups"],
                resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:*"],
            )
        )
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                sid="Tracing",
                actions=[
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                    "xray:GetSamplingRules",
                    "xray:GetSamplingTargets",
                ],
                resources=["*"],
            )
        )
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                sid="Metrics",
                actions=["cloudwatch:PutMetricData"],
                resources=["*"],
                conditions={"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
            )
        )
        bucket.grant_read_write(runtime_role)
        table.grant_read_write_data(runtime_role)

        # ---- Policy for whoever runs the CLI against AWS (laptop or CI) ---------
        runner_policy = iam.ManagedPolicy(
            self,
            "RunnerPolicy",
            managed_policy_name=f"startup-swarm-runner-{stage}",
            description="Attach to the IAM user/role that runs `startup-swarm` against this stage",
            statements=[bedrock_invoke],
        )
        bucket.grant_read_write(runner_policy)
        table.grant_read_write_data(runner_policy)

        CfnOutput(self, "ArtifactBucketName", value=bucket.bucket_name)
        CfnOutput(self, "StateTableName", value=table.table_name)
        CfnOutput(self, "AgentRuntimeRoleArn", value=runtime_role.role_arn)
        CfnOutput(self, "RunnerPolicyArn", value=runner_policy.managed_policy_arn)
