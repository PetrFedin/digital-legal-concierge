#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# Preserve the historical one-command local run. Production/Timeweb must
# receive an explicitly reviewed, full DEPLOY_EXACT_SHA from the operator.
if [ "${COMPOSE_FILE:-docker-compose.yml}" = "docker-compose.yml" ] && [ -z "${DEPLOY_EXACT_SHA:-}" ]; then
  export DEPLOY_EXACT_SHA="$(git rev-parse --verify HEAD)"
fi

exec bash ./deploy.sh
