"""Delivery targets: where an application runs.

A target answers one question — *where and how does this application run?* —
and does it in five steps:

1. validate the target-specific configuration and prerequisites;
2. provision or identify the destination and return where to connect;
3. accept the prepared artifact and non-secret configuration;
4. deploy, then run the target's and the application's own checks;
5. report what it supports and what it observed.

The first implementation is ``proxmox_lxc``: an application installed into a
Proxmox LXC guest with Ansible. A later target could provision a virtual
machine and install into it, or run Docker inside a virtual machine. Those
targets are **not implemented**. A manifest that names one fails before
anything is built, downloaded or started.

Two rules hold the interface together:

- **An application does not know its target.** Source, artifact, configuration,
  secrets and expected behaviour are the same whatever runs them. Only the
  target's own settings change.
- **A target never decides what to install.** It receives what the shared
  pipeline built and hands it to the destination.

The registry below is the whole of what this version supports.
"""

from __future__ import annotations

from .base import Target, TargetError, TargetResult
from .registry import (
    available_targets,
    is_implemented,
    register,
    resolve,
    unimplemented_targets,
)

# Importing the implementations is what registers them. The order matters only
# in that the base module must load before the implementations subclass it.
from . import proxmox_lxc as _proxmox_lxc  # noqa: E402,F401

__all__ = [
    "Target",
    "TargetError",
    "TargetResult",
    "available_targets",
    "is_implemented",
    "register",
    "resolve",
    "unimplemented_targets",
]
