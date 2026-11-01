# Plan: record the operator's installation state in a private repository

**Status:** approved 2026-11-01, not yet implemented. §1 records the one thing
already done, and how to undo it.

---

## 1. Already done, and how to undo it

`bruj0/deployflow-state` **exists and is private**, with its initial README and
nothing else. It was created with `gh repo create … --private`.

```bash
gh repo delete bruj0/deployflow-state --yes    # remove it
rm -rf /home/bruj0/projects/deployflow-state   # remove the clone
```

No file has been copied into it, no script has been written, and no document in
this repository has been changed. If you would rather the repo were named
something else, renaming before the first sync costs nothing; after it, the
clone path and the script's default would both need updating.

---

## 2. The problem

Two files describe every install on this machine, and neither is in any git
repository:

| File | What it holds | Ignored by |
|---|---|---|
| `app.yaml` | every application, its target, its source pin, its secret *names*, and the `state:` block each build and install writes back | `.gitignore:31` |
| `inventory/` | the Proxmox host, each guest's vmid, hostname and group, `app_roles`, per-guest sizing | `.gitignore:39` |

That is the right call for a repository a stranger can clone — one of them names
machines only you can reach, the other names your VMIDs. It is the wrong call
for durability: if this machine dies, the only description of what is running on
`kvm.bruj0.net` dies with it, and `appctl list` on a fresh clone honestly reports
"no guest" for everything.

The record belongs somewhere private and versioned, not in this repository and
not only on this disk.

---

## 3. What the private repository holds

```
deployflow-state/
  README.md        what this is, what is excluded, how to restore  (hand-written)
  app.yaml         copied from the deployflow checkout
  inventory/       copied from the deployflow checkout
    hosts.yml
    group_vars/<group>/main.yml
```

**Excluded, on purpose:**

| Path | Why |
|---|---|
| `.env` | the only credentials in the tree; gitignored, mode 0600, and never read by the sync script |
| `tmp/` | run logs — kept forever locally, but large and not a description of the install |
| `apps/*/files/dist/` | build artifacts, reproducible from a pinned lockfile |

`app.yaml` carries secret **names** (`secrets: [...]`), never values. `inventory/`
carries hostnames, addresses and VMIDs. Both are private but neither is a
credential, which is exactly the boundary this repository already draws.

A guard in the script refuses the commit if the staged set contains `.env`, a
`*.key`/`*.pem`/`id_*` file, or `-----BEGIN … PRIVATE KEY-----` in any staged
blob — so a future edit that pulls a secret in fails loudly instead of publishing
it to a private repo that still has more readers than the operator.

---

## 4. The sync script

`scripts/sync_state.sh`, in **this** repository. Generic: it names no
application, and it takes the state repository's location from
`DEPLOYFLOW_STATE_DIR`, defaulting to a sibling of the deployflow checkout.

```bash
scripts/sync_state.sh            # copy, commit if changed, push if ahead
scripts/sync_state.sh --check    # report what differs, change nothing
```

Behaviour, in order:

1. **Refuse to guess.** No `app.yaml` → "nothing to record", exit 0 (that is a
   fresh clone, not an error). No state clone, or one without a remote → print
   the two exact commands to fix it and exit 2. The script never creates a
   repository; that is `deployflow-init`'s job (§6), and a sync that could also
   create a remote would be one that could create one somewhere you did not
   mean.
2. **Lock.** `flock` on a file in `$XDG_RUNTIME_DIR`, so a burst of triggers
   cannot interleave.
3. **Copy.** `app.yaml` as one file; `inventory/` with `rsync -a --delete`, so a
   group deleted here is deleted there. Nothing else is read.
4. **Guard.** Stage with `git add -A`, then refuse the commit if the staged set
   matches the denylist above.
5. **Commit** only when something is staged. Message:

   ```
   state: capture the install record at 2026-11-01T20:31:05Z

    app.yaml                | 8 +++++---
    inventory/hosts.yml     | 3 +++
    2 files changed, 9 insertions(+), 2 deletions(-)
   ```

6. **Push** when the branch is ahead of its upstream — not only when this run
   committed, so a push that failed while the agent was locked is retried by the
   next run. Never `--force`.

### Push authentication

The remote is SSH, so the push needs the Bitwarden agent. The script exports
`SSH_AUTH_SOCK="$HOME/.ssh/agent.sock"` when that socket exists, because a
non-interactive run — cron, or an agent's shell — does not inherit it. Measured
now: the socket is present and holds three ED25519 keys.

**Failure mode:** if the agent is locked, the commit succeeds locally and the
push fails; the script exits non-zero, logs it, and the next run retries. Nothing
is lost, because the commit is already on disk.

---

## 5. The trigger — the repository's own skill

There is no daemon. The sync runs because the workflow that changes these files
ends with it: the skill an agent follows to add or change an application gains a
final step, and `AGENTS.md` makes that a rule rather than a suggestion.

Three places name it, and all three are part of this change:

- `.agents/skills/deployflow-add-app/SKILL.md` — a last step, **Sync the
  installation record**, running `scripts/sync_state.sh` after the final checks
  pass. The skill already ends by proving the service; this step records what
  was proved, with the context of the run that changed it.
- `AGENTS.md` — "any change to `app.yaml` or `inventory/` ends with a sync", so
  an agent working without the skill is bound the same way.
- The README, so a human reading it knows the mechanism exists.

Why the skill and not a background job: these two paths are gitignored, so the
only thing that ever changes them is a workflow step — an install, or an edit to
the inventory. Putting the sync at the end of that workflow records the change
where it happens, and can say why; a timer knows only that a file moved.

**Honest limitations**, both of which the docs state rather than hide:

- A run started outside the workflow — `appctl install` typed into a shell —
  does not sync until someone runs the skill or the script. The
  `scripts/sync_state.sh --check` line in `## Before you commit` is the net: it
  reports a difference instead of letting the record drift.
- An inventory edit made with no agent involved is captured the same way, by
  that one command.

Measured alternatives, and why each was not chosen:

| Option | Why not |
|---|---|
| cron every minute | not unavailable — cron runs here (`pid 507`) — but wrong in kind: it syncs at an arbitrary moment, cannot say *why* a file changed, and would commit a half-finished edit |
| systemd `PathChanged=` + timer | unavailable on this host: `systemctl --user` answers "Failed to connect to bus", so it was never an option here |
| a wrapper that runs `appctl` then syncs | changes how the tool is invoked, and a run started any other way still skips the record |
| a hook inside `appctl` | a config-driven command is code execution from YAML, which this repository's own rule ("configure with YAML, not with code") forbids |
| a git hook in the state repo | `app.yaml` is gitignored here, so no commit in *this* repo ever touches it |

The script does not change because of this decision. What disappears is
`--install`: there is nothing to install, and one fewer mechanism that could be
half-configured.

---

## 6. The new skill: `deployflow-init`

`.agents/skills/deployflow-init/SKILL.md`, in the shape `deployflow-add-app`
already uses — frontmatter with `name` and `description`, a numbered process,
and postconditions a green run must satisfy. It takes a machine from "a checkout
of this repository" to "connected to the Proxmox host, with a real inventory and
a synced state repository". On a machine that is already configured it checks
and reports rather than rewriting.

Its steps, and what each one proves:

1. **Prerequisites.** `ansible-playbook`, the two collections from
   `requirements.yml`, `gh` authenticated with `repo` scope, `ssh -T
   git@github.com`, and an SSH agent socket with a key loaded. Each failure
   names the command that fixes it.
2. **Inventory.** Absent → create it from `inventory.example/` and ask for the
   four values a template cannot know: the Proxmox host, the SSH port, the SSH
   user, and the public key `common/lxc` injects. Present → read it back and
   report it, never overwrite it.
3. **Access.** `ansible pve -m ping` must return `SUCCESS`; `pct list` names the
   VMIDs in use, so a free one is chosen rather than guessed; `pvesm status`
   confirms the storage named in `defaults.storage`; the template named in
   `defaults.template` exists in `/var/lib/vz/template/cache`.
4. **Addresses.** For every guest under `lxc`, `pct exec <vmid> -- hostname -I`,
   printed as a vmid / hostname / address table. **Read-only.** The inventory
   deliberately carries no address — Proxmox leases one and `common/lxc`
   discovers it — so this is for the operator (what an MCP client or an `ssh`
   command needs), never written back.
5. **Secrets.** `appctl secrets check` names what is missing; `appctl secrets
   init` creates the gitignored `.env`; `appctl secrets set NAME --generate` per
   key. The skill never prints a value.
6. **The state repository.** Missing → `gh repo create <owner>/deployflow-state
   --private`, clone it beside the checkout, run `scripts/sync_state.sh`.
   Present → run the sync. Then verify: the script's own line, `git log -1
   --stat`, and the credential scan from §10.
7. **Report.** A table of what was checked and what it answered, in the shape
   `appctl` already prints for its `check:` lines — so a green report is
   evidence, not an "ok".

Idempotent by construction: every step reads before it writes, and the only
files it creates are ones that are absent.

---

## 7. Restore

The point of the exercise. On a fresh machine:

```bash
git clone git@github.com:bruj0/deployflow.git
git clone git@github.com:bruj0/deployflow-state.git deployflow-state
cd deployflow
cp ../deployflow-state/app.yaml .
cp -r ../deployflow-state/inventory .
appctl secrets check     # will name the keys that are missing; set them with `appctl secrets set`
appctl list              # every application, its guest, and when it was last installed
```

Secrets are **not** in the state repo, so a restore is not a complete recovery of
a runnable machine — it is a complete recovery of *what was there*. That is the
honest boundary, and it is the same one the repository already draws for `.env`.

---

## 8. Documentation and skill changes

### `.agents/skills/deployflow-add-app/SKILL.md` (existing)

- **A final step**, *Sync the installation record*: run `scripts/sync_state.sh`
  and check its exit status. Placed after "Prove it", so the record is written
  only once the role's own checks have passed.
- **One sentence near the top** binding every change, not only a new
  application: an edit to `app.yaml` or `inventory/` is finished when the sync
  has run.

### `.agents/skills/deployflow-init/SKILL.md` (new)

The skill described in §6. Its `description` has to earn its trigger — an agent
reads that line and decides whether to load the skill — so it names the
situations rather than the topic: "set up DeployFlow on a new machine", "the
inventory is missing", "check that DeployFlow can reach Proxmox", "where are my
guests", "the state repository is not initialised".

### `README.md`

- **New section**, before `## Documentation`: *"Your installation record"* —
  what is ignored and why, where it is recorded, the command that syncs it, and
  the restore recipe.
- **One row corrected** in the Features table. It currently says the private
  inventory's hosts "stay out of git", which stops being true. It becomes "stay
  out of *this* repository and are recorded in a private one".
- **TOC entry** for the new section, matching the hand-maintained list.
- **One row** in the Documentation table for the new skill, beside the add-app
  skill.

### `AGENTS.md`

- **Rewrite** `## The inventory is not in git` → `## The inventory and the
  manifest are not in this git`. Keep every existing reason, and add: the record
  is synced to `deployflow-state` by the workflow; a change that adds or moves a
  guest is not complete until the sync has run; and the rule that neither file
  may ever be committed here stays exactly as it is.
- **Update the tree comment** at line 26, which currently reads
  `inventory/ … GITIGNORED, and absent on a fresh clone`.
- **Add one line** to `## Before you commit`: `scripts/sync_state.sh --check`
  must report no difference, or the run has left the record behind.
- **Name the new skill** where an agent preparing a machine would look, next to
  *Adding an application*.

### The documentation site

`.agents/skills/deployflow-add-app/SKILL.md` is a **published page**: it is in
`PAGES` (`scripts/build_site_docs.py:51` → `project/add-app-skill.md`) and in the
nav as *Add-app skill*. Editing it therefore means running the site build before
the commit — `python scripts/build_site_docs.py --clean && mkdocs build --strict`,
which is already in the pre-commit checklist.

A new page has to be registered **twice**, and getting it half right breaks the
strict build: `PAGES` decides whether the file is copied into `site_src/`, and
`nav` decides whether MkDocs accepts what it finds there. A copied page with no
nav entry fails `--strict` with "not included in the nav".

**Decided:** publish `deployflow-init` beside the add-app skill — one line in
`PAGES`, one in `nav`. A reader who needs to stand a machine up is exactly the
reader the site serves, and the README will point at it.

---

## 9. Decisions (answered 2026-11-01)

1. **Repo name and location.** **Decided:** keep `bruj0/deployflow-state`,
   cloned at `~/projects/deployflow-state`. The script's default state directory
   is a sibling of the checkout, so nothing needs configuring.
2. **Trigger.** **Decided:** skill-driven, with no background job.
   `scripts/sync_state.sh --check` in the pre-commit checklist is the net for a
   run or an edit that happened outside the workflow. The limitation in §5
   stands as written.
3. **Scope.** **Decided:** `app.yaml` + `inventory/` only. `tmp/runs/` stays out:
   it is evidence, not a description, and it grows without bound.
4. **Controllers.** **Decided:** this machine is the only one that pushes to the
   state repository, so the non-fast-forward path in §4 should never fire.
5. **Secrets boundary.** **Confirmed:** the state repository is not a secret
   store. `.env` stays local; a restore recovers *what was there*, not a
   runnable machine.
6. **The init skill's reach.** **Decided:** preflight only. It checks, creates
   what is absent, and reports; it does not run an install. That keeps it
   read-only on a configured machine and idempotent by construction.
7. **Publishing the init skill.** **Decided:** publish it beside the add-app
   skill — one line in `PAGES`, one in `nav` (§8).

---

## 10. Acceptance, once approved

```bash
scripts/sync_state.sh --check     # before: reports the two paths as different
scripts/sync_state.sh             # copies, commits, pushes
git -C ~/projects/deployflow-state log -1 --stat
scripts/sync_state.sh             # second run: "in sync", exit 0

# the step the add-app skill now ends with, after an install:
appctl install xerj && scripts/sync_state.sh
git -C ~/projects/deployflow-state log -1 --format=%s   # the state: block

printf '\n# hand edit\n' >> inventory/group_vars/xj/main.yml
scripts/sync_state.sh
git -C ~/projects/deployflow-state log -1 --format=%s   # the edit

# the init skill on an already-configured machine:
# follow it end to end and confirm every step reports, and none rewrites a file
```

`touch app.yaml` on its own is **not** a test: rsync copies it, git sees an
identical blob, and no commit happens. That is correct behaviour — a change with
no content is not a change worth recording.

The last check is the one that matters most: a private repository is still a
published one from the point of view of the machine's future.

```bash
grep -rIl -e 'BEGIN .*PRIVATE' -e 'ApiKey [0-9a-f]\{32,\}' ~/projects/deployflow-state \
  || echo 'no credential in the record'
```
