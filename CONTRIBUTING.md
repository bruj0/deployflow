# Contributing

Read [AGENTS.md](AGENTS.md) first. It holds the rules that are not stylistic.

## Before you commit

```bash
pytest tests -q
ansible-playbook --syntax-check -i inventory/hosts.yml playbooks/site.yml
appctl install <app>   # twice; the second must report changed=0
```

`git status` must not show `.env`, `tmp/`, `apps/*/files/dist/`, `tools/` or
`inventory/`.

**`inventory/` is gitignored.** It names your own Proxmox host, the guest IDs
and the addresses of machines only you can reach. A fresh clone has no
inventory at all, and that is the expected state — the committed templates live
in `inventory.example/`, and the skill documents how to create the real one:

```bash
mkdir -p inventory/group_vars
cp inventory.example/hosts.yml inventory/hosts.yml
cp -r inventory.example/group_vars/. inventory/group_vars/
```

The syntax check above therefore needs a local inventory. On a fresh clone,
use the template instead — it exercises the same playbook and role paths:

```bash
ansible-playbook --syntax-check -i inventory.example/hosts.yml playbooks/site.yml
```

## What a change must preserve

- **An application is one directory.** Nothing in `common/` or `playbooks/`
  changes when you add one.
- **The shared code names no application and no target.** `pct` and the guest
  network live in `src/appctl/targets/proxmox_lxc.py`.
- **Every role checks that its application works.** A green playbook is not
  evidence of a working service.
- **A second run reports `changed=0`.** A role that reports a change every run
  is reinstalling itself and says nothing about its own state.
- **An unknown key is an error.** A misspelled key would otherwise be a silent
  no-op.
