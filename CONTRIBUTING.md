# Contributing

Read [AGENTS.md](AGENTS.md) first. It holds the rules that are not stylistic.

## Before you commit

```bash
pytest tests -q
ansible-playbook --syntax-check -i inventory/hosts.yml playbooks/site.yml
appctl install <app>   # twice; the second must report changed=0
```

`git status` must not show `.env`, `tmp/`, `apps/*/files/dist/` or `tools/`.

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
