---
name: deployflow-init
description: >-
  Use when setting up DeployFlow on a machine, or when asked to check that it can
  reach Proxmox, find where the guests are, work out which VMIDs are free, see
  what is already deployed, create the inventory, or initialise the private state
  repository. Covers the preflight an operator needs before the first install:
  toolchain and GitHub access, the inventory, SSH access to the Proxmox host, the
  addresses Proxmox has leased to each guest, the secrets a run needs, and the
  first sync of the installation record. Also covers the read-only audit of a
  machine that is already configured. Do NOT use for adding an application (use
  deployflow-add-app), for choosing a Proxmox template, or for writing a new
  delivery target.
---

# Initialise DeployFlow on a machine

Read [AGENTS.md](../../../AGENTS.md) first. This skill is the preflight it
assumes: everything here is about the machine, not about an application.

**This skill is a preflight.** It reads, reports, and creates only what is
absent. On a machine that is already configured it changes nothing, and it never
creates a guest — `appctl install` does that.

Two files are the operator's own and are gitignored in this repository:

| File | Holds | Where it is recorded |
|---|---|---|
| `app.yaml` | applications, targets, pins, secret names, `state:` | private `deployflow-state` repo |
| `inventory/` | Proxmox host, guest VMIDs, groups, `app_roles`, sizing | private `deployflow-state` repo |

Neither is ever committed here. Step 6 records them in the private repository;
that is what makes them survive this machine.

---

## 1. Prerequisites

Run each check. A failure names the command that fixes it — do not continue past
one, because every later step will fail in a way that points somewhere else.

```bash
command -v ansible-playbook                       # pipx install ansible-core
python3 -c 'import ansible; print(ansible.__version__)'
python3 -m appctl --help                          # PYTHONPATH=src python3 -m appctl
gh auth status                                    # gh auth login
timeout 15 ssh -T git@github.com                  # an agent with a GitHub key loaded
```

- **`appctl`** is usually not installed system-wide in this repository. Use
  `PYTHONPATH=src python3 -m appctl <command>` from the checkout.
- **Collections.** `ansible-galaxy collection install -r requirements.yml`. Two
  are declared: `community.general` and `ansible.posix`.
- **GitHub over SSH.** The state repository is cloned and pushed over SSH. If
  `ssh -T git@github.com` says "Permission denied", the key agent is locked —
  start it before going further, or step 6 will commit locally and fail to push.
- **No `pct` on this machine.** `pct` runs on the Proxmox host only, reached
  over SSH. Nothing here needs it locally.

## 2. The inventory

Check whether it exists before anything else:

```bash
test -d inventory && echo present || echo absent
```

**Absent** — this is a fresh clone, and that is expected, not broken. Create it
from the templates and ask the operator for the four values a template cannot
know:

```bash
mkdir -p inventory/group_vars
cp inventory.example/hosts.yml inventory/hosts.yml
cp -r inventory.example/group_vars/. inventory/group_vars/
```

| Value | Key | Where |
|---|---|---|
| Proxmox host, SSH port, SSH user | `ansible_host`, `ansible_port`, `ansible_user` | `inventory/hosts.yml`, group `proxmox` |
| The public key `common/lxc` injects | `lxc_ssh_public_key` | `inventory/group_vars/all/main.yml` |

**Present** — read it back and report it. Do not overwrite a working inventory
to make it look like the template.

Two rules that cost an afternoon each, and both fail with a warning that is easy
to miss among a long inventory:

- **A group name carries no hyphen**, and a **host may not share a group's
  name**. `ex` / `example-app` is the pair to copy; `xj` / `xerj` is the other.
- **No address is written into the inventory.** Proxmox leases one and
  `common/lxc` discovers it on every play. A static address on an Ubuntu 26.04
  guest comes up with no IPv4 at all.

## 3. Access to the Proxmox host

Always pass the inventory explicitly — without `-i`, Ansible reads a different
one and the failure looks like a playbook problem.

```bash
ansible -i inventory/hosts.yml pve -m ping
ansible -i inventory/hosts.yml pve -m shell -a 'pct list'
ansible -i inventory/hosts.yml pve -m shell -a 'pvesm status'
ansible -i inventory/hosts.yml pve -m shell -a 'ls /var/lib/vz/template/cache/'
```

What each answer means:

- **`ping` must be `SUCCESS`.** `UNREACHABLE` is the SSH route, the port, or the
  key — not DeployFlow.
- **`pct list` is the answer to "which VMIDs are free".** Report the VMIDs and
  hostnames in use; a VMID already present fails at `pct create`.
- **`pvesm status` confirms the storage** named by `defaults.storage` in
  `app.yaml` exists and is active.
- **The template directory must contain** the exact filename in
  `defaults.template`. A near-miss (`26.04-1` vs `26.04-2`) is a different base
  image, and the role pins it for that reason.

## 4. Addresses

The inventory deliberately has no `ansible_host` for a guest. Read the lease back
from Proxmox, for each host listed under `lxc:` in `inventory/hosts.yml`:

```bash
ansible -i inventory/hosts.yml pve -m shell -a 'pct exec <vmid> -- hostname -I'
```

Print a table — vmid, hostname, first IPv4 — because this is the answer to
"where is it": what an `ssh` command, a browser or an MCP client needs, and what
the operator will ask for next.

**Read-only. Do not write the address back into the inventory.** Proxmox renews
or changes the lease, and a stored address then goes stale silently.

A guest that prints nothing is a finding, not a formatting problem: it is
stopped, or it has no lease. Report it and continue.

## 5. Secrets

```bash
PYTHONPATH=src python3 -m appctl secrets check     # exit 1 names what is missing
PYTHONPATH=src python3 -m appctl secrets init      # writes .env (0600) + .env.example
PYTHONPATH=src python3 -m appctl secrets set <name> --generate
```

- **Never print a value**, and never pass one in `argv` — it is visible in `ps`.
- `.env` is gitignored and mode 0600. It is **not** in the state repository and
  must not be.
- An application may need no secret at all. An empty `secrets: []` is a
  decision, not an omission — the XERJ node generates its own admin key on the
  guest.

## 6. The private state repository

The two operator files are recorded in `deployflow-state`, a private repository
cloned beside this checkout. `scripts/sync_state.sh` copies them, commits what
changed, and pushes.

```bash
gh repo view <owner>/deployflow-state          # does it exist?
```

**Absent**, create it and clone it beside the checkout:

```bash
gh repo create <owner>/deployflow-state --private \
  --description "The operator's own DeployFlow installation record" \
  --add-readme --clone=false
git clone git@github.com:<owner>/deployflow-state.git ../deployflow-state
```

**Present**, make sure the clone exists at the same path
(`DEPLOYFLOW_STATE_DIR` overrides it).

Then sync and verify:

```bash
./scripts/sync_state.sh                 # copy, commit, push
./scripts/sync_state.sh --check         # exit 0 when the record is current
git -C ../deployflow-state log -1 --stat
grep -rIl -e 'BEGIN .*PRIVATE' -e 'ApiKey [0-9a-f]\{32,\}' ../deployflow-state \
  --exclude-dir=.git || echo 'no credential in the record'
```

The script refuses to stage `.env`, `*.key`, `*.pem`, `id_*`, or a PEM private
key block. If it refuses, that is the guard working: remove the file, do not
work around the check.

**If the push fails**, the GitHub key agent is locked. The commit is already on
the local clone, and the next run pushes it — nothing was lost, and nothing
should be force-pushed.

## 7. Report

Finish with what was checked and what it answered, in the shape `appctl` already
prints for its `check:` lines. A green report is evidence; "setup complete" is
not.

```
check: ansible-playbook <version>, appctl importable, gh authenticated as <user>
check: inventory present — 1 proxmox host, <n> guests, <n> groups
check: ansible pve -m ping: SUCCESS
check: pct list: <vmids in use>
check: storage <name>: active; template <filename>: present
check: secrets: <n> required, <n> set
check: deployflow-state: <commit>, pushed to origin/<branch>
check: state repo scan: no credential
```

---

## Postconditions

A run is complete only when all of these hold.

1. `ansible -i inventory/hosts.yml pve -m ping` returns `SUCCESS`.
2. Every guest in `inventory/hosts.yml` under `lxc:` has a current address in the
   report, or is named as stopped.
3. `PYTHONPATH=src python3 -m appctl secrets check` exits 0, or names exactly the
   keys the operator still has to set.
4. `PYTHONPATH=src python3 -m appctl list` reports every application and the
   guest it is deployed to.
5. `./scripts/sync_state.sh --check` exits 0 — the private record matches this
   machine.
6. The credential scan prints nothing.

## What this skill does not do

- **It does not create a guest.** `appctl install <app>` runs the `lxc` tag and
  creates the container.
- **It does not install an application.** Use `deployflow-add-app`.
- **It does not store a secret.** `.env` stays on this machine; the state
  repository records the *names* only, and a restore needs the values re-entered.
- **It does not commit `inventory/` or `app.yaml` to this repository.** Both are
  gitignored, and a file from either landing in a commit is a leak.
