"""Tests for the roles' own postconditions.

These read the role files as text and assert on what the role claims. That is
a blunt instrument, and it is here because the failure it guards against was
a green play over a broken service: the role installed a release, the play
reported `changed`, every assertion the role makes passed, and the server was
still the one that had been running for nine days.

A play can only report what its tasks report. If a task never asks for a
restart, nothing downstream notices that the process on the guest is not the
one that was just installed.

So these assert the *condition* the restart is gated on, rather than trying
to run a play: the signal that the release was reinstalled has to be one of
the things that makes the unit restart.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def restart_state_expression(role_tasks: str) -> str:
    """The `state:` expression of the role's systemd task."""
    match = re.search(r"state: >-\n(.*?)\n\s+daemon_reload:", role_tasks, re.S)
    assert match, "no systemd task with a templated state was found"
    return match.group(1)


def test_release_role_restarts_when_the_release_was_reinstalled():
    """A version bump must restart the unit, not just install the release.

    The bug this pins: the release install replaced the whole tree under
    omr_prefix while the process kept serving the old one. The dashboard
    returned a page whose every static asset was a 404, because the HTML came
    off the new disk while the asset manifest in the old process's memory
    named files the new release did not have.
    """
    tasks = read("apps/omniroute-release/tasks/main.yml")

    assert "omr_npm_install" in restart_state_expression(tasks), (
        "the release role does not restart the unit when the release was "
        "reinstalled, so a version bump leaves the old process serving"
    )


def test_release_role_install_task_reports_its_change():
    """The install task must report that it changed something.

    The restart above is gated on omr_npm_install.changed. A command reports
    changed by default, but a default nobody wrote down is a default that
    gets refactored away — and this one is load-bearing.
    """
    tasks = read("apps/omniroute-release/tasks/main.yml")

    install = re.search(
        r"- name: Install the pinned OmniRoute release.*?register: omr_npm_install"
        r"(.*?)(?=\n- name:)",
        tasks,
        re.S,
    )
    assert install, "the pinned release install task was not found"
    assert "changed_when: true" in install.group(1), (
        "the release install does not report changed, so the restart that "
        "depends on it cannot fire"
    )


def test_release_role_restart_is_still_gated_for_a_settled_play():
    """The steady state must stay at changed=0.

    A restart on every play would bounce the service every run and report
    changed forever — the other half of the same trap. The gate has to be a
    list of specific things that changed, not "always restart".
    """
    tasks = read("apps/omniroute-release/tasks/main.yml")
    expression = restart_state_expression(tasks)

    for signal in ("omr_secrets_written.changed", "omr_env_written.changed",
                   "omr_unit.changed"):
        assert signal in expression, f"the restart lost its {signal} condition"

    assert "else 'started'" in expression, (
        "the restart is unconditional, so a settled play reports changed"
    )


def test_source_built_role_restarts_when_the_build_is_stale():
    """The source-built role must restart on a stale build.

    It already does, through om_build_freshness. This is here because the
    two OmniRoute roles are copies of one another in every other respect, and
    a fix to one is a fix that has to be made to the other.
    """
    tasks = read("apps/omniroute/tasks/main.yml")

    assert "om_build_freshness" in restart_state_expression(tasks), (
        "the source-built role does not restart the unit on a stale build"
    )
