"""Tests for the command line.

The commands are the operator's interface, so these tests check the messages
an operator reads when something is wrong: a target that does not exist, a
target that is not built, and a secret that is missing.
"""

from __future__ import annotations

import textwrap

from appctl.cli import main


def write(tmp_path, body: str):
    path = tmp_path / "app.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


BASE = """
    version: 1
    applications:
      example-app:
        description: sample
        target:
          kind: proxmox_lxc
          options:
            hostname: example-app
        source:
          kind: package
          package: "example-app==1.0.0"
"""


def run(capsys, *argv):
    """Run the CLI and return its exit code and everything it printed.

    Errors go to stderr, so both streams are captured: a message the operator
    reads is a message wherever it was written.
    """
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_targets_lists_the_implemented_one(capsys):
    code, out = run(capsys, "targets")
    assert code == 0
    assert "proxmox_lxc" in out
    assert "not implemented" in out


def test_list_shows_the_target(capsys, tmp_path):
    path = write(tmp_path, BASE)
    code, out = run(capsys, "-C", str(path), "list")
    assert code == 0
    assert "proxmox_lxc" in out
    assert "example-app" in out


def test_install_refuses_a_target_that_is_not_implemented(capsys, tmp_path):
    body = BASE.replace("kind: proxmox_lxc", "kind: vm")
    path = write(tmp_path, body)
    code, out = run(capsys, "-C", str(path), "install", "example-app")
    assert code == 1
    assert "not implemented" in out
    assert "proxmox_lxc" in out


def test_install_refuses_an_unknown_target(capsys, tmp_path):
    body = BASE.replace("kind: proxmox_lxc", "kind: mainframe")
    path = write(tmp_path, body)
    code, out = run(capsys, "-C", str(path), "install", "example-app")
    assert code == 1
    assert "unknown target kind" in out


def test_a_missing_config_is_reported(capsys, tmp_path):
    code, out = run(capsys, "-C", str(tmp_path / "absent.yaml"), "list")
    assert code == 1
    assert "no config at" in out


def test_init_writes_a_config_that_loads(capsys, tmp_path):
    path = tmp_path / "app.yaml"
    code, out = run(capsys, "-C", str(path), "init")
    assert code == 0
    assert path.is_file()
    # The template must survive its own validator.
    code, out = run(capsys, "-C", str(path), "list")
    assert code == 0
