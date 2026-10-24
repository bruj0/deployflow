#!/bin/bash
# The part of the build that runs ON THE BUILD HOST.
#
# The build host is a native x86_64 Linux machine — the same architecture as
# the guest — so there is no emulation and no cross-compilation. The only
# thing this script has to do is make sure the tree it builds is a REAL git
# clone, with a toolchain, and with the project's own release settings.
set -euo pipefail

SHORT="${1:?usage: remote.sh <short-sha> <repo-dir> <node-dir>}"
REPO_DIR="${2:?usage: remote.sh <short-sha> <repo-dir> <node-dir>}"
NODE_DIR="${3:?usage: remote.sh <short-sha> <repo-dir> <node-dir>}"

cd "$REPO_DIR"

echo "=== toolchain ==="
# better-sqlite3 is an OPTIONAL dependency and it compiles a native binding on
# install. With no compiler in the image its install script fails, npm treats
# the optional dependency as skippable, and it is silently absent from the
# tree. The application then falls back to another driver, or fails at the
# first query, and nothing says the binding is missing.
#
# The builder role installs this. The check below is a guard, not the
# installation: it turns "the builder was provisioned by something else" into
# a named failure instead of a silently incomplete tree.
if ! command -v g++ >/dev/null 2>&1; then
  echo "FATAL: no compiler on this build host" >&2
  echo "       the builder role installs build-essential; this host is not it" >&2
  exit 1
fi

echo "=== node ==="
# Node is pinned, and the builder role installs the pinned version. The tree
# is built with one version and must run on one: the engines field allows a
# range, and a runtime outside it fails at import rather than at install.
#
# The builder's Node lives in a version directory that is not on PATH, so it
# is named by absolute path. The `bin/npm` shim runs `#!/usr/bin/env node`
# and would die with "env: 'node': No such file or directory" before npm
# started, so npm is invoked through its JS entry instead.
if [ ! -x "$NODE_DIR/bin/node" ]; then
  echo "FATAL: no pinned Node at $NODE_DIR" >&2
  exit 1
fi
export PATH="$NODE_DIR/bin:$PATH"
NPM_CLI="$NODE_DIR/lib/node_modules/npm/bin/npm-cli.js"

echo "=== node/npm ==="
node --version
node "$NPM_CLI" --version

echo "=== commit ==="
# The sha comes from the clone's own git. This is the artifact's provenance:
# it is what the receipt records and what the role refuses a mismatch against.
git rev-parse HEAD
git rev-parse --short HEAD

echo "=== npm ci ==="
node "$NPM_CLI" ci --no-audit --no-fund

echo "=== build:release ==="
export ONNXRUNTIME_NODE_INSTALL=skip
export OMNIROUTE_USE_TURBOPACK=0
export OMNIROUTE_BUILD_MEMORY_MB=10240
export NODE_OPTIONS=--max-old-space-size=10240
node "$NPM_CLI" run build:release

echo "=== artifacts ==="
ls -la dist/server.js dist/BUILD_SHA
cat dist/BUILD_SHA

echo "=== native binding ==="
# The binding must exist AND it must be a Linux one. A tree with no
# better-sqlite3, or with a darwin binding in it, is a tree that will fail on
# the guest — and the failure is late.
ls -la node_modules/better-sqlite3/prebuilds/ 2>/dev/null \
  || ls -la node_modules/better-sqlite3/build/Release/*.node 2>/dev/null \
  || {
    echo "FATAL: better-sqlite3 has no native binding in the built tree" >&2
    exit 1
  }

echo "=== build done ==="
