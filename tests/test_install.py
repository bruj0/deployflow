"""Tests for the run installer.

The installer is what turns an Ansible play into two things the operator can
use: a summary line, and the evidence that the application's own checks
passed. The second is the one that matters — "41 tasks, 0 failed" is a count,
and a count does not say the service works.

These tests cover the two pieces of that translation: pulling the check
report out of the play's output, and carrying the manifest's own version pin
into the play so a role can refuse to install something else.
"""

from __future__ import annotations

import io
import json
import textwrap
from pathlib import Path

from appctl.config import load
from appctl.install import Installer, source_vars

MANIFEST = """
    version: 1
    applications:
      myapp:
        description: sample
        target:
          kind: proxmox_lxc
          options:
            hostname: myapp
        source:
          kind: package
          package: "myapp==1.2.3"
"""


def write_manifest(tmp_path: Path, body: str = MANIFEST) -> Path:
    path = tmp_path / "app.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_source_vars_splits_the_pin(tmp_path):
    """The manifest's pin reaches the role as a name and a version."""
    cfg = load(write_manifest(tmp_path))
    assert source_vars(cfg.app("myapp")) == {
        "appctl_source_package": "myapp",
        "appctl_source_version": "1.2.3",
    }


def test_source_vars_ignores_extras(tmp_path):
    """An extras marker is part of the requirement, not part of the name."""
    body = MANIFEST.replace('"myapp==1.2.3"', '"myapp[browser]==1.2.3"')
    cfg = load(write_manifest(tmp_path, body))
    vars = source_vars(cfg.app("myapp"))
    assert vars["appctl_source_package"] == "myapp"
    assert vars["appctl_source_version"] == "1.2.3"


def test_source_vars_is_empty_without_a_version(tmp_path):
    """A package with no version pin cannot be compared, so nothing is sent.

    Sending the name alone would make a role compare a name against itself
    and always agree, which is worse than sending nothing.
    """
    body = MANIFEST.replace('"myapp==1.2.3"', '"myapp"')
    cfg = load(write_manifest(tmp_path, body))
    assert source_vars(cfg.app("myapp")) == {}


def test_source_vars_is_empty_for_a_repo_source(tmp_path):
    """A repository source has a ref, not a version to compare."""
    body = """
        version: 1
        applications:
          myapp:
            target:
              kind: proxmox_lxc
              options:
                hostname: myapp
            source:
              kind: repo
              repo: /path/to/checkout
              ref: main
            build:
              python_version: "3.14"
    """
    cfg = load(write_manifest(tmp_path, body))
    assert source_vars(cfg.app("myapp")) == {}


def test_the_vars_file_carries_the_source_pin(tmp_path):
    """The pin travels in the extra-vars file, next to the secrets.

    Not in argv: the file is mode 0600 and removed at exit, and that is the
    only place the play's whole variable set lives.
    """
    cfg = load(write_manifest(tmp_path))
    installer = Installer(cfg)
    handle = installer._vars_file({}, {}, source_vars(cfg.app("myapp")))
    try:
        payload = json.loads(Path(handle).read_text(encoding="utf-8"))
    finally:
        installer.cleanup()
    assert payload["appctl_source_package"] == "myapp"
    assert payload["appctl_source_version"] == "1.2.3"


def test_the_vars_file_is_omitted_when_there_is_nothing_to_send(tmp_path):
    """No secrets, no rendered configs and no pin means no file at all."""
    cfg = load(write_manifest(tmp_path))
    installer = Installer(cfg)
    assert installer._vars_file({}, {}, {}) is None


CHECK_PLAY = """\
TASK [example-app : Report the checks that passed] *****************************
task path: /app/apps/example-app/tasks/main.yml:194
ok: [example-app] => {
    "msg": [
        "example-app is installed and answering on port 8080",
        "import example_app: ok",
        "endpoint /: HTTP 200"
    ]
}

TASK [Hand the installed venv to the service account] **************************
"""


def _result():
    """A stand-in for the installer's Result, with the fields the pump writes."""
    return type("R", (), {"tasks_seen": 0, "tasks_changed": 0, "tasks_failed": 0,
                          "commands_seen": 0, "problems": [], "checks": []})()


def _pump(installer, play):
    """Feed a play's output through the pump and return what it collected."""

    class FakeProc:
        def __init__(self, lines):
            self.stdout = iter(lines)

    result = _result()
    log = io.StringIO()
    commands = io.StringIO()
    installer._pump(FakeProc(play.splitlines()), log, commands, result)
    return result


def test_the_check_report_is_read_out_of_the_play(tmp_path):
    """The application's own report becomes the summary the operator reads.

    The lines are collected from the play's output, so they are the
    application's statement about itself rather than a count of tasks.
    """
    cfg = load(write_manifest(tmp_path))
    result = _pump(Installer(cfg), CHECK_PLAY)
    assert result.checks == [
        "example-app is installed and answering on port 8080",
        "import example_app: ok",
        "endpoint /: HTTP 200",
    ]


def test_a_play_without_a_check_report_has_no_checks(tmp_path):
    """A role that reports no checks produces no evidence, and says nothing."""
    cfg = load(write_manifest(tmp_path))
    play = "TASK [example-app : Install the application] *********************\n"
    result = _pump(Installer(cfg), play)
    assert result.checks == []
