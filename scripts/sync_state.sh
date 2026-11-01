#!/usr/bin/env bash
#
# Record the operator's own installation state in a private repository.
#
# `app.yaml` and `inventory/` are what describe every install on this machine —
# the applications, their source pins, the guest IDs, the groups, and the
# `state:` block each build and install writes back. Both are gitignored here,
# because they name machines only the operator can reach. That is the right call
# for a public repository and the wrong one for durability: if this machine dies,
# the only description of what is running dies with it.
#
# So they are copied into a private repository and pushed. Nothing else is
# copied. `.env` holds the only credentials in the tree and this script never
# reads it; `tmp/` is evidence rather than description, and grows without bound.
#
# Called from .agents/skills/deployflow-add-app/SKILL.md after any change, and
# from .agents/skills/deployflow-init/SKILL.md the first time. It is safe to run
# at any moment: with nothing to do it says so and exits 0.
#
# Usage:
#   scripts/sync_state.sh            copy, commit if changed, push if ahead
#   scripts/sync_state.sh --check    report whether anything differs; exit 1 if so
#
# Environment:
#   DEPLOYFLOW_STATE_DIR   the private clone (default: a sibling of this checkout)

set -euo pipefail

usage() {
  cat <<'EOF'
usage: scripts/sync_state.sh [--check]

  (no flag)   copy app.yaml and inventory/ into the private clone, commit what
              changed, and push when the branch is ahead
  --check     report whether the clone differs from this checkout; exit 1 if it
              does, 0 if it is in sync, 2 if the setup itself is wrong

Environment:
  DEPLOYFLOW_STATE_DIR   the private clone (default: a sibling of this checkout)
EOF
}

MODE="sync"
case "${1:-}" in
  "") ;;
  --check) MODE="check" ;;
  -h | --help) usage; exit 0 ;;
  *) echo "sync_state: unknown argument: $1" >&2; exit 2 ;;
esac

for tool in rsync git flock cmp; do
  # A missing tool must not read as "nothing differs", which is what every
  # `|| true` below would turn it into.
  command -v "$tool" >/dev/null || { echo "sync_state: $tool is not installed" >&2; exit 2; }
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="${DEPLOYFLOW_STATE_DIR:-$(dirname "$ROOT")/deployflow-state}"
APP_YAML="$ROOT/app.yaml"
INVENTORY="$ROOT/inventory"

# The agent this machine uses for GitHub. Set only when nothing else did, and
# only when the socket exists: a script run from an agent's shell already has
# SSH_AUTH_SOCK, and overriding that would be the script's business.
if [ -z "${SSH_AUTH_SOCK:-}" ] && [ -S "$HOME/.ssh/agent.sock" ]; then
  export SSH_AUTH_SOCK="$HOME/.ssh/agent.sock"
fi

# A fresh clone of this repository has no manifest and no inventory. That is the
# truth about the machine, not a failure to record it.
if [ ! -f "$APP_YAML" ]; then
  echo "sync_state: no app.yaml here — nothing to record"
  exit 0
fi

# Refuse a state directory inside this checkout: it would be copied onto itself,
# and everything in it would then be one `git add` away from the public repo.
case "$STATE_DIR" in
  "$ROOT" | "$ROOT"/*)
    echo "sync_state: refusing to sync into a directory inside this checkout: $STATE_DIR" >&2
    exit 2
    ;;
esac

# Never create the repository here. A sync that could also create a remote could
# create one somewhere the operator did not mean; that is deployflow-init's job,
# and it asks first.
if [ ! -d "$STATE_DIR/.git" ]; then
  cat >&2 <<EOF
sync_state: $STATE_DIR is not a git clone.

Create the private repository and clone it beside this checkout:

  gh repo create <owner>/deployflow-state --private --add-readme --clone=false
  git clone git@github.com:<owner>/deployflow-state.git "$STATE_DIR"

Or set DEPLOYFLOW_STATE_DIR to an existing clone.
EOF
  exit 2
fi

if [ -z "$(git -C "$STATE_DIR" remote)" ]; then
  echo "sync_state: $STATE_DIR has no remote; nothing could be pushed to it" >&2
  exit 2
fi

# ---------------------------------------------------------------------------
# What differs
# ---------------------------------------------------------------------------
#
# rsync's dry run is the comparison, not a diff of two trees built by hand: it
# is the same code path the copy uses, so "in sync" in --check means the copy
# would move nothing.
differences() {
  # app.yaml is compared by content. rsync would compare size and mtime, and a
  # file restored from a backup with an older mtime but identical bytes would
  # then read as a difference — a false alarm in the one command that is meant
  # to catch a real drift.
  if ! cmp -s "$APP_YAML" "$STATE_DIR/app.yaml"; then
    echo "app.yaml differs"
  fi
  # inventory/ is a tree; --checksum ignores timestamps for the same reason, and
  # --delete is what lets a group removed here be removed there. Unchanged
  # content produces no line at all, so anything printed is a real difference.
  rsync -a -c --delete --dry-run --itemize-changes \
    "$INVENTORY/" "$STATE_DIR/inventory/" 2>/dev/null || true
}

if [ "$MODE" = "check" ]; then
  pending="$(differences)"
  if [ -z "$pending" ]; then
    echo "sync_state: in sync ($STATE_DIR)"
    exit 0
  fi
  echo "sync_state: the record differs from $STATE_DIR:"
  printf '%s\n' "$pending" | sed 's/^/  /'
  exit 1
fi

# ---------------------------------------------------------------------------
# Copy, then commit only what the guards allow
# ---------------------------------------------------------------------------

# One sync at a time. The lock lives in .git/, which is never tracked, so it
# cannot become part of the record.
exec 9>"$STATE_DIR/.git/deployflow-sync.lock"
if ! flock -n 9; then
  echo "sync_state: another sync is running; nothing done"
  exit 0
fi

# `-a` preserves the mtime, so a copy that changes nothing leaves the clone's
# timestamps alone as well as its bytes. `install` would set them to "now" and
# make every later comparison look like a change.
rsync -a "$APP_YAML" "$STATE_DIR/app.yaml"
rsync -a --delete --exclude='.git' "$INVENTORY/" "$STATE_DIR/inventory/"

git -C "$STATE_DIR" add -A

staged="$(git -C "$STATE_DIR" diff --cached --name-only)"

# The deny list is paths first: a credential that reaches the clone is already
# too far, whether or not its content would be caught.
if [ -n "$staged" ]; then
  forbidden="$(printf '%s\n' "$staged" | grep -Ei \
    '(^|/)\.env($|\.)|\.(key|pem|p12|pfx)$|(^|/)id_(rsa|ed25519|ecdsa|dsa)|(^|/)\.ssh/' || true)"
  if [ -n "$forbidden" ]; then
    echo "sync_state: refusing to record credentials. Unstage and remove:" >&2
    printf '%s\n' "$forbidden" | sed 's/^/  /' >&2
    exit 2
  fi
  # And content second, for a key pasted into a file with an innocent name.
  if git -C "$STATE_DIR" diff --cached -U0 | grep -q 'BEGIN [A-Z ]*PRIVATE KEY'; then
    echo "sync_state: refusing to record a private key found in a staged file" >&2
    exit 2
  fi
fi

if [ -z "$staged" ]; then
  echo "sync_state: copy unchanged"
else
  summary="$(git -C "$STATE_DIR" diff --cached --stat | tail -n 1)"
  git -C "$STATE_DIR" commit -q -F - <<EOF
state: capture the install record at $(date -u +%Y-%m-%dT%H:%M:%SZ)

${summary}
EOF
  echo "sync_state: committed $(git -C "$STATE_DIR" rev-parse --short HEAD) — ${summary}"
fi

# ---------------------------------------------------------------------------
# Push whenever the branch is ahead, not only when this run committed
# ---------------------------------------------------------------------------
#
# A push that failed while the agent was locked leaves a local commit. The next
# run has nothing to commit and must still push, or the record stays stranded on
# this machine while the script reports success.
branch="$(git -C "$STATE_DIR" symbolic-ref --short HEAD)"
upstream="$(git -C "$STATE_DIR" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"

if [ -z "$upstream" ]; then
  git -C "$STATE_DIR" push -q -u origin "$branch" && echo "sync_state: pushed (new upstream origin/$branch)"
elif [ "$(git -C "$STATE_DIR" rev-list --count "$upstream..HEAD")" -gt 0 ]; then
  git -C "$STATE_DIR" push -q origin "$branch" && echo "sync_state: pushed to $upstream"
else
  echo "sync_state: in sync ($STATE_DIR)"
fi
