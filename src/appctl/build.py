"""Produce the artifacts an application role installs.

This is apps/example-app/build.sh generalised into a command driven by app.yaml.
Every check that script made is kept, because each one exists because of a
specific way a build can quietly lie:

  dirty tree      a wheel built from uncommitted edits records a commit that
                  does not contain its own source
  commit mismatch the wheel is asked what commit it is, rather than the
                  checkout being asked, so the artifact states its own
                  provenance
  lock hash       `--require-hashes` fails the whole install if any entry has
                  no sha256, so it is checked here where the message can name
                  the package

What is committed and what is not is a deliberate split, unchanged from the
script: the lockfile is a pure function of the repo's pyproject.toml, so a diff
of it means something. The wheel is a multi-megabyte binary that changes on
every commit, and BUILDINFO records its sha256, which is what the role checks
before installing.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import Application, Config
from .errors import BuildError


@dataclass
class BuildResult:
    """What a build produced, for reporting and for the role's BUILDINFO."""

    app: str
    commit: str = ""
    wheel: str = ""
    wheel_sha256: str = ""
    packages: int = 0
    lock_path: Path | None = None
    built: bool = False

    def summary(self) -> list[str]:
        """Lines for the operator, most useful first."""
        out = []
        if self.commit:
            out.append(f"commit:  {self.commit[:12]}")
        if self.wheel:
            out.append(f"wheel:   {self.wheel}")
        if self.wheel_sha256:
            out.append(f"sha256:  {self.wheel_sha256}")
        if self.lock_path:
            out.append(f"lock:    {self.packages} packages, {self.lock_path}")
        return out


def _run(argv: list[str], cwd: Path | None = None, *, env: dict[str, str] | None = None) -> str:
    """Run a command, raising BuildError with its output on failure.

    Output is captured rather than streamed so a failure can show the compiler
    messages that explain it. A build that fails on the laptop should show why
    in the five seconds it takes, not on the guest at three in the morning.
    """
    merged = {**os.environ, **(env or {})}
    try:
        proc = subprocess.run(
            argv, cwd=cwd, env=merged, capture_output=True, text=True, timeout=1800
        )
    except FileNotFoundError as exc:
        raise BuildError(f"{argv[0]} is not installed", hint=f"install it, or set PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise BuildError(f"{' '.join(argv[:3])} timed out after 30 minutes") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-25:]
        raise BuildError(
            f"{' '.join(argv[:3])} failed with exit {proc.returncode}",
            hint="\n".join(tail) if tail else None,
        )
    return proc.stdout


def _require(tool: str, why: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise BuildError(f"{tool} is required but not on PATH", hint=why)
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_lock(lock_path: Path) -> tuple[int, list[str]]:
    """Count pinned packages and return those with no sha256.

    Done here, not at install time, because a missing hash has to be a build
    failure with a package name attached. At install time pip's error names
    neither the package nor the reason.
    """
    if not lock_path.is_file():
        raise BuildError(f"no lockfile at {lock_path}")
    count = 0
    missing: list[str] = []
    current: str | None = None
    has_hash = False
    for line in lock_path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9_.\-]+)==", line)
        if match:
            current, has_hash, count = match.group(1), False, count + 1
        elif "--hash=sha256:" in line:
            has_hash = True
        elif not line.strip() and current:
            if not has_hash:
                missing.append(current)
            current = None
    return count, missing


def _app_dir(config: Config, app: Application) -> Path:
    return config.root / "apps" / app.name


def inspect(config: Config, app_name: str) -> dict[str, str]:
    """Report the committed artifacts without building anything."""
    app = config.app(app_name)
    files = _app_dir(config, app) / "files"
    lock = files / "requirements.lock"
    info = {
        "application": app.name,
        "source": app.source.kind,
        "needs_build": "yes" if app.needs_build else "no",
        "lockfile": str(lock.relative_to(config.root)) if lock.is_file() else "MISSING",
    }
    if lock.is_file():
        count, missing = verify_lock(lock)
        info["packages"] = str(count)
        info["hashes"] = "complete" if not missing else f"MISSING for {', '.join(missing)}"
    buildinfo = files / app.source.output / "BUILDINFO"
    if buildinfo.is_file():
        # YAML, because the role reads it with from_yaml. See _write_buildinfo.
        import yaml

        data = yaml.safe_load(buildinfo.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict):
            info.update({str(k): str(v) for k, v in data.items()})
        else:
            info["BUILDINFO"] = f"unreadable: expected a mapping, got {type(data).__name__}"
    else:
        info["wheel"] = "NOT BUILT"
    return info


def build(config: Config, app_name: str, *, ref: str | None = None, fetch: bool | None = None,
          dry_run: bool = False) -> BuildResult:
    """Build one application's artifacts.

    A package-source application returns immediately with built=False: there is
    nothing to produce, and saying so is more useful than an error.
    """
    app = config.app(app_name)
    if not app.needs_build:
        # The message has to fit the kind it is talking about. A package source
        # names the version; a toolchain source has no version and no
        # repository, and saying "installed from None" would be a true-looking
        # sentence about nothing.
        if app.source.kind == "toolchain":
            raise BuildError(
                f"{app.name} is a toolchain guest, so there is nothing to build",
                hint=f"its role assembles the environment itself; "
                     f"run `appctl install {app.name}` to provision it",
            )
        raise BuildError(
            f"{app.name} is installed from {app.source.package}, so there is nothing to build",
            hint=f"to change the version, edit source.package in {config.path.name} and re-run `appctl install`",
        )

    build_cfg = app.build
    assert build_cfg is not None  # guaranteed by config validation
    source = app.source
    want_ref = ref or build_cfg.ref or source.ref
    do_fetch = build_cfg.fetch if fetch is None else fetch
    python_version = build_cfg.python_version or config.defaults.python_version

    repo = Path(source.repo or "").expanduser()
    if not (repo / ".git").is_dir():
        raise BuildError(
            f"{repo} is not a git checkout",
            hint="set source.repo to a checkout, or set kind: package if the app is on PyPI",
        )

    _require("git", "https://git-scm.com/")
    _require("uv", "https://docs.astral.sh/uv/")

    if do_fetch and not dry_run:
        _run(["git", "-C", str(repo), "fetch", "--all", "--tags", "--prune"])

    resolved = _resolve_commit(repo, want_ref)
    result = BuildResult(app=app.name, commit=resolved)

    files = _app_dir(config, app) / "files"
    dist = files / source.output
    lock = files / "requirements.lock"

    if dry_run:
        result.lock_path = lock
        return result

    _assert_clean(repo)
    print(f"==> building {app.name} at {resolved[:12]}")
    print(f"    repo:   {repo}")
    print(f"    python: {python_version}")

    work = Path(tempfile.mkdtemp(prefix=f"appctl-{app.name}-"))
    try:
        tree = work / "src"
        tree.mkdir()
        _archive(repo, resolved, tree)
        _seed_commit(tree, resolved)

        if source.webui:
            _build_webui(tree, source.webui, app)
        wheel = _build_wheel(tree, work)
        _verify_wheel_commit(wheel, resolved)
        result.packages = _write_lock(tree, work, lock, python_version, build_cfg.lock_requirement, app)
        if build_cfg.verify_lock:
            count, missing = verify_lock(lock)
            if missing:
                raise BuildError(
                    f"{len(missing)} lockfile entries carry no sha256: {', '.join(missing)}",
                    hint="pip --require-hashes would refuse the whole install",
                )
            result.packages = count

        if dist.exists():
            shutil.rmtree(dist)
        dist.mkdir(parents=True)
        shutil.copy2(wheel, dist / wheel.name)
        result.wheel = wheel.name
        result.wheel_sha256 = _sha256(dist / wheel.name)
        _write_buildinfo(dist / "BUILDINFO", app, resolved, result, python_version)
        result.lock_path = lock
        result.built = True
    finally:
        shutil.rmtree(work, ignore_errors=True)

    return result


def _resolve_commit(repo: Path, ref: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise BuildError(
            f"{ref!r} does not resolve to a commit in {repo}",
            hint="pass --fetch if the ref is on a remote that has not been fetched",
        )
    return proc.stdout.strip()


def _assert_clean(repo: Path) -> None:
    """Refuse a dirty checkout.

    The wheel records a commit sha. Built from a dirty tree, that sha describes
    code that was never pushed and cannot be reproduced by anyone else, which
    defeats the point of recording it.
    """
    unstaged = subprocess.run(["git", "-C", str(repo), "diff", "--quiet", "HEAD"],
                              capture_output=True).returncode != 0
    staged = subprocess.run(["git", "-C", str(repo), "diff", "--cached", "--quiet", "HEAD"],
                            capture_output=True).returncode != 0
    if unstaged or staged:
        raise BuildError(
            f"{repo} has uncommitted changes",
            hint="commit or stash them: a wheel built from a dirty tree records a commit "
                 "that does not contain its own source",
        )


def _archive(repo: Path, commit: str, dest: Path) -> None:
    """Export a commit into a throwaway tree.

    The build writes into the source tree: a frontend install creates
    node_modules with hundreds of packages, and a web build writes dist/. Doing
    that in the checkout would leave a working tree that is dirty in ways that
    have nothing to do with the work.
    """
    archive = subprocess.run(
        ["git", "-C", str(repo), "archive", "--format=tar", commit],
        capture_output=True, check=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dest, filter="data")


def _seed_commit(tree: Path, commit: str) -> None:
    """Write the commit into the tree before building.

    Applications that embed a build hook resolve their own version either from
    .git or from a file inside the package. `git archive` produces no .git, so
    without this the wheel ships with no provenance at all.
    """
    for candidate in (tree / "src" / "_build_commit.txt",
                      tree / "example-app" / "_build_commit.txt",
                      tree / "_build_commit.txt"):
        if candidate.parent.is_dir():
            candidate.write_text(f"{commit}\n", encoding="utf-8")
            return


def _pick_package_manager(want: str) -> str:
    """Resolve 'auto' to whatever is installed, preferring bun."""
    if want == "bun":
        _require("bun", "https://bun.sh/  or set webui.package_manager to npm")
        return "bun"
    if want == "npm":
        _require("npm", "nodejs.org  or set webui.package_manager to bun")
        return "npm"
    if shutil.which("bun"):
        return "bun"
    if shutil.which("npm"):
        return "npm"
    raise BuildError(
        "neither bun nor npm is on PATH, and a webui build needs one",
        hint="install bun or node, or remove the webui block from app.yaml if the app has no frontend",
    )


def _build_webui(tree: Path, webui, app: Application) -> None:
    """Build the frontend bundle before the wheel, so the wheel contains it.

    A frozen lockfile is used when there is one. A bundle built from a silently
    different dependency set is a different artifact under the same name, which
    is worse than a failed build.
    """
    manager = _pick_package_manager(webui.package_manager)
    directory = tree / webui.path
    if not directory.is_dir():
        raise BuildError(f"{webui.path} not found in the exported tree for {app.name}")

    print(f"==> building the webui bundle ({manager})")
    if webui.install_command:
        install = webui.install_command
    elif manager == "bun":
        install = "install --frozen-lockfile"
    else:
        install = "ci"
    _run([manager, *install.split()], cwd=directory)
    _run([manager, "run", webui.build_command], cwd=directory)

    if webui.out:
        produced = tree / webui.out
        if not produced.is_dir():
            raise BuildError(
                f"the webui build produced no {webui.out}/",
                hint="check webui.out and webui.build_command in app.yaml",
            )
        print(f"    bundle: {sum(1 for _ in produced.rglob('*') if _.is_file())} files")


def _build_wheel(tree: Path, work: Path) -> Path:
    """Build the wheel and return its path."""
    print("==> building the wheel")
    outdir = work / "wheel"
    _run(["uv", "build", "--wheel", "--out-dir", str(outdir)], cwd=tree)
    wheels = sorted(outdir.glob("*.whl"))
    if not wheels:
        raise BuildError("uv build produced no wheel")
    return wheels[0]


def _verify_wheel_commit(wheel: Path, commit: str) -> None:
    """Ask the wheel which commit it is, and compare.

    Read out of the artifact rather than from the checkout: a mismatch means the
    tree that was exported was not the tree that was intended.
    """
    with zipfile.ZipFile(wheel) as archive:
        names = [n for n in archive.namelist() if n.endswith("_build_commit.txt")]
        if not names:
            return  # the app embeds no provenance; nothing to check
        recorded = archive.read(names[0]).decode().strip()
    if recorded and recorded != commit:
        raise BuildError(
            f"the wheel records commit {recorded[:12]} but {commit[:12]} was archived",
            hint="the build did not use the tree it claims to",
        )


def _write_lock(tree: Path, work: Path, lock: Path, python_version: str,
                requirement: str | None, app: Application) -> int:
    """Compile the dependency tree from the repo's own pyproject, not PyPI.

    Compiled from the checkout so the tree tracks the fork. A published release
    can lag the fork by any number of commits, and a lock built from it would
    pin a dependency set the source does not ask for.
    """
    print(f"==> compiling the dependency tree for Python {python_version}")
    target = requirement or str(tree / "pyproject.toml")
    if not Path(target).exists():
        raise BuildError(f"{target} not found; set build.lock_requirement in app.yaml")
    _run([
        "uv", "pip", "compile", target,
        "--python-version", python_version,
        "--generate-hashes", "--no-header",
        "-o", str(lock),
    ])
    count, missing = verify_lock(lock)
    print(f"    {count} packages pinned")
    if missing:
        raise BuildError(
            f"{len(missing)} entries carry no sha256: {', '.join(missing)}",
            hint="pip --require-hashes would refuse the whole install",
        )
    return count


def _write_buildinfo(path: Path, app: Application, commit: str, result: BuildResult,
                     python_version: str) -> None:
    """Record provenance next to the wheel.

    Written as YAML, not `key=value`, because the role reads it with
    `from_yaml`. The original build.sh wrote `key=value`, which YAML parses as
    a bare scalar rather than a mapping, so `dict(...)` failed with
    "dictionary update sequence element #0 has length 1; 2 is required". The
    role and the writer had to agree on one format; YAML is what the role
    already assumed.

    The role reads this before installing anything and compares the sha256 in
    it against the wheel it copied. That check exists because a truncated copy
    and a rebuilt wheel both look like a successful copy.
    """
    repo_url = subprocess.run(
        ["git", "-C", app.source.repo or ".", "config", "--get", "remote.origin.url"],
        capture_output=True, text=True,
    ).stdout.strip() or (app.source.repo or "")
    header = (
        "# Written by `appctl build`. Not committed: regenerate with\n"
        f"# `appctl build {app.name}`. The role reads this to prove the wheel it\n"
        "# is about to install is the one this build produced.\n"
    )
    body = "\n".join([
        f"fork_url: {repo_url}",
        f"fork_commit: {commit}",
        f"wheel: {result.wheel}",
        f"wheel_sha256: {result.wheel_sha256}",
        # Quoted, and this is not cosmetic. Unquoted, YAML reads 3.14 as a
        # float, and the role asserts
        #   example-app_build.python_target == app_python_version
        # where app_python_version is the string "3.14". 3.14 != "3.14", so the
        # assertion fails on a build that is in fact correct. The same applies to
        # any value that could parse as a number, a bool or a date.
        f'python_target: "{python_version}"',
        f'built: "{datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}"',
    ])
    path.write_text(f"{header}{body}\n", encoding="utf-8")
