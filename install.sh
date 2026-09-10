#!/bin/sh
set -eu
cd "$(dirname "$0")"
command -v docker >/dev/null 2>&1 || { echo "Docker não encontrado." >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "Docker Compose não disponível." >&2; exit 1; }
mkdir -p data
chmod 700 data
docker compose -f compose.yaml config >/dev/null
docker compose -f compose.yaml up -d --build
echo "Neutralis Pools iniciado em http://umbrel.local:8788"
