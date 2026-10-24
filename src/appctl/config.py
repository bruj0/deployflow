"""Load and validate app.yaml.

The config is the only thing this tool reads to know what to do. It is
deliberately declarative: an application, where it comes from, what has to be
built, and which secrets it needs. Nothing here special-cases an application by
name, so adding one is a configuration change.

Validation is strict on purpose. A key that is misspelled is a silent no-op
otherwise, and a silent no-op in a deploy tool is how the wrong thing gets
installed. Unknown keys are an error, and the error names the key and where it
was found.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigError

# The file the tool looks for, relative to the repository root.
CONFIG_NAME = "app.yaml"

# Keys accepted at each level. Anything else is a typo and is rejected.
# Kept as explicit sets rather than inferred from the dataclass fields: a
# dataclass silently ignores an attribute it was not given, which is exactly the
# failure mode this module exists to prevent.
_TOP_LEVEL = {"version", "defaults", "applications", "build"}
_DEFAULTS = {"python_version", "template", "storage", "rootfs_gb", "cores", "memory_mb", "swap_mb", "features", "onboot"}
# `state` is written by the tool, never by an operator. It is in the schema so
# that a record survives a round trip; see src/lxc/state.py.
_APP = {"name", "description", "target", "source", "build", "secrets", "config", "state"}
_CONFIG = {"files", "target", "mode", "vars"}
_SOURCE = {"kind", "package", "repo", "ref", "python", "webui", "output"}
_BUILD = {"python_version", "lock_requirement", "ref", "fetch", "verify_lock"}
_TARGET = {"kind", "options"}
_WEBUI = {"path", "out", "package_manager", "build_command", "install_command"}

# Where a secret is required: any one of these conditions puts it in that app's
# required set. Kept as data so the message can say which condition failed
# instead of only "a secret is missing".
_WILDCARD_BIND = "bind_host is a wildcard address"


@dataclass(frozen=True)
class WebUI:
    """A frontend bundle built before the wheel, if the application has one."""

    path: str
    out: str
    package_manager: str = "auto"          # auto | bun | npm
    build_command: str = "build"
    install_command: str | None = None      # defaults per package manager


@dataclass(frozen=True)
class Source:
    """Where an application's code comes from.

    Three kinds exist because three genuinely different things are being
    described:

      package   a version on PyPI. Reproducible to the wheel.
      repo      a commit in a git checkout. Reproducible to the commit, and the
                wheel has to be built here because PyPI lags the fork.
      toolchain nothing is installed from anywhere. The application's own role
                assembles the environment another application needs — a build
                guest, a toolchain server. It has no artifact, so there is
                nothing to build and nothing to fetch.

    A `repo` source requires `build`. The other two do not: there is nothing to
    build.
    """

    kind: str = "package"
    package: str | None = None              # e.g. "someapp[browser]==1.2.3"
    repo: str | None = None                 # path to a git checkout
    ref: str = "HEAD"
    python: str | None = None               # e.g. "3.14"
    webui: WebUI | None = None
    output: str = "dist"                    # relative to files/, not committed

    @property
    def label(self) -> str:
        """A one-line description of where this application comes from.

        Used by `appctl list` and by the build command's messages. The point of
        it is that each kind gets a sentence that is true of it, instead of one
        generic phrase that is true of none: a toolchain has no package and no
        repository, and printing `None` for either would look like a bug.
        """
        if self.kind == "package":
            return self.package or ""
        if self.kind == "repo":
            return f"repo@{self.ref}"
        return self.kind


@dataclass(frozen=True)
class Build:
    """How to turn a repo source into installable artifacts."""

    python_version: str | None = None
    lock_requirement: str | None = None     # defaults to the repo's pyproject
    ref: str | None = None                  # overrides Source.ref
    fetch: bool = False
    verify_lock: bool = True


@dataclass(frozen=True)
class RenderedConfig:
    """A configuration file rendered from YAML and placed on the guest.

    This exists so an application never needs a line of its own code to be
    configured. The YAML is rendered on the controller, written to
    tmp/rendered/<app>/<name>.json, and copied to `target` by common/python.

    The application is expected to fill in its own defaults for anything absent
    — example-app does exactly that in config/loader.py:merge_missing_defaults,
    which "recursively add[s] missing defaults without replacing configured
    values". So a minimal rendered file is the normal case, not a degraded one.

    Substitutions in the YAML use {{ name }} and are resolved from `vars` plus a
    few built-ins. `${NAME}` is left alone on purpose: that is the convention
    applications use to reference a secret at runtime, from an EnvironmentFile,
    and rewriting it here would put the secret in a file in this repository.
    """

    target: str
    files: tuple[str, ...]
    mode: str = "0640"
    # Values keep their YAML type. A whole-value placeholder yields that type, so
    # `"port": "{{ port }}"` with port: 8900 renders the number 8900 and not the
    # string "8900", which some applications reject. A partial placeholder is
    # always a string, because string concatenation is what it means.
    vars: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class TargetRef:
    """Which delivery target runs this application, and its own settings.

    ``kind`` names a registered target. ``options`` holds whatever that target
    needs — a Proxmox host and CTID for one, a machine image for another. The
    shared schema does not read inside ``options``: each target validates its
    own settings, so a new target never requires changing this module.
    """

    kind: str
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def bind_host(self) -> str:
        """Where the application listens, if the target says so.

        Some targets expose a network address and some do not. An application
        that does not declare one gets the conservative default.
        """
        return str(self.options.get("bind_host", "127.0.0.1"))


@dataclass(frozen=True)
class Application:
    """One application: what it is, where it comes from, what it needs."""

    name: str
    description: str = ""
    target: TargetRef = field(default_factory=lambda: TargetRef(kind=""))
    source: Source = field(default_factory=Source)
    build: Build | None = None
    config: RenderedConfig | None = None
    # Last build and install, written by appctl. Read-only for an operator.
    state: dict[str, object] = field(default_factory=dict)
    # Required secrets. A target may add to this list.
    secrets: tuple[str, ...] = ()

    @property
    def needs_build(self) -> bool:
        """True when this application has artifacts to produce before install.

        A repo source always does. A package source does not: the wheel already
        exists on PyPI. A toolchain source does not either: the guest assembles
        the toolchain itself, so there is no artifact to produce on the
        controller.
        """
        return self.source.kind == "repo"

    @property
    def required_secrets(self) -> tuple[str, ...]:
        """Secrets that must be present before this app can be installed.

        The list comes from the manifest. A target may require more; it says so
        during validation, where the failure can name the target's own reason.
        """
        return self.secrets


@dataclass(frozen=True)
class Defaults:
    """Guest shape inherited by any application that does not override it."""

    python_version: str = "3.14"
    template: str = "ubuntu-26.04-standard_26.04-1_amd64.tar.zst"
    storage: str = "data1"
    rootfs_gb: int = 16
    cores: int = 2
    memory_mb: int = 2048
    swap_mb: int = 512
    features: str = "nesting=1,fuse=1,keyctl=1"
    onboot: int = 0


@dataclass(frozen=True)
class Config:
    """The whole of app.yaml, validated."""

    version: int
    defaults: Defaults
    applications: dict[str, Application]
    path: Path
    # Raw mapping, for keys this version does not model yet. Reading a config
    # written for a newer tool should not silently lose data.
    raw: dict[str, Any] = field(default_factory=dict)

    def app(self, name: str) -> Application:
        """Look up one application, with a message naming what is available."""
        if name not in self.applications:
            known = ", ".join(sorted(self.applications)) or "none"
            raise ConfigError(
                f"no application named {name!r} in {self.path}",
                hint=f"applications in this config: {known}",
            )
        return self.applications[name]

    @property
    def root(self) -> Path:
        """The repository root: the directory holding app.yaml."""
        return self.path.parent


def find_config(start: Path | None = None) -> Path:
    """Locate app.yaml, walking upward from `start`.

    Walking up means the tool works from a subdirectory and from anywhere else
    that sits inside the repository, which is the common case when an agent is
    working in apps/example-app/.
    """
    here = (start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        config = candidate / CONFIG_NAME
        if config.is_file():
            return config
    raise ConfigError(
        f"no {CONFIG_NAME} found in {here} or any parent directory",
        hint=f"run `appctl init` to create one, or pass --config /path/to/{CONFIG_NAME}",
    )


def _require_mapping(value: Any, where: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{where} must be a mapping, got {type(value).__name__}")
    return value


def _reject_unknown(data: dict[str, Any], allowed: set[str], where: str) -> None:
    """Fail on any key the schema does not define.

    This is the strictness the whole module exists for. A misspelled key would
    otherwise be dropped and the operator would find out from a service that
    came up with the wrong port.
    """
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ConfigError(
            f"unknown key{'s' if len(unknown) > 1 else ''} in {where}: "
            f"{', '.join(unknown)}",
            hint=f"accepted keys: {', '.join(sorted(allowed))}",
        )


def _webui(data: Any, where: str) -> WebUI | None:
    if data is None:
        return None
    block = _require_mapping(data, where)
    _reject_unknown(block, _WEBUI, where)
    if "path" not in block:
        raise ConfigError(f"{where}.path is required when webui is present")
    return WebUI(
        path=block["path"],
        out=block.get("out", ""),
        package_manager=block.get("package_manager", "auto"),
        build_command=block.get("build_command", "build"),
        install_command=block.get("install_command"),
    )


def _source(data: Any, where: str) -> Source:
    block = _require_mapping(data, where)
    _reject_unknown(block, _SOURCE, where)
    kind = block.get("kind", "package")
    if kind not in ("package", "repo", "toolchain"):
        raise ConfigError(
            f"{where}.kind must be 'package', 'repo' or 'toolchain', got {kind!r}",
            hint="use kind: package for something published on PyPI, "
                 "kind: repo to build from a checkout, "
                 "kind: toolchain for a guest that exists to host a toolchain",
        )
    if kind == "package" and not block.get("package"):
        raise ConfigError(f"{where}.package is required when kind is 'package'")
    if kind == "repo" and not block.get("repo"):
        raise ConfigError(f"{where}.repo is required when kind is 'repo'")
    return Source(
        kind=kind,
        package=block.get("package"),
        repo=block.get("repo"),
        ref=block.get("ref", "HEAD"),
        python=block.get("python"),
        webui=_webui(block.get("webui"), f"{where}.webui"),
        output=block.get("output", "dist"),
    )


def _build(data: Any, where: str) -> Build | None:
    if data is None:
        return None
    block = _require_mapping(data, where)
    _reject_unknown(block, _BUILD, where)
    return Build(
        python_version=block.get("python_version"),
        lock_requirement=block.get("lock_requirement"),
        ref=block.get("ref"),
        fetch=bool(block.get("fetch", False)),
        verify_lock=bool(block.get("verify_lock", True)),
    )


def _guest(data: Any, name: str) -> Guest:
    where = f"applications.{name}.guest"
    block = _require_mapping(data, where)
    _reject_unknown(block, _GUEST, where)
    if "hostname" not in block:
        raise ConfigError(f"{where}.hostname is required")
    vmid = block.get("vmid")
    return Guest(
        hostname=block["hostname"],
        vmid=int(vmid) if vmid is not None else None,
        ip_optional=bool(block.get("ip_optional", False)),
        bind_host=block.get("bind_host", "127.0.0.1"),
    )


# Sections the tool writes. Anything else under `state:` is a hand-edit that the
# next build would silently drop, so it is rejected rather than ignored.
_STATE_SECTIONS = {"built", "installed"}


def _state(data: Any, where: str) -> dict[str, Any]:
    """Validate the tool-written state block without reinterpreting it.

    It is deliberately not modelled as a dataclass: the writer emits it as text
    to preserve comments, so the only thing that matters here is that the shape
    is one the writer can produce again.
    """
    if data is None:
        return {}
    block = _require_mapping(data, where)
    unknown = sorted(set(block) - _STATE_SECTIONS)
    if unknown:
        raise ConfigError(
            f"unknown key{'s' if len(unknown) > 1 else ''} in {where}: {', '.join(unknown)}",
            hint=f"appctl writes this block; accepted sections are "
                 f"{', '.join(sorted(_STATE_SECTIONS))}",
        )
    return block


def _rendered(data: Any, where: str) -> RenderedConfig | None:
    if data is None:
        return None
    block = _require_mapping(data, where)
    _reject_unknown(block, _CONFIG, where)
    for required in ("target", "files"):
        if required not in block:
            raise ConfigError(f"{where}.{required} is required when config is present")
    files = block["files"]
    if isinstance(files, str):
        files = [files]
    if not files:
        raise ConfigError(f"{where}.files must name at least one YAML file")
    raw_vars = _require_mapping(block.get("vars"), f"{where}.vars")
    return RenderedConfig(
        target=block["target"],
        files=tuple(files),
        mode=str(block.get("mode", "0640")),
        vars=dict(raw_vars),
    )


def _target(data: Any, name: str) -> TargetRef:
    """Read the target block.

    Only ``kind`` is checked here. Everything else belongs to the target that
    will run it, and a shared schema that read inside another target's options
    would have to change every time a target gained a setting.
    """
    where = f"applications.{name}.target"
    block = _require_mapping(data, where)
    _reject_unknown(block, _TARGET, where)
    kind = block.get("kind")
    if not kind:
        raise ConfigError(
            f"{where}.kind is required",
            hint="name the target that runs this application, for example: kind: proxmox_lxc",
        )
    options = _require_mapping(block.get("options"), f"{where}.options")
    return TargetRef(kind=str(kind), options=dict(options))


def _application(name: str, data: Any) -> Application:
    where = f"applications.{name}"
    block = _require_mapping(data, where)
    _reject_unknown(block, _APP, where)
    secrets = block.get("secrets") or ()
    if isinstance(secrets, str):
        secrets = (secrets,)
    source = _source(block.get("source"), f"{where}.source")
    build = _build(block.get("build"), f"{where}.build")
    if source.kind == "repo" and build is None:
        raise ConfigError(
            f"{where} has source.kind: repo but no build block",
            hint="a repo source must say how to build; add a build: block, "
                 "or set source.kind to package if it is published on PyPI",
        )
    return Application(
        name=name,
        description=block.get("description", ""),
        target=_target(block.get("target"), name),
        source=source,
        build=build,
        config=_rendered(block.get("config"), f"{where}.config"),
        state=_state(block.get("state"), f"{where}.state"),
        secrets=tuple(secrets),
    )


def _defaults(data: Any) -> Defaults:
    block = _require_mapping(data, "defaults")
    _reject_unknown(block, _DEFAULTS, "defaults")
    return Defaults(
        python_version=str(block.get("python_version", "3.14")),
        template=block.get("template", "ubuntu-26.04-standard_26.04-1_amd64.tar.zst"),
        storage=block.get("storage", "data1"),
        rootfs_gb=int(block.get("rootfs_gb", 16)),
        cores=int(block.get("cores", 2)),
        memory_mb=int(block.get("memory_mb", 2048)),
        swap_mb=int(block.get("swap_mb", 512)),
        features=block.get("features", "nesting=1,fuse=1,keyctl=1"),
        onboot=int(block.get("onboot", 0)),
    )


def load(path: Path | None = None) -> Config:
    """Read and validate app.yaml.

    Args:
        path: the config file, or None to search upward from the working
              directory.

    Raises:
        ConfigError: if the file is missing, is not valid YAML, or does not
            match the schema. The message always names the offending key.
    """
    config_path = path or find_config()
    if not config_path.is_file():
        raise ConfigError(f"no config at {config_path}")

    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {config_path}: {exc}") from exc

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        # YAML errors carry a line number; keep it, it is the whole value of the
        # message.
        raise ConfigError(f"{config_path} is not valid YAML: {exc}") from exc

    root = _require_mapping(data, str(config_path))
    _reject_unknown(root, _TOP_LEVEL, str(config_path))

    version = root.get("version", 1)
    if not isinstance(version, int):
        raise ConfigError(f"version must be an integer, got {type(version).__name__}")

    raw_apps = _require_mapping(root.get("applications"), "applications")
    applications = {name: _application(name, body) for name, body in raw_apps.items()}

    return Config(
        version=version,
        defaults=_defaults(root.get("defaults")),
        applications=applications,
        path=config_path,
        raw=root,
    )


def dump(config: Config) -> str:
    """Render a config back to YAML. Used by `appctl init`."""
    body: dict[str, Any] = {
        "version": config.version,
        "defaults": {
            "python_version": config.defaults.python_version,
            "template": config.defaults.template,
            "storage": config.defaults.storage,
            "rootfs_gb": config.defaults.rootfs_gb,
            "cores": config.defaults.cores,
            "memory_mb": config.defaults.memory_mb,
            "swap_mb": config.defaults.swap_mb,
        },
        "applications": {},
    }
    for name, app in config.applications.items():
        entry: dict[str, Any] = {}
        if app.description:
            entry["description"] = app.description
        entry["target"] = {"kind": app.target.kind}
        if app.target.options:
            entry["target"]["options"] = dict(app.target.options)
        source: dict[str, Any] = {"kind": app.source.kind}
        if app.source.kind == "package":
            source["package"] = app.source.package
        elif app.source.kind == "repo":
            source["repo"] = app.source.repo
            source["ref"] = app.source.ref
        # A toolchain source has nothing to record: there is no package and no
        # repository. The kind alone says where it comes from, which is the
        # whole truth about it.
        if app.source.webui:
            source["webui"] = {
                "path": app.source.webui.path,
                "package_manager": app.source.webui.package_manager,
            }
        entry["source"] = source
        if app.secrets:
            entry["secrets"] = list(app.secrets)
        body["applications"][name] = entry
    return yaml.safe_dump(body, sort_keys=False, default_flow_style=False)


# A minimal config used by `appctl init` when the repository has none. It is
# commented rather than empty because an empty config that passes validation
# teaches nothing; this one shows the shape of every key.
TEMPLATE = """\
# Configuration for appctl. Read by `appctl build`, `appctl install` and
# `appctl secrets`. Unknown keys are rejected rather than ignored.
version: 1

defaults:
  # The interpreter the guest's venv is built from. Must match the Python the
  # lockfile was compiled for, or pip installs wheels for the wrong ABI.
  python_version: "3.14"
  # Stock Proxmox template. Pinned by exact filename: bumping it changes the apt
  # candidate set in one step.
  template: ubuntu-26.04-standard_26.04-1_amd64.tar.zst
  storage: data1
  rootfs_gb: 16
  cores: 2
  memory_mb: 2048
  swap_mb: 512
  features: nesting=1,fuse=1,keyctl=1
  onboot: 0

applications:

  # An application published on PyPI. Nothing to build: the wheel already
  # exists. `appctl build myapp` says so and exits 0.
  #
  # myapp:
  #   description: one line, shown by `appctl list`
  #   target:
  #     kind: proxmox_lxc        # the only implemented target
  #     options:
  #       hostname: myapp
  #       vmid: 121               # optional; omit to let the role skip create
  #       bind_host: 127.0.0.1    # 0.0.0.0 also requires secrets below
  #   source:
  #     kind: package
  #     package: "myapp[browser]==1.2.3"
  #   secrets: []                 # names of keys required in .env

  # An application built from a git checkout. `appctl build` produces the wheel
  # and the sha256-pinned lockfile under apps/<name>/files/.
  #
  # myapp:
  #   description: built from a fork
  #   target:
  #     kind: proxmox_lxc
  #     options:
  #       hostname: myapp
  #       vmid: 121
  #       bind_host: 0.0.0.0
  #   source:
  #     kind: repo
  #     repo: /path/to/checkout
  #     ref: HEAD                 # a tag, branch or commit
  #     webui:                    # omit when there is no frontend
  #       path: webui
  #       package_manager: auto   # auto | bun | npm
  #       build_command: build
  #   build:
  #     python_version: "3.14"
  #     lock_requirement: null     # null compiles the repo's own pyproject.toml
  #     fetch: false               # git fetch before resolving ref
  #     verify_lock: true          # refuse a lock entry with no sha256
  #   secrets:
  #     - myapp_provider_api_key

  # A target that is planned but not built. The tool refuses it before it
  # builds, downloads or starts anything.
  #
  # myapp:
  #   target:
  #     kind: vm                  # planned; not implemented
"""


def deployed_apps(config: Config) -> dict[str, str]:
    """Which applications are wired to a guest, and on which.

    Read from `app_roles` in inventory/group_vars/*.yml — the same declaration
    play 2 loops over. This is the answer to "what will `appctl install` with no
    arguments actually do", which cannot be derived from app.yaml alone: a config
    may describe an application nobody has deployed.

    It is deliberately a shallow scan rather than a full Ansible inventory
    parser. The shapes that matter are a top-level `app_roles` list and the
    `lxc` group's host names; anything richer is not something this repository
    uses, and a partial parse that fails loudly beats a full one that silently
    disagrees with the play.

    The scan is RECURSIVE, because Ansible's group_vars are per-group
    directories: `group_vars/<group>/main.yml`. A flat glob finds nothing here,
    every application reads as "not on any guest", and the install refuses with
    a message that points at the wrong file.
    """
    inventory = config.root / "inventory"
    group_vars = inventory / "group_vars"
    if not group_vars.is_dir():
        return {}

    found: dict[str, str] = {}
    for path in sorted(group_vars.rglob("*.yml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
        if not isinstance(data, dict):
            continue
        roles = data.get("app_roles")
        if not isinstance(roles, list):
            continue
        # The group is the directory the file is in: `group_vars/omrsrc/main.yml`
        # belongs to the group `omrsrc`, not to a group called `main`.
        group = path.parent.name
        for role in roles:
            if isinstance(role, str):
                found.setdefault(role, group)
    return found
