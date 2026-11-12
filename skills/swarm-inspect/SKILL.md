---
name: swarm-inspect
description: >-
  Read and critique what startup-swarm produced: the vision, market research, BRD, mocks, HLD, LLD, backlog, review
  verdicts, token usage and generated code, and explain or handle its gates. Use after a swarm run, or when the user asks
  "what did the swarm make", "why did it stop", "show the token usage", "approve the CEO gate", or "review the generated code".
---

# swarm-inspect

Work from the files a run wrote, in `artifacts/<stage>/<project>/` (default `artifacts/beta/<project>/`).

## Where things are

| File or folder | What it holds |
|---|---|
| `00_CEO_VISION.md`, `01_MARKET_RESEARCH.md`, `01_BRD.md`, `02_MOCKS.md` | Strategy and requirements |
| `02_CEO_DECISION.md` | The CEO gate decision and any required changes |
| `03_HLD.md`, `04_LLD.md`, `05_SPRINT_BACKLOG.md` | Architecture, detailed design, tickets |
| `reviews/*.json` and `*.punchlist.md` | Each reviewer's verdict and the merged blockers |
| `tickets/` | One report per built ticket |
| `generated_project/` | The code and tests |
| `usage_last_run.json` | Real token counts per agent for the last run |
| `.history/` | Every version of every document |

## Steps

1. `startup-swarm status` (add `--json` if you need to parse it): phase, gates, tokens.
2. Read the documents in order and summarise for the user: what is being built, for whom, what was decided.
3. Read `reviews/` for blockers that were merged into a punch list and whether they were fixed or escalated to
   `99_OPEN_ISSUES.md`.
4. Review `generated_project/` like a normal code review: does it match the LLD, are the tests meaningful, any hard-coded
   secrets or unsafe calls. Be direct about weak spots. The agents can be wrong.
5. Report tokens from `usage_last_run.json` and remind the user to check billing. Do not invent a dollar figure.

## Gates

- Engineering cannot start until the CEO gate is approved. If it is blocked, show the CEO's reasons and the required changes.
- With `founder_approval_required: true` the run pauses for the founder. **Only the user can approve a gate.** Never run
  `startup-swarm approve-gate ceo` or `approve-gate founder` on your own. Show the evidence, ask, and run it only if the user
  says to. After approval, re-run the same `startup-swarm run` command to continue.
