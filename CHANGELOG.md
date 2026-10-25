# Changelog

## 0.3.0 (2026-10-25): cloud-neutral core

Breaking
- Package renamed to `startup_swarm` (was top-level `orchestrator` and `agents`).
  Update imports: `from startup_swarm.agents import StaffSWEAgent`.
- AWS hosting moved to [startup-swarm-aws](https://github.com/pankajkumar-codes/startup-swarm-aws):
  CDK stacks, S3/DynamoDB storage, AgentCore entrypoint and the deploy workflow.
- `config.yaml` is no longer in the repo root; run `startup-swarm init` to create one.
  Model IDs are now per provider (`models.<tier>.model_ids.<provider>`).
- Offline mode env var is `SWARM_MOCK=1` (`AWS_MOCK_MODE=1` still works).

Added
- Model providers: Claude API (`anthropic`), Amazon Bedrock (`bedrock`), Google Vertex AI
  (`vertex`), Microsoft Foundry / Azure (`foundry`), offline `mock`. Each is an optional extra.
- Plugin entry points for storage backends (`startup_swarm.backends`) and providers
  (`startup_swarm.providers`).
- `startup-swarm init` and `startup-swarm info` commands; `--provider` and `--backend` flags.
- Release workflow: tag `vX.Y.Z` builds the package and creates a GitHub release.
- CI builds the wheel and runs it from a clean environment; tests on Python 3.11 to 3.13.

## 0.2.0 (2026-10-24): review and fixes of the Gemini-generated 1.0.0

Correctness
- CEO gate now decides: strict JSON decision, revision loop, hard stop before architecture.
  (1.0.0 logged "CEO Executive Gate passed" without checking anything.)
- Reviews fail closed: unparseable review = blocker. (1.0.0 defaulted to APPROVED.)
- Review feedback is applied to mocks, HLD and LLD too (1.0.0 only revised the BRD).
- Deduplication actually merges identical blockers from different reviewers.
- Integration test pointed at a config path that only existed in Gemini's sandbox.

Features
- Phase 5 produces real code and tests in `generated_project/` (per-ticket: code, tests,
  Lead SWE code review, one fix pass). 1.0.0 wrote a markdown "manifest".
- `startup-swarm status`, `task` (direct engineering mode), `approve-gate` commands.
- Resume from the last finished phase; optional founder approval gate; ticket batches.
- S3 mirror and DynamoDB state are actually used (1.0.0 created them but never wrote to them).
- Market Researcher gets the reference repo's real README and file list.
- Templates are generic and actually used in prompts.

Tokens and models
- Bedrock Converse API with `cachePoint` caching laid out so reviewers share a cached prefix;
  real cache read/write tokens reported. (1.0.0 cached a ~80-token system prompt, below the
  minimum cacheable size, and reported a made-up 70% cache figure in mock mode.)
- Hard token budget with clean pause.
- Models updated from outdated Claude 3.5 IDs to Claude Sonnet 5.5 / Haiku 5.5 inference profiles.

AWS
- Removed fictional `bedrock-agentcore.yaml` and `agentcore run --workflow` command; added a real
  AgentCore Runtime entrypoint and a script that stages it into an AgentCore CLI project.
- Runtime role trusts `bedrock-agentcore.amazonaws.com` with source account/ARN conditions
  (1.0.0 trusted `bedrock.amazonaws.com`) and allows inference profiles.
- GitHub Actions use OIDC instead of long-lived access keys; CI runs before every deploy;
  CDK synth runs in CI.
