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
```

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

Two kinds, because they describe genuinely different things.

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
```

`appctl build <name>` on a `package` source exits non-zero and says there is
nothing to build. That is deliberate: silently succeeding would hide a
misconfiguration.

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
`inventory/group_vars/*.yml`:

```yaml
# inventory/group_vars/example-app.yml
app_roles:
  - example-app
```

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
block is rewritten. See [../state.py](../state.py).

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
| unparseable YAML | the YAML error, with its line number |

`appctl list` shows what was parsed, which is the quickest way to confirm a file
was read the way you meant it.