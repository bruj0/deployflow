"""The interface every delivery target implements.

Deliberately small. Each method here is a step that the first target, Proxmox
LXC, actually performs. A method that no target can honour honestly has no
place in the interface, because a method that returns "not supported" is a
promise nobody keeps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class TargetError(Exception):
    """A target could not do what was asked.

    Raised before any side effect when the request itself is impossible: an
    unknown target, a missing prerequisite, an unsupported operation.
    """

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


@dataclass(frozen=True)
class Prerequisite:
    """One thing that must be true before a target can run.

    ``check`` returns None when the prerequisite holds, or a message saying
    what is wrong. The message is what the operator reads, so it names the
    remedy rather than the symptom.
    """

    name: str
    check: Any
    remedy: str = ""


@dataclass
class TargetContext:
    """What a target receives for one application.

    It carries the configuration, the name of the application being delivered,
    the run settings and the secret values for this run. The target does not
    read the whole manifest; it reads its own application.

    ``secret_values`` holds only the secrets this application requires, and
    only for the duration of the run.
    """

    config: Any
    app_name: str
    verbosity: int = 3
    forks: int = 10
    heartbeat: int = 30
    secret_values: dict[str, str] = field(default_factory=dict)
    #: Flags the operator passed after a bare `--`. They belong to the
    #: executor, so the target forwards them rather than interpreting them.
    extra_args: list[str] = field(default_factory=list)

    def app(self, name: str | None = None) -> Any:
        """The application this context is about."""
        return self.config.app(name or self.app_name)

    @property
    def root(self):
        """The repository root."""
        return self.config.root

    @property
    def applications(self) -> dict:
        """Every application in the manifest."""
        return self.config.applications


@dataclass
class TargetResult:
    """What a target observed while deploying.

    ``checks`` is the evidence that the application works, not that the
    command exited zero. Each entry is one named observation.
    """

    target: str
    guest: str = ""
    address: str = ""
    changed: int = 0
    failed: int = 0
    tasks: int = 0
    seconds: float = 0.0
    checks: list[str] = field(default_factory=list)
    logs: Path | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed == 0


class Target:
    """Base class for a delivery target.

    Subclasses set ``kind`` and ``summary`` and implement the steps they can
    perform. The defaults fail loudly rather than silently doing nothing.
    """

    #: The value that appears in ``target.kind`` in the manifest.
    kind: str = ""

    #: One line describing where this target deploys to.
    summary: str = ""

    #: What this target can do, for `appctl targets`.
    capabilities: tuple[str, ...] = ()

    def validate(self, config: Any) -> None:
        """Check the target's own settings and prerequisites.

        Called before any build, download or execution. Raising here is the
        cheapest way to stop a run that cannot succeed.
        """

    def provision(self, config: Any) -> dict[str, str]:
        """Create or identify the destination.

        Returns the connection facts the rest of the run needs, such as the
        guest name and its address.
        """
        raise TargetError(
            f"target {self.kind!r} does not provision anything",
            hint="this target is not implemented",
        )
    def deploy(self, config: Any, artifacts: dict[str, Path],
               rendered: dict[str, str]) -> TargetResult:
        """Install the application and run its checks."""
        raise TargetError(
            f"target {self.kind!r} does not deploy anything",
            hint="this target is not implemented",
        )

    def describe(self) -> str:
        """A short line for `appctl targets`."""
        return f"{self.kind}: {self.summary}"
