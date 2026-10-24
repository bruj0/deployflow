# Building OmniRoute from source

What it takes to build `cheaper-inference/OmniRoute` at `main` into an
artifact that runs on an x86_64 Linux guest, and what fails on the way there.

This is written from the measurements, not from the theory. Every failure
listed here happened, and the symptom is what was actually observed — not what
would be expected.

## The shape of the problem

| | |
|---|---|
| Controller | arm64 macOS (Apple Silicon), 48 GB |
| Guest | x86_64 Linux, Proxmox LXC |
| Upstream | `cheaper-inference/OmniRoute`, branch `main`, version `3.8.52` |
| Commit built | `edab7c3a59bf7d6f040d581dbda3a46adbee0d57` |
| Runtime | Node `>=22.22.2 <23 \|\| >=24.0.0 <27` — pinned to 24.14.1 |
| Build recipe | `npm run build:release` |

The controller and the guest are different architectures. That single fact
causes every hard problem below.

## The platform mismatch, and why it is slow to find

A tree built on arm64 macOS carries darwin-only native binaries:

- `sharp-darwin-arm64`
- `@esbuild/darwin-arm64`
- `lightningcss-darwin-arm64`

The guest needs `sharp-linux-x64` and friends.

**`better-sqlite3` hides the problem.** It ships a prebuild for every
platform, so it loads on the guest even from a macOS build, and the tree gets
all the way to serving requests before `sharp` fails deep in a request
handler. Checking `better-sqlite3` alone would have said "the tree is fine".

The check that actually catches it is a *write* through the native binding, not
a load:

```javascript
const db = new Database(":memory:");
db.exec("CREATE TABLE t (x INTEGER)");
db.prepare("INSERT INTO t VALUES (?)").run(42);
```

## Where the build can run, and where it cannot

Three options were tried. Two failed, and they failed for different reasons.

### 1. Native build on the controller — wrong architecture

Worked. Produced a `dist/server.js` that ran locally. Useless for the guest:
the natives are darwin.

### 2. Container on the controller, emulated — failed twice

`docker run --platform linux/amd64` on the arm64 host. The architecture is
right and then two separate things go wrong.

**Failure A — out of memory.** The Next.js webpack build needs roughly 10 GB.
The emulated process sees the VM's memory, not the container's limit, and is
SIGKILLed at about 5.6 GB:

```
Next.js build worker exited with code: null and signal: SIGKILL
```

Raising the Colima VM from 8 GB to 20 GB fixes this one. The build then
proceeds normally and generates all 637 static pages.

**Failure B — the Go runtime faults under qemu.** esbuild is a **Go** binary.
Under qemu its runtime dies:

```
fatal error: receive on synctest channel from outside bubble

goroutine 3 [running]:
main.(*serviceType).sendRequest(...)
	github.com/evanw/esbuild/cmd/esbuild/service.go:192 +0x12b
...
Next.js build worker exited with code: null and signal: SIGSEGV
```

This is not a memory problem and no amount of memory fixes it. Emulation is
simply not reliable for this build.

**A third, smaller failure in the same image:** `node:24-bookworm-slim` has
`gcc` but no `g++` or `make`. `better-sqlite3` is an *optional* dependency
that compiles a native binding on install; with no toolchain its install
script fails, npm treats the optional dependency as skippable, and it is
**silently absent** from the tree. The application then falls back to another
driver or fails at the first query, and nothing says the binding is missing.

### 3. Native build on an x86_64 host — works

The Proxmox node is x86_64: the guest's exact platform. 12 CPUs, 125 GB RAM,
96 GB free.

| | emulated container | native x86_64 |
|---|---|---|
| `npm ci` | 8 min | **1 min** |
| webpack build | SIGSEGV | **637 pages, 17 s** |
| esbuild | Go runtime fault | **works** |

The build runs there as itself. No emulation, no cross-compilation.

## The build host is a guest this repository provisions

The first version of this build used the Proxmox node itself as the build
host, because it was already there and already x86_64. That works, and it is
the wrong shape: the node is shared infrastructure, and a build that reaches
11.5 GB of RSS and writes 16 GB to `/root` is a build that can take the node
with it.

So the build host is now a guest — CT 123, `builder` — provisioned by this
repository like any other application. It is created by `appctl install
builder` and it installs no service: it is a **toolchain**, an application
whose purpose is to be the environment another application is built in.

```bash
appctl install builder        # creates CT 123, installs the toolchain
sh apps/omniroute/build/omr-build-linux.sh   /Users/bruj0/Projects/Omniroute main
```

### How the build finds it

Guest hostnames do not resolve from the controller. Proxmox registers the
hostname in *its own* resolver, and the controller is not on that side of the
network — `ansible -i inventory/hosts.yml builder -m stat` fails with
"Could not resolve hostname builder". The address has to be discovered, and
`common/lxc` already does that: it reads the lease with
`pct exec <vmid> -- hostname -I` and sets `ansible_host` from it.

The build script needs the same address, and it is not the ansible
controller. So the builder role publishes it to a file both sides can read:

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

**The user is part of the address, not an argument.** The build script reads
`user` from the same file and builds `root@10.0.0.80` from it. Left out,
`ssh` defaults to `$USER` — which on this controller is not `root` — and the
connection fails with "Too many authentication failures" after the agent
offers five keys against an `authorized_keys` that holds one. That is a
confusing error for a credential problem that is not really a credential
problem.

### The toolchain is asserted, not assumed

The builder role installs `build-essential`, `git`, `ca-certificates`,
`xz-utils` and `rsync`, and a pinned Node 24.14.1 fetched with a checksum.
It then asserts the properties a build depends on:

| Assertion | What it catches |
|---|---|
| `uname -m` is `x86_64` | a builder running under emulation, where esbuild's Go runtime faults |
| Node reports the pinned version | a runtime the tree was not built against |
| `npm-cli.js` exists | a Node tarball that is not a Node tarball |
| ≥ 24 GB of RAM | a guest that thrashes during the build (see below) |
| ≥ 6 cores | a guest that builds too slowly to be worth having |
| ≥ 40 GB free on the workspace | a build that fails on "No space left on device" |
| `registry.npmjs.org` reachable | a build that fails at `npm ci` with a network error |

Each of these is a *result*, not an exit code. A role that installs a
compiler and reports success has not said anything about whether a build can
run on it.

### 12 GB was not enough, and the failure was silent

The first builder was created with 12 GB of RAM — 10 GB of heap cap plus 2 GB
of headroom, which sounds right on paper. It was not.

The build's webpack process reaches about **11.5 GB RSS** against that 10 GB
heap cap. On a 12 GB guest the arithmetic leaves nothing: the build was
observed spinning with 96 MB free and 348 MB available, and then the guest
**stopped answering SSH entirely**. The banner timed out. `pct exec` returned:

```
lxc-attach: 123: ../src/lxc/attach.c: lxc_attach: 1615
Cannot allocate memory - Failed to clone attached process
```

Even the Proxmox host could not get a shell into the container, because
cloning a process inside the container needs memory the container does not
have.

Nothing in the build's own output said "out of memory". The play that
launched it was green. The failure looked like a network problem — a
connection timeout — and it was a memory problem, three layers down.

**24 GB clears it.** The build then runs with 21 GB available and a peak RSS
of 2.7 GB in the observed sample, with the page cache holding the rest. The
role asserts the floor, so a builder resized by hand is checked at install
time rather than discovered during a build.

This is the lesson the framework already had, restated at a new scale: *a
guest that is too small does not fail at install, it fails hours later with
an error that names a memory limit rather than the guest.*

### The clone is pinned to a commit, not to a branch

The build host clones the repository itself (see the next section for why),
and the first version of the script cloned `--branch main`. `main` moves: a
push between the controller reading `HEAD` and the builder cloning produces
an artifact built from a commit the receipt does not name.

The receipt is the role's provenance check — the role refuses a tree whose
receipt does not match the checkout's `HEAD` — so a receipt that is wrong
defeats the check it exists to satisfy. The clone is now pinned:

```bash
git clone --branch "$REF" "$REPO_URL" "$BUILD_DIR"
git -C "$BUILD_DIR" fetch --quiet origin "$COMMIT"
git -C "$BUILD_DIR" checkout --quiet --detach "$COMMIT"
test "$(git -C "$BUILD_DIR" rev-parse HEAD)" = "$COMMIT"
```

The explicit fetch is needed because a shallow branch clone may not contain
the commit once the branch has moved past it, and the final `test` is there
so the build fails rather than shipping an artifact from the wrong commit.

### The toolchain lives on the builder, not in the build script

The remote build script used to install its own toolchain: `apt-get install
build-essential`, download Node, unpack it. Now it asserts that the builder
has them and fails with a named error if it does not.

That is a better shape for three reasons. The build is reproducible because
the environment is provisioned and checksummed by a role, not by whatever
`apt-get` returned on the day. The builder's Node is pinned and checksummed
in one place — `apps/builder/defaults/main.yml` — instead of two copies that
can drift. And the build script's failure message can name the actual cause,
because the check is a check and not an installation:

```
FATAL: no compiler on this build host
       the builder role installs build-essential; this host is not it
```

### npm is invoked through its JS entry, not its bin shim

`/opt/builder/node/bin/npm` is a symlink to `npm-cli.js`, and that file
starts with `#!/usr/bin/env node`. Nothing under `/opt` is on `PATH` on a
stock guest, so the shim dies:

```
env: 'node': No such file or directory
```

The build invokes the interpreter by absolute path and the entry script by
path:

```bash
NPM_CLI="$NODE_DIR/lib/node_modules/npm/bin/npm-cli.js"
node "$NPM_CLI" ci --no-audit --no-fund
```

This is the same trap the skill already records for the OpenAI CLI shims, and
it is worth stating again because it is a failure with no useful message: the
shell reports a missing `node` when the problem is that `node` exists and is
not on the `PATH`.

## What the build host needs, and why a clone rather than a transfer

Two independent reasons, both measured.

**The sha has to resolve.** `build:release` runs
`OMNIROUTE_BUILD_SHA=$(git rev-parse --short HEAD)`. A transferred tree has no
`.git`, so the sha is empty and the artifact carries no provenance at all —
`dist/BUILD_SHA` is written empty and the receipt has nothing to record.

**A tar of the controller's checkout carries macOS sidecars.** Every file on
an APFS volume may have a `._name` AppleDouble companion. They survive
`tar`, and esbuild reads them as source files:

```
src/mitm/._aliasConfig.ts:1:0: ERROR: Unexpected "\x00"
src/mitm/._childExitCleanup.ts:1:0: ERROR: Unexpected "\x00"
...
Error: Build failed with 77 errors
```

So the build host runs `git clone` itself. The repository is public, so it
needs no credential. A private one needs a token the host already holds.

## The dependency tree is not a graph you can walk

The server runs from `dist/` alone. Verified by moving the root
`node_modules` away and starting it — it served, bound its ports, and shut
down cleanly. `dist/` carries its own `node_modules` (741 MB) and its own
`.build/next` (312 MB).

The CLI is different. It needs packages that are not in `dist/node_modules`.
The first attempt computed their closure by scanning the CLI's imports. That
is fragile in three separate ways, and each one only surfaced on the guest:

| What the scan missed | Symptom on the guest |
|---|---|
| `await import("tsx/esm")` — a **dynamic** import | `Cannot find package 'tsx' imported from .../bin/omniroute.mjs` |
| the loader resolves from `src/` and `open-sse/`, not just `bin/` | `Cannot find package 'ws' imported from .../open-sse/utils/setupPolyfill.ts` |
| `@esbuild/linux-x64` is an **optional** dependency, chosen by an install script that reads `os` and `cpu` | `The package "@esbuild/linux-x64" could not be found, and is needed by esbuild` |

The third one is the reason a dependency walk can never be complete: npm
installs those packages by running a script that inspects the current
platform, not by resolving a graph. No graph walk reaches them.

**So the tree `npm ci` produced is shipped as-is.** It is correct by
construction. The cost is a 1.2 GB transfer that takes seconds on a LAN.

The CLI's own diagnostic is the check that it is right:

```
Summary: 7 ok, 43 warning(s), 0 failure(s)
```

The 43 warnings are absent optional provider CLIs (`claude`, `codex`,
`gemini` and friends). Zero failures is the signal.

## What the artifact is

```
dist/            1.0 GB   the server bundle, with its own .build/next
bin/             the CLI
node_modules/    the dependency tree, native linux-x64
```

The root `.build/` is **5.4 GB of webpack cache** and does not travel. The
build has already produced the 312 MB `dist/.build/next` that the runtime
reads.

The root `node_modules` is 4.3 GB unpacked but tarballs to 1.2 GB.

## Pinning the runtime

The `engines` field allows a range, and a runtime outside it fails at import
rather than at install:

```json
{"node": ">=22.22.2 <23 || >=24.0.0 <27"}
```

The tree is built with one Node and must run on one. Node **24.14.1** is
pinned and checksummed against the sha256 `nodejs.org` publishes in its
`SHASUMS256.txt`.

npm's own `bin` shim runs `#!/usr/bin/env node`, and nothing under
`app_home` is on `PATH` on a stock guest, so it dies with
`env: 'node': No such file or directory` before the application starts. The
interpreter is therefore named by path, and the operator-facing command is a
wrapper rather than a symlink to the package's own `bin` entry.

## The receipt is what makes `changed=0` possible

The build writes `.deployflow-build.json` into the checkout:

```json
{
  "commit": "edab7c3a59bf7d6f040d581dbda3a46adbee0d57",
  "shortCommit": "edab7c3a59",
  "version": "3.8.52",
  "ref": "main",
  "platform": "linux-amd64",
  "node": "24",
  "builtAt": "2026-10-24T16:30:24Z"
}
```

The role compares the commit in the receipt against the checkout's `HEAD` and
refuses a tree that does not match. Without it the role has nothing to
compare, and either reinstalls every time or trusts an mtime.

**The role never builds.** A role that rebuilds on every play cannot tell
"no changes" from "rebuilt".

## The rsync flags that are load-bearing

The tree is shipped with `ansible.posix.synchronize`. Two flags are not
optional, and each failure is silent:

- **`--links`** — without it a symlinked `node_modules` arrives with its
  packages but empty subdirectories, and every entry point fails with
  `ERR_MODULE_NOT_FOUND` for a package that is present.
- **`--times`** — without it `synchronize`'s own `--delay-updates` staging
  renames every file at the end of the run, giving it a fresh mtime. The next
  run sees the whole tree as different, transfers all of it again, and reports
  changed forever.

Two flags have to be **absent**, and both were found the hard way:

- **`--chown`** — the controller is macOS, whose rsync is `openrsync`, a
  compatible reimplementation that does not implement it:
  ```
  rsync: unrecognized option `--chown=omniroute:omniroute'
  ```
  The task fails outright. Ownership is set on the guest by a separate task
  instead, which works the same on every controller.

- **`--owner --group`** — these make rsync set each file's owner to the
  *controller's* uid, which is 501 on macOS and nobody on the guest. The
  chown task then sets it back, and the next run repeats both. The role
  reported `changed=2` forever on a tree that had not changed. Without them,
  rsync leaves the ownership of existing files alone and the second run is
  `changed=0`.

## The postconditions that matter

| Assert | Guards against |
|---|---|
| `dist/server.js` is on the guest | a transfer that stopped half way |
| `better-sqlite3` loads **and takes a write** | a native module built for the wrong platform |
| `omniroute doctor` is healthy | the application's own opinion of itself |
| `storage.sqlite` exists and is not empty | a running server with nothing to log in to |
| the listener is on the configured address | the process listening somewhere else |
| `/healthz` answers 200 | a service that binds a port and does not serve |
| `/v1/chat/completions` answers 401 without a key | an inference plane any device can spend |
| `/v1` answers 401 for a bad key, and names the gate | a proxy answering 401 instead of the application |
| `/api/providers` answers 401 | a dashboard serving an anonymous caller |
| the unit is enabled | a service that does not come back after a reboot |

The key gate checks are the ones worth reading. A server that serves `/v1`
without a key spends the operator's provider credentials for anyone who can
reach the port, and nothing else in the play would notice.

## Measured results

| | |
|---|---|
| Build on the builder guest | 28 min end to end, including the transfer |
| `npm ci` | 1 min (8 min emulated) |
| Transfer to the guest | ~50 s for 3.9 GB over rsync with compression |
| First install | 80 tasks, 2 changed |
| Second install | 80 tasks, **0 changed** |
| Build host | CT 123, `builder`, `10.0.0.80`, 6 cores / 24 GB / 60 GB |
| Runtime guest | CT 122, `omniroute-src`, `10.0.0.78` |
| Served version | `3.8.52`, `BUILD_SHA=edab7c3a59` |

## Reproducing it

```bash
appctl install builder        # the build host; CT 123

apps/omniroute/build/omr-build-linux.sh \
  /Users/bruj0/Projects/Omniroute main

appctl install omniroute
appctl install omniroute      # must be changed=0
```

The build script takes the checkout and the ref. It finds the build host
itself, by reading `tmp/builders/builder.endpoint` — the file the builder
role writes. There is no host argument, because a guest address is a DHCP
lease and a host argument would be a value that goes stale.
