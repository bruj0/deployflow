# AGENTS.md

How to work in this repository. Read this before changing anything.

## What this is

Application delivery to a deployment target. A stock Ubuntu template goes in,
Ansible configures it, the application runs under systemd with a sha256-pinned
dependency tree. No container image is ever built.

`appctl` is the tool: it builds artifacts, delivers them through the named
target, and manages secrets. It is the intended entry point for everything.

## The two boundaries that matter

```
app.yaml        configuration: applications, targets, builds, secrets
common/         framework. Names no application, anywhere.
  lxc/          pct create from a stock template, DHCP, address discovery
  python/       toolchain, service account, venv — the contract
apps/           one directory per application
  example-app/  the sample used by the quickstart
  builder/      a toolchain: a guest that exists to build other applications
src/appctl/     the appctl package
  targets/      where an application runs; proxmox_lxc is the only one built
inventory/      the operator's own guests. GITIGNORED, and absent on a fresh
                clone; inventory.example/ holds the templates.
```

`roles_path = common:apps`. A role's `meta` dependency declares what it needs,
so a new application never edits a playbook.

**An application must not require anything from another application, and
`common/` and `src/appctl/` must never name one.** If your change makes the
framework aware of a specific app, it is in the wrong place — put it in
`apps/<name>/`.

**The shared code must not name a target.** `pct`, the container ID, the
template, the storage, the bridge and the guest network live in
`src/appctl/targets/proxmox_lxc.py`. A module outside `targets/` that mentions
one of them is in the wrong place. A new target is a new file under
`targets/`, and it does not change `config.py`.

**A role that installs no service does not depend on `common/python`.** That
dependency is what asserts `app_port` is defined, and a toolchain serves
nothing. A role with no dependency still runs — play 2 loops over each host's
`app_roles` and `include_role`s it — so no playbook changes. See
`apps/builder/meta/main.yml`.

**A toolchain's product is a machine that can build, so its role asserts the
machine.** Not the exit code of the install commands. A guest that is too
small does not fail at install — it fails hours later, during a build, with
an error that names a memory limit rather than the guest.

## Commands

```bash
uv tool install --editable .       # or: uv pip install -e .

appctl list                        # every app, its target, and last install
appctl targets                     # implemented targets, and the planned ones
appctl secrets check               # required secrets present? exit 1 if not
appctl render                      # YAML -> JSON, without deploying
appctl build example-app           # wheel + pinned lockfile + BUILDINFO
appctl install example-app         # deliver one application; the name is positional
appctl install                     # valid only when exactly one is configured
```

The application name is a positional argument, not a flag. Flags follow it, and
anything after a bare `--` is passed to ansible-playbook untouched:

```bash
appctl install example-app -q                # terse
appctl install example-app --forks 4         # guests at once
appctl install example-app -- --check --diff # straight to ansible-playbook
```

Use the `--` form for anything the tool also defines. Passing `--quiet` without
it does nothing useful, because ansible-playbook has no such option.

Run logs are kept and never pruned. Each run writes `commands.txt`,
`log.txt`, `recap.txt`, `errors.txt` and `meta.txt` under `tmp/runs/TIMESTAMP/`.
Read `commands.txt` first; `log.txt` is mostly SSH plumbing.

`docs/architecture.md` lists the invariants. Read it before changing a role:
several of them are failures that passed review and only showed up under test,
and several produce a green play and a broken service.

## The tool

The tool is `appctl` in `src/appctl/`. Its own documentation is in `docs/`, and
it takes precedence over this file for anything about the tool:

| | |
|---|---|
| [docs/README.md](docs/README.md) | the index |
| [docs/architecture.md](docs/architecture.md) | how it fits together, and where the target boundary is |
| [docs/configuration.md](docs/configuration.md) | every key in `app.yaml` |
| [docs/build-pipeline.md](docs/build-pipeline.md) | the two build paths |
| [docs/rendering.md](docs/rendering.md) | how YAML becomes the configuration the guest reads |
| [docs/secrets.md](docs/secrets.md) | how a secret reaches the guest |
| [docs/targets.md](docs/targets.md) | the target interface, and how to add a target |

## Adding an application

Read [`.agents/skills/deployflow-add-app/SKILL.md`](.agents/skills/deployflow-add-app/SKILL.md)
first. It has the full process, the postconditions a role must carry, and the
sentinel traps.

The short version: one directory under `apps/`, an entry in `app.yaml`, and
`changed=0` on the second `appctl install`. Nothing in `common/` or
`playbooks/` changes.

## Rules that are not stylistic

**A provider may need no secret at all.** Some model providers authenticate with
OAuth: the guest holds a token in its own state directory, and nothing secret
belongs in this repository for them. An assertion that demands an API key for
an OAuth provider is a wrong requirement, not a safe one — it blocks a run that
is correct. Read the application's `defaults/main.yml` before changing that.

**Secrets.** `.env` at the repo root, gitignored, mode 0600, written by
`appctl secrets set`. `NAME value` and `NAME --generate` are both supported;
the bare form prompts. `.env.example` is
committed and lists which keys are required. Never put a secret in `app.yaml`,
in `group_vars`, or in a role default. Never pass one in `argv` — it is visible
in `ps`. `appctl` redacts password-shaped values from the logs it writes.

**`ansible-vault` is gone.** The vault file this repo used was encrypted with a
throwaway password that was then deleted, which made it undecryptable and
unrecoverable. Do not reintroduce it.

**Guests get a DHCP lease, not an address.** Proxmox's SDN zone hands one out and
registers the hostname; `common/lxc` reads it back with
`pct exec <vmid> -- hostname -I` and sets `ansible_host`. A statically addressed
Ubuntu 26.04 guest comes up with no IPv4 at all and `systemd-networkd` exiting
`243/CREDENTIALS`. Do not add an address to the inventory.

**`appctl install` refuses to guess.** With several applications deployed it
stops and prints the exact command to run. Configuring an application does not
deploy it — only an `app_roles` entry does, and `appctl list` shows which is
which. Secrets are checked for the selected application only; requiring them all
up front lets one you are not deploying block one you are.

**`appctl install X` also runs the `lxc` tag.** Play 1 is what discovers
the lease; without it play 2 cannot resolve the guest. It is guarded by a stat,
so on an existing guest it costs a few no-op tasks.

**Configure with YAML, not with code.** An application's settings live in
`apps/<name>/config.yml` and are rendered on the controller by `appctl render`,
which `install` runs for you. `common/python` copies the result to
`app_config_target`. Do **not** add a script to `files/` that runs on the guest
to produce configuration: the application merges its own defaults into whatever
it is given, so a second implementation is redundant and free to drift. Use
`{{ name }}` for controller substitution and `${NAME}` for runtime secrets; the
second form is what keeps a secret out of the repository.

**Never round-trip `app.yaml` through a YAML dumper.** It carries a comment
above nearly every setting, explaining why it is what it is, and `yaml.safe_dump`
destroys all of them — measured, 18 comment lines in and 0 out. Anything that
writes configuration must edit the slice it owns and leave the rest
byte-identical. That is what `src/appctl/state.py` does: it rewrites only the
`state:` block, and refuses rather than guesses when it cannot find it.

**`state:` is tool-written.** A build or an install fills it in. Do not hand-edit
it, and do not add sections to it: only `built` and `installed` are accepted, and
anything else would be silently dropped by the next write. It lives in a committed
file, so every run is a diff — that is the trade for having the record next to
the thing it describes.

**Unknown config keys are an error.** `app.yaml` is strictly validated on
purpose: a misspelled key would otherwise be dropped and the service would come
up on the wrong port.

## Sentinels: the trap that wastes the most time

Every task that checks a precondition is a sentinel, and a sentinel that can
never succeed is worse than none — it looks like a guard and silently keeps a
gate permanently open, so the role reinstalls itself on every play.

All of these shipped green here and were found only by running the play twice:

| Trap | Fix |
|---|---|
| `'changed' in stdout` | matches `"unchanged"`. Use `== 'changed'` after `trim` |
| importing a name that does not exist | check the import on the guest before trusting it |
| a glob read by `next()` twice | `StopIteration`. Materialise with `list()` |
| `regex_replace '^.*:'` | greedy: keeps the port, not the host |
| `':\\d+$'` in a YAML block scalar | block scalars are literal; Jinja reads `\\d` as an escaped backslash and matches nothing |

The acceptance test for any role change is `changed=0` on a second
`appctl install`. If it reports changes, one of the above is the reason.

## Verified state

Measured on Proxmox VE 9.2.3:

- Base Ubuntu 26.04, Python 3.14.4
- `appctl install <app>` → a settled run reports `changed=0`
- `appctl build <app>` → byte-identical wheel for the same commit

These are observations from one node, not a benchmark. Do not quote them as a
comparison with another deployment system.

Drift repair in one re-run: deleted entrypoint, stopped and disabled unit,
deleted state directory. A first settle after a real change takes longer,
because the built tree crosses the link.

## Before you commit

- `ansible-playbook --syntax-check playbooks/site.yml`
- `appctl secrets check` — a run needs these
- `git status` must not show `.env`, `tmp/`, `apps/*/files/dist/`, `tools/`, or
  `inventory/`
- `appctl install <app>` twice: the second must be `changed=0`
- `git diff app.yaml` shows only the `state:` block changed, never the comments
- `pytest tests` passes

## The inventory is not in git

`inventory/` is gitignored. It names the operator's own Proxmox host, the guest
IDs and the addresses of machines only they can reach.

Nothing in it is a credential, and none of it is public either — which is
exactly why it does not belong in a repository that a stranger can clone. The
committed templates are in `inventory.example/`, and a fresh clone creates the
real inventory from them:

```bash
mkdir -p inventory/group_vars
cp inventory.example/hosts.yml inventory/hosts.yml
cp -r inventory.example/group_vars/. inventory/group_vars/
```

Two things follow from the directory being absent on a fresh clone, and both
are expected rather than broken:

- `appctl list` reports every application as "no guest", because it finds no
  `app_roles`. That is the truth about a clone with no inventory.
- The CI syntax check runs against `inventory.example/hosts.yml`, which
  exercises the same playbook and the same role paths.

**Do not commit `inventory/`.** It is in `.gitignore`, so `git add inventory/`
would need `-f` — and if a file from it ever lands in a commit, treat it as a
leak. The host names and guest IDs are not secret, but they are not public.