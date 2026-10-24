# omniroute

OmniRoute, **built from a source checkout**, served by one systemd unit on one
guest. Nothing OmniRoute-related runs on the controller except the build: no
checkout is read during the play except to check a receipt, and no bundle is
produced there.

This is the from-source shape. The published-release shape — one pinned npm
version, `npm install` fetches it — is a different role and is not this one.

This application works. It is not a sample: it deploys, it verifies, and a
second install reports `changed=0`. It is also the harder of the two shipped
shapes, because it is the one an agent is most likely to get wrong — the build
cannot run where the agent is, and the role has to refuse a stale tree rather
than rebuild one.

## What is deployed

| | |
|---|---|
| Guest | `omniroute-src`, CT 122 |
| Source | `bruj0/OmniRoute` at `main`, resolved to a commit |
| Build | Linux amd64, on the `builder` guest — never on the controller, never emulated |
| Runtime | Node 24.14.1, pinned and checksummed, under `app_home/node` |
| Unit | `omniroute.service` |
| Port | 20128 — dashboard, `/v1` proxy, MCP/A2A and management API together |
| Port | 20132 — the realtime dashboard WebSocket, published to match |
| State | `/opt/omniroute/state`, never touched by a reinstall |
| Install root | `/opt/omniroute/app/src`, beside the inert Python venv |
| Secrets | five, all generated, none ever in `argv` |

## Why the build runs on its own guest

The controller is arm64 macOS. The guest is x86_64 Linux. A tree built on the
controller carries **darwin-only** native binaries — `sharp-darwin-arm64`,
`@esbuild/darwin-arm64`, `lightningcss-darwin-arm64` — and the guest's Linux
builds need `sharp-linux-x64`.

`better-sqlite3` hides this: it ships a prebuild for every platform, so it
loads on the guest even from a macOS build. `sharp` and `esbuild` do not, and
they fail much later as an unrelated error deep in a request handler.

So the build runs on a **native x86_64 build host**. That host is a guest this
repository provisions for the purpose: `apps/builder/`, a `toolchain`
application with 6 cores, 24 GB of RAM and a pinned Node.

```bash
appctl install builder        # the build host; CT 123

apps/omniroute/build/omr-build-linux.sh \
  /Users/bruj0/Projects/Omniroute main
```

The build script finds the host itself, by reading
`tmp/builders/builder.endpoint` — the file the builder role writes. There is
no host argument: a guest address is a DHCP lease and a host argument would
be a value that goes stale.

The settings match the project's own CI recipe
(`.github/workflows/build.yml`): `ONNXRUNTIME_NODE_INSTALL=skip`,
`OMNIROUTE_USE_TURBOPACK=0`, and a 10 GB heap cap.

### Why not a container on the controller

A container with `--platform linux/amd64` on an arm64 host gets the
architecture right and then falls over, in two separate ways:

| Failure | Cause |
|---|---|
| `Next.js build worker exited with code: null and signal: SIGKILL` at ~5.6 GB | the emulated process sees the VM's memory, not the container's limit. Raising the VM to 20 GB fixes this one. |
| `fatal error: receive on synctest channel from outside bubble` / `SIGSEGV` | esbuild is a **Go** binary, and the Go runtime faults under qemu. No amount of memory fixes this. |

The second one is the real reason. Emulation is not reliable for this build,
so the build host is native.

It was the Proxmox node at first, because that machine was already x86_64 and
already there. That is the wrong shape: the node is shared infrastructure,
and a build that reaches 11.5 GB of RSS and writes 16 GB to `/root` is a build
that can take the node with it. So the build host is a guest now, provisioned
like any other application.

### Why the build host clones the fork

The build host runs `git clone` itself rather than receiving a transferred
tree, for two measured reasons:

- **`git rev-parse --short HEAD` has to resolve.** The build reads the sha
  from git, and a transferred tree has no `.git`, so the artifact carries no
  provenance at all.
- **A tar of the controller's checkout carries macOS AppleDouble sidecars**
  (`._aliasConfig.ts` and hundreds like it). esbuild reads them as source
  files and the build fails with 77 errors:
  `src/mitm/._aliasConfig.ts:1:0: ERROR: Unexpected "\x00"`.

And it clones a **commit**, not a branch. `git clone --branch main` resolves
`main` at clone time, and `main` moves: a push between the controller reading
`HEAD` and the builder cloning produces an artifact built from a commit the
receipt does not name — which defeats the provenance check the receipt exists
to satisfy.

```bash
git clone --branch "$REF" "$REPO_URL" "$BUILD_DIR"
git -C "$BUILD_DIR" fetch --quiet origin "$COMMIT"
git -C "$BUILD_DIR" checkout --quiet --detach "$COMMIT"
test "$(git -C "$BUILD_DIR" rev-parse HEAD)" = "$COMMIT"
```

The explicit fetch is needed because a shallow branch clone may not contain
the commit once the branch has moved past it, and the final `test` is there so
a wrong commit fails the build rather than shipping.

### Why the dependency tree is not reconstructed

The CLI's dependency closure was first computed by walking the CLI's imports.
That is fragile in three ways, and each one only showed up on the guest:

| Miss | Symptom on the guest |
|---|---|
| `await import("tsx/esm")` — a dynamic import | `Cannot find package 'tsx'` |
| the loader resolves from `src/` and `open-sse/`, not just `bin/` | `Cannot find package 'ws'` |
| `@esbuild/linux-x64` is an *optional* dependency chosen by an install script | `The package "@esbuild/linux-x64" could not be found, and is needed by esbuild` |

So the tree `npm ci` produced is shipped as-is. It is correct by
construction.

## The receipt

The build writes `.deployflow-build.json` into the checkout:

```json
{
  "commit": "edab7c3a59bf7d6f040d581dbda3a46adbee0d57",
  "shortCommit": "edab7c3a59",
  "version": "3.8.52",
  "ref": "main",
  "platform": "linux-amd64",
  "node": "24",
  "builtAt": "2026-10-24T15:35:29Z"
}
```

The role reads it and refuses to ship a tree whose commit is not the commit in
the checkout. This is what makes the second run reach `changed=0`: without a
receipt the role has nothing to compare and either reinstalls every time or
trusts an mtime.

**The role never builds.** A role that rebuilds on every play cannot tell "no
changes" from "rebuilt". The build is an operator act, and the role's job is
to refuse.

## What the role refuses

Each of these is a refusal with a message that names what to do, not a
warning:

| Refusal | Why |
|---|---|
| no checkout on the controller | the role ships a tree, it does not fetch one |
| the checkout is not on the requested ref | two plays that both build `main` produce two different artifacts |
| the receipt names a different commit | the tree is not the one that was asked for |
| `dist/server.js` does not exist | a checkout without a build looks exactly like a build that failed |
| the tree did not arrive on the guest | the transfer is the part most likely to fail quietly |
| `better-sqlite3` does not load | the usual cause is a tree built on the wrong platform |

## Postconditions

| Assert | Guards against |
|---|---|
| `dist/server.js` is on the guest | a transfer that stopped half way |
| `better-sqlite3` loads and takes a write | a native module built for the wrong platform |
| `omniroute doctor` is healthy | the application's own opinion of itself |
| `storage.sqlite` exists and is not empty | a running server with nothing to log in to |
| the listener is on the address the role configured | the process listening somewhere else |
| `/healthz` answers 200 | a service that binds a port and does not serve |
| `/v1/chat/completions` answers 401 without a key | an inference plane any device on the network can spend |
| `/v1` answers 401 for a bad key, and names the gate | a proxy answering 401 instead of the application |
| `/api/providers` answers 401 | a dashboard serving an anonymous caller |
| the unit is enabled | a service that does not come back after a reboot |

The key gate checks are the ones worth reading. A server that serves `/v1`
without a key spends the operator's provider credentials for anyone who can
reach the port, and nothing else in the play would notice.

## Quickstart

The guest takes a DHCP lease, so its address moves. `appctl install` prints
it.

```bash
# The build host. A guest, provisioned like any other application.
appctl install builder

# Build it for the guest's platform, on that host. This is the slow part —
# about 28 minutes for a 2 GB JavaScript tree.
apps/omniroute/build/omr-build-linux.sh \
  /Users/bruj0/Projects/Omniroute main

# Deploy it.
appctl install omniroute

# Second run must be changed=0.
appctl install omniroute
```

The checkout path above is the one this was developed against. Point the role
at yours in `inventory/group_vars/omrsrc/main.yml`:

```yaml
om_local_source: /path/to/OmniRoute
```

The role ships a tree; it does not fetch one. A missing checkout is refused
with a message saying so, not with a failed transfer.

Then:

```bash
curl -s http://<guest>:20128/healthz        # -> ok
```

`ok` means ready. A `503` means the server is still starting; anything else
means read `journalctl -u omniroute -n 50` on the guest.

## The secrets

Five credentials, all required, none of them ever in `argv`:

```bash
appctl secrets set omniroute_jwt_secret            --generate
appctl secrets set omniroute_api_key_secret        --generate
appctl secrets set omniroute_storage_encryption_key --generate
appctl secrets set omniroute_initial_password       --generate
appctl secrets set omniroute_ws_bridge_secret      --generate
```

`omniroute_initial_password` is the dashboard password, applied once by the
role's bootstrap step. It is not a secret the operator reads; it is a secret
the application is given.

## Gotchas this role carries

- **`STORAGE_ENCRYPTION_KEY` and `storage.sqlite` are one backup set.** A
  rotated key leaves every stored credential undecryptable.
- **`JWT_SECRET` is persisted by the application** when it is absent, so it
  is set explicitly. Otherwise a wiped state directory silently rotates it and
  signs every operator out.
- **The password is applied once.** The role writes a stamp after the
  bootstrap step succeeds; without it the role would re-apply it on every
  play and lock the operator out.
- **`om_cli` is a wrapper, not a symlink.** The package's own bin entry runs
  `#!/usr/bin/env node`, and nothing under `app_home` is on `PATH` on a
  stock guest, so it dies before the application starts.
- **A newline in a credential truncates it.** systemd parses the
  EnvironmentFile line by line, so a value carrying one becomes a different
  credential rather than an error. The role asserts none of them has one.
- **The guest needs 40 GB.** The tree is 2 GB and the install unpacks it.
