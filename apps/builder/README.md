# builder

A guest whose purpose is to build other applications.

It installs **no service** and publishes **no port**. It assembles the
environment a build needs — a compiler, a pinned Node, a workspace — and then
stops. Nothing runs on it except the builds you send it.

This is a `toolchain` source. The other two kinds — `package` and `repo` —
describe an application whose code comes from somewhere. This one's code comes
from nowhere, because it *is* the environment.

## What is provisioned

| | |
|---|---|
| Guest | `builder`, CT 123 |
| CPU | 6 cores |
| RAM | 24 GB |
| Swap | 4 GB |
| Disk | 60 GB |
| Toolchain | `build-essential`, `git`, `ca-certificates`, `xz-utils`, `rsync` |
| Node | 24.14.1, pinned and checksummed, at `/opt/builder/node` |
| Workspace | `/var/lib/builder/work` |
| Service | none |
| Port | none |

## Install it

```bash
appctl install builder
appctl install builder        # must report changed=0
```

The manifest key is `builder` and the role directory is `apps/builder/`.
They have to match: `appctl install` selects the role with
`--tags lxc,<key>`, and a key that is not a role name selects nothing. The
play runs, changes nothing, and reports success.

## Why 24 GB

Measured, not guessed. The build's webpack process caps its heap at 10 GB
(`NODE_OPTIONS=--max-old-space-size=10240`) and reaches about **11.5 GB RSS**
before it finishes.

A 12 GB builder was tried first. It failed in a way that named nothing
useful: the build spun with 96 MB free and 348 MB available, the guest stopped
answering SSH entirely, and `pct exec` returned

```
lxc-attach: 123: ../src/lxc/attach.c: lxc_attach: 1615
Cannot allocate memory - Failed to clone attached process
```

The play that launched the build was green. The failure looked like a network
timeout.

The role asserts the floor, so a builder that is resized by hand is checked at
install time rather than discovered during a build.

## How a build finds it

Guest hostnames do not resolve from the controller — Proxmox registers the
hostname in *its own* resolver, and the controller is not on that side of the
network. The address has to be discovered.

`common/lxc` already does that: it reads the lease with
`pct exec <vmid> -- hostname -I` and sets `ansible_host` from it. The builder
role writes the same address to a file the build script can read:

```
tmp/builders/builder.endpoint
```

```ini
host=10.0.0.80
user=root
node_dir=/opt/builder/node
work=/var/lib/builder/work
```

The file lives under `tmp/`, which is gitignored, because it is a runtime
artifact: the address is a DHCP lease and changes when the guest is rebuilt.
It is rewritten by every play, so it is never stale.

**`user` is part of the file on purpose.** A script that reads `host` and
leaves the user out gets `$USER` from ssh — which is not `root` on the
controller — and the connection fails with "Too many authentication
failures". That error looks like a credential problem and is not one.

## What the role asserts

Every assertion is about the machine, not about an install. A role that
installs a compiler and reports success has not said anything about whether a
build can run on it.

| Assertion | What it catches |
|---|---|
| `uname -m` is `x86_64` | a builder under emulation, where esbuild's Go runtime faults |
| Node reports the pinned version | a runtime the tree was not built against |
| `npm-cli.js` exists | a Node tarball that is not a Node tarball |
| ≥ 24 GB of RAM | a guest that thrashes during the build |
| ≥ 6 cores | a guest too slow to be worth having |
| ≥ 40 GB free on the workspace | a build that fails on "No space left on device" |
| `registry.npmjs.org` reachable | a build that fails at `npm ci` with a network error |

## Why it does not depend on `common/python`

`common/python` asserts that `app_port` is defined. A builder serves nothing,
so it has no port, and the assertion would fail on a guest that is otherwise
correct.

`apps/builder/meta/main.yml` declares `dependencies: []` on purpose. A role
with no dependency still runs — play 2 of `playbooks/site.yml` loops over
each host's `app_roles` and `include_role`s it — so nothing in the playbook
changes.

The consequence: a builder gets none of the framework's `app_home`, `app_dir`
or `app_venv` variables, and none of the recursive chown in the play's
post_tasks. It defines what it needs.

## The runtime is pinned in one place

Node's version and checksum live in `apps/builder/defaults/main.yml`:

```yaml
builder_node_version: 24.14.1
builder_node_sha256: 84d38715d449447117d05c3e71acd78daa49d5b1bfa8aacf610303920c3322be
```

The build script does **not** install a runtime. It asserts the builder has
one and fails with a named error if it does not:

```
FATAL: no pinned Node at /opt/builder/node
```

That is the difference between an environment that is provisioned and one that
is whatever `apt-get` returned on the day.

## npm is invoked through its JS entry

`/opt/builder/node/bin/npm` is a symlink to `npm-cli.js`, and that file
starts with `#!/usr/bin/env node`. Nothing under `/opt` is on `PATH` on a
stock guest, so the shim dies:

```
env: 'node': No such file or directory
```

Invoke the interpreter by absolute path and the entry script by path:

```bash
NPM_CLI="$NODE_DIR/lib/node_modules/npm/bin/npm-cli.js"
node "$NPM_CLI" ci --no-audit --no-fund
```

## Where it is used

`apps/omniroute` builds OmniRoute on this guest. The full story — including
why the build cannot run on the controller — is in
[../omniroute/docs/BUILDING-FROM-SOURCE.md](../omniroute/docs/BUILDING-FROM-SOURCE.md).
