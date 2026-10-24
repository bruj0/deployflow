# omniroute

OmniRoute, **built from a source checkout**, served by one systemd unit on one
guest. Nothing OmniRoute-related runs on the controller except the build: no
checkout is read during the play except to check a receipt, and no bundle is
produced there.

This is the from-source shape. The published-release shape — one pinned npm
version, `npm install` fetches it — is a different role and is not this one.

## What is deployed

| | |
|---|---|
| Guest | `omniroute-src`, CT 122 |
| Source | `bruj0/OmniRoute` at `main`, resolved to a commit |
| Build | Linux amd64, on the controller, in a container |
| Runtime | Node 24.14.1, pinned and checksummed, under `app_home/node` |
| Unit | `omniroute.service` |
| Port | 20128 — dashboard, `/v1` proxy, MCP/A2A and management API together |
| Port | 20132 — the realtime dashboard WebSocket, published to match |
| State | `/opt/omniroute/state`, never touched by a reinstall |
| Install root | `/opt/omniroute/app/src`, beside the inert Python venv |

## Why the build runs in a container

The controller is arm64 macOS. The guest is x86_64 Linux. A tree built on the
controller carries **darwin-only** native binaries — `sharp-darwin-arm64`,
`@esbuild/darwin-arm64`, `lightningcss-darwin-arm64` — and the guest's Linux
builds need `sharp-linux-x64`.

`better-sqlite3` hides this: it ships a prebuild for every platform, so it
loads on the guest even from a macOS build. `sharp` and `esbuild` do not, and
they fail much later as an unrelated error deep in a request handler.

So the build runs in a Linux amd64 container:

```bash
apps/omniroute/build/omr-build-linux.sh /Users/bruj0/Projects/Omniroute main
```

The settings match the project's own CI recipe
(`.github/workflows/build.yml`): `ONNXRUNTIME_NODE_INSTALL=skip`,
`OMNIROUTE_USE_TURBOPACK=0`, and a 10 GB heap cap.

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
  "builtAt": "2026-10-24T15:19:00Z"
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
# Build it for the guest's platform. This is the slow part.
apps/omniroute/build/omr-build-linux.sh /Users/bruj0/Projects/Omniroute main

# Deploy it.
appctl install omniroute-src

# Second run must be changed=0.
appctl install omniroute-src
```

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
