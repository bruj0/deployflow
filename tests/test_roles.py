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

def test_container_role_restarts_when_the_image_moved():
    """A digest that moved must restart the container, not just pull the image.

    The symptom of not restarting: the container keeps running the build it
    was started with, while `docker image inspect` reports the new one. The
    application then reports the new version and serves the old code, and
    nothing says so.
    """
    tasks = read("apps/aurral/tasks/main.yml")
    expression = restart_state_expression(tasks)

    assert "aurral_image_pull.changed" in expression, (
        "the role does not restart the container when the pinned digest moved"
    )


def test_container_role_restart_is_still_gated_for_a_settled_play():
    """The steady state must stay at changed=0.

    A restart on every play would bounce the container every run and report
    changed forever. The gate has to name the specific things that changed.
    """
    tasks = read("apps/aurral/tasks/main.yml")
    expression = restart_state_expression(tasks)

    for signal in ("aurral_env_written.changed", "aurral_compose_written.changed",
                   "aurral_unit.changed"):
        assert signal in expression, f"the restart lost its {signal} condition"

    assert "else 'started'" in expression, (
        "the restart is unconditional, so a settled play reports changed"
    )


def test_container_role_does_not_regenerate_the_admin_key():
    """The admin API key must be kept, not regenerated.

    A regenerated key does not fail the install. Aurral stores its own API key
    in the settings table when the environment variable is absent, so the
    server keeps serving and every script that authenticates with the old key
    stops working — with no message from the install that anything changed.
    """
    tasks = read("apps/aurral/tasks/main.yml")

    assert "aurral_existing_env.AURRAL_API_KEY" in tasks, (
        "the role does not reuse the admin API key it finds on the guest"
    )
    assert "aurral_existing_env" in tasks, (
        "the role does not read the existing environment file before writing it"
    )


def test_tunnel_token_never_reaches_argv():
    """The tunnel token must go through an EnvironmentFile, not argv.

    A token on the command line is visible in ps to every process on the guest
    for the life of the connector. The unit template is what decides this, so
    the assertion is on the unit and not on the task that writes it.
    """
    unit = read("apps/aurral/templates/aurral-tunnel.service.j2")

    assert "EnvironmentFile=" in unit, (
        "the tunnel unit does not read the token from an EnvironmentFile"
    )
    assert "TUNNEL_TOKEN" not in unit, (
        "the tunnel unit carries the token inline instead of in an EnvironmentFile"
    )


def test_tunnel_refuses_to_start_without_credentials():
    """An enabled tunnel with no token must fail at a task boundary.

    The alternative is a connector that starts, fails to authenticate and
    retries forever while the hostname stays dead — a green play over a
    broken service.
    """
    tasks = read("apps/aurral/tasks/main.yml")

    assert "aurral_tunnel_token" in tasks, (
        "the role does not check that the tunnel token is present"
    )
    assert "aurral_tunnel_enabled | bool" in tasks, (
        "the tunnel credential check is not gated on the tunnel being enabled"
    )