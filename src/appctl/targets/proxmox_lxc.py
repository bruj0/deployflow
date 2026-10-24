"""The `proxmox_lxc` target: an application in a Proxmox LXC guest.

This is the first and only implemented target. It creates the guest from a
stock template when it does not exist, discovers its DHCP lease, and then runs
Ansible against it to install the application and check that the service works.

Everything Proxmox-specific lives here: `pct`, the container ID, the template
name, the storage, the bridge and the guest network. The shared code above
this module knows none of them.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .base import Target, TargetContext, TargetError, TargetResult
from .registry import register

# Keys this target reads from `target.options`.
_REQUIRED_OPTIONS = ("hostname",)


@register
class ProxmoxLxcTarget(Target):
    """Install an application into a Proxmox LXC guest with Ansible."""

    kind = "proxmox_lxc"
    summary = "an application installed into a Proxmox LXC guest with Ansible"
    capabilities = (
        "create a guest from a stock template",
        "discover the guest address over DHCP",
        "build and install an application from a pinned artifact",
        "run application-level checks after install",
    )

    # -- configuration ----------------------------------------------------

    def validate(self, context: TargetContext) -> None:
        """Check the target's settings before anything is built or started.

        Every failure here is cheaper than a failed play: no artifact has been
        produced, no container has been created, and the message can name the
        missing value.
        """
        app = context.app()
        options = dict(app.target.options)

        missing = [key for key in _REQUIRED_OPTIONS if not options.get(key)]
        if missing:
            raise TargetError(
                f"target {self.kind!r} for {app.name!r} is missing: "
                f"{', '.join(missing)}",
                hint=f"add it under applications.{app.name}.target.options, "
                     f"for example: hostname: {app.name}",
            )

        if not (context.root / "playbooks" / "site.yml").is_file():
            raise TargetError(
                f"no playbook at {context.root / 'playbooks' / 'site.yml'}",
                hint="the target needs the Ansible play; run from the repository root",
            )

        if shutil.which("ansible-playbook") is None:
            raise TargetError(
                "ansible-playbook is not on PATH",
                hint="install it with:  uv tool install ansible-core",
            )

        # The application has to be wired to a guest for the play to install it.
        # This belongs to the target, not to the shared CLI: whether an
        # application is "deployed" depends on where it runs.
        from ..config import deployed_apps

        deployed = deployed_apps(context.config)
        if app.name not in deployed:
            raise TargetError(
                f"{app.name} is configured but is not on any guest",
                hint="add it to app_roles in inventory/group_vars/<group>.yml, "
                     f"or run `appctl list` to see what is deployed",
            )

    # -- execution --------------------------------------------------------

    def provision(self, context: TargetContext) -> dict[str, str]:
        """Identify the guest this application runs in.

        The guest is created by the first play, not here. Creating it needs
        `pct` on the Proxmox host, and the play already does that, guarded by a
        stat so a second run is a no-op. This method reports the destination so
        the run can name it before it starts.
        """
        app = context.app()
        options = dict(app.target.options)
        facts = {"hostname": str(options.get("hostname", app.name))}
        vmid = options.get("vmid")
        if vmid:
            facts["vmid"] = str(vmid)
        return facts

    def deploy(self, context: TargetContext, artifacts: dict[str, Path],
               rendered: dict[str, str]) -> TargetResult:
        """Install the application into the guest and check that it works."""
        from .. import install as install_mod

        app_name = context.app_name
        app = context.app()
        options = dict(app.target.options)

        installer = install_mod.Installer(
            context.config,
            verbosity=context.verbosity,
            forks=context.forks,
            heartbeat=context.heartbeat,
        )
        try:
            result = installer.run(
                list(context.extra_args) + ["--tags", ",".join(["lxc", app_name])],
                # The context calls them secret_values; the installer calls
                # them secret_vars. They are the same mapping, and the rename is
                # at this boundary so neither name leaks into the other.
                secret_vars=dict(context.secret_values or {}),
                rendered=rendered,
                label=app_name,
            )
        finally:
            installer.cleanup()

        target_result = TargetResult(
            target=self.kind,
            guest=str(options.get("hostname", app.name)),
            address=str(getattr(result, "address", "") or ""),
            changed=result.tasks_changed,
            failed=result.tasks_failed,
            tasks=result.tasks_seen,
            seconds=result.seconds,
            checks=list(getattr(result, "checks", []) or []),
            logs=result.directory,
        )
        if result.returncode != 0 and not target_result.failed:
            target_result.failed = 1
        return target_result


def lxc_options(hostname: str, vmid: int | None = None,
                bind_host: str = "127.0.0.1") -> dict[str, Any]:
    """Build the `target.options` block for this target.

    A helper so a manifest generator and a test do not have to spell the shape
    twice.
    """
    options: dict[str, Any] = {"hostname": hostname, "bind_host": bind_host}
    if vmid is not None:
        options["vmid"] = vmid
    return options


__all__ = ["ProxmoxLxcTarget", "lxc_options"]
