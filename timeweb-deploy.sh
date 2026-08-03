#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
COMPOSE_FILE=docker-compose.timeweb.yml exec bash ./deploy.sh
