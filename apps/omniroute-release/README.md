# omniroute-release

OmniRoute, **installed from a published npm release**, served by one systemd
unit on one guest.

This is the release shape. The other OmniRoute role in this repository
(`apps/omniroute/`) builds a tree from a source checkout on a build guest;
this one installs a released artifact that npm publishes, and the guest's own
`npm install` does the work. Nothing OmniRoute-related runs on the controller
except the install itself.

## What is deployed

| | |
|---|---|
| Guest | `omniroute`, CT 120 |
| Release | `omniroute@3.8.51` from npm, pinned |
| Runtime | Node 24.14.1, pinned and checksummed, under `app_home/node` |
| Unit | `omniroute.service` |
| Port | 20128 — dashboard, `/v1` proxy, MCP/A2A and management API together |
| Port | 20132 — the realtime dashboard WebSocket, published to match |
| State | `/opt/omniroute/state`, never touched by a reinstall |
| Install root | `/opt/omniroute/app/npm`, beside the inert Python venv |
| Secrets | five, all generated, none ever in `argv` |

## Install it

```bash
appctl install omniroute-release
appctl install omniroute-release        # must report changed=0
```

The manifest key is `omniroute-release` and the role directory is
`apps/omniroute-release/`. They have to match: `appctl install` selects the
role with `--tags lxc,<key>`, and a key that is not a role name selects
nothing.

## Why its secrets are named `omniroute_release_*`

Two guests run this application: this role installs the published release, and
`apps/omniroute/` builds one from a checkout. Both derive their paths from
`app_name`, so both write `/opt/omniroute` and both read a credentials file
with the same five keys.

One `.env` cannot hold two storage encryption keys. The release role's
credentials are therefore named `omniroute_release_*`, and the source-built
role keeps the unscoped names. Two guests, two key sets, one file.

The cost is a name that repeats the role, and the benefit is that adopting a
guest under this role cannot rotate a key the other guest is using. The
storage encryption key and `storage.sqlite` are one backup set: a rotated key
leaves every stored provider credential undecryptable.

## Why the role is named `omniroute-release` but the app is `omniroute`

Two roles cannot share a name, and `apps/omniroute/` already owns the first
one. So the role directory is `omniroute-release`.

The guest side is a different question, and the answer is `app_name:
omniroute`. Everything the framework derives comes from `app_name`:

| Derived from `app_name` | Value |
|---|---|
| `app_user` | `omniroute` |
| `app_home` | `/opt/omniroute` |
| `app_dir` | `/opt/omniroute/app` |
| `app_state_dir` | `/opt/omniroute/state` |
| `app_service_name` | `omniroute` |

A second name would mean a second service account, a second install root and
a **second state directory** — and the state directory is where the stored
provider keys, the encrypted credential table and the conversation history
live. A second name would be a working service with an empty database.

So the role name and `app_name` differ on purpose. The role name is what the
inventory and `appctl` refer to; `app_name` is what the guest sees.

## The credentials file is group-readable, on purpose

The credentials live in `state/secrets/env`, owned by `root` with group
`app_user` and mode `0640`. The service account can **read** it and cannot
**write** it.

That is not a weakening. The file stays root-owned and unwritable by the
process it belongs to, so a compromised application cannot rewrite the key
that encrypts its own store. What it does have to do is *read* it, because
the operator wrapper in `/usr/local/bin/omniroute` loads it and exports what
it finds.

The failure this prevents was measured on CT 120. With the file at `0600` and
group `root`:

```
FAIL Storage/encryption: Encrypted credentials exist but STORAGE_ENCRYPTION_KEY is missing
```

The server was perfectly healthy throughout. It had the key, because systemd
reads the EnvironmentFile as PID 1 before it drops privileges. The
`doctor` diagnostic did not, because it runs through the wrapper as the
service account — and the wrapper could not open a `root`-only file. The role
failed on a guest whose service was fine.

The same applies to the directory holding it: `root:app_user` at `0750`, so
the service account can traverse into it.

## What the role refuses

Each of these is a failure a green play would otherwise hide:

| Check | Catches |
|---|---|
| the credentials are non-empty | a secret that was never set, before a 546 MB download |
| the password is at least 8 characters | `omniroute setup` rejecting it later, against a one-time bootstrap |
| no credential contains a newline | a value silently truncated by the EnvironmentFile format |
| the pinned Node is on disk | a runtime outside the range OmniRoute accepts |
| npm is 11 or later | `--allow-scripts` being rejected mid-install |
| the installed version is the pinned one | a release someone bumped by hand |
| the package `.env` is gone | a file inside the install root that a version bump replaces |
| the embedding library is writable | the memory subsystem silently degraded to FTS5-only |
| the native SQLite binding loads | a server running on the WASM fallback with WAL disabled |
| the store exists and is non-empty | a running server with nothing to log in to |
| the store is in WAL mode | a driver that cannot do write-ahead logging |
| the listeners are on the configured address | "the service is on the LAN" versus "the variables say it should be" |
| an anonymous `/v1` call is refused | an inference plane any device on the network can spend |
| an invalid key is refused | the key gate being off, which lets invalid keys through as anonymous traffic |
| an anonymous `/api` call is refused | a dashboard with no administrator password |
| the unit is enabled | a service that comes back stopped after a reboot |

The last three are the ones worth reading. They run from the controller
against the guest's LAN address, because the question they answer is what a
peer on the network can reach — and inside the guest the same request can be
answered differently.

## The version pin

`omr_version` is pinned to an exact version, never `latest`. A floating tag
makes two plays of the same role install different code, which is the
opposite of what a pinned role is for.

The role compares the installed version by asking the release itself
(`omniroute --version`), not by reading a stamp file. A stamp can disagree
with the directory it describes — a half-finished install, a directory left
behind by an interrupted play — and then every task that trusts it skips the
install that would have repaired it.

## The compile toolchain is off

`omr_install_build_toolchain: false`. `better-sqlite3` ships a prebuild for
`linux-x64` at this Node, so a compiling guest is the exception rather than
the rule.

When a version bump outruns the prebuild, the role does not silently carry a
C++ compiler on every guest: it fails at the binding assertion and names the
remedy — either rebuild the binding in place, or set
`omr_install_build_toolchain: true` and re-run.

## Why the secrets never reach `argv`

`omniroute setup --non-interactive` reads `INITIAL_PASSWORD` from the
environment. The wrapper takes it from the root-owned credentials file, so
the password is never a command-line argument — and `ps` on the guest is
world-readable.

The bootstrap runs **once**. It is gated on a marker in the state tree, so a
reinstall does not re-apply it and lock the operator out. The marker is never
written over a failure: a marker written over a failure is the one thing that
would make the next play skip the task that has to be retried.

## Source

This role was migrated from the `omniroute` application in the
`proxmox-lxc-ansible` repository, which is where the release shape was
originally written and where CT 120 was first provisioned.
