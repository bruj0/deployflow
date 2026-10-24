<p align="center">
  <img src="assets/logo.png" alt="DeployFlow" width="420">
</p>

# DeployFlow

**Point your coding agent at this repository and tell it to install an
application. It writes the deployment, you review it, and the service is
verified before anyone calls it done.**

DeployFlow is a workflow for delivering applications to servers. It defines
what an application needs, how it should be installed, and how we know it
actually works. The agent does the writing. The workflow does the proving.

The current implementation delivers to a container on a Proxmox host with
Ansible. That is the first destination, not the definition of the project.
The workflow itself — manifest, roles, checks, verification — is what
transfers when the next destination arrives.

## The problem it solves

An agent can write a deployment in a few minutes. What it cannot do, on its
own, is tell you the deployment is *correct*:

- The playbook finished green. That proves Ansible ran, not that the service
  works. A database file exists on a server that was never bootstrapped, and a
  process binds the wrong port — both produce a green play.
- The same steps run on every machine, and the differences between machines
  are where deployments break.
- The knowledge of "how this application should be installed" lives in
  someone's head, and leaves when they do.
- The next person, or the next agent, starts from nothing.

DeployFlow turns that into a repository an agent can work inside:

| | |
|---|---|
| **The application declares itself** | A manifest says where it comes from, what it needs, and what "working" means for it. |
| **The agent writes the delivery** | A role per application, generated against a documented contract, reviewable before it runs. |
| **The workflow proves it works** | Each application carries its own checks, and they run after every install. |
| **The steps are repeatable** | A second install on the same inputs changes nothing, and says so. |
| **The knowledge is in the repo** | The next agent reads it instead of guessing. |

## How it works

```
you:  "install a new application"
agent: reads the contract, writes the manifest and the role,
       runs the checks, reports what it changed and what it verified
you:   reviews the diff, approves the run
run:   installs the pinned artifact, then checks the service actually answers
```

Four parts:

### 1. The manifest

What an application is: where its code comes from, what configuration it takes,
which secrets it needs, and which destination runs it. One file, `app.yaml`:

```yaml
applications:
  myapp:
    description: what it is, in one line
    target:
      kind: proxmox_lxc        # the destination that runs it
      options:
        hostname: myapp
        vmid: 121
    source:
      kind: repo               # a checkout, built here
      repo: /path/to/myapp
      ref: HEAD
    build:
      python_version: "3.14"
      verify_lock: true        # refuse a lock entry with no hash
    config:
      target: "{{ app_state_dir }}/config.json"
      files:
        - apps/myapp/config.yml
      vars:
        app_port: 9000
    secrets:
      - myapp_api_key
```

### 2. The application role

How it is installed, in one directory per application. The agent writes this;
it follows the contract in `AGENTS.md`:

```yaml
# apps/myapp/tasks/main.yml
- name: Install the application
  ansible.builtin.pip:
    requirements: "{{ role_path }}/files/requirements.lock"
    virtualenv: "{{ app_venv }}"
    extra_args: "--require-hashes"

- name: Check that the application answers a request
  ansible.builtin.uri:
    url: "http://127.0.0.1:{{ app_port }}/"
  register: app_endpoint
  failed_when: false

- name: Fail when the application does not answer
  ansible.builtin.fail:
    msg: "The service listens but does not answer"
  when: app_endpoint.status is undefined
```

### 3. The target

Where it runs. The manifest names one, and the target owns everything about
that destination:

```python
# src/appctl/targets/proxmox_lxc.py
@register
class ProxmoxLxcTarget(Target):
    kind = "proxmox_lxc"
    summary = "an application installed into a Proxmox LXC guest with Ansible"

    def validate(self, context):
        """Reject a bad setup before anything is built or started."""
        if not context.app().target.options.get("hostname"):
            raise TargetError("target is missing: hostname")

    def deploy(self, context, artifacts, rendered):
        """Install it, run its checks, and report what was observed."""
        ...
```

Two more destinations are named so a manifest can ask for them and get a clear
answer, but they are not built yet:

```console
$ appctl targets
implemented targets:
  proxmox_lxc: an application installed into a Proxmox LXC guest with Ansible
planned, not implemented:
  docker_vm: provision a virtual machine and run the application in Docker there
  vm: provision a virtual machine and install the application into it
```

### 4. The checks

The part most tools skip. A deployment is not done when the command succeeds.
It is done when the service answers. Each application carries its own checks,
because only the application knows what "working" means for it:

```yaml
# apps/myapp/tasks/main.yml
- name: Check that the package imports
  ansible.builtin.command:
    argv: ["{{ app_venv }}/bin/python", "-c", "import myapp"]
  changed_when: false

- name: Check that the service is listening on the requested port
  ansible.builtin.command:
    argv: [ss, -ltn]
  changed_when: false
  register: app_listeners

- name: Fail when the service is not listening on the requested port
  ansible.builtin.fail:
    msg: "The service is running but not listening on port {{ app_port }}"
  when: app_port | string not in app_listeners.stdout
```

The port check matters more than it looks. `systemctl is-active` says the
process is up; it does not say the process bound the port you asked for. A
service that came up on 8000 instead of 9000 passes an is-active check and
fails every request.

## What you get when the agent does it

A reviewer reading the diff sees the intent, not a wall of generated YAML:

- the manifest block for the new application,
- one role directory with the install steps and the checks,
- a pinned dependency lockfile, so the tree that installs is the tree that was
  reviewed,
- a recorded `state:` block saying what was built and when it was last
  installed.

The agent's own reasoning stays in its session. What lands in the repository is
declarative and reviewable.

## Features

| Feature | What it gives you | Where |
|---|---|---|
| **Declarative application manifest** | One file describes every application: source, config, secrets, destination. Unknown keys are an error, so a typo fails loudly. | `app.yaml` |
| **Agent-written application roles** | A scaffold, a contract and a checklist, so an agent can write a new role that matches the others. | `.agents/skills/deployflow-add-app/` |
| **Reproducible artifacts** | Every dependency pinned by hash. The build refuses a lockfile without one. | `appctl build` |
| **Application-level verification** | Each app checks that it imports, is active, listens where it was asked to, and answers a request. | `apps/<app>/tasks/` |
| **Idempotency check** | A second install reports `changed=0`, which proves the run is repeatable rather than reinstalling itself. | `appctl install` |
| **Secrets out of the repository** | Secrets live in a gitignored file, reach the run in a mode-0600 file, and never appear in `argv` or in the logs. | `appctl secrets` |
| **Pluggable destinations** | A target interface separates "what the application is" from "where it runs". One target ships today. | `src/appctl/targets/` |
| **Fails before side effects** | An unsupported destination or a missing secret stops the run before anything is built, downloaded or started. | `appctl install` |
| **Run evidence** | Every run keeps its commands, log, recap and errors, so a failure is a file to read rather than a scrollback to search. | `tmp/runs/` |
| **Build hosts as applications** | A guest whose purpose is to build other applications is a `toolchain` source: it installs no service, and its role asserts the machine can build rather than that an install succeeded. | `apps/builder/` |

## Requirements

### To use the workflow with an agent

| Requirement | Why |
|---|---|
| A coding agent that can read a repository and write files | The agent writes the manifest and the role. |
| An agent that follows the repository's instructions | `AGENTS.md` and `.agents/skills/deployflow-add-app/` hold the contract and the checklist. |
| A git repository per deployment | The manifest and the roles are the source of truth and the review surface. |
| A place to keep secrets | A `.env` file, gitignored. The agent never needs to read a secret value. |

### To run a delivery on the current target

| Requirement | Detail |
|---|---|
| A Proxmox VE host | You need `pct` and SSH access to it. |
| A cached Linux template on that host | The guest is created from it. |
| SSH access from the controller to the host | The framework drives `pct` over SSH. |
| `ansible-playbook` on the controller | The play does the configuring. |
| Python 3.12 or later on the controller | That is the CLI's own requirement. |
| A real application to install | The sample role is a reference. Point the agent at your application. |

The first guest also needs a bridge and storage on the Proxmox host, and a
network path from the controller to the guest. The role discovers the guest's
address over DHCP, so you do not configure one.

SSH access means whatever your SSH setup already provides. The framework
does not read `~/.ssh/config` by name, and it does not need one. If you keep
your hosts in an SSH config, name the host in the inventory and the alias
resolves on its own:

```yaml
all:
  children:
    proxmox:
      hosts:
        pve:
          ansible_host: pve.example.net   # a Host block in ~/.ssh/config
          ansible_user: root
```

### To add a new destination

| Requirement | Detail |
|---|---|
| A target implementation | One file under `src/appctl/targets/`. See [docs/targets.md](docs/targets.md). |
| The target's own prerequisites | It validates them before the run starts. |
| Checks that fit the destination | The application's own checks still apply; the target adds its own. |
| An acceptance test on a real host | The same application, installed twice, with one deliberate failure caught by a check. |

## Try it

```bash
git clone https://github.com/bruj0/deployflow.git
cd deployflow
```

Then tell your agent:

> Read AGENTS.md and .agents/skills/deployflow-add-app/SKILL.md, then add support for
> <your application>. Use the deployflow-add-app skill. Show me the diff before you run
> anything.

The skill covers the manifest, the role, the pinned lockfile, the checks, and
the traps that make a run reinstall itself forever.

To look around first:

```bash
appctl targets      # which destinations exist, and which are not built yet
appctl list         # what this repository has configured
```

## Documentation

| | |
|---|---|
| [docs/overview.md](docs/overview.md) | The current implementation: the `appctl` command, the Proxmox LXC target, the manifest and the roles |
| [docs/targets.md](docs/targets.md) | The target interface, and how to add a destination |
| [docs/architecture.md](docs/architecture.md) | How the parts fit together, and the rules that must hold |
| [docs/configuration.md](docs/configuration.md) | Every key in the manifest |
| [docs/build-pipeline.md](docs/build-pipeline.md) | How an artifact is produced and pinned |
| [docs/rendering.md](docs/rendering.md) | How YAML becomes the configuration the server reads |
| [docs/secrets.md](docs/secrets.md) | How a secret reaches the server |
| [AGENTS.md](AGENTS.md) | The rules for changing this repository |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to contribute, and what a change must preserve |

## Status

Early. The Proxmox LXC destination is implemented and tested against one host.
Two more destinations are interfaces only: they are named, so a manifest can
ask for them and get a clear answer, and they fail before anything is built.
See [docs/targets.md](docs/targets.md) for what each would have to prove before
it could be called supported.

## License

MIT. See [LICENSE](LICENSE).
</content>