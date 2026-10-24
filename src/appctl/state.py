"""Record what was done to each application, in `app.yaml` itself.

An operator with several applications needs to answer "what is actually running
on that guest, and when did it last change?" without reading logs. So a build or
an install writes a short record back into the configuration, next to the
application it describes.

**The file is edited as text, not loaded and re-dumped.** `app.yaml` carries 18
lines of comment that say why each value is what it is, and a PyYAML round-trip
destroys every one of them — verified. Rewriting the whole file to append a
timestamp is a bad trade. So this module rewrites only the `state:` block, which
it owns, and leaves every other byte alone.

That is also why `state:` is a known key in the schema even though an operator
never writes it by hand.

The cost, stated plainly: a timestamp in a committed file is a diff on every
run. That is the point of a record, but it is churn, and it belongs in review
noise. If a deployment's git history should only carry intentional changes,
remove the block or keep `app.yaml` out of version control.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from .config import Config
from .errors import AppctlError

# The marker line, written verbatim so a human can see what the block is.
HEADER = "    # Written by appctl after a build or an install. Do not hand-edit."
STATE_KEY = "state:"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _quote(value: str) -> str:
    return f'"{value}"'


def render_block(record: dict) -> str:
    """Build the `state:` block text. Quoted values, so YAML keeps the types."""
    lines = [HEADER, "    " + STATE_KEY]
    for section, entries in record.items():
        lines.append(f"      {section}:")
        for key, value in entries.items():
            if isinstance(value, int):
                lines.append(f"        {key}: {value}")
            else:
                lines.append(f"        {key}: {_quote(str(value))}")
    return "\n".join(lines)


def _app_extent(lines: list[str], name: str) -> tuple[int, int]:
    """Line range of the `name:` block under `applications:`, as 0-based [start, end)."""
    header = f"  {name}:"
    try:
        start = lines.index(header)
    except ValueError:
        raise AppctlError(
            f"cannot find application {name!r} in the configuration",
            hint="run `appctl list` to see the applications this config has",
        ) from None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if line.strip() and not line.startswith("    "):
            # Back at the level of the application key, so this one ended here.
            end = i
            break
    return start, end


def _existing_state(lines: list[str], start: int, end: int) -> tuple[int, int] | None:
    """Line range of the `state:` block inside an application, if present.

    The block runs until something is no longer indented under `state:`, so the
    default end is the end of the application, not the line after the key. The
    scan then narrows it for a block that is not last.
    """
    for i in range(start, end):
        if lines[i].strip() == STATE_KEY and lines[i].startswith("    "):
            block_end = end
            for j in range(i + 1, end):
                line = lines[j]
                if line.strip() and not line.startswith("      "):
                    block_end = j
                    break
            return i, block_end
    return None


def write(config: Config, name: str, record: dict) -> None:
    """Replace or insert the `state:` block for one application.

    Refuses rather than guesses. If the application's block cannot be located, or
    the state block cannot be delimited, it raises instead of writing a file it
    has misread — a corrupted configuration is much worse than a missing record.
    """
    path = config.path
    original = path.read_text(encoding="utf-8")
    lines = original.split("\n")

    start, end = _app_extent(lines, name)
    found = _existing_state(lines, start, end)
    block = render_block(record).split("\n")

    if found:
        first, last = found
        # Drop the old header line too, so a rebuild does not stack headers.
        if first > 0 and lines[first - 1].strip().startswith("# Written by appctl"):
            first -= 1
        lines[first:last] = block
    else:
        # Append inside the application, after its last line of content.
        insert_at = end
        while insert_at > start + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines[insert_at:insert_at] = [""] + block

    path.write_text("\n".join(lines), encoding="utf-8")


def record_build(config: Config, name: str, result) -> None:
    """Record a completed build: which commit, which wheel, which hash."""
    record = _existing_record(config, name)
    record["built"] = {
        "commit": result.commit,
        "wheel": result.wheel,
        "wheel_sha256": result.wheel_sha256,
        "packages": result.packages,
        "at": _now(),
    }
    write(config, name, record)


def record_install(config: Config, name: str, result, *, changed: int, failed: int) -> None:
    """Record a completed install: when, and whether anything actually changed.

    The result is a target's report, so the counts come from the target rather
    than from one executor. That keeps the record meaningful when another
    target reports its own numbers.
    """
    record = _existing_record(config, name)
    record["installed"] = {
        "at": _now(),
        "changed": changed,
        "failed": failed,
        "tasks": getattr(result, "tasks", 0),
        "seconds": int(getattr(result, "seconds", 0)),
        "target": getattr(result, "target", ""),
    }
    write(config, name, record)


def _existing_record(config: Config, name: str) -> dict:
    """Read the current state block for an application, as a plain dict."""
    lines = config.path.read_text(encoding="utf-8").split("\n")
    start, end = _app_extent(lines, name)
    found = _existing_state(lines, start, end)
    if not found:
        return {}
    return _parse(lines[found[0] + 1 : found[1]])


def _parse(block: list[str]) -> dict:
    """Turn the block's text back into a dict.

    A small hand parser rather than yaml.safe_load on the slice: the slice is
    not valid YAML on its own — it is indented under `state:` — and indenting it
    would be more fragile than reading six lines of shape we wrote ourselves.
    """
    record: dict = {}
    section: str | None = None
    for line in block:
        if not line.strip() or line.strip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        text = line.strip()
        if indent == 6 and text.endswith(":"):
            section = text[:-1]
            record[section] = {}
        elif indent == 8 and section and ":" in text:
            key, _, value = text.partition(":")
            value = value.strip().strip('"')
            record[section][key.strip()] = int(value) if value.isdigit() else value
    return record
