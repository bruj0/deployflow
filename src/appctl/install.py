"""Run the Ansible play, streaming and logging.

Carried over from run.py rather than rewritten, because the behaviour there was
arrived at by measurement and re-deriving it would be a waste of a live host:

  streaming        the play is slow and a silent terminal cannot be told apart
                   from a hung one
  heartbeat        reports how long the current task has been silent, which is
                   the number that distinguishes slow from wedged
  commands.txt     one line per command with its task; log.txt is 150 KB of
                   which one line alone is 52 KB of gathered facts
  redaction        a password passed as an extra var comes back in the log at
                   -vvv, twice: once from our own argv, once from Ansible's
                   extra_vars echo

The one new thing is where secrets come from. They are read from .env and
written to a temporary file passed as `--extra-vars @file`, so no secret
appears in argv, in `ps`, or in the run logs.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import render as render_mod
from . import secrets as secrets_mod
from .config import Config
from .errors import InstallError

TASK_RE = re.compile(r"^TASK \[(.+?)\]\s*\*+\s*$")
RESULT_RE = re.compile(r"^(ok|changed|skipping|failed|fatal|rescued|ignored|unreachable):")
FATAL_RE = re.compile(r"^(fatal|failed):", re.I)
# Ansible writes "[ERROR]:" with brackets. Missing them means a play that dies
# before its first task reports a clean run.
ERROR_RE = re.compile(r"^\[?ERROR\]?:", re.I)
COMMAND_RE = re.compile(r'^\s*"cmd":\s*(\[.*\]|".*")')
EXEC_RE = re.compile(r"^<[^>]+>\s+EXEC ")
REMOTE_EXEC_RE = re.compile(r"SSH:\s*EXEC\s+ssh\b.*?'(.*)'\s*$")
LIST_OPENER_RE = re.compile(r'^\s*"(cmd|argv|extra_args)":\s*\[\s*$')
NULL_ARG_RE = re.compile(r'^\s*"(?:path|src|dest|name)":\s*null,?$')
EXTRA_VARS_RE = re.compile(r"^(\s*)extra_vars:\s*(.*)$")

ARG_KEYS = ("cmd", "argv", "_raw_params", "src", "dest", "path", "name",
            "requirements", "extra_args", "virtualenv", "content", "url", "state", "mode")
ARG_RE = re.compile(r'^\s*"(' + "|".join(ARG_KEYS) + r')":\s*(.+?),?$')
ARG_MAX_LEN = 120
SECRETISH_RE = re.compile(r"(pass|secret|token|api_?key|credential)", re.I)
SECRET_NAME_RE = re.compile(r"^(--?e\s+|-e)?\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)$")


def redact_argv(cmd: list[str]) -> list[str]:
    """Blank the value of any password-shaped -e name=value."""
    out = []
    for arg in cmd:
        m = SECRET_NAME_RE.match(arg.strip())
        if m and SECRETISH_RE.search(m.group("name")):
            prefix = m.group(1) or ""
            out.append(f"{prefix}{m.group('name')}=***REDACTED***".strip())
        else:
            out.append(arg)
    return out


@dataclass
class Result:
    label: str
    returncode: int = 0
    seconds: float = 0.0
    tasks_seen: int = 0
    commands_seen: int = 0
    tasks_changed: int = 0
    tasks_failed: int = 0
    directory: Path = field(default_factory=lambda: Path("."))
    problems: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.label}: {'OK' if self.returncode == 0 else 'FAILED'} in {self.seconds:.0f}s "
            f"({self.tasks_seen} tasks, {self.tasks_changed} changed, "
            f"{self.tasks_failed} failed, {self.commands_seen} commands)"
        )


def _echo(msg: str = "") -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}" if msg else "", flush=True)


class Installer:
    """Runs ansible-playbook and turns its output into both a terminal view and
    a set of files worth keeping afterwards."""

    def __init__(self, config: Config, *, verbosity: int = 3, forks: int = 10,
                 heartbeat: int = 30) -> None:
        self.config = config
        self.verbosity = verbosity
        self.forks = forks
        self.heartbeat = heartbeat
        self._shared = {"last_output": [time.monotonic()], "last_task": ["(starting)"]}
        self._list_open: str | None = None
        self._list_items: list[str] = []

    def command(self, extra: list[str], secret_vars: dict[str, str],
                rendered: dict[str, str] | None = None) -> list[str]:
        cmd = [
            "ansible-playbook",
            "-i", str(self.config.root / "inventory" / "hosts.yml"),
            "playbooks/site.yml",
            "--forks", str(self.forks),
        ]
        if self.verbosity:
            cmd.append("-" + "v" * self.verbosity)
        # Secrets go in a file, never argv: an extra var on the command line is
        # visible in ps on the controller for the whole run. The rendered config
        # paths travel in the same file: they are controller paths, harmless, and
        # it keeps the play's whole variable set in one place.
        handle = self._vars_file(secret_vars, rendered)
        if handle is not None:
            cmd += ["--extra-vars", f"@{handle}"]
        cmd += extra
        return cmd

    def _vars_file(self, values: dict[str, str],
                   rendered: dict[str, str] | None = None) -> Path | None:
        """Write the extra-vars file, mode 0600, removed when the run ends.

        It carries the secrets and, when any application declares one, the paths
        of the config files rendered on the controller. Nothing else.

        The file holds a FLAT mapping. It must not be wrapped in an `ansible`
        key: that namespace is a config-file convention and does not apply to
        `--extra-vars @file`. A wrapped file is accepted without complaint and
        silently defines nothing, so every secret arrives empty and the role
        fails with "all three secrets are required" while the controller holds
        perfectly good values. Measured both forms against ansible-playbook:
        wrapped gives an undefined variable, flat gives the value.
        """
        rendered = rendered or {}
        if not values and not rendered:
            return None
        import tempfile

        fd, name = tempfile.mkstemp(prefix="appctl-vars-", suffix=".json")
        with os.fdopen(fd, "w") as handle:
            payload = dict(values)
            if rendered:
                payload["appctl_rendered_configs"] = rendered
            json.dump(payload, handle)
        os.chmod(name, 0o600)
        self._cleanup_files = getattr(self, "_cleanup_files", [])
        self._cleanup_files.append(Path(name))
        return Path(name)

    def cleanup(self) -> None:
        for path in getattr(self, "_cleanup_files", []):
            path.unlink(missing_ok=True)
        self._cleanup_files = []

    def run(self, extra: list[str], *, label: str = "play",
            outdir: Path | None = None, secret_vars: dict[str, str] | None = None,
            rendered: dict[str, str] | None = None) -> Result:
        stamp = outdir or (self.config.root / "tmp" / "runs" /
                           datetime.now().strftime("%Y%m%d-%H%M%S"))
        directory = stamp / label
        directory.mkdir(parents=True, exist_ok=True)
        result = Result(label=label, directory=directory)

        cmd = self.command(extra, secret_vars or {}, rendered)
        _echo("run: " + " ".join(redact_argv(cmd)))
        _echo(f"--> {directory / 'commands.txt'}")

        started = time.monotonic()
        self._shared["last_output"][0] = started
        self._shared["last_task"][0] = "(starting)"

        log_path = directory / "log.txt"
        cmd_path = directory / "commands.txt"
        # Both opened together so neither leaks a descriptor if the run dies.
        with log_path.open("w", buffering=1) as log, cmd_path.open("w", buffering=1) as cmds:
            cmds.write("# commands for this run, one per line\n\n")
            log.write(f"# started {datetime.now().isoformat()}\n")
            log.write(f"# {' '.join(redact_argv(cmd))}\n")

            try:
                proc = subprocess.Popen(
                    cmd, cwd=self.config.root, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, bufsize=1,
                )
            except FileNotFoundError as exc:
                raise InstallError(
                    "ansible-playbook is not on PATH",
                    hint="install it with:  uv tool install ansible-core  "
                         "or:  pipx install ansible-core",
                ) from exc

            reader = threading.Thread(target=self._pump, args=(proc, log, cmds, result), daemon=True)
            reader.start()
            while reader.is_alive():
                reader.join(timeout=self.heartbeat if self.heartbeat else 1.0)
                if reader.is_alive() and self.heartbeat:
                    quiet = time.monotonic() - self._shared["last_output"][0]
                    _echo(f"    still on: {self._shared['last_task'][0]} "
                          f"({quiet:.0f}s without output, {time.monotonic() - started:.0f}s elapsed)")
            reader.join()
            proc.wait()
            result.returncode = proc.returncode

        result.seconds = time.monotonic() - started
        self._write_reports(result, cmd)
        return result

    def _pump(self, proc, log, cmds, result: Result) -> None:
        assert proc.stdout is not None
        shared = self._shared
        for raw in proc.stdout:
            line = raw.rstrip("\n")

            # Ansible echoes extra vars back at -vvv and above. A secret passed
            # with -e returns here in clear text, so the line is rewritten before
            # it reaches the file.
            ev = EXTRA_VARS_RE.match(line)
            if ev:
                # Split on the quotes that delimit each tuple element, not on
                # commas: a value can itself contain one.
                parts = re.findall(r"'([^']*)'|\"([^\"]*)\"", ev.group(2))
                values = [a or b for a, b in parts] or [ev.group(2).strip()]
                red = ", ".join(redact_argv([v])[0] for v in values)
                line = f"{ev.group(1)}extra_vars: ({red},)"

            log.write(line + "\n")
            shared["last_output"][0] = time.monotonic()

            m = TASK_RE.match(line)
            if m:
                shared["last_task"][0] = m.group(1)
                result.tasks_seen += 1
                _echo(f"TASK {m.group(1)}")
                continue

            r = RESULT_RE.match(line)
            if r:
                kind = r.group(1)
                if kind == "changed":
                    result.tasks_changed += 1
                elif kind in ("failed", "fatal", "unreachable"):
                    result.tasks_failed += 1
                    result.problems.append(f"{shared['last_task'][0]}: {kind}")
                    _echo(f"    !! {kind} :: {shared['last_task'][0]}")
                continue

            if REMOTE_EXEC_RE.match(line) and self.verbosity >= 4:
                remote = REMOTE_EXEC_RE.match(line).group(1)
                if remote:
                    cmds.write(f"{shared['last_task'][0]}\n    {remote.strip()}\n\n")
                    result.commands_seen += 1
                    continue

            if self.verbosity >= 3:
                if self._list_open is None and LIST_OPENER_RE.match(line):
                    am = ARG_RE.match(line.strip().rstrip(":"))
                    self._list_open = am.group(1) if am else "cmd"
                    continue
                if self._list_open is not None:
                    if re.match(r"^\s*\],?\s*$", line):
                        joined = " ".join(self._list_items)
                        _echo(f"    {self._list_open}: {self._list_items}")
                        cmds.write(f"{shared['last_task'][0]}\n    {joined}\n\n")
                        result.commands_seen += 1
                        self._list_open, self._list_items = None, []
                        continue
                    item = line.strip().rstrip(",").strip('"')
                    if item:
                        self._list_items.append(item)
                if NULL_ARG_RE.match(line):
                    continue
                am = ARG_RE.match(line)
                if am and len(am.group(2)) <= ARG_MAX_LEN:
                    key, value = am.group(1), am.group(2)
                    result.commands_seen += 1
                    shown = "***REDACTED***" if SECRETISH_RE.search(key) or SECRETISH_RE.search(value[:80]) else value
                    _echo(f"    {key}: {shown}")
                    cmds.write(f"{shared['last_task'][0]}\n    {key}: {shown}\n\n")
                    continue
            if self.verbosity >= 4 and EXEC_RE.match(line):
                _echo(f"    {line.strip()}")

            if FATAL_RE.match(line) or ERROR_RE.match(line):
                result.tasks_failed += 1
                result.problems.append(f"{shared['last_task'][0]}: {line}")
                _echo(f"    !! {line}")

    def _write_reports(self, result: Result, cmd: list[str]) -> None:
        text = (result.directory / "log.txt").read_text(errors="replace")
        recap = [l for l in text.splitlines()
                 if l.startswith("PLAY RECAP") or re.match(r"^\S+\s+: ok=", l)]
        (result.directory / "recap.txt").write_text("\n".join(recap) + "\n")

        problems, task = [], "(unknown)"
        for line in text.splitlines():
            m = TASK_RE.match(line)
            if m:
                task = m.group(1)
            elif FATAL_RE.match(line) or ERROR_RE.match(line):
                problems.append(f"{task}\n  {line.strip()}")
        if result.returncode != 0 and not problems:
            problems.append(f"the play exited {result.returncode} but no error line was "
                            f"recognised. Read {result.directory / 'log.txt'} for the raw output.")
        (result.directory / "errors.txt").write_text("\n".join(problems) + "\n" if problems else "")

        (result.directory / "meta.txt").write_text(json.dumps({
            "label": result.label,
            "exit_code": result.returncode,
            "seconds": round(result.seconds, 1),
            "tasks_seen": result.tasks_seen,
            "commands_seen": result.commands_seen,
            "tasks_changed": result.tasks_changed,
            "tasks_failed": result.tasks_failed,
            "finished_at": datetime.now().isoformat(),
            "command": redact_argv(cmd),
        }, indent=2) + "\n")
