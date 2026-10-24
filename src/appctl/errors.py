"""Errors this tool raises on purpose.

One class, so the CLI can catch it and print a message without a traceback. A
traceback here would be the tool's fault, not the operator's, and the operator
cannot act on a stack frame.
"""

from __future__ import annotations


class AppctlError(Exception):
    """A failure the operator can act on.

    Carries a hint separately from the message so the CLI can print the hint on
    its own line, and a `exit_code` so callers do not need to know the mapping.
    """

    def __init__(self, message: str, *, hint: str | None = None, exit_code: int = 1) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.exit_code = exit_code


class ConfigError(AppctlError):
    """app.yaml is missing, unreadable, or does not match the schema."""


class SecretsError(AppctlError):
    """A required secret is absent from .env."""


class BuildError(AppctlError):
    """A build step failed, or a precondition for it was not met."""


class InstallError(AppctlError):
    """The Ansible play could not be started."""
