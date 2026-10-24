#!/bin/sh
# Build OmniRoute from source for a Linux guest and write the build receipt.
#
# The build happens inside a Linux container on purpose. The controller is
# arm64 macOS and the guest is x86_64 Linux: a tree built on the controller
# carries darwin-only native binaries (sharp-darwin-arm64, @esbuild/darwin-arm64)
# that cannot load on the guest. The failure is late and unrelated-looking, so
# it is much cheaper to build on the guest's platform than to diagnose it
# afterwards.
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
# Usage:
#   omr-build-linux.sh <checkout> [ref]
#
# Writes <checkout>/.deployflow-build.json, which the role reads to refuse a
# tree that was not built from the commit it was asked for.
set -eu

CHECKOUT="${1:?usage: omr-build-linux.sh <checkout> [ref]}"
REF="${2:-main}"
HERE=$(cd "$(dirname "$0")" && pwd)

cd "$CHECKOUT"

# The receipt is written after the build, and it names the commit the build
# ran against. The role compares that commit against the checkout's HEAD, so a
# receipt from an older build is refused rather than shipped.
COMMIT=$(git rev-parse HEAD)
SHORT=$(git rev-parse --short HEAD)
VERSION=$(node -p "require('./package.json').version")

echo "==> building $VERSION at $COMMIT"

docker run --rm --platform linux/amd64 \
  -v "$CHECKOUT:/src" \
  -v "$HERE/omr-build-linux-inner.sh:/inner.sh:ro" \
  -w /src \
  -e ONNXRUNTIME_NODE_INSTALL=skip \
  -e OMNIROUTE_USE_TURBOPACK=0 \
  -e OMNIROUTE_BUILD_MEMORY_MB=10240 \
  -e NODE_OPTIONS=--max-old-space-size=10240 \
  node:24-bookworm \
  /bin/sh /inner.sh "$SHORT"

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
