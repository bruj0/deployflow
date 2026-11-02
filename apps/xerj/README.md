# xerj

XERJ, **the search node**, served by one systemd unit on one guest.

XERJ is one Rust binary from a GitHub release — not a wheel, not an npm
package. That makes this the third application shape in this repository:
`example-app` installs a wheel, `omniroute-release` installs a published npm
release, `builder` installs no service at all, and this one installs a pinned
release archive and does the installing itself.

## What is deployed

| | |
|---|---|
| Guest | `xerj`, CT 124 |
| Release | `xerj==1.0.0-rc.100`, a pinned archive with its sha256 committed |
| Install root | `/opt/xerj/app`, beside the inert Python venv |
| Binary | `/opt/xerj/app/bin/xerj`, root-owned and `0755` |
| Configuration | `/opt/xerj/app/xerj.toml`, written by this role |
| Unit | `xerj.service` |
| Port | 9200 — Elasticsearch-compatible, 9201 native REST, 9202 gRPC |
| State | `/opt/xerj/state/data` — the index, the WAL, the segments and the admin key |
| Corpus | `/opt/xerj/state/corpus`, created empty |
| Secrets | none. The node generates its own admin key |

## Install it

```bash
appctl install xerj
appctl install xerj        # must report changed=0
```

The manifest key is `xerj` and the role directory is `apps/xerj/`. They match:
`appctl install` selects the role with `--tags lxc,<key>`, and a key that is
not a role name selects nothing — the play runs, changes nothing and reports
success.

## Why `kind: package` for a Rust binary

The manifest schema has three source kinds and requires a `build:` block for
`repo`. `package` is its word for "a released artifact pinned by version",
which is what `omniroute-release` already uses for an npm release.

The benefit is concrete rather than convenient. `appctl` splits
`xerj==1.0.0-rc.100` and passes both halves into the play as
`appctl_source_package` and `appctl_source_version`, so the role can assert
that the manifest and its own pin agree. A `name@version` form carries no
comparison, and the assertion silently checks nothing.

## There is no `config:` block, and there is no secret

Both are deliberate, and both are the reason this role writes files itself.

`appctl render` emits exactly one JSON document. XERJ reads TOML, so there is
nothing for the framework to render — the role templates `xerj.toml` and the
unit instead. This is the same shape as `omniroute-release`, whose dotenv file
is also not renderable.

There is no secret either. The node generates its own admin key on first start
and writes it to `<data_dir>/admin.key` at mode `0600`. Putting a key in
`.env`, in the unit's `Environment=`, or in the TOML would be a regression: it
would have to live in this repository or in the process environment, and the
generated file does not.

## Why the bind is `0.0.0.0` with TLS off

XERJ ships loopback-only, auth on, TLS off, and it *refuses to start* on a
non-loopback bind while TLS is off unless
`server.allow_insecure_network_bind = true`. That refusal is a test in the
engine, not a comment.

This role refuses the same combination independently, so the exposure has to
be written down twice before it happens:

| | Where |
|---|---|
| the operator's choice | `bind_host: 0.0.0.0` in `app.yaml` |
| the engine's refusal | `allow_insecure_network_bind` in `xerj.toml` |
| this role's refusal | `xerj_allow_insecure_bind` in the role's defaults |
| the restriction | a `ufw` rule the role owns and asserts it applied |

The bind is a LAN bind with the admin key as the only control. The key is a
single shared credential: XERJ has no per-agent key and no rotation path short
of replacing `<data_dir>/admin.key` and restarting. That is the cost of this
posture, and the firewall is what keeps the exposure off the rest of the
network.

To keep the node loopback-only, set `bind_host: 127.0.0.1` in the manifest and
`xerj_allow_insecure_bind: false` in the role. Then nothing is exposed and no
firewall is applied.

### The firewall

`ufw` denies incoming by default, keeps SSH reachable, and allows the node's
port from `xerj_lan_cidr` only. `10.0.0.0/8` is the range `common/lxc` puts
guest `vnet0` on, so on its own the rule documents intent rather than
restricting anyone.

Set `xerj_manage_firewall: false` only if something outside this guest
already filters it. The role then says so in its report rather than quietly
claiming a restriction it did not apply — a claimed-but-unapplied firewall
is exactly the "plausible but broken" state this repository exists to refuse.

## The binary is pinned by version *and* digest

`xerj_version` is never `latest`, and `xerj_sha256` is the digest published
beside the archive in the same release. A floating tag makes two plays of the
same role install different code, which is the opposite of what a pinned role
is for.

The digest proves the archive matches the `.sha256` published next to it. It
is an integrity check, not a signature and not a provenance statement.

The role then reads the installed binary's own `--version` and refuses a
binary that disagrees with the pin. That is what catches a rollback which
left the old one in place — otherwise every later task passes and the stale
binary keeps serving.

## The unit passes `--config` and no other flag

Flags and a config file are two sources of truth, and this role cannot afford
a second place for a setting to disagree. So the TOML carries the bind
address, the three ports, the data directory and the embedding mode, and the
unit carries nothing but the path.

`auto_port = false` is stated rather than defaulted. If it were true, a node
that failed to bind 9200 could quietly publish 9201, and every client would
keep talking to whatever holds 9200.

## The data directory is the boundary

It holds the index, the WAL, the segments and the generated admin key, and it
lives under `app_state_dir` so replacing the binary never destroys an index.

It must not sit inside any folder passed to `xerj autoindex`: `data/` is not
in XERJ's built-in ignore list, so a data directory inside a corpus would be
indexed. It is a sibling of the corpus directory, never a parent.

## What the role refuses

Each of these is a failure a green play would otherwise hide:

| Check | Catches |
|---|---|
| the manifest's pin matches the role's | two places naming different versions |
| the digest is 64 lowercase hex characters | a placeholder reaching `get_url` |
| the installed binary reports the pinned version | a stale or rolled-back binary |
| `admin.key` exists, `0600`, owned by the service account | the node started with auth misconfigured |
| the listeners are on the configured address | the process is up but bound elsewhere |
| `GET /` with the admin key returns 200 | listening but not authenticating |
| `GET /_cluster/health` returns 200 | serving HTTP but not an index |
| free space on `/` is at least 8 GB | an ingest that fails part-way through |
| the firewall is active and restricts the port | a claimed-but-unapplied firewall |

The key is read with `slurp` and used with `uri` under `no_log: true`, so it
never appears in `argv` — and `ps` on the guest is world-readable. The request
goes to `http://127.0.0.1:{{ app_port }}/` on the guest itself, so nothing is
exposed by the check and nothing depends on the bind address.

## The embedding mode is lexical

`lexical` is the built-in feature-hashing embedder: no model, no download, no
network at startup, and not semantics. `neural` loads a ~90 MB BERT model at
startup and refuses to start if that load fails, which would turn a first run
into a HuggingFace availability test.

The mode is fixed per index at ingest time, so changing it later does not
re-embed anything. A corpus indexed lexically stays lexical.

## Reaching the node from the controller

The node is on the LAN and the guest's address is a DHCP lease, so the address
is discovered rather than written down. `pct exec <vmid> hostname -I` on the
Proxmox host reads it back.

The admin key lives on the guest. Copy it across once, and keep it out of
this repository:

```bash
scp root@<guest-address>:/opt/xerj/state/data/admin.key ./xerj-admin.key
chmod 600 ./xerj-admin.key
```

Then point a client at it:

```bash
export XERJ_URL=http://<guest-address>:9200
export XERJ_AUTH="ApiKey $(cat ./xerj-admin.key)"
xerj autoindex ~/projects/something --prefix something --url "$XERJ_URL" --api-key "$XERJ_AUTH"
```

`xerj mcp` is a proxy and does not start a node — that is stated in its
`--help` and in its error when nothing answers. On the guest itself, `xerj
autoindex` and `xerj mcp` discover `admin.key` on their own, because the
node's own tooling reads `<data_dir>/admin.key` for a loopback bind.

## The corpus is empty on purpose

A fresh guest has nothing to index. The role creates
`{{ app_state_dir }}/corpus` so the local-corpus shape is a later sync rather
than a re-architecture.

Three shapes are available, and they are not exclusive: clients push
documents through the API; a folder is synced into the corpus directory and
indexed with `xerj autoindex <folder> --prefix <name>`; or a host bind-mount
is used, which the target does not model today.

Move to a larger disk before the first real corpus. The 32 GB below is sized
for code and documentation, not for a log archive, and the role refuses to
start a node with less than 8 GB free on `/`.

## The sizing is an estimate, not a measurement

The guest gets 4 cores, 4096 MB of RAM and a 32 GB rootfs, set in
`inventory/group_vars/xj/main.yml` rather than in the framework defaults.

No benchmark of XERJ at any corpus size was run on this hardware for this
deployment. These are starting allocations chosen to leave room for a
code-and-docs corpus. The project's own scale ticket is still open, so raise
the values there rather than by hand — a resized guest is otherwise a fact
only the Proxmox host knows.

## Traps this role is written around

- **A sentinel that matches `unchanged`.** `'changed' in stdout` is true for
  `"unchanged"`; the version check compares against `== 'changed'` after a
  trim.
- **`get_url` without `checksum`** re-downloads on every play and reports
  `changed`, which is the most common way a second run stops being
  `changed=0`.
- **`unarchive` without `creates:`** re-extracts every run and changes the
  mtime of the binary the unit is running.
- **A read-only `$HOME`** breaks `~/.xerj/by-port/`, not the service, and the
  symptom is a misleading "no node is listening" hint.
- **`ufw default` is not a sentinel.** `ufw default deny incoming` prints
  *"Default incoming policy changed to 'deny'"* on **every** call, including
  one that changes nothing. A `changed_when` on that phrase reported 2
  changed on every play — this role's second run was exactly that until the
  task was guarded by reading `ufw status verbose` and comparing
  `Default: deny (incoming)` first. `ufw allow` *is* idempotent;
  `ufw default` is not.

## Source

The design, the four decisions and the measured run are written up in
[`docs/plans/xerj-on-lxc.md`](../../docs/plans/xerj-on-lxc.md).
