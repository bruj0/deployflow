"""Render an application's YAML configuration on the controller.

The point of this module is that an application needs **no code of its own** to
be configured. It ships YAML; this turns it into the JSON the guest reads, and
`common/python` copies it into place.

Why this exists rather than a per-application script on the guest:

  example-app — the application that prompted it — ships its own minimal
  `render-config.json` for Render.com, and `example-app/config/loader.py` defines
  merge_missing_defaults: "Recursively add missing defaults without replacing
  configured values". The application already merges defaults into a partial
  config. A role that did the same thing again, in Python, shipped to the guest,
  duplicated the application's own behaviour with a second implementation of it
  that could drift.

  Verified on a live guest: a two-key config.json boots the service and
  answers /health with ready=true. The extra 200 lines were not buying anything.

Two substitution syntaxes, deliberately distinct:

  {{ name }}   substituted here, on the controller, from vars plus built-ins
  ${NAME}      left untouched, for the application to resolve at runtime from
               its EnvironmentFile

Keeping them apart is what makes this safe to commit. A secret never appears in
this repository, in a rendered file, or in a log: it stays a `${NAME}` the
application substitutes on the guest, where the EnvironmentFile is mode 0600 and
root-owned.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from .config import Application, Config, RenderedConfig
from .errors import AppctlError

# {{ name }} or {{ name | default }} — whitespace tolerant, no nested braces.
PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")

# Names available without declaring them under config.vars.
#
# The three paths mirror common/python/defaults/main.yml, which derives them from
# app_name the same way. That is a deliberate, checked duplication rather than a
# coincidence: appctl has to know where the state directory is to render a config
# into it, and common/python has to create it. Two places that must agree is only
# safe if something makes them agree, so `check_paths_agree` below compares the
# value the guest actually has against what appctl rendered to.
BUILTINS = ("app_name", "app_home", "app_dir", "app_state_dir")


def default_paths(app_name: str) -> dict[str, str]:
    """The contract's paths for an application, matching common/python exactly."""
    return {
        "app_home": f"/opt/{app_name}",
        "app_dir": f"/opt/{app_name}/app",
        "app_state_dir": f"/opt/{app_name}/state",
    }


class RenderError(AppctlError):
    """A YAML file could not be rendered."""


def context(app: Application, declared: dict[str, object]) -> dict[str, object]:
    """Names available to `{{ }}` in a rendered file.

    Built-ins first so a `vars:` entry of the same name wins: an application that
    needs to override a built-in should be able to say so rather than be refused.
    """
    ctx = {"app_name": app.name}
    ctx.update(default_paths(app.name))
    ctx.update(declared)
    return ctx


def substitute(node: object, ctx: dict[str, object], origin: str) -> object:
    """Replace {{ name }} throughout a parsed YAML tree.

    Recursive so a placeholder works inside a list or a nested mapping, which is
    where they usually are — a model name inside a providers block, a port inside
    a channel.

    Only whole-string values and string keys are substituted. A value that is
    only part of a string (`prefix-{{ x }}`) is left alone rather than guessed
    at, because the type of the result would be ambiguous and a config that
    renders differently on two runs is worse than one that does not render.
    """
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            new_key = key
            if isinstance(key, str):
                new_key = substitute(key, ctx, origin)
            out[new_key] = substitute(value, ctx, origin)
        return out
    if isinstance(node, list):
        return [substitute(item, ctx, origin) for item in node]
    if isinstance(node, str):
        whole = PLACEHOLDER_RE.fullmatch(node.strip())
        if whole:
            # The entire value is one placeholder: return the value as declared,
            # so a port stays an int and a host stays a str.
            return _lookup(whole.group(1), ctx, origin)
        if PLACEHOLDER_RE.search(node):
            # A partial reference, as in "{{ app_state_dir }}/workspace". The
            # result is a string by definition — that is what concatenation means
            # — so every substituted value is stringified.
            return PLACEHOLDER_RE.sub(
                lambda m: str(_lookup(m.group(1), ctx, origin)), node
            )
    return node


def _lookup(name: str, ctx: dict[str, object], origin: str) -> object:
    if name not in ctx:
        raise RenderError(
            f"{origin}: {{{{{name}}}}} is not defined",
            hint=f"add it under applications.{app_name_guess(origin)}.config.vars "
                 f"in app.yaml, or write the value inline",
        )
    return ctx[name]


def app_name_guess(origin: str) -> str:
    """Application name implied by a path like apps/<name>/files/config.yml.

    Used only to name the right block in an error message, so a wrong guess costs
    a slightly less helpful hint rather than a wrong behaviour.
    """
    parts = Path(origin).parts
    if "apps" in parts:
        idx = parts.index("apps")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return "the application"


def render_file(path: Path, ctx: dict[str, object]) -> dict:
    """Read one YAML file, substitute, and return a JSON-ready dict."""
    if not path.is_file():
        raise RenderError(f"{path} does not exist", hint="check config.files in app.yaml")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RenderError(f"{path} is not valid YAML: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise RenderError(
            f"{path} must contain a mapping at the top level, got {type(data).__name__}",
            hint="the file becomes the whole config document",
        )
    result = substitute(data, ctx, str(path))
    assert isinstance(result, dict)
    return result


def render_app(config: Config, app: Application) -> tuple[Path, RenderedConfig] | None:
    """Render one application's config, or None when it declares none.

    Writes to tmp/rendered/<app>/config.json — outside the repository, so a
    rendered file can never be committed by accident.
    """
    spec = app.config
    if spec is None:
        return None

    ctx = context(app, spec.vars)
    merged: dict = {}
    # Later files win, key by key, so a base file plus an override file is a
    # normal arrangement rather than a special case.
    for relative in spec.files:
        source = (config.root / relative).resolve()
        try:
            source.relative_to(config.root)
        except ValueError:
            raise RenderError(
                f"{relative} is outside the repository",
                hint="config.files names files inside this repository",
            ) from None
        for key, value in render_file(source, ctx).items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = _deep(merged[key], value)
            else:
                merged[key] = value

    target_dir = config.root / "tmp" / "rendered" / app.name
    target_dir.mkdir(parents=True, exist_ok=True)
    out = target_dir / "config.json"
    out.write_text(json.dumps(merged, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return out, spec


def _deep(base: dict, patch: dict) -> dict:
    out = dict(base)
    for key, value in patch.items():
        out[key] = _deep(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def render_selected(config: Config, names: list[str]) -> dict[str, str]:
    """Render only the named applications.

    `install` uses this so that a run for one application never rewrites another
    one's config. Rendering everything would be harmless on a read-only path, but
    it means the file an application is about to be given depends on which
    unrelated applications happen to be configured.
    """
    rendered: dict[str, str] = {}
    for name in names:
        result = render_app(config, config.app(name))
        if result is None:
            continue
        path, _ = result
        rendered[name] = str(path)
    return rendered


def render_all(config: Config) -> dict[str, str]:
    """Render every application's config.

    Returns a map of application name to the rendered file, which is passed to
    Ansible so the role can copy it. An application that declares no config is
    absent from the map rather than mapped to None.
    """
    rendered: dict[str, str] = {}
    for name, app in sorted(config.applications.items()):
        result = render_app(config, app)
        if result is None:
            continue
        path, _ = result
        rendered[name] = str(path)
    return rendered
