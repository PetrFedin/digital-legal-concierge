#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

image="${TEST_IMAGE:-digital-legal-concierge:test}"

docker build --pull -f Dockerfile.test -t "$image" .
docker run --rm "$image" pytest -q "$@"
