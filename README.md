<p align="center">
  <img src="assets/logo.png" alt="DeployFlow" width="420">
</p>

# DeployFlow

Deploy applications to Proxmox LXC containers with Ansible, then verify the
service works — not just that the deployment command succeeded.

The tool is `appctl`. It builds a pinned artifact, installs it into a guest,
and then runs checks that reach the application the way a user does.

```bash
appctl targets                     # what can be delivered to, and what cannot yet
appctl list                        # what is configured
appctl build example-app           # produce the artifacts the role installs
appctl install example-app         # install it and check that it answers
appctl secrets check               # are the required secrets set?
```

## What it does

A stock Ubuntu template goes in, Ansible configures it, and the application
runs under systemd with a sha256-pinned dependency tree. No container image is
built, so there is no tarball to store and no builder in the loop.

| | |
|---|---|
| Install | a pinned application into a Proxmox LXC guest, with Ansible |
| Reproducibility | the lockfile carries a hash for every dependency; the build refuses one that does not |
| Verification | application-level checks after the install, not only a green playbook |
| Secrets | in a gitignored `.env`, passed to Ansible in a mode-0600 file, never in `argv` |

## Why the checks matter

Ansible reporting success proves that Ansible ran. It does not prove the
application is usable. Each of these passed review and only failed in use:

| The check | What it catches |
|---|---|
| The package imports | A dependency tree that did not install |
| The unit is active | A service systemd accepted and then dropped |
| The port is listening | A process that bound somewhere other than the requested port |
| The endpoint answers | A service that listens and returns nothing |

Every application role carries its own checks, because only the application
knows what "working" means for it. The sample role in `apps/example-app/`
shows the pattern.

## Targets

A **target** says where and how an application runs. The manifest names one:

```yaml
applications:
  example-app:
    target:
      kind: proxmox_lxc
      options:
        hostname: example-app
        vmid: 121
```

`proxmox_lxc` is the only implemented target. Two more are planned:

| Target | What it would do | Status |
|---|---|---|
| `vm` | Provision a virtual machine and install the application into it | planned |
| `docker_vm` | Provision a virtual machine and run the application in Docker there | planned |

A manifest that names an unimplemented target fails **before** it builds,
downloads or starts anything:

```
error: target kind 'vm' is planned but not implemented

provision a virtual machine and install the application into it.
implemented targets: proxmox_lxc
```

That failure is deliberate. An application that asks for a virtual machine must
not be silently installed into a container instead.

## Quickstart

You need a Proxmox VE host, a cached Linux template on it, and SSH access to
the host.

```bash
git clone https://github.com/bruj0/deployflow.git
cd deployflow

uv tool install --editable .

cp app.example.yaml app.yaml        # then edit it
cp inventory/hosts.yml inventory/hosts.yml   # already a copy; edit the host
```

Edit three things:

1. `app.yaml` — the application, its port and its target options.
2. `inventory/hosts.yml` — your Proxmox host and the guest.
3. `inventory/group_vars/all/main.yml` — your SSH public key.

Then:

```bash
appctl secrets init                  # create .env from the required keys
appctl secrets set NAME 'value'      # add each secret
appctl secrets check                 # confirm they are all set
appctl render                        # see the configuration before it lands
appctl install example-app
```

Run the install a second time. It should report `changed=0`: the same inputs
produce the same guest.

## What an application role owes the framework

An application is one directory under `apps/`. Nothing in `common/` or
`playbooks/` changes when you add one.

```yaml
# apps/myapp/defaults/main.yml
app_name: myapp        # the only required value
app_port: 9000
```

```yaml
# apps/myapp/meta/main.yml
dependencies:
  - role: python       # declares the dependency, so no playbook changes
```

`app_name` derives the service account, the directories, the virtual
environment and the unit name. `app_port` has no derivation and you must set it.

## Layout

```
app.yaml                 the one configuration file: applications and targets
common/                  framework. Names no application, anywhere.
  lxc/                   pct create from a stock template, DHCP, address discovery
  python/                toolchain, service account, venv — the contract
apps/                    one directory per application
  example-app/           the sample used by the quickstart
src/appctl/              the appctl package
  targets/               the delivery-target interface and its implementations
    proxmox_lxc.py       the only implemented target
docs/                    architecture, configuration, rendering, secrets, build
playbooks/site.yml
inventory/               guests; the only file you edit to add one
```

`roles_path = common:apps`. A role's `meta` dependency declares what it needs,
so adding an application never edits a playbook.

## Commands

| Command | What it does |
|---|---|
| `appctl list` | Every application, its target, its destination and its last install |
| `appctl targets` | The implemented targets, and the planned ones that are not built |
| `appctl init` | Write a starting `app.yaml` |
| `appctl build <app>` | Produce the artifacts the role installs |
| `appctl build --check` | Verify the committed artifacts and build nothing |
| `appctl render` | Render the application configuration, without deploying |
| `appctl install <app>` | Install it and run the checks |
| `appctl secrets init\|set\|list\|check` | Manage the `.env` |

Anything after a bare `--` goes straight to `ansible-playbook`:

```bash
appctl install example-app -- --check --diff
```

## Documentation

| | |
|---|---|
| [docs/architecture.md](docs/architecture.md) | How the parts fit together, and where the target boundary is |
| [docs/configuration.md](docs/configuration.md) | Every key in `app.yaml` |
| [docs/build-pipeline.md](docs/build-pipeline.md) | How an artifact is produced and pinned |
| [docs/rendering.md](docs/rendering.md) | How YAML becomes the configuration the guest reads |
| [docs/secrets.md](docs/secrets.md) | How a secret reaches the guest |
| [docs/targets.md](docs/targets.md) | The target interface, and how to add a target |

## Status

Early. `proxmox_lxc` is implemented and tested against one Proxmox VE node.
The `vm` and `docker_vm` targets are interfaces only. See
[docs/targets.md](docs/targets.md) for what each would have to prove before it
could be called supported.

## License

MIT. See [LICENSE](LICENSE).
