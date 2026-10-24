"""Command line entry point.

Commands, matching the jobs:

    appctl build <app>    produce the artifacts the role installs
    appctl install        run the delivery target for one application
    appctl secrets ...    manage the .env the play reads
    appctl list           what is configured
    appctl targets        which delivery targets exist
    appctl init           write a starting app.yaml

Every command exits non-zero on failure so this works in CI without a wrapper.
Errors print as one message plus one hint, never a traceback: an operator cannot
act on a stack frame, and a traceback here would be this tool's fault.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import build as build_mod
from . import config as config_mod
from . import render as render_mod
from . import secrets as secrets_mod
from . import state as state_mod
from . import targets as targets_mod
from .errors import AppctlError

BANNER = "appctl"


def _echo(msg: str = "") -> None:
    """Print one line.

    BrokenPipeError is handled because piping this tool into `head` or `less`
    closes stdout early, and an unhandled one produces a traceback that buries
    whatever the operator actually needed to read. Exiting quietly is the
    correct behaviour: they stopped reading on purpose.
    """
    try:
        print(msg, flush=True)
    except BrokenPipeError:
        # Devnull so the interpreter's own flush at exit does not raise again.
        import os as _os

        _os.dup2(_os.open(_os.devnull, _os.O_WRONLY), sys.stdout.fileno())
        raise SystemExit(0)


def _load(args) -> config_mod.Config:
    path = Path(args.config).resolve() if args.config else None
    return config_mod.load(path)


# --------------------------------------------------------------------------
# list
# --------------------------------------------------------------------------

def _last_installed(app) -> str:
    """When the guest was last converged, from the state block appctl writes."""
    installed = app.state.get("installed") if app.state else None
    if not isinstance(installed, dict):
        return "never"
    at = str(installed.get("at", ""))[:16].replace("T", " ")
    failed = installed.get("failed", 0)
    suffix = f"  {failed} FAILED" if failed else ""
    return at + suffix


def _deployment_note(app, group: str | None, vmid: int | None) -> str:
    """Short note for the DEPLOYED column: is this app on a guest, and which."""
    if group:
        return group
    if vmid:
        return "no guest"
    return "not wired"


def cmd_targets(args) -> int:
    """Show which delivery targets exist, and which ones work.

    The planned targets are listed too. A reader who finds `vm` in a manifest
    should learn from this that it is not built yet, rather than from a failed
    install.
    """
    _echo("implemented targets:")
    for kind in targets_mod.available_targets():
        target = targets_mod.resolve(kind)
        _echo(f"  {target.describe()}")
        for capability in target.capabilities:
            _echo(f"      - {capability}")

    planned = targets_mod.unimplemented_targets()
    if planned:
        _echo()
        _echo("planned, not implemented:")
        for kind, what in sorted(planned.items()):
            _echo(f"  {kind}: {what}")
        _echo()
        _echo("  a manifest that names one of these fails before it builds or")
        _echo("  starts anything.")
    return 0


def cmd_list(args) -> int:
    cfg = _load(args)
    deployed = config_mod.deployed_apps(cfg)
    if not cfg.applications:
        _echo(f"no applications configured in {cfg.path}")
        _echo(f"  template: {cfg.defaults.template}")
        return 0

    _echo(f"config: {cfg.path}")
    _echo(f"python: {cfg.defaults.python_version}   template: {cfg.defaults.template}")
    _echo()

    # Column widths from the data, so a long package spec is not cut off. A
    # truncated column here hides exactly the thing the operator came to read.
    rows: list[tuple[str, str, str, str, str, str, str]] = []
    for name, app in sorted(cfg.applications.items()):
        if app.source.kind == "package":
            source = app.source.package or ""
        else:
            ref = (app.build.ref if app.build and app.build.ref else app.source.ref) or ""
            source = f"repo@{ref}"
        missing = secrets_mod.missing(cfg.root, app.secrets)
        if not app.secrets:
            state = "-"
        elif missing:
            state = f"{len(missing)} missing"
        else:
            state = "ok"
        options = app.target.options
        destination = str(options.get("hostname") or "-")
        vmid = options.get("vmid")
        if vmid:
            destination = f"{destination} ({vmid})"
        rows.append((
            name,
            _deployment_note(app, deployed.get(name), vmid),
            _last_installed(app),
            source,
            app.target.kind or "-",
            destination,
            state,
        ))

    headers = ("APPLICATION", "DEPLOYED", "LAST INSTALLED", "SOURCE", "TARGET", "DESTINATION", "SECRETS")
    widths = [
        max(len(headers[i]), max((len(r[i]) for r in rows), default=0))
        for i in range(len(headers))
    ]
    _echo("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip())
    _echo("  ".join("-" * w for w in widths))
    for row in rows:
        _echo("  ".join(row[i].ljust(widths[i]) for i in range(len(row))).rstrip())

    unwired = sorted(n for n in cfg.applications if n not in deployed)
    if unwired:
        _echo()
        _echo(f"  not on any guest: {', '.join(unwired)}")
        _echo("  these are configured but nothing installs them. To deploy one, add it")
        _echo("  to app_roles in inventory/group_vars/<group>.yml.")
    return 0


# --------------------------------------------------------------------------
# init
# --------------------------------------------------------------------------

def cmd_init(args) -> int:
    # `init` is the one command that must work without an existing config,
    # so it defaults to the working directory rather than searching upward.
    target = Path(args.config).resolve() if args.config else Path.cwd() / config_mod.CONFIG_NAME
    if target.exists() and not args.force:
        raise AppctlError(
            f"{target} already exists",
            hint="pass --force to overwrite it, or edit it directly",
        )
    target.write_text(config_mod.TEMPLATE, encoding="utf-8")
    _echo(f"wrote {target}")
    _echo()
    _echo("Next:")
    _echo(f"  appctl secrets init            create .env from the required keys")
    _echo(f"  appctl secrets set <NAME>      set a secret")
    _echo(f"  appctl install                 run the play")
    return 0


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------

def cmd_build(args) -> int:
    cfg = _load(args)
    if args.check:
        info = build_mod.inspect(cfg, args.app)
        width = max(len(k) for k in info)
        for key, value in info.items():
            _echo(f"  {key.ljust(width)}  {value}")
        missing = info.get("hashes", "").startswith("MISSING")
        return 1 if missing else 0

    result = build_mod.build(
        cfg, args.app, ref=args.ref,
        fetch=True if args.fetch else None, dry_run=args.dry_run,
    )
    if args.dry_run:
        _echo(f"would build {args.app} at {result.commit[:12] if result.commit else '?'}")
        return 0
    for line in result.summary():
        _echo(f"  {line}")
    if result.built:
        state_mod.record_build(cfg, args.app, result)
        _echo(f"  recorded in {cfg.path.name}")
    return 0


# --------------------------------------------------------------------------
# render
# --------------------------------------------------------------------------

def cmd_render(args) -> int:
    """Render every configured application's YAML to JSON.

    Runs automatically before `install`; exposed on its own so the rendered file
    can be read and reviewed without deploying anything. That matters, because
    the rendered file is the thing the guest will actually run.
    """
    cfg = _load(args)
    rendered = render_mod.render_all(cfg)
    if not rendered:
        _echo("no application declares a config block; nothing to render")
        return 0
    for name, path in sorted(rendered.items()):
        app = cfg.app(name)
        size = Path(path).stat().st_size
        ctx = render_mod.context(app, app.config.vars)
        target = render_mod.substitute(app.config.target, ctx, "app.yaml")
        _echo(f"{name}: {target}  <-  {', '.join(app.config.files)}  ({size} bytes)")
        _echo(f"  rendered: {path}")
    return 0


# --------------------------------------------------------------------------
# install
# --------------------------------------------------------------------------

def select_apps(cfg, requested: str | None) -> list[str]:
    """Decide which applications this run installs, and say why.

    An operator with several applications must name one. Installing everything
    because no name was given is the wrong default: it turns one missing secret
    on an application nobody asked for into a failed run for the one they did.

    With exactly one application configured, that one is chosen, because having
    to type the only name in the repository is friction with no benefit.

    Whether an application is *deployed* is not decided here. That is a
    question about the destination, so each target answers it during
    validation.
    """
    if requested:
        # Validated here so a typo names the alternatives rather than producing an
        # empty --tags and a play that quietly does nothing.
        cfg.app(requested)
        return [requested]

    names = sorted(cfg.applications)
    if not names:
        raise AppctlError(
            "no applications are configured",
            hint=f"add one to {cfg.path.name}",
        )
    if len(names) == 1:
        return names
    raise AppctlError(
        f"{len(names)} applications are configured; name the one to install",
        hint="  " + "\n  ".join(f"appctl install {n}" for n in names)
             + "\n  or run `appctl list` to see the full picture",
    )


def cmd_install(args) -> int:
    cfg = _load(args)

    # Resolve the target before anything else happens. An unimplemented target
    # must fail before a secret is read, a file is rendered or a play starts.
    #
    # The name is validated first, so a typo names the applications that do
    # exist rather than reporting an unknown target for an application that is
    # not there at all.
    if args.app:
        cfg.app(args.app)
    targets = {}
    for name, app in cfg.applications.items():
        try:
            targets[name] = targets_mod.resolve(app.target.kind)
        except targets_mod.TargetError as exc:
            if args.app and args.app != name:
                continue
            raise AppctlError(exc.message, hint=exc.hint) from exc

    selected = select_apps(cfg, args.app)

    _echo("installing: " + ", ".join(selected))
    for name in selected:
        _echo(f"  {name}: target {targets[name].kind}")

    # Secrets for the selected applications only. Discovering a missing secret
    # halfway through a play leaves half the guest configured, and demanding one
    # for an application that is not being installed blocks a run that was
    # otherwise fine.
    required: list[str] = []
    for name in selected:
        for secret in cfg.app(name).secrets:
            if secret not in required:
                required.append(secret)
    values = secrets_mod.ensure_for_install(cfg.root, required) if required else {}

    # Rendered here, on the controller, before the play starts. An application
    # that declared a config block gets its YAML turned into JSON here rather
    # than by a script shipped to the guest — see src/lxc/render.py for why.
    rendered = render_mod.render_selected(cfg, selected)
    if rendered:
        for name, path in sorted(rendered.items()):
            # Rendered with the same context, so what is printed is what lands on
            # the guest rather than the unsubstituted template.
            app = cfg.app(name)
            spec = app.config
            ctx = render_mod.context(app, spec.vars)
            target = render_mod.substitute(spec.target, ctx, "app.yaml") if spec else "?"
            sources = ", ".join(spec.files) if spec else "?"
            _echo(f"rendered {name}: {target}  from  {sources}")

    # The target validates its own settings before it runs anything. A missing
    # hostname or a missing playbook is caught here, with nothing built.
    for name in selected:
        context = targets_mod.TargetContext(
            config=cfg,
            app_name=name,
            verbosity=args.verbosity,
            forks=args.forks,
            heartbeat=args.heartbeat,
            secret_values=values if name == selected[0] else {},
            extra_args=list(args.ansible_args),
        )
        try:
            targets[name].validate(context)
            targets[name].provision(context)
        except targets_mod.TargetError as exc:
            raise AppctlError(exc.message, hint=exc.hint) from exc

    # Recorded whether the play passed or failed: a failed install is exactly the
    # event an operator needs to see in the file rather than dig out of logs.
    exit_code = 0
    for name in selected:
        context = targets_mod.TargetContext(
            config=cfg,
            app_name=name,
            verbosity=args.verbosity,
            forks=args.forks,
            heartbeat=args.heartbeat,
            secret_values=values,
            extra_args=list(args.ansible_args),
        )
        try:
            outcome = targets[name].deploy(context, artifacts={}, rendered=rendered)
        except targets_mod.TargetError as exc:
            raise AppctlError(exc.message, hint=exc.hint) from exc

        state_mod.record_install(
            cfg, name, outcome,
            changed=outcome.changed, failed=outcome.failed,
        )
        _echo(f"  state recorded in {cfg.path.name}")
        _echo()
        _echo(f"{name}: {outcome.target} -> {outcome.guest}  "
              f"{outcome.changed} changed, {outcome.failed} failed, "
              f"{outcome.tasks} tasks, {outcome.seconds:.0f}s")
        for check in outcome.checks:
            _echo(f"  check: {check}")
        if outcome.logs:
            _echo(f"  logs: {outcome.logs}")
        if exit_code == 0 and not outcome.ok:
            exit_code = 1

    return exit_code


# --------------------------------------------------------------------------
# secrets
# --------------------------------------------------------------------------

def cmd_secrets_init(args) -> int:
    cfg = _load(args)
    names: list[str] = []
    for app in cfg.applications.values():
        for name in app.secrets:
            if name not in names:
                names.append(name)

    example = secrets_mod.write_example(cfg.root, names)
    _echo(f"wrote {example}")

    env = secrets_mod.env_path(cfg.root)
    if env.exists() and not args.force:
        _echo(f"{env} already exists; left alone")
        return 0
    secrets_mod.write_env(
        cfg.root, {n: "" for n in names},
        comment=(
            "Secrets for this deployment.\n"
            "Gitignored. Never commit it, and never paste its contents anywhere.\n"
            "Set a value with:  appctl secrets set <NAME> '<value>'\n"
            "Generate one with: appctl secrets set <NAME> --generate\n"
        ),
    )
    _echo(f"wrote {env} with mode 0600")
    if names:
        _echo()
        _echo(f"{len(names)} key(s) to fill in:")
        for name in names:
            _echo(f"  {name}")
    return 0


def cmd_secrets_set(args) -> int:
    """Set one or more secrets.

    Three forms, and the first is the one people actually type:

        appctl secrets set NAME                 prompt, value never in argv
        appctl secrets set NAME VALUE           positional value
        appctl secrets set NAME --generate      random value

    `--generate` accepts several names at once; the positional-value form is one
    name at a time, because `set A valueA B valueB` is not a thing anyone means.
    """
    cfg = _load(args)
    rest = list(args.rest)

    if args.generate:
        if len(rest) == 0:
            raise AppctlError("nothing to generate", hint="name at least one secret: appctl secrets set NAME --generate")
        values = {name: secrets_mod.generate() for name in rest}
        for name, value in values.items():
            _echo(f"{name}: generated {len(value)} characters")
    elif len(rest) == 1:
        values = {rest[0]: ""}
        _prompt_for(rest[0], values)
    elif len(rest) == 2:
        values = {rest[0]: rest[1]}
    elif len(rest) == 0:
        raise AppctlError("no secret named", hint="usage: appctl secrets set NAME [VALUE] | NAME --generate")
    else:
        raise AppctlError(
            f"expected one name, or one name and one value; got {len(rest)} arguments",
            hint="for several at once use --generate: appctl secrets set A B --generate",
        )

    path = secrets_mod.write_env(cfg.root, values)
    _echo(f"updated {path}")
    _echo("  values are not printed; check with `appctl secrets list`")
    return 0


def _prompt_for(name: str, values: dict[str, str]) -> None:
    """Read one value from the terminal without echoing it."""
    import getpass

    if not sys.stdin.isatty():
        raise AppctlError(
            "no value given and stdin is not a terminal",
            hint=f"pass one explicitly:  appctl secrets set {name} '<value>'\n"
                 f"       or generate one:  appctl secrets set {name} --generate",
        )
    values[name] = getpass.getpass(f"{name}: ")


def cmd_secrets_list(args) -> int:
    cfg = _load(args)
    values = secrets_mod.read_env(cfg.root)
    required: list[str] = []
    for app in cfg.applications.values():
        for name in app.secrets:
            if name not in required:
                required.append(name)

    if not values and not required:
        _echo(f"no {secrets_mod.ENV_NAME} at {secrets_mod.env_path(cfg.root)}")
        _echo("  run `appctl secrets init` to create one")
        return 0

    names = sorted(set(values) | set(required))
    width = max(len(n) for n in names)
    _echo(f"{secrets_mod.ENV_NAME}: {secrets_mod.env_path(cfg.root)}")
    for name in names:
        present = name in values and values[name] != ""
        state = "set" if present else ("EMPTY" if name in values else "not set")
        marker = "required" if name in required else ""
        _echo(f"  {name.ljust(width)}  {state:<8} {marker}")
    return 0


def cmd_secrets_check(args) -> int:
    cfg = _load(args)
    required: list[str] = []
    for app in cfg.applications.values():
        for name in app.secrets:
            if name not in required:
                required.append(name)
    if not required:
        _echo("no secrets are required by this config")
        return 0

    results = secrets_mod.check(cfg.root, required)
    width = max(len(c.name) for c in results)
    failed = False
    for entry in results:
        if entry.empty:
            failed = True
            _echo(f"  {entry.name.ljust(width)}  MISSING")
        else:
            _echo(f"  {entry.name.ljust(width)}  ok")
    if failed:
        _echo()
        _echo(f"{sum(1 for c in results if c.empty)} of {len(results)} required secrets are not set")
        _echo(f"  set them with: appctl secrets set <NAME> '<value>'")
        _echo(f"  or generate:   appctl secrets set <NAME> --generate")
        return 1
    _echo(f"all {len(results)} required secrets are set")
    return 0


# --------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------

def _default_verbosity() -> int:
    """-vvv by default: the first level that reports the command a module ran."""
    if os.environ.get("APPCTL_QUIET"):
        return 0
    return 3


def _verbosity_flags(parser: argparse.ArgumentParser) -> None:
    """Verbosity flags, added to the top level and to every subcommand.

    Without this they are only accepted before the subcommand, so the natural
    `appctl install -q` fails with "unrecognized arguments" while
    `appctl -q install` works. That asymmetry is a trap, not a design.
    """
    # SUPPRESS, not None/False. A subparser writes its own defaults back over
    # whatever the parent parsed, so without this `appctl -q install` is reset
    # to quiet=False by the subparser and the flag silently does nothing. With
    # SUPPRESS the attribute is only set when the flag is actually given, and
    # the parent's value survives.
    parser.add_argument("-v", "--verbose", action="count", default=argparse.SUPPRESS,
                        help="Ansible verbosity; default 3 (shows each command). Use -q for 0.")
    parser.add_argument("-q", "--quiet", action="store_true", default=argparse.SUPPRESS,
                        help="terse: task results only")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="appctl",
        description="Build, install and operate applications on delivery targets.",
    )
    parser.add_argument("-C", "--config", help="path to app.yaml (default: search upward)")
    _verbosity_flags(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="show configured applications")
    p_list.set_defaults(func=cmd_list)

    p_targets = sub.add_parser(
        "targets",
        help="show the delivery targets, and which are implemented",
    )
    p_targets.set_defaults(func=cmd_targets)

    p_init = sub.add_parser("init", help="write a starting app.yaml")
    p_init.add_argument("--force", action="store_true", help="overwrite an existing config")
    p_init.set_defaults(func=cmd_init)

    p_build = sub.add_parser("build", help="produce an application's install artifacts")
    p_build.add_argument("app", help="application name from app.yaml")
    p_build.add_argument("--ref", help="override source.ref")
    p_build.add_argument("--fetch", action="store_true", help="git fetch before resolving ref")
    p_build.add_argument("--check", action="store_true", help="report committed artifacts, build nothing")
    p_build.add_argument("--dry-run", action="store_true", help="resolve the ref and stop")
    p_build.set_defaults(func=cmd_build)

    p_render = sub.add_parser(
        "render",
        help="render application config from YAML (done automatically by install)",
    )
    p_render.set_defaults(func=cmd_render)

    p_install = sub.add_parser(
        "install",
        help="run the Ansible play for one application",
        usage="appctl install [APP] [options]",
        epilog=(
            "APP is positional. With several applications deployed it is "
            "required, and the tool will say so rather than guess:\n"
            "  appctl install example-app\n"
            "  appctl install example-app -- --check --diff\n"
            "Anything after a bare -- goes straight to ansible-playbook."
        ),
    )
    p_install.add_argument(
        "app",
        nargs="?",
        metavar="APP",
        help="application to install; required when more than one is deployed",
    )
    p_install.add_argument("--forks", type=int, default=10, help="guests at once (default 10)")
    p_install.add_argument("--heartbeat", type=int, default=30,
                           help="progress line every N seconds (default 30, 0 disables)")
    _verbosity_flags(p_install)
    p_install.set_defaults(func=cmd_install)

    p_secrets = sub.add_parser("secrets", help="manage the .env the play reads")
    ssub = p_secrets.add_subparsers(dest="secrets_command", required=True)

    s_init = ssub.add_parser("init", help="create .env and .env.example")
    s_init.add_argument("--force", action="store_true", help="overwrite an existing .env")
    s_init.set_defaults(func=cmd_secrets_init)

    s_set = ssub.add_parser("set", help="set one or more secrets")
    s_set.add_argument("rest", nargs="*", metavar="NAME [VALUE]",
                       help="a name, or a name and its value")
    s_set.add_argument("--generate", action="store_true",
                       help="generate a random value instead of reading one")
    s_set.set_defaults(func=cmd_secrets_set)

    s_list = ssub.add_parser("list", help="show which secrets are set, never their values")
    s_list.set_defaults(func=cmd_secrets_list)

    s_check = ssub.add_parser("check", help="verify the required secrets are set")
    s_check.set_defaults(func=cmd_secrets_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Split at a bare -- so anything after it reaches ansible-playbook verbatim.
    if "--" in argv:
        cut = argv.index("--")
        own, passthrough = argv[:cut], argv[cut + 1:]
    else:
        own, passthrough = argv, []

    parser = build_parser()
    args = parser.parse_args(own)
    # Ansible arguments are exactly what followed a bare --, never anything
    # argparse decided was unrecognized. REMAINDER was the obvious way to do
    # this and it is wrong: mixed with a positional it also swallows this
    # tool's own flags, so `appctl install example-app --quiet` passed --quiet to
    # ansible-playbook, which has no such option, and the play exited 2.
    args.ansible_args = passthrough

    # Resolve verbosity once, from whichever position the flag was given.
    level = getattr(args, "verbose", None)
    quiet = getattr(args, "quiet", False)
    args.verbosity = 0 if quiet else (3 if level is None else level)
    if not hasattr(args, "verbosity"):
        args.verbosity = 3
    if not hasattr(args, "forks"):
        args.forks = 10
    if not hasattr(args, "heartbeat"):
        args.heartbeat = 30
    if not hasattr(args, "config"):
        args.config = None

    try:
        return args.func(args)
    except AppctlError as exc:
        print(f"\nerror: {exc.message}", file=sys.stderr)
        if exc.hint:
            print(f"\n{exc.hint}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
