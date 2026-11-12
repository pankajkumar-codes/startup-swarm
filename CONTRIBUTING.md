# Contributing

Thanks for helping. This is a small project, so the rules are short.

## Set up

```bash
git clone https://github.com/pankajkumar-codes/startup-swarm.git
cd startup-swarm
python3 -m venv .venv && source .venv/bin/activate     # Windows: py -3.12 -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -e ".[dev,all]"
```

## Before you open a PR

```bash
python -m pytest -q                                    # offline, no API key needed
black --check startup_swarm tests && flake8 startup_swarm tests
```

- Tests must run offline. Use the mock provider or fakes; never call a real model in a test.
- Work on a branch and open a PR into `main`. `main` is protected and CI must pass.
- Keep a PR to one change. Add a line to `CHANGELOG.md` for anything a user would notice.
- Process rules (gates, fail-closed reviews, budget cap) live in code in `startup_swarm/pipeline.py`.
  Please do not move them into prompts.
- Never commit `.env*`, API keys or an `artifacts/` folder.

## Good first contributions

- Try a provider or model we have not tested (Bedrock, Vertex, Foundry) and report what happened.
- Share a run: the idea, the config, token use, and what was good or bad in the output.
- Improve a template in `startup_swarm/templates/` or a role prompt.
- Add a storage backend or provider as a plugin (see "Storage backends and plugins" in the README).

## Releases

Maintainers bump `__version__`, merge, then push a matching `vX.Y.Z` tag. The Release workflow builds the
package and creates a GitHub release.

### Publishing to PyPI (one-time setup)

1. Create an account at pypi.org and turn on two-factor authentication.
2. On PyPI go to **Your projects > Publishing** and add a *pending publisher*: project name `startup-swarm`,
   owner `pankajkumar-codes`, repository `startup-swarm`, workflow `release.yml`, environment `pypi`.
3. In GitHub go to **Settings > Environments**, create an environment named `pypi`
   (optionally with yourself as required reviewer).
4. In **Settings > Secrets and variables > Actions > Variables** add `PUBLISH_TO_PYPI` = `true`.

From the next `vX.Y.Z` tag the release is also published to PyPI. No token is stored in GitHub.
