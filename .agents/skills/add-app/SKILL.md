---
name: add-app
description: >-
  Use when adding, porting or wiring up a new application to this repository,
  or when asked "add an app", "new application", "deploy X with ansible",
  "add a service to the guest", "how do I add another app", "port <app> to this
  repo", or "scaffold a new app role". Covers the common/apps split, the
  app_name-derived contract, the meta/main.yml dependency, the target block in
  app.yaml, generating a sha256-pinned lockfile for the guest's Python, and the
  postconditions a role must carry so a green play cannot hide a broken
  service. Also covers an application with no Python wheel — a JavaScript tree
  built on the controller and shipped — which the role installs instead of
  building. Do NOT use for adding a guest to inventory, for choosing Proxmox
  templates, for writing a new delivery target, or for anything outside this
  repository.
---

# Add an application

An application is **one directory** under `apps/`. Nothing in `common/` and
nothing in `playbooks/site.yml` changes when you add one. If your change touches
either, stop — you are about to put application knowledge in the framework, which
is the one thing this layout exists to prevent.

## The two roots

| Root | Knows about | Contains |
|---|---|---|
| `common/` | guests, never applications | `lxc/` (create), `python/` (contract) |
| `apps/` | its own application, never guests | `example-app/`, and whatever you add |

`roles_path = common:apps` in `ansible.cfg`. An application may not require
anything from another application.

## The contract: one variable

`common/python/defaults/main.yml` derives **everything** from `app_name`:
`app_user`, `app_home`, `app_dir`, `app_venv`, `app_state_dir`,
`app_service_name`. You set `app_name` and every path follows.

Two values have no derivation and you must set them: `app_port` and anything else
unique to your app.

## Process

### 1. Scaffold

```bash
mkdir -p apps/<name>/{defaults,tasks,meta,files}
```

### 2. `defaults/main.yml` — the whole interface

```yaml
---
app_name: myapp          # the ONLY required value
app_port: 9000           # no derivation exists for this
app_service_name: myapp  # defaults to app_name; set only if they differ
```

### 3. `meta/main.yml` — declare the dependency, not the playbook

```yaml
---
dependencies:
  - role: python
```

Ansible runs a role's dependencies before its own tasks, so `common/python`
prepares the guest first. **This is what lets a new app avoid editing any
playbook.** Verified: including an application runs all nine `common/python` tasks with
`python` named nowhere in the playbook.

### 4. Build artifacts

Only for `kind: repo`. For `kind: package` the wheel already exists on PyPI and
`appctl build myapp` says so and exits non-zero rather than failing silently.

```bash
appctl build myapp                # wheel + pinned lockfile + BUILDINFO
appctl build myapp --check        # verify committed artifacts, build nothing
```

`defaults.python_version` in `app.yaml` must match the interpreter the lockfile
was compiled for. `appctl build` refuses any lock entry without a sha256, which
is the same condition `--require-hashes` enforces at install time — caught at
build time it names the package; caught at install time pip names nothing.

What is committed: the lockfile only. The wheel and BUILDINFO are gitignored,
because the wheel changes on every commit and BUILDINFO records the sha256 the
role checks before installing anything.

### 5. Configuration

If the application has settings, write them as YAML. No code.

```yaml
# app.yaml
config:
  target: "{{ app_state_dir }}/config.json"
  files: [apps/myapp/config.yml]
  vars: { port: 9000 }
```

```yaml
# apps/myapp/config.yml
server: { port: "{{ port }}", token: "${MYAPP_TOKEN}" }
```

`appctl render` turns that into JSON under `tmp/rendered/<app>/`, and
`common/python` copies it to `target`. Set `app_config_target` in the role's
`defaults/main.yml` so the framework knows where to put it.

Expect the file to be **partial**. Applications merge their own defaults into
whatever they are given — many applications do it themselves — so only what
differs from a default belongs in the YAML.

**Never write a script that runs on the guest to produce configuration.** That
was tried here: a script merged settings into config.json on every play. The
application already does that merge itself, so the script was a second
implementation of the application's behaviour, shipped to every guest, free to
drift from it. A two-key config.json boots the service.

Use `{{ name }}` for values `appctl` substitutes on the controller, and `${NAME}`
for secrets the application resolves at runtime from its EnvironmentFile. The
second form is what keeps a secret out of the repository, the rendered file and
the logs.

### 6. `tasks/main.yml`

Copy the closest existing role. `apps/example-app/` is the reference: a wheel
install, a systemd unit, a config merge, secrets, and the postconditions that
prove the service answers.

Keep every postcondition — see below. They are the point of the role, not
boilerplate.

### 7. Name the target

Every application says where it runs:

```yaml
# app.yaml
applications:
  myapp:
    target:
      kind: proxmox_lxc      # the only implemented target
      options:
        hostname: myapp
        vmid: 121
        bind_host: 127.0.0.1  # 0.0.0.0 exposes the service to the network
```

`kind` must be a registered target. `vm` and `docker_vm` are planned and fail
before anything is built, so an application that names one cannot be deployed
yet. Run `appctl targets` to see the current list.

`options` belongs to the target. The shared schema does not read inside it, so
a misspelled key there is caught by the target, not by the schema.

### 8. Wire it up and deploy

Configuring the application in `app.yaml` does **not** deploy it. It is deployed
when its name is in some guest's `app_roles` — that is what `appctl list` shows
in its DEPLOYED column.

Add `<name>` to the guest's `app_roles`:

```yaml
# inventory/group_vars/<group>.yml
app_roles:
  - myapp
```

The guest itself is one block in `inventory/hosts.yml` under `lxc`, plus a group
that holds it. **Name the group without hyphens, and do not let a group and a
host share a name** — Ansible rejects both with a warning that is easy to miss
among a long inventory. `ex` and `example-app` is the pair to copy.

The guest block carries `ansible_user`, `lxc_vmid` and `lxc_hostname`. Leave the
address out: Proxmox leases one and `common/lxc` reads it back.

### 9. Secrets

Declare the names in `app.yaml` under the application, then:

```bash
appctl secrets init                    # writes .env (0600) and .env.example
appctl secrets set myapp_api_key '...'  # or: appctl secrets set NAME --generate
appctl secrets check                   # exit 1 while any is unset
```

`appctl install` refuses to start while a required secret is absent, and names
the missing keys. Never put a secret in `app.yaml`, in `group_vars`, or in a
role default.

### 10. Prove it

```bash
appctl install myapp     # first run: expect several changed
appctl install myapp     # second run: MUST be changed=0

appctl list                     # DEPLOYED must show myapp, not "no guest"
```

**The second run reaching `changed=0` is the acceptance test.** A role that
reports `changed` every run reinstalls the tree each time and tells you nothing
about its own state. See "Sentinels" below — a broken sentinel is the most
common reason this test fails.

Then inject drift and confirm one re-run repairs it:

```bash
rm -f /opt/myapp/app/venv/bin/myapp     # deleted entrypoint
systemctl stop myapp && systemctl disable myapp
rm -rf /opt/myapp/state                  # mutable state must survive
appctl install myapp
```

For a shipped-tree application the entrypoint lives in the tree instead of the
venv — delete a file the controller still has and check that rsync puts it back.
Then run the play once more and require `changed=0` again: drift repair that
leaves the next run reporting changes has fixed the symptom and not the
sentinel.

## An application that is not a Python wheel

Every step above assumes a wheel. An application that ships a JavaScript tree —
or any prebuilt artifact with no `pyproject.toml` — needs the role to do the
installing. `apps/deepseek-harness/` is the worked example, and it is the second
kind of application this repository has.

**`appctl build` will fail on it and that is expected.** `build` exports a git
tree, optionally builds a `webui` bundle, then runs `uv build --wheel`. There is
no path to "copy this tree". The `app.yaml` entry still needs a `build` block,
because the schema requires one for `kind: repo` and `appctl list` prints it as
the SOURCE column — say in a comment that it is inert, so the next reader does
not go looking for a wheel. `appctl install` reads none of it: install runs the
play and nothing else.

**The build stays an operator act on the controller.** `pnpm install` and
`pnpm run build` are run by a person, not by the role, because a role that
rebuilds on every play cannot tell "no changes" from "rebuilt". The role's job is
to refuse to ship a tree whose install or build is older than its sources:

```yaml
- name: Read the newest source file in the checkout
  # git ls-files, not a find: lib/, dist/ and node_modules are gitignored, so
  # this is exactly the set that can invalidate the build, with no build output
  # counted as an edit.
  ansible.builtin.shell:
    cmd: git -C {{ dsh_local_source | quote }} ls-files -z | xargs -0 -r stat -c %Y -- | sort -n | tail -1
  delegate_to: localhost
  become: false
```

Fail with the exact command to run. Comparing ages is all that is available —
there is no build receipt to check against — so use `stat`'s `mtime`, not
`mtime_epoch` (see Gotchas).

**Shipping the tree** is one `ansible.posix.synchronize` from the controller:

```yaml
rsync_opts: >-
  --links --times --owner --group --chown={{ app_user }}:{{ app_user }}
  --exclude=.git --exclude=**/tests
delegate_to: localhost
```

Each flag is load-bearing and each failure is silent — see Gotchas for what
happens without them. Exclude tests and fixtures wholesale: they are the largest
block of source and nothing at run time resolves through them. Use `delete:
true` so the guest cannot keep a file the controller dropped.

**The runtime goes beside the tree, not inside it.** `app_dir` is the install
root and `common/python`'s venv already lives in it; put the shipped tree under
it (`app_dir/src`) so the two never collide. `app_state_dir` stays where the
framework put it, and nothing in the shipped tree is written at run time.

**A runtime the template does not ship** goes in as a pinned, checksummed
release that the role verifies and unpacks — `get_url` with
`checksum: "sha256:…"`, then `unarchive`. Both halves of that have a gotcha
below. Read the installed version back and assert it, rather than trusting a
stamp file beside it.

**Guest shape cannot be set per application.** `app.yaml`'s `guest:` block
accepts only `hostname`, `vmid`, `ip_optional` and `bind_host`; anything else is
rejected as an unknown key. Cores, memory and rootfs come from `app.yaml`
`defaults` or from the guest's own `group_vars`, which is the only per-guest
override.

**Never run two installs against one guest.** They share apt and dpkg, and the
second fails on `Could not get lock /var/lib/dpkg/lock-frontend`, which names
nothing about what actually collided.

## Postconditions: non-negotiable

Every role must assert the result, not the exit code. These four caught real
bugs that a green play hid:

| Assert | Guards against |
|---|---|
| the CLI runs (`myapp --version`) | a deleted entrypoint with the package intact |
| the package imports (`python -c "import myapp"`) | a half-installed tree that still has a console script |
| the app's own data exists (a row, a file) | a running server with nothing to log in to |
| `ss -ltn` shows the requested address | the process listening somewhere you did not ask for |

**Assert the authority the process reports, not the one you asked for.** The
strongest form of the last row: read the listener's own URL from its log, and
fail unless it carries the address the role configured. Rebuilding the URL from
a variable asserts what the role already believes and proves nothing about what
is running. Do that read inside the guest, in one shell step, when the value is
a credential — the launch token of an authenticated service is a live credential
for the life of the process, and a task that interpolates it puts it in the
controller's log.

**Wait for the service before asserting on it.** `systemd` returns from `start`
when it accepts the job, which is seconds before a composed application binds.
`wait_for` on the port, and on the state file, so the assertions that follow read
a running service. An empty `ss` reading during boot is indistinguishable from
address drift, and a drift check that cannot tell the difference restarts a
service that was starting correctly.

**Do not use `--force` on an app's init command.** A failed init leaves a
half-written state directory and refuses to run again; `--force` destroys the
database on top of that. Recovery means deleting the state directory.

**Pass secrets through the environment, never `argv`.** `ps` is world-readable
on the guest.

## Sentinels: the four ways to break `changed=0`

Every one of these shipped green. All were found by running the play twice and
asking why the second run still reported changes. Read this section before
writing the checks in `tasks/main.yml`.

**A sentinel that can never succeed is worse than no sentinel.** It looks like a
guard, it passes review, and it silently keeps a gate permanently open so the
role reinstalls itself forever.

| Trap | Why it is always wrong |
|---|---|
| `'changed' in stdout` | matches `"unchanged"`. Use `== 'changed'` after a `trim`. |
| an import of a name that does not exist | the class may live in a different module. Verify the import on the guest before trusting it. |
| a glob consumed by `next()` twice | `next(g, None) and (next(g) / 'f')` takes the only match on the first call and raises `StopIteration` on the second. Materialise with `list()`. |
| a `changed_when` test that is true when nothing changed | always compare the whole value, not a substring |

### Greedy regexes and block scalars

Both of these cost real time and neither raises an error.

```yaml
# WRONG: greedy, matches up to the LAST colon, so '127.0.0.1:18790' -> '18790'
| map('regex_replace', '^.*:', '')

# WRONG: a YAML block scalar is literal, so Jinja reads \d as an escaped
# backslash and the regex matches a literal backslash. Strips nothing.
| map('regex_replace', ':\\d+$', '')

# RIGHT: strip by the numeric port, then trim
| map('trim')
| map('regex_replace', ':\d+$', '')
```

A trailing newline survives unless you `trim`, and a trimmed value still fails
to equal an untrimmed expectation. Check both sides.

## Gotchas, all measured on this host

- **The framework must have no default for `app_name` or `app_port`.** A role's
  `meta` dependency has its defaults applied *after* the including role's own,
  so a default in `common/python` silently overrides the application's and the
  guest gets the wrong paths. It stayed hidden while there was one application,
  because its name happened to equal the default. Do not add one back.
- **`.j2` files belong in `templates/`, not `files/`.** `ansible.builtin.template`
  resolves `src` from `templates/`; in `files/` it fails with "Could not find or
  access". The `.j2` suffix is the tell.
- **`playbook_dir` is `playbooks/`, not the repo root.** Use `lxc_repo_root` from
  `group_vars/all` for controller paths. A role default will not do: it does not
  exist on `localhost`, which is where `delegate_to: localhost` puts the lookup.
- **`app_name` must be a valid path segment.** It is interpolated into
  `app_home`, `app_dir` and `app_venv` without sanitising.
- **`virtualenv_command` is a command line, not a path.** Use
  `"{{ app_venv }}/bin/python -m venv"`. Hardcoding `/usr/bin/python3` lets the
  interpreter and the venv drift apart silently.
- **The pip module takes `requirements:`, never `name:`** for a lockfile path.
  `name` parses its argument as a requirement and rejects a path.
- **`--no-deps` is required** alongside `--require-hashes`. With hashes present
  pip cannot resolve, so every package must already be named in the file.
- **Gate the install on the lockfile changing or the package being absent.**
  The pip module reports `changed` every time it runs, which is what stops a
  settled play from reinstalling 200 wheels on each run.
- **Playwright's cache is `chromium-1243`, not `chromium_headless_shell-*`.**
  A sentinel using the headless-shell name never matches and re-downloads 115 MB
  every run. Assert on a versioned directory, not on the cache root.
- **`playwright install-deps` takes no `--with-deps`** and reports `changed` on
  every invocation. Gate it on a library it must install, e.g.
  `dpkg-query -W -f=${Status} libnss3`.
- **Pin `PLAYWRIGHT_BROWSERS_PATH`** to the account the service runs as, or the
  browser lands in root's cache where the service cannot see it.
- **A venv left by an older template is a trap.** It installs wheels for the
  interpreter it was built with and fails at the first compiled import.
  `common/python` asserts the version and fails loudly; keep that check.
- **`unarchive` defaults to the controller, not the guest.** Fetching a tarball
  with `get_url` and then unpacking it needs `remote_src: true`, or the module
  looks for the path on the controller and fails with "Could not find or access
  … on the Ansible Controller" while the file sits on the guest. `remote_src`
  then makes it refuse a `dest` that does not exist, so create the directory
  first — the two errors name the wrong thing.
- **`synchronize` skips symlinks and resets mtimes.** Both are fatal for a
  JavaScript tree and neither is reported. Without `--links`, a pnpm `node_modules`
  arrives with its packages but an empty `node_modules/@deepseek-ai/`, and every
  entry point fails `ERR_MODULE_NOT_FOUND` for a package that is present.
  Without `--times`, `synchronize`'s own `--delay-updates` stages each file under
  a temporary name and renames it at the end of the run, which gives every file a
  fresh mtime — so the next run's quick check sees the whole tree as different and
  transfers all of it again, reporting `changed` forever. The play never settles.
- **`stat` has no `mtime_epoch` on ansible-core 2.16.** It returns float seconds
  in `mtime`. Asking for the removed key fails the conditional with "'dict
  object' has no attribute 'mtime_epoch'", which reads like a missing file rather
  than a missing field.
- **The stock Ubuntu template ships no `curl`.** A shell assertion that probes the
  service with it retries to its timeout against "command not found" and reports
  a broken service that is serving perfectly well. Install it in the role, or use
  `ansible.builtin.uri`.
- **A runtime installed under a version directory is not on `PATH`, and its own
  bin shims then fail.** Every `bin/*` entry of such a runtime is
  `#!/usr/bin/env node`, so running `{{ dsh_node_dir }}/bin/corepack` dies with
  `env: 'node': No such file or directory` before the tool starts. Invoke the
  interpreter by absolute path on the tool's JS entry instead. The same applies
  to any `package.json` `bin` a guest should expose: a symlink to it runs
  whatever `node` `PATH` finds, which on a stock guest is nothing at all — write
  a wrapper that names the pinned interpreter, `DSH_HOME` and the state the tool
  expects.
- **Read a version from the artifact that was built, not from a second copy of
  it.** A package manager version belongs in the project's own `packageManager`
  field; a guest that hard-codes it can end up holding a tool the tree was never
  built with, and the mismatch surfaces at use, not at install.
- **A host with no fixed address cannot be asserted by name.** Assert the address
  the process reports it bound, not a configured one, or the assertion tests the
  inventory rather than the service.

## Adding a guest, not an app

Different task. One block in `inventory/hosts.yml`, no address — Proxmox leases
one and `common/lxc` reads it back:

```yaml
lxc:
  hosts:
    my-app:
      ansible_user: root
      lxc_vmid: 121
      lxc_hostname: my-app
```

Read `docs/PLAN-proxmox-lxc-ansible.md` before changing anything in
`common/`. Section 8 is a list of failures that passed review and only showed up
under test; several of them produce a green play and a broken service.
