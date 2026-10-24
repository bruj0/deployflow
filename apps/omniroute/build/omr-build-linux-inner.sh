#!/bin/sh
# The part of the build that runs inside the Linux container.
#
# Kept separate from the wrapper so the container gets one small file rather
# than a bind mount of the whole role directory: the mount is read-only and
# this file has no dependency on anything else.
#
# The build sha is passed in rather than read from git here: the container has
# the checkout mounted, but reading git inside it would give the same answer
# with more moving parts.
set -eu

SHORT="${1:?usage: inner.sh <short-sha>}"
cd /src

echo "=== node ==="
node --version
npm --version

# A compiler, before anything is installed.
#
# better-sqlite3 is an OPTIONAL dependency and it compiles a native binding on
# install. With no toolchain in the image its install script fails, npm treats
# the optional dependency as skippable, and it is silently absent from the
# tree. The application then falls back to another driver, or fails at the
# first query, and nothing says the binding is missing.
#
# The node:24-bookworm-slim image has no gcc at all, which is why this is
# here and not in the Dockerfile.
if ! command -v g++ >/dev/null 2>&1; then
  echo "=== installing the build toolchain ==="
  apt-get update -qq
  apt-get install -y -qq --no-install-recommends build-essential python3
fi
g++ --version | head -1
make --version | head -1

echo "=== npm ci ==="
npm ci --no-audit --no-fund

echo "=== build:release ==="
OMNIROUTE_BUILD_SHA="$SHORT" npm run build:release

echo "=== build done ==="
ls -la dist/server.js dist/BUILD_SHA

# The native binding must be there, and it must be a Linux one. A tree with no
# better-sqlite3, or with a darwin binding in it, is a tree that will fail on
# the guest — and the failure is late.
echo "=== native binding ==="
ls -la node_modules/better-sqlite3/prebuilds/ 2>/dev/null \
  || ls -la node_modules/better-sqlite3/build/Release/*.node 2>/dev/null \
  || {
    echo "FATAL: better-sqlite3 has no native binding in the built tree" >&2
    exit 1
  }
