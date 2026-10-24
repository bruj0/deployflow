"""Secrets live in a .env at the repository root.

Why .env and not ansible-vault:

  The vault this repository used had a real problem. It was encrypted with a
  throwaway password that was then deleted, so the file was undecryptable and
  unrecoverable — every run failed at "Attempting to decrypt but no vault
  secrets found" and could not be fixed from the repository. A secret store you
  can permanently lock yourself out of is a worse default than a plain file
  that is simply not committed.

  .env inverts the trade. The file is readable by the operator, editable by
  hand, and gitignored. It is exposed only if someone deliberately forces it
  into git. The cost is that there is no second factor, which is why the file
  is created with mode 0600 and `appctl secrets check` tells you plainly when a
  required key is absent.

Values reach Ansible as extra-vars read from a temporary file, never in argv,
so a secret does not appear in `ps` on the controller. `run.py` already had to
solve this for its own log redaction; that logic is shared rather than
duplicated.
"""

from __future__ import annotations

import os
import secrets as _secrets
import stat
from dataclasses import dataclass
from pathlib import Path

from .errors import SecretsError

ENV_NAME = ".env"
ENV_TEMPLATE_NAME = ".env.example"

# Names that mean a secret is missing, not merely unset. Used for the same
# redaction reason as in run.py: a value passed to Ansible as an extra var
# comes back in the log at -vvv.
_SECRETISH = ("pass", "secret", "token", "key", "credential")


def env_path(root: Path) -> Path:
    return root / ENV_NAME


def example_path(root: Path) -> Path:
    return root / ENV_TEMPLATE_NAME


def _is_secret_name(name: str) -> bool:
    lowered = name.lower()
    return any(marker in lowered for marker in _SECRETISH)


def generate(length: int = 40) -> str:
    """A random value for a secret that has no external source.

    token_urlsafe gives 32 bytes of entropy from the stdlib, which is the right
    default for a bearer token. Not used for anything with an external issuer:
    a provider API key has to come from the provider.
    """
    return _secrets.token_urlsafe(length)[:length]


def write_env(root: Path, values: dict[str, str], *, comment: str | None = None) -> Path:
    """Write .env with mode 0600, preserving existing keys and their order.

    Rewritten in place rather than appended, so `appctl secrets set` twice for
    the same key leaves one entry, not two. A duplicate KEY in a .env file is a
    well-known source of "I changed it and nothing happened".
    """
    target = env_path(root)
    existing = read_env(root)

    lines: list[str] = []
    if comment:
        for line in comment.splitlines():
            lines.append(f"# {line}" if line else "#")
        lines.append("")

    for name in existing:
        value = values.get(name, existing[name])
        lines.append(f"{name}={_quote(value)}" if name in values else f"{name}={_quote(existing[name])}")

    for name, value in values.items():
        if name not in existing:
            lines.append(f"{name}={_quote(value)}")

    target.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    target.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return target


def _quote(value: str) -> str:
    """Quote a value if it needs it, so spaces and `#` survive a round trip."""
    if value == "":
        return '""'
    if any(ch in value for ch in ' \t#"\'') or value != value.strip():
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def read_env(root: Path) -> dict[str, str]:
    """Parse .env without requiring python-dotenv to be installed.

    Delegates to python-dotenv when available because it handles the edge cases
    properly, and falls back to a small parser otherwise so a missing optional
    dependency degrades to "slightly less clever" rather than "broken".
    """
    target = env_path(root)
    if not target.is_file():
        return {}
    try:
        from dotenv import dotenv_values

        return {k: v for k, v in dotenv_values(target).items() if v is not None}
    except ImportError:
        pass
    return _simple_parse(target.read_text(encoding="utf-8"))


def _simple_parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[name] = value
    return out


@dataclass(frozen=True)
class Check:
    """The result of checking one required secret."""

    name: str
    present: bool
    empty: bool


def check(root: Path, required: tuple[str, ...] | list[str]) -> list[Check]:
    """Report which required secrets are present, without revealing values."""
    values = read_env(root)
    return [
        Check(name=name, present=name in values, empty=(name not in values or values[name] == ""))
        for name in required
    ]


def missing(root: Path, required: tuple[str, ...] | list[str]) -> list[str]:
    """Names of required secrets that are absent or empty."""
    return [c.name for c in check(root, required) if c.empty]


def ensure_for_install(root: Path, required: tuple[str, ...] | list[str]) -> dict[str, str]:
    """Return the required secrets, or explain precisely what is missing.

    Called before every play. Failing here, with the missing names listed, is
    far better than letting the role fail halfway through with "example-app refuses
    a wildcard bind without a token".
    """
    absent = missing(root, required)
    if absent:
        raise SecretsError(
            f"{len(absent)} required secret{'s' if len(absent) > 1 else ''} not set in {env_path(root)}",
            hint="set them with:  appctl secrets set "
                 + " ".join(absent)
                 + "\n       or check the whole set with:  appctl secrets check",
        )
    values = read_env(root)
    return {name: values[name] for name in required}


def write_example(root: Path, names: list[str]) -> Path:
    """Write .env.example listing the required keys with empty values.

    Committed on purpose. It is the list of what this deployment needs without
    being any of the values, so it can go in a pull request and a fresh clone
    knows what to fill in.
    """
    target = example_path(root)
    body = [
        "# Secrets this deployment needs, and the values for them.",
        "# Copy to .env and fill in:  appctl secrets init",
        "#",
        "# .env is gitignored. Do not commit it, and do not paste its contents into",
        "# a log, a ticket or a chat. `appctl secrets list` prints names only.",
        "",
    ]
    for name in names:
        # Case-insensitive: these names are conventionally upper case, so a
        # case-sensitive endswith silently classified every one of them as
        # "set me" when they are in fact generatable.
        generatable = _is_secret_name(name) and name.lower().endswith(("_secret", "_token"))
        body.append(f"{name}=  # {'appctl secrets set ' + name + ' --generate' if generatable else 'set me'}")
    target.write_text("\n".join(body).rstrip("\n") + "\n", encoding="utf-8")
    return target


def redact(values: dict[str, str]) -> dict[str, str]:
    """Blank out anything password-shaped, for logging."""
    return {
        k: ("***REDACTED***" if _is_secret_name(k) else v)
        for k, v in values.items()
    }


def load_into_environ(root: Path) -> dict[str, str]:
    """Read .env into this process's environment without overwriting the real one.

    A variable already present in the environment wins. An operator who exports
    ANTHROPIC_API_KEY for their own tooling should not have it silently replaced
    by the repository's value.
    """
    values = read_env(root)
    applied = {}
    for name, value in values.items():
        if name not in os.environ:
            os.environ[name] = value
            applied[name] = value
    return applied
