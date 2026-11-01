# Plan: run XERJ as a guest

**Status:** deployed on 2026-11-01 to vmid 124, group `xj`. Measured on the
guest: first run 28 changed, second run needed one fix to reach `0 changed`
(see §8), drift repaired in one run (4 changed) and settled at `0 changed`.
The node answers on `10.0.0.81:9200`; an unauthenticated request returns 401.
**Scope:** one new application, `apps/xerj/`, one `app.yaml` entry, one guest in
`inventory/`. Nothing in `common/` and nothing in `playbooks/` changed.

XERJ is a Rust binary from a GitHub release, not a wheel and not an npm package.
That makes it the third kind of application this repository has already handled
(`omniroute-release` installs a released artifact; `builder` installs a toolchain),
and it reuses the infrastructure of the first.

---

## 1. Decisions this plan needs from you

Four, in order of how much they cost to reverse later.

### D1 — Security posture. The only one that really matters.

XERJ ships **loopback-only, auth on, TLS off**, and it *refuses to start* on a
non-loopback bind while TLS is off unless
`server.allow_insecure_network_bind = true`. That refusal is a test
(`engine/crates/xerj-server/tests/cleartext_bind_fail_closed.rs`), not a comment.

| | Bind | Client access | Cost |
|---|---|---|---|
| **A** | `127.0.0.1` | SSH tunnel, or `xerj autoindex` run on the guest | No exposure. MCP from your laptop needs a persistent tunnel, which VS Code's `mcp.json` cannot create. |
| **B** | `0.0.0.0` + `allow_insecure_network_bind = true` | `XERJ_URL=http://xerj:9200` + `XERJ_AUTH=ApiKey …` | The admin key and every document body cross the LAN in cleartext. Nothing detects this; it works fine. |
| **C** | `0.0.0.0` + `[tls] enabled = true` | `https://` + a cert the client trusts | Needs a cert XERJ's own HTTP client will accept (no `--insecure-tls` for `xerj mcp`), plus `allow_insecure_grpc_h2c` for gRPC or none at all. |

**Decided: B, with a source restriction on the guest.** The role refuses a
non-loopback bind unless `xerj_allow_insecure_bind` is true, and refuses a
claimed-but-unapplied firewall in its postconditions. Two things make it
defensible rather than sloppy:

- a `ufw` rule on the guest that denies incoming by default, keeps SSH reachable,
  and allows `9200` **only from `xerj_lan_cidr`** — owned by this role. No other
  role here opens a firewall, so this is a new pattern; setting
  `xerj_manage_firewall: false` disables it, and the role then says so rather
  than claiming a restriction it did not apply;
- `bind_host: 0.0.0.0` written in `app.yaml`, so `appctl list` and `git diff`
  both show that the exposure was chosen rather than inherited.

`10.0.0.0/8` is what `common/lxc` puts guest `vnet0` on, so on its own the CIDR
documents intent rather than restricting anyone. Narrow `xerj_lan_cidr` to your
own subnet to make it a real limit.

The bind variable is `xerj_bind_host`, not `app_bind_host`: `common/python`
defines `app_bind_host` too, and a meta-dependency's defaults are applied after
the including role's, so the framework's `127.0.0.1` would silently win.

### D2 — What does the node index? — **decided: pushed, corpus directory present**

A fresh guest contains nothing to index. Three shapes, not exclusive:

- **Pushed** — clients write documents through `POST /<index>/_doc` or use
  `xerj index` against the remote node with `--url`/`--auth`. Needs no storage
  on the guest.
- **Local corpus** — an rsync of a folder (a repo, a docs tree) into
  `{{ app_state_dir }}/corpus`, then `xerj autoindex <folder> --prefix <name>`
  on the guest. Needs real disk and a re-sync story.
- **Host bind-mount** — `pct set --mp0`, not modelled by the target today; not
  part of this change.

The role ships the empty, reachable node and creates
`{{ app_state_dir }}/corpus` so the local-corpus shape is a later sync rather
than a re-architecture. Move to a larger disk before the first real corpus; the
32 GB below is sized for code and docs, not for a log archive.

### D3 — Size. — **decided: 4 cores / 4096 MB / 32 GB**

Set in `inventory/group_vars/xj/main.yml`, not in the framework defaults. This
is an estimate, not a measurement: XERJ has not been benchmarked at any corpus
size on this hardware, and the project's own scale ticket
(`demo/usecases/autoindex/scale/TICKET_server-unbounded-ingest-heap.md`) is open.
The role refuses to start a node with less than 8 GB free on `/`, so a full
rootfs fails at a task boundary instead of mid-ingest.

### D4 — VMID. — **decided: 124**

Free in your inventory (120, 121, 122, 123 are taken).

---

## 2. What gets created

```
apps/xerj/
  defaults/main.yml      the interface: name, port, version, digest, paths
  meta/main.yml          dependencies: [role: python]
  tasks/main.yml         download, verify, install, configure, unit, prove
  templates/xerj.toml.j2 the node's configuration — the single source of truth
  templates/xerj.service.j2
```

Plus, outside the role:

- one block in `app.yaml` (`applications.xerj`);
- one host in `inventory/hosts.yml` under `lxc:`;
- one group `xj` in `inventory/hosts.yml` holding that host;
- `inventory/group_vars/xj/main.yml` with `app_roles: [xerj]` and the size
  overrides.

`inventory/` is gitignored, so only the first bullet is a commit.

---

## 3. The manifest entry

```yaml
xerj:
  description: the XERJ search node, from a pinned release archive
  target:
    kind: proxmox_lxc
    options:
      hostname: xerj
      vmid: 124
      bind_host: 0.0.0.0        # D1=B. 127.0.0.1 for D1=A.
  source:
    kind: package
    package: "xerj==1.0.0-rc.100"
  secrets: []
```

Two things worth flagging:

**`kind: package` is a stretch, deliberately.** The schema has three kinds and
requires a `build` block for `repo`. `package` is its word for "a released
artifact pinned by version", which is what `omniroute-release` already uses for
an npm release. The benefit is concrete: `appctl` splits `xerj==1.0.0-rc.100`
and passes `appctl_source_package` / `appctl_source_version` into the play, so
the role can assert the manifest and its own pin agree
(`src/appctl/install.py:97-110`). A `@version` form would silently skip that
assertion.

**`1.0.0-rc.100` is a real published tag** — it is the repository's latest
release, and the role's digest is the one published beside the archive:

```
25aec3e84b904c995bd2d58abe4f83badb8a6f9d1d56dbe2f22180bada9b00cc
```

The digest proves the archive matches the `.sha256` published beside it. It is
an integrity check, not a signature or a provenance statement.

**`secrets: []` is correct, not an omission.** The node generates its own admin
key on first start and writes it to `<data_dir>/admin.key` (mode 0600). Putting
a key in `.env`, in the unit's `Environment=`, or in the TOML would be a
regression: it would have to live in this repository or in the process
environment, and the generated file does not. This matches the AGENTS.md rule
that a provider may legitimately need no secret.

---

## 4. The role

### `defaults/main.yml`

```yaml
app_name: xerj
app_port: 9200                 # ES-compat listener; REST = +1, gRPC = +2
xerj_bind_host: 0.0.0.0        # D1. The role refuses a non-loopback bind
                               # unless xerj_allow_insecure_bind is also true.
xerj_allow_insecure_bind: true # D1=B. false for a loopback bind.
xerj_manage_firewall: true
xerj_lan_cidr: "10.0.0.0/8"    # narrow this to make it a real limit

xerj_version: "1.0.0-rc.100"
xerj_target: x86_64-unknown-linux-gnu
xerj_sha256: "25aec3e8…"       # from the release's .sha256 asset

xerj_bin: "{{ app_dir }}/bin/xerj"
xerj_config: "{{ app_dir }}/xerj.toml"
xerj_data_dir: "{{ app_state_dir }}/data"
xerj_corpus_dir: "{{ app_state_dir }}/corpus"
xerj_embed_mode: lexical       # documented default; not neural
xerj_min_free_gb: 8
```

### `templates/xerj.toml.j2`

One file, and the unit's `ExecStart` passes only `--config`. Flags and a config
file are two sources of truth and this role cannot afford that.

```toml
[server]
data_dir = "{{ xerj_data_dir }}"
bind_address = "{{ xerj_bind_host }}"
es_compat_port = {{ app_port }}
rest_port = {{ app_port + 1 }}
grpc_port = {{ app_port + 2 }}
allow_insecure_network_bind = {{ 'true' if xerj_allow_insecure_bind else 'false' }}
auto_port = false

[auth]
enabled = true
# admin_api_key deliberately unset: the node generates one into <data_dir>/admin.key

[tls]
enabled = false

[embedding]
mode = "{{ xerj_embed_mode }}"
```

### `meta/main.yml`

```yaml
dependencies:
  - role: python
```

The dependency is what creates the `xerj` account, `app_dir` (0750),
`app_state_dir` (0700) and the venv. XERJ uses none of the Python; the venv sits
inert beside the binary, exactly as it does in `omniroute-release`. The
alternative — defining the paths in the role and skipping the dependency, as
`builder` does — is not available to an application that installs a service
(the invariant in `docs/architecture.md`), and duplicating the account and
directory logic would be worse than an unused venv.

### `tasks/main.yml`, in order

1. **Assert the interface.** `app_name`, `app_port` defined; `xerj_bind_host` is
   loopback *or* `xerj_allow_insecure_bind` is true. Fail with the reason, not
   with xerj's own startup refusal.
2. **Assert the pin.** `appctl_source_package == "xerj"` and
   `appctl_source_version == xerj_version`, per the skill's guard.
3. **Read the installed binary's version** (`{{ xerj_bin }} --version`,
   `failed_when: false`) and set one fact, `xerj_needs_install`. The binary's own
   answer, not a stamp file — `omniroute-release`'s Node install is the model.
4. **Fetch the archive** with `get_url`, `checksum: "sha256:{{ xerj_sha256 }}"`,
   to `{{ app_home }}/xerj-{{ xerj_version }}-{{ xerj_target }}.tar.gz`. The
   digest is committed in `defaults/main.yml`; `get_url` re-downloads only when
   it is absent or wrong.
5. **Unpack and install the binary** to `{{ xerj_bin }}` (root:root, 0755) with
   `unarchive: remote_src: true` + `copy: remote_src: true`, guarded by
   `creates:`.
6. **Verify the installed version** equals `xerj_version`. Without this step a
   rollback leaves the old binary in place and every later task passes.
7. **Template `xerj.toml`** (owner `app_user`, 0640, with a backup) and the unit
   (root, 0644). Both register their result; a change triggers an explicit
   restart after the start, not a handler, because a handler runs after the
   checks and they must see the running configuration.
8. **Create the corpus directory** (`{{ app_user }}`, 0750), per D2.
9. **Apply the firewall** if `xerj_bind_host` is not loopback: install `ufw`,
   deny incoming by default, keep `22/tcp` reachable, `allow from
   {{ xerj_lan_cidr }} to any port {{ app_port }} proto tcp`, then enable it only
   when `ufw status` says inactive. Asserted afterwards with `ufw status`.
   Idempotent because each `ufw` command reports the same output when the rule
   already exists.
10. **Enable and start the unit**, restart it if either file changed, then
    `wait_for` port `{{ app_port }}`.

### Unit

```ini
[Unit]
Description=the XERJ search node
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={{ app_user }}
Group={{ app_user }}
WorkingDirectory={{ app_home }}
ExecStart={{ xerj_bin }} --config {{ xerj_config }}
Restart=on-failure
RestartSec=2
LimitNOFILE=65536
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Deliberately **not** `ProtectSystem=strict` in the first version: the node
publishes `$HOME/.xerj/by-port/<port>.data-dir` and its admin-key pointer on
every start (`engine/crates/xerj-common/src/nodedir.rs`), which is what makes
"no node is listening" diagnosable. A read-only `/opt` breaks that diagnostic,
not the service, and it would be a confusing failure. If we harden later, it is
`ReadWritePaths={{ app_state_dir }} {{ app_home }}/.xerj`.

### Postconditions — the point of the role

Every one of these stands in for a failure a green play would otherwise hide.

| Check | The failure it catches |
|---|---|
| `{{ xerj_bin }} --version` matches the pin | a stale or rolled-back binary |
| `admin.key` exists, 0600, owned by `app_user` | the node started with auth misconfigured |
| `ss -lnt` shows `127.0.0.1:9200` **and** the bound address | the process is up but bound elsewhere |
| `GET /` with `Authorization: ApiKey …` → 200 | listening but not authenticating |
| `GET /_cluster/health` → 200, `status` present | serving HTTP but not an index |
| free space on `/` ≥ 8 GB | an ingest that will fail part-way through |
| `Report the checks that passed` | the summary reports task counts, not evidence |

The key is read with `slurp` and used with `uri` under `no_log: true`, so it
never appears in argv (`ps`) or in a log. The request goes to
`http://127.0.0.1:{{ app_port }}/` on the guest itself, so nothing is exposed by
the check and nothing depends on D1.

---

## 5. What this deliberately does not do

- **Not `curl -fsSL https://xerj.org/get | sh`.** That script resolves the
  latest release, so two plays install different code, and it has no digest this
  role can pin. It is right for a laptop and wrong for a role whose second run
  must be `changed=0`.
- **Not the `.deb`.** It would install to `/usr/bin/xerj`, outside `app_dir`,
  and fight the pin. The `.tar.gz` puts one binary under one directory.
- **Not `xerj init`.** That writes `.mcp.json` / `.codex/config.toml` for agents
  *on the guest*. This guest serves; it does not vendor an agent config. Worth
  running by hand if you later index locally there.
- **No `config:` block in `app.yaml`.** `appctl render` emits exactly one JSON
  document and XERJ's configuration is TOML. The role therefore writes the file
  as a template — the same shape as `omniroute-release`, whose dotenv file is
  also not renderable, and the same reason there is nothing for the framework to
  render.
- **No `--insecure`.** It disables auth *and* TLS. Loopback-only would still be
  reachable by a local process, and B needs auth to be meaningful.

---

## 6. Client wiring

On the guest, `xerj autoindex` and `xerj mcp` discover `admin.key` themselves
(loopback only). From your laptop, neither can, so both need the URL and the
header:

```bash
scp root@xerj:/opt/xerj/state/data/admin.key ./xerj-admin.key   # once; chmod 600
export XERJ_URL=http://xerj:9200
export XERJ_AUTH="ApiKey $(cat ./xerj-admin.key)"
xerj autoindex ~/projects/xerj --prefix xerj
```

VS Code (`~/.config/Code/User/mcp.json`), with a stdio proxy on the laptop that
points at the guest:

```jsonc
{
  "servers": {
    "xerj": {
      "type": "stdio",
      "command": "/home/bruj0/.local/bin/xerj",
      "args": ["mcp"],
      "env": {
        "XERJ_URL": "http://xerj:9200",
        "XERJ_AUTH": "ApiKey <the key>"
      }
    }
  }
}
```

Two honest notes. `xerj mcp` is a proxy and does not start a node — that is
stated in its `--help` and in its error when nothing answers. And the key is one
shared admin credential: XERJ has no per-agent key and no rotation path short of
replacing the file and restarting. That is a reason to prefer A or C, and it is
the strongest argument against B.

Under D1=A the same thing works with
`ssh -N -L 9200:127.0.0.1:9200 root@xerj` in a terminal, and
`XERJ_URL=http://127.0.0.1:9200` in the config.

---

## 7. Acceptance

```bash
appctl install xerj        # first run: several changed
appctl install xerj        # second run: MUST be changed=0
appctl list                # DEPLOYED shows xerj, not "no guest"
```

Then inject the three drifts that have actually happened here and check one
re-run repairs them, ending at `changed=0` again:

```bash
rm -f /opt/xerj/app/bin/xerj
systemctl stop xerj && systemctl disable xerj
rm -f /opt/xerj/app/xerj.toml
appctl install xerj
appctl install xerj        # changed=0
```

**Measured on 2026-11-01, vmid 124:**

| Run | Result |
|---|---|
| first install | 28 changed, 0 failed, 64 tasks, 151 s |
| second install | 2 changed — `ufw default` was not idempotent; fixed, see §8 |
| third install | **0 changed**, 37 s |
| after injecting the three drifts | 4 changed, all checks green, 39 s |
| run after the repair | **0 changed**, 37 s |

From the controller, `http://10.0.0.81:9200/` and `/_cluster/health` both answer
**401** without a key — reachable on the LAN, and the auth gate applies to the
network, not only to loopback.

`app.yaml` is gitignored in this repository (`.gitignore:31`; the committed
template is `app.example.yaml`), so a run leaves no diff there — the `state:`
block it now carries is the record of the install. `git status` should show only
the new role, this plan, and the `app.example.yaml` documentation block; never
`inventory/`, `.env`, or `tmp/`.

---

## 8. Traps this role is written around

- **A sentinel that matches `unchanged`.** `'changed' in stdout` is true for
  `"unchanged"`; the version check uses `== 'changed'` after `trim`.
- **`get_url` without `checksum`** re-downloads on every play and reports
  `changed`, which is the most common way a second run stops being `changed=0`.
- **`unarchive` without `creates:`** re-extracts every run and changes the
  mtime of the binary the unit is running.
- **A read-only `$HOME`** breaks `~/.xerj/by-port/`, not the service, and the
  symptom is a misleading "no node is listening" hint.
- **A data directory inside an indexed folder.** XERJ's help says `data/` is
  not in the built-in ignore list, so `data_dir` inside a corpus would be
  indexed. It lives in `app_state_dir`, which the corpus directory is a sibling
  of — never a parent.
- **`--port` claims three ports.** `es_compat_port = N`, `rest_port = N+1`,
  `grpc_port = N+2`; a hard-coded 9200 in a client and a changed `app_port`
  disagree silently.
- **`ufw default` is not a sentinel.** Measured: `ufw default deny incoming`
  prints *"Default incoming policy changed to 'deny'"* on **every** call,
  including one that changes nothing. A `changed_when` on that phrase reported 2
  changed on every play — this role's second run was exactly that until the task
  was guarded by reading `ufw status verbose` and comparing
  `Default: deny (incoming)` first. `ufw allow` *is* idempotent
  (`Rules updated` vs `Skipping adding existing rule`); `ufw default` is not.

---

## 9. What is still open

The four decisions above are made. What remains is operational, and none of it
changes the role:

1. **The guest is running.** vmid 124, address `10.0.0.81`, deployed and settled
   at `0 changed`. The remaining items are operational choices, not unfinished
   work.
2. **`xerj_lan_cidr`.** `10.0.0.0/8` is the whole `vnet0` range, so it restricts
   nothing today. Set it to the subnet your workstation is on if you want the
   firewall rule to mean something.
3. **If `ufw` cannot run in the container.** The role fails rather than
   installing a LAN-reachable node it cannot restrict. Set
   `xerj_manage_firewall: false` only if another control filters the guest; the
   run then reports that no firewall was applied instead of claiming one.
4. **The first corpus.** Point `xerj autoindex` at something (`--prefix` per
   folder). Until then the node is reachable and empty, which is the correct
   state rather than a broken one.
5. **The admin key is shared.** One credential for every client, no rotation
   path short of replacing `<data_dir>/admin.key` and restarting. If more than
   one person or agent ever needs access, option C (TLS) is the next step, not a
   second key.
