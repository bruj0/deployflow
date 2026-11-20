# Configuration reference

The full shape of `app.yaml`. The repository README has the short version; this
is the one to check a key against.

**Unknown keys are an error.** The tool rejects any key not listed here, naming
the key and where it was found. A misspelled key would otherwise be dropped, and
a silently dropped key is how a service comes up on the wrong port.

## Top level

```yaml
version: 1              # integer
defaults: {...}         # guest shape, inherited by every application
applications: {...}     # the applications
shared_disks: {...}     # optional: the volumes two or more guests share
```

## `shared_disks`

One volume that two or more guests hold at the same time: one writes, the
others read. This is the mechanism for a media library that one service
downloads and another serves.

```yaml
shared_disks:
  media:
    storage: data2              # the Proxmox storage the volume lives on
    size_gb: 1000               # the volume's size, in GiB
    volume: vm-900-disk-0       # the LVM volume name
    filesystem: btrfs           # only btrfs
    idmap: "u:1000:101000:1;g:1000:101000:1"
    host_uid: 101000            # the host-side uid the files belong to
    trees:
      - /music                  # directories on the volume, created by the role
      - /movies
```

| Key | |
|---|---|
| `storage` | a Proxmox storage. The role reads its thin pool from the storage, not from a name it assumes. |
| `size_gb` | the size the volume is created at. A thin volume grows with `lvs -L +<n>G`. |
| `volume` | the LVM volume name. It must be `vm-<vmid>-disk-<n>`. |
| `filesystem` | `btrfs`, and nothing else. |
| `idmap` | the ownership mapping, the same on every guest that attaches the volume. |
| `host_uid` | the uid the files are stored under on the host. |
| `trees` | the directories on the volume. Each is created and owned by the role. |

### Why the volume name carries an unused VMID

A volume is owned by the VMID in its name, and **destroying that container
deletes the volume** — even while another container still uses it. Proxmox says
so when it refuses:

```
ignore deletion of 'data2:vm-900-disk-0', CT 220 isn't the owner!
```

So the name must carry a VMID no container uses. `vm-900-disk-0` is the
convention; VMID 900 is not in the range the operator allocates from.

A name Proxmox cannot parse is refused when it is attached, so the
`vm-<vmid>-disk-<n>` shape is not cosmetic:

```
Could not rescan volume size - unable to parse lvm volume name 'shared-media'
```

### Why btrfs, and why one idmap

ext4 can be mounted once. The second guest's pre-start hook fails, long after
the play that caused it. btrfs allows one mount per device, so two guests hold
the same volume at once, one `rw` and one `ro`.

btrfs stores **one** idmap per filesystem, in its superblock. The first guest
to mount it decides, and a second guest asking for a different one is refused
and refuses to start. So every guest that attaches a disk uses the same
`idmap`, and the manifest holds it in one place.

The idmap must be **one-to-one**: `u:1000:101000:1` maps the single guest uid
`1000` to the single host uid `101000`. A wider idmap overlaps the guest's
own `lxc.idmap` (`u 0 100000 65536`), the tree appears as `nobody`, and the
writer cannot write to it. The check is on the **container** side of the
mapping: pick a container uid outside `0..65536`.

The files on the volume must be owned by `host_uid`. The idmap maps
ownership; it does not set it.

### Attaching a disk to a guest

The disk is declared once, in the manifest. Which guest holds it, and whether
it holds it read-only, is declared in that guest's `group_vars`:

```yaml
# inventory/group_vars/jf/main.yml
shared_disk_mounts:
  - disk: media
    mountpoint: /mnt/volume
    read_only: true
```

The attachments do **not** travel as play variables. Extra vars are global, so
passing them would attach every guest's mounts to every guest. The disk
attachments live next to the guest facts they belong with, and the role joins
the two per guest.

A guest that names a disk the manifest does not declare is refused before
anything runs.

### What the role does

`common/shared_disk` runs on the Proxmox node, once, before the guests: it
creates the volume if it does not exist, refuses a volume that is not btrfs,
creates the trees, gives them the host-side ownership, and asserts it. The
`common/lxc` role then attaches the volume to each guest that names it, before
the guest starts, because Proxmox attaches the volume when it starts it.

A second `appctl install` reports `changed=0`: the role finds the volume, the
filesystem and the trees, and changes nothing.

## `defaults`

```yaml
defaults:
  python_version: "3.14"                              # quoted; unquoted is a float
  template: ubuntu-26.04-standard_26.04-1_amd64.tar.zst
  storage: data1
  rootfs_gb: 16
  cores: 2
  memory_mb: 2048
  swap_mb: 512
  features: nesting=1,fuse=1,keyctl=1
  onboot: 0
```

Every key has a default, so `defaults:` may be omitted entirely.

## `applications.<name>`

```yaml
applications:
  <name>:
    description: one line, shown by `appctl list`   # optional
    target:     {...}                               # required
    source:     {...}                               # required
    build:      {...}                               # required when source.kind is repo
    config:     {...}                               # optional
    secrets:    [NAME, ...]                         # optional
```

`name` is the key. It must match the directory name under `apps/`, because the
role is looked up by it.

### `target`

```yaml
target:
  kind: proxmox_lxc         # required: the target that runs this application
  options:                  # optional: the target's own settings
    hostname: example-app-ansible
    vmid: 117               # optional; omit and the role skips create
    bind_host: 0.0.0.0      # default 127.0.0.1
```

`kind` names a registered target. `proxmox_lxc` is the only one implemented;
`vm` and `docker_vm` are planned and fail before anything is built. See
[targets.md](targets.md).

`options` belongs to the target. The shared schema does not read inside it, so
a new target never changes `config.py`. A misspelled key under `options` is
caught by the target, not by this schema.

For `proxmox_lxc` there is deliberately **no address field.** A guest takes a
DHCP lease from Proxmox's SDN zone and `common/lxc` reads the address back. An
`ip:` key is rejected. This is not caution: a statically addressed Ubuntu 26.04
guest comes up with no IPv4 at all, an empty `/etc/netplan/`, and
`systemd-networkd` exiting `243/CREDENTIALS`, and `apt` cannot resolve.
Verified on a live container.

### `source`

Three kinds, because they describe genuinely different things. What differs is
not the value of `kind` but what has to happen before the application can be
installed.

| Kind | The code comes from | Has an artifact? | `appctl build` |
|---|---|---|---|
| `package` | a published version | yes — it already exists | exits non-zero: nothing to build |
| `repo` | a commit in a checkout | no — it has to be built | builds it |
| `toolchain` | nowhere — the application *is* the environment | no | exits non-zero: nothing to build |

```yaml
# published on PyPI — nothing to build
source:
  kind: package
  package: "myapp[browser]==1.2.3"    # required when kind is package

# built from a checkout — wheel plus pinned lockfile
source:
  kind: repo                          # required
  repo: /path/to/checkout             # required when kind is repo
  ref: HEAD                           # default HEAD
  output: dist                        # default dist, relative to files/
  webui: {...}                        # optional, see below

# nothing is installed from anywhere — the application IS the environment
source:
  kind: toolchain                     # required
```

`appctl build <name>` on a `package` source exits non-zero and says there is
nothing to build. That is deliberate: silently succeeding would hide a
misconfiguration.

#### `kind: repo`

The `build:` block is required for this kind and the tool rejects a `repo`
source without one, naming the omission. It describes a **Python** build —
a wheel plus a pinned lockfile — and that is what `appctl build` produces.

An application built from a source checkout in another language does not use
`appctl build`. It has its own build script, because the build has to run on
the platform the artifact targets and the tree is far too large to move
around. [`apps/omniroute/`](../apps/omniroute/) is the worked example: its
`build:` block is deliberately inert, and the real build is
`apps/omniroute/build/omr-build-linux.sh`. See
[build-pipeline.md](build-pipeline.md#when-an-application-needs-no-build).

#### `kind: toolchain`

A toolchain application installs no service and publishes no port. Its role
assembles the environment another application is built in — a compiler, a
pinned runtime, a workspace — and then stops.

It exists because the alternative is to describe that environment as one of
the other two kinds, and both are lies:

- `package` claims something is installed from a registry. Nothing is.
- `repo` claims there is a checkout to build. There is not one; and the schema
  requires a `build:` block for a `repo` source, so the manifest would carry a
  block that does nothing.

So a toolchain source has no `package`, no `repo` and no `build:` block, and
`appctl build` on it says there is nothing to build for the same reason a
`package` does.

The role for a toolchain has **no dependency on `common/python`**, and that is
load-bearing rather than a style choice: `common/python` asserts that
`app_port` is defined, and a builder serves nothing. A role with no dependency
still runs — play 2 of `playbooks/site.yml` loops over each host's
`app_roles` and `include_role`s it — so nothing in the playbook changes.

The builder application in this repository is the worked example. Its
postconditions assert the properties a build depends on rather than the exit
codes of the install commands, and it publishes its address to
`tmp/builders/<name>.endpoint` so the build script can find it: guest
hostnames do not resolve from the controller, because Proxmox's resolver is
not the controller's.

The minimums are measured, not guessed. A builder sized at 12 GB passed every
install check and then failed during a build, with an error that named a
memory limit rather than the guest — and the guest stopped answering SSH
entirely. The role asserts the floor it needs, so a guest resized by hand is
checked at install time rather than discovered during a build.

See [`apps/builder/`](../apps/builder/) for the working role.

### `source.webui`

Only for an application with a frontend.

```yaml
webui:
  path: webui                   # required, relative to the exported tree
  package_manager: auto         # auto | bun | npm — default auto
  build_command: build          # default build
  install_command: null         # null picks bun --frozen-lockfile or npm ci
```

The bundle is built before the wheel so the wheel carries it and the guest never
needs Node.

### `build`

Required when `source.kind` is `repo`; the tool rejects a `repo` source without
one, naming the omission.

```yaml
build:
  python_version: "3.14"    # default: defaults.python_version
  lock_requirement: null    # null compiles the checkout's own pyproject.toml
  ref: null                 # overrides source.ref
  fetch: false              # git fetch before resolving ref
  verify_lock: true         # refuse a lock entry with no sha256
```

`lock_requirement: null` is the normal case. It compiles from the checkout's own
`pyproject.toml` so the tree tracks the fork, rather than from a PyPI release
that may lag it.

### `config`

Renders YAML on the controller. See [rendering.md](rendering.md).

```yaml
config:
  target: "{{ app_state_dir }}/config.json"   # required
  files: [apps/<name>/config.yml]             # required; a string is accepted
  mode: "0640"                                # default 0640
  vars:                                       # optional, values keep their type
    port: 9000
```

`target` is resolved by Ansible, so a `{{ }}` expression there is fine. `vars`
values keep the type YAML gives them: `port: 9000` is an integer, and a
whole-value `{{ port }}` renders the integer.

### `secrets`

Names of keys required in `.env` before this application can be installed.

```yaml
secrets:
  - myapp_provider_api_key
  - myapp_webui_token_secret
```

`appctl install` refuses to start while any of them is unset, and names the
missing ones. See [secrets.md](secrets.md).

## Deploying

`app.yaml` says an application *exists*; the inventory says it is *deployed*.
The two are separate, and `appctl` reports them separately.

An application is deployed when its name appears in an `app_roles` list under
`inventory/group_vars/*/main.yml`:

```yaml
# inventory/group_vars/ex/main.yml
app_roles:
  - example-app
```

The inventory is gitignored — it holds your own hosts — so a fresh clone has
none. Create it from `inventory.example/`; the skill
(`.agents/skills/deployflow-add-app/SKILL.md`) has the full procedure.

`appctl list` shows that in the DEPLOYED column. Selection follows from it:

The name is **positional**: `appctl install example-app`. Flags follow it, and
anything after a bare `--` goes to ansible-playbook.

| Situation | What `appctl install` does |
|---|---|
| `example-app`, deployed | installs it |
| `example-app`, not deployed | refuses, says to add it to `app_roles` |
| no name, exactly one deployed | installs that one |
| no name, several deployed | refuses and prints the exact commands |
| no name, none deployed | refuses |

Secrets are checked for the **selected** applications only. That is the point of
requiring a selection: with N applications, demanding every secret up front means
one application you are not deploying can block the one you are.

## `state` — written by the tool

Never hand-edit. A build or an install writes it:

```yaml
state:
  built:
    commit: "a27f294f52293c476ff8ccc546439be203b066ca"
    wheel_sha256: "7cf11926d6329554810538f751e3d6f5dca0f6e265179350710e6f0de2c2a004"
    packages: 96
    at: "2026-10-11T11:58:31Z"
  installed:
    at: "2026-10-11T11:58:14Z"
    changed: 0
    failed: 0
    tasks: 71
    seconds: 40
```

An install is recorded whether it passed or failed. Only `built` and `installed`
are accepted; anything else is rejected, because the next write would drop it
silently.

The file is edited as text, not loaded and re-dumped: `app.yaml` carries 18
comment lines and a PyYAML round-trip destroys every one. Only the `state:`
block is rewritten. See [../src/appctl/state.py](../src/appctl/state.py).

**The trade:** a timestamp in a committed file is a diff on every run. That is
what a record is, but it is churn in review.

## Substitutions in rendered files

Two syntaxes, and the difference is the point:

| | |
|---|---|
| `{{ name }}` | substituted by `appctl` on the controller, from `config.vars` plus the built-ins below |
| `${NAME}` | **left exactly as written**, resolved by the application at runtime from its EnvironmentFile |

Built-ins: `app_name`, `app_home`, `app_dir`, `app_state_dir`. The three paths
mirror `common/python/defaults/main.yml`. Anything else must be declared under
`config.vars`.

A placeholder occupying a whole value keeps its declared type. A placeholder
inside a longer string is always a string, because concatenation is what it means:

```yaml
port: "{{ api_port }}"                  # → 8900   (an int)
workspace: "{{ app_state_dir }}/work"    # → "/opt/myapp/state/work"
```

## Validation

Everything is checked at load, before any command does work:

| Problem | Message |
|---|---|
| misspelled key | `unknown key in applications.x: <key>` |
| `repo` source with no `build` | `<app> has source.kind: repo but no build block` |
| missing `guest.hostname` | `applications.x.guest.hostname is required` |
| `webui` with no `path` | `applications.x.source.webui.path is required` |
| a shared disk that is not btrfs | `shared_disks.x.filesystem must be 'btrfs'` |
| a shared volume name Proxmox cannot parse | `shared_disks.x.volume must be named vm-<vmid>-disk-<n>` |
| a shared disk with no idmap | `shared_disks.x.idmap is required` |
| an idmap that is not one-to-one | `shared_disks.x.idmap entry 'u:...' maps 4 uids, not 1` |
| an idmap over the guest's own range | `shared_disks.x.idmap entry 'u:...' maps uid 1000, which overlaps the guest's own lxc.idmap` |
| an idmap with no group mapping | `shared_disks.x.idmap has no g mapping` |
| two shared disks claiming one volume | `shared_disks.x and shared_disks.y both use the volume '...'` |
| a guest attaching a disk the manifest does not declare | `a guest attaches the shared disk 'x', which the manifest does not declare` |
| unparseable YAML | the YAML error, with its line number |

`appctl list` shows what was parsed, which is the quickest way to confirm a file
was read the way you meant it.