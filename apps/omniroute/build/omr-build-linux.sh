#!/bin/sh
# Build OmniRoute from source for a Linux guest and write the build receipt.
#
# WHERE the build runs is the whole point of this script.
#
# The controller is arm64 macOS. The guest is x86_64 Linux. A tree built on the
# controller carries darwin-only native binaries — sharp-darwin-arm64,
# @esbuild/darwin-arm64 — that cannot load on the guest. The failure is late
# and unrelated-looking, so it is much cheaper to build on the guest's
# platform than to diagnose it afterwards.
#
# That leaves two ways to build on x86_64 Linux, and only one of them works:
#
#   1. A container on the controller, emulated with qemu.
#      `docker run --platform linux/amd64` on an arm64 host. It gets the
#      architecture right and then falls over:
#
#        - The webpack build needs ~10 GB and the emulated process sees the
#          VM's memory, not the container's limit, so it is SIGKILLed at
#          around 5.6 GB. Raising the VM's memory to 20 GB fixes that one.
#        - esbuild is a GO binary, and the Go runtime faults under qemu:
#            fatal error: receive on synctest channel from outside bubble
#            Next.js build worker exited with code: null and signal: SIGSEGV
#          This is not a memory problem and no amount of memory fixes it.
#          Emulation is simply not reliable for this build.
#
#   2. A native x86_64 machine.
#      The Proxmox host this repository already talks to is x86_64, with 12
#      CPUs and 125 GB of RAM. The build runs there as itself: no qemu, no
#      Go-runtime faults, and `npm ci` takes one minute instead of eight.
#
# So the build runs on a BUILD HOST, over SSH, and the artifact comes back to
# the controller as a tree the role can ship.
#
# The build settings match .github/workflows/build.yml, which is the project's
# own recipe for the same artifact:
#
#   ONNXRUNTIME_NODE_INSTALL=skip  the runtime is optional and its download is
#                                   large; the package's own postinstall
#                                   handles it when it is wanted
#   OMNIROUTE_USE_TURBOPACK=0     the CI build uses webpack
#   NODE_OPTIONS                   the CI build caps the heap
#
# The build host is the guest this repository provisions for exactly this
# purpose: `appctl install builder` creates it, gives it a compiler and a
# pinned Node, and publishes its address to
# tmp/builders/builder.endpoint. The build reads that file, so the build
# host is found rather than named — a guest address is a DHCP lease and
# changes when the guest is rebuilt.
#
# Usage:
#   omr-build-linux.sh <checkout> [ref]
#
#   <checkout>   a clone of the fork on the controller
#   [ref]        the branch to build; defaults to main
#
# Writes <checkout>/.deployflow-build.json, which the role reads to refuse a
# tree that was not built from the commit it was asked for.
set -eu

CHECKOUT="${1:?usage: omr-build-linux.sh <checkout> [ref]}"
REF="${2:-main}"
HERE=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$HERE/../../.." && pwd)
REPO_URL="${OMR_REPO_URL:-https://github.com/bruj0/OmniRoute.git}"

# The builder's endpoint. Read from the file the builder role writes, not from
# a variable and not from a hostname: guest names do not resolve from the
# controller, because Proxmox's resolver is not the controller's.
ENDPOINT_FILE="$REPO_ROOT/tmp/builders/builder.endpoint"
if [ ! -f "$ENDPOINT_FILE" ]; then
  echo "FATAL: no builder endpoint at $ENDPOINT_FILE" >&2
  echo "       run 'appctl install builder' first" >&2
  exit 1
fi

# KEY=VALUE lines. Read one at a time so a missing key is an error with a
# name in it rather than an empty value that fails three lines later.
endpoint() {
  sed -n "s/^$1=//p" "$ENDPOINT_FILE"
}

BUILD_HOST=$(endpoint host)
BUILD_USER=$(endpoint user)
BUILD_WORK=$(endpoint work)
BUILD_NODE_DIR=$(endpoint node_dir)

if [ -z "$BUILD_HOST" ] || [ -z "$BUILD_USER" ] || [ -z "$BUILD_WORK" ] || [ -z "$BUILD_NODE_DIR" ]; then
  echo "FATAL: $ENDPOINT_FILE is missing a host, user, work or node_dir key" >&2
  exit 1
fi

# The user is part of the address, not a separate argument. Left out, ssh
# defaults to $USER — which on this controller is not root — and every
# connection fails with "Too many authentication failures" after the agent
# offers five keys against an authorized_keys that holds one.
BUILD_TARGET="$BUILD_USER@$BUILD_HOST"

cd "$CHECKOUT"

# The receipt is written after the build, and it names the commit the build
# ran against. The role compares that commit against the checkout's HEAD, so a
# receipt from an older build is refused rather than shipped.
COMMIT=$(git rev-parse HEAD)
SHORT=$(git rev-parse --short HEAD)
VERSION=$(node -p "require('./package.json').version")

echo "==> building $VERSION at $COMMIT on $BUILD_HOST ($BUILD_WORK)"

# The build host needs a real git clone, not a transferred tree. Two reasons,
# both measured:
#
#   - `git rev-parse --short HEAD` has to resolve, because the build script
#     reads the sha from git. A transferred tree has no .git, so the sha is
#     empty and the artifact carries no provenance.
#   - A tar of the controller's checkout carries macOS AppleDouble sidecars
#     (`._aliasConfig.ts` and hundreds like it). esbuild reads them as
#     source files and the build fails with 77 errors:
#       src/mitm/._aliasConfig.ts:1:0: ERROR: Unexpected "\x00"
#
# So the host clones the fork itself. The fork is public, so this needs no
# credential; a private fork needs a token the host already holds.
#
# The clone is then pinned to the EXACT commit the controller resolved, not to
# the branch tip. `git clone --branch main` resolves main at clone time, and
# main moves: a push between the controller reading HEAD and the builder
# cloning produces an artifact built from a commit the receipt does not name.
# The receipt is the role's provenance check, so it has to be true.
#
# The commit is fetched explicitly because a shallow branch clone may not
# contain it once the branch has moved past it.
BUILD_DIR="$BUILD_WORK/omr-build"
ssh "$BUILD_TARGET" "rm -rf '$BUILD_DIR' && \
  git clone --branch '$REF' '$REPO_URL' '$BUILD_DIR' && \
  git -C '$BUILD_DIR' fetch --quiet origin '$COMMIT' && \
  git -C '$BUILD_DIR' checkout --quiet --detach '$COMMIT' && \
  test \"\$(git -C '$BUILD_DIR' rev-parse HEAD)\" = '$COMMIT'"

# The build script is COPIED to the host, not referenced by a controller path.
# A controller path means nothing on the host, and the failure is the host
# reporting a path that does not exist on it:
#   bash: /Users/bruj0/Projects/deployflow/apps/...: No such file or directory
scp -q "$HERE/omr-build-linux-remote.sh" "$BUILD_TARGET:$BUILD_WORK/omr-build-remote.sh"

# The build itself. The toolchain is installed first because better-sqlite3
# compiles a native binding on install, and with no compiler npm treats the
# optional dependency as skippable and it is silently absent from the tree.
ssh "$BUILD_TARGET" "bash '$BUILD_WORK/omr-build-remote.sh' '$SHORT' '$BUILD_DIR' '$BUILD_NODE_DIR'"

# The artifact comes back as a tarball. Only the tree the runtime needs
# travels:
#
#   dist/            the server bundle, with its own .build/next
#   bin/             the CLI
#   node_modules/    the dependency tree, which is part of the artifact
#
# The root .build/ directory does NOT travel: it is 5.4 GB of webpack cache
# and the build has already produced the 312 MB dist/.build/next that the
# runtime reads. `.git` does not travel either. The receipt does travel — it
# is what the role reads to prove what was built.
ssh "$BUILD_TARGET" "tar -czf '$BUILD_WORK/omr-built.tar.gz' -C '$BUILD_DIR' \
  --exclude=./.git --exclude=./.build --exclude=./docs --exclude=./tests \
  --exclude=./open-sse --exclude=./node_modules ."

# The dependency tree is separate and is NOT reconstructed from a graph.
#
# The first attempt walked the CLI's imports to compute the closure of
# packages the runtime needs. That is fragile in a way that only shows up on
# the guest:
#
#   - the CLI registers its TypeScript loader with `await import("tsx/esm")`,
#     a DYNAMIC import a static scan does not see
#   - the loader then resolves imports from the whole source tree, so the
#     closure has to cover src/ and open-sse/, not just bin/
#   - @esbuild/linux-x64 and @img/sharp-linux-x64 are OPTIONAL dependencies
#     chosen by an install script that reads os and cpu, so a dependency walk
#     never reaches them
#
# Each of those produced a tree that failed on the guest with an
# unrelated-looking error. Shipping the tree `npm ci` produced is correct by
# construction, and the cost is a transfer that takes seconds.
ssh "$BUILD_TARGET" "tar -czf '$BUILD_WORK/omr-nm.tar.gz' -C '$BUILD_DIR' node_modules"

scp -q "$BUILD_TARGET:$BUILD_WORK/omr-built.tar.gz" "$CHECKOUT/../omr-built.tar.gz"
scp -q "$BUILD_TARGET:$BUILD_WORK/omr-nm.tar.gz" "$CHECKOUT/../omr-nm.tar.gz"

tar -xzf "$CHECKOUT/../omr-built.tar.gz" -C "$CHECKOUT"

# The dependency tree is unpacked into the CHECKOUT, not into
# <checkout>/node_modules. The tarball holds a `node_modules` directory, so
# extracting it into an existing node_modules produces
# <checkout>/node_modules/node_modules — and every import then fails with
# ERR_MODULE_NOT_FOUND for a package that is present.
rm -rf "$CHECKOUT/node_modules"
tar -xzf "$CHECKOUT/../omr-nm.tar.gz" -C "$CHECKOUT"
rm -f "$CHECKOUT/../omr-built.tar.gz" "$CHECKOUT/../omr-nm.tar.gz"

# Clean up the builder.
#
# The checkout is 16 GB — 7.4 GB of it is a webpack cache that never travels
# — and the two tarballs are another 1.5 GB. Left in place, they take the
# builder's disk from 51 GB free to 36 GB, and the next build fails the
# builder's own disk floor with "too small for the build". That is a
# confusing way to learn that the previous build left a mess.
#
# The artifact is on the controller by this point, so nothing here is needed
# again. The checkout is removed rather than the whole workspace, so a
# builder shared with another build keeps the other's tree.
ssh "$BUILD_TARGET" "rm -rf '$BUILD_DIR' '$BUILD_WORK/omr-built.tar.gz' '$BUILD_WORK/omr-nm.tar.gz' '$BUILD_WORK/omr-build-remote.sh'"

# The receipt. Written with a heredoc rather than by a tool, so it has no
# dependency and no chance of a subtly different schema: the role reads it
# with from_json and every field is named here.
cat > .deployflow-build.json <<EOF
{
  "commit": "$COMMIT",
  "shortCommit": "$SHORT",
  "version": "$VERSION",
  "ref": "$REF",
  "platform": "linux-amd64",
  "node": "24",
  "builtAt": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

echo "==> receipt written to $CHECKOUT/.deployflow-build.json"
cat .deployflow-build.json
