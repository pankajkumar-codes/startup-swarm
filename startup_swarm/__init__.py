"""startup-swarm: a ten-agent software startup with review gates and token controls.

Public API (stable across 0.x minor releases):

    from startup_swarm import StartupPipeline, Settings, make_client
    from startup_swarm.agents import StaffSWEAgent
    from startup_swarm.backends import Backend           # for cloud plugins
    from startup_swarm.state_store import StateStore     # for cloud plugins
    from startup_swarm.artifact_bus import RemoteMirror  # for cloud plugins
"""

__version__ = "0.3.0"

from startup_swarm.config import ModelSpec, Settings  # noqa: E402
from startup_swarm.llm import make_client  # noqa: E402
from startup_swarm.pipeline import RunOutcome, StartupPipeline  # noqa: E402

__all__ = ["__version__", "ModelSpec", "RunOutcome", "Settings", "StartupPipeline", "make_client"]
