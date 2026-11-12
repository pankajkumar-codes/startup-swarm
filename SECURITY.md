# Security policy

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private reporting:
**Security tab > Report a vulnerability** on this repository.
You will get a first reply within a few days.

## What to know about this tool

- startup-swarm sends your idea, config and any `--context-file` content to the model provider you choose.
  Do not include secrets in them.
- Generated code is written to disk but never executed by the orchestrator. Review it before running it.
- File writes are confined to the output folder. Paths that escape it, `.git/`, and (in `task --target-dir`
  mode) `.github/` and files you did not pass with `--context-file` are refused.
- API keys are read from environment variables or your cloud's credential chain and are never written to artifacts.

Supported versions: the latest release.
