# Security

Report a vulnerability privately rather than in a public issue.

Use GitHub's private vulnerability reporting on this repository, or email the
maintainer. Please include the version or commit, the target you were using
(`proxmox_lxc` is the only implemented one), and the steps that reproduce it.

What to expect: an acknowledgement, then a fix or an explanation of why the
report is not a vulnerability.

## What this tool does and does not protect

- **Secrets** live in a gitignored `.env`, mode 0600. They reach Ansible in a
  mode-0600 extra-vars file, never in `argv`, so they do not appear in `ps`.
  The tool redacts password-shaped values from the logs it writes, but that is
  a best effort against a known set of shapes, not a guarantee.
- **The guest's SSH host key** is trusted on first contact (`accept-new`). The
  `proxmox_lxc` target removes a stale `known_hosts` entry for an address it
  has just created, because a DHCP lease can hand an old address to a new
  container.
- **The Proxmox host** is reached over SSH and needs permission to run `pct`.
  The tool does not manage that permission.
