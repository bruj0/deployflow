"""The registry of delivery targets.

One place that knows which targets exist, which are implemented, and what to
say when a manifest asks for something that is not there.

The registry lists **planned** targets separately from **implemented** ones.
That distinction is the point of the module: a manifest may name a target that
is planned but not built, and the answer must be a clear failure before any
side effect, not a silent fallback to the only target that works.
"""

from __future__ import annotations

from .base import Target, TargetError

# Implemented targets, by kind.
_IMPLEMENTED: dict[str, type[Target]] = {}

# Targets that are planned but not built. Naming one fails loudly.
_PLANNED: dict[str, str] = {
    "vm": "provision a virtual machine and install the application into it",
    "docker_vm": "provision a virtual machine and run the application in Docker there",
}


def register(target: type[Target]) -> type[Target]:
    """Register an implemented target. Usable as a decorator."""
    kind = target.kind
    if not kind:
        raise TargetError(f"target {target.__name__} has no kind")
    _IMPLEMENTED[kind] = target
    return target


def available_targets() -> list[str]:
    """Implemented target kinds, sorted."""
    return sorted(_IMPLEMENTED)


def unimplemented_targets() -> dict[str, str]:
    """Planned target kinds and what each would do."""
    return dict(_PLANNED)


def is_implemented(kind: str) -> bool:
    """Whether a target kind can be used."""
    return kind in _IMPLEMENTED


def resolve(kind: str) -> Target:
    """Return an instance of the named target.

    Raises TargetError for an unknown or unimplemented kind. It never falls
    back to another target: an application that asks for a VM must not be
    silently installed into a container.
    """
    if not kind:
        raise TargetError(
            "no target.kind in this application",
            hint=f"set target.kind to one of: {', '.join(available_targets())}",
        )
    if kind in _IMPLEMENTED:
        return _IMPLEMENTED[kind]()
    if kind in _PLANNED:
        raise TargetError(
            f"target kind {kind!r} is planned but not implemented",
            hint=f"{_PLANNED[kind]}. "
                 f"implemented targets: {', '.join(available_targets())}",
        )
    known = sorted(set(_IMPLEMENTED) | set(_PLANNED))
    raise TargetError(
        f"unknown target kind {kind!r}",
        hint=f"known target kinds: {', '.join(known)}",
    )
