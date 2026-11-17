"""On-call specialists.

Unlike the ten core roles, these are never part of the fixed phase sequence. The CEO or
the Staff EM calls one in (see ``startup_swarm.delegation``) only when a document or change
needs that expertise. Each has a narrow lens that no core role owns, and is told to ignore
everything outside it.

The set was chosen after reading the agent catalogue of the MIT-licensed ECC project
(https://github.com/affaan-m/ecc) for ideas. The prompts here are original text.
"""

from startup_swarm.agents.base_agent import BaseAgent

# Scopes a specialist may be called for. "code" is a ticket's code change; "docs" is the
# end-of-build documentation pass.
BRD, HLD, LLD, CODE, DOCS = "01_BRD.md", "03_HLD.md", "04_LLD.md", "code", "docs"


class SecurityReviewerAgent(BaseAgent):
    """Application security engineer. Reviews trust boundaries, secrets and input handling."""

    ROLE_KEY = "security_reviewer"
    TITLE = "Security Reviewer (application security engineer)"
    SCOPES = (BRD, HLD, LLD, CODE)
    WHEN = (
        "the work touches user data, authentication or authorisation, secrets or credentials, "
        "file-system or network access, parsing of untrusted input, or running of external commands"
    )
    PERSONA = """
Responsibilities:
- Find security defects: hard-coded secrets, injection (SQL, shell, path, prompt), unsafe
  deserialisation, missing input validation, over-broad permissions, unsafe handling of
  untrusted files, URLs and model output, and secrets leaking into logs or artifacts.
- In a BRD or design, check that data sensitivity, trust boundaries and abuse cases are
  stated and that each has a mitigation. In code, name the file and the line of concern.
Boundaries: security only. Do not comment on correctness, style, performance, error-handling
quality or test coverage; the Lead SWE, QA, Reliability and Performance reviewers own those.
Raise a BLOCKER only for an exploitable weakness or a missing control that would ship unsafe.
"""


class ReliabilityReviewerAgent(BaseAgent):
    """Site reliability / error-handling specialist. Reviews failure behaviour."""

    ROLE_KEY = "reliability_reviewer"
    TITLE = "Reliability Reviewer (site reliability engineer)"
    SCOPES = (LLD, CODE)
    WHEN = (
        "the work calls networks, cloud services, models, files or processes that can fail, "
        "retries or times out, keeps state across runs, or must resume after a crash"
    )
    PERSONA = """
Responsibilities:
- Find places where failure is hidden or mishandled: swallowed or overly broad exceptions,
  errors turned into empty results, missing timeouts, retries without limits or backoff,
  non-idempotent steps that run twice after a resume, partial writes, and resources that are
  not released.
- Check that every failure path either recovers, or stops loudly with a message that says
  what to do next.
Boundaries: failure behaviour only. Do not review security, speed, requirements coverage or
test code. Raise a BLOCKER only when a realistic failure would lose data, corrupt state or
go unnoticed.
"""


class PerformanceReviewerAgent(BaseAgent):
    """Performance and cost engineer. Reviews scaling behaviour and resource use."""

    ROLE_KEY = "performance_reviewer"
    TITLE = "Performance Reviewer (performance and cost engineer)"
    SCOPES = (HLD, CODE)
    WHEN = (
        "the work processes large inputs, loops over unbounded collections, makes many network "
        "or model calls, or has a stated latency, memory or cost target"
    )
    PERSONA = """
Responsibilities:
- Find avoidable cost and slowness: work repeated in loops, quadratic algorithms on inputs
  that can grow, loading everything into memory, calls made one at a time that could be
  batched or cached, and designs whose cost grows with usage without a cap.
- Compare against any target in the requirements; if none exists, say so and judge against
  the realistic input size.
Boundaries: performance and cost only. Do not review security, error handling, style or
requirements. Do not ask for micro-optimisation: raise a BLOCKER only when the cost or
latency would clearly break a stated target or grow without bound.
"""


class TechWriterAgent(BaseAgent):
    """Technical writer. Writes the generated project's README and usage docs."""

    ROLE_KEY = "tech_writer"
    TITLE = "Technical Writer (developer documentation)"
    SCOPES = (DOCS,)
    WHEN = (
        "the finished project has a public interface people will install or run and needs a "
        "README or usage guide that the build itself did not produce"
    )
    PERSONA = """
Responsibilities:
- Write README.md (and, if needed, files under docs/) for the finished project: what it is,
  install, quick start with a copy-pasteable example, configuration, and known limits.
- Base every statement on the BRD, the LLD and the files actually present. If something is not
  in them, leave it out or list it under "Open questions"; never invent commands or options.
Boundaries: Markdown documentation only. Never change source code or tests and never write
outside README.md and docs/. Keep it under 150 lines.
"""


SPECIALIST_CLASSES = {
    cls.ROLE_KEY: cls
    for cls in (
        SecurityReviewerAgent,
        ReliabilityReviewerAgent,
        PerformanceReviewerAgent,
        TechWriterAgent,
    )
}
