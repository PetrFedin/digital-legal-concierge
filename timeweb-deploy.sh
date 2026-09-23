#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
COMPOSE_FILES=docker-compose.timeweb.yml:docker-compose.timeweb.split.yml exec bash ./deploy.sh
