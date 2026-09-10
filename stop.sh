#!/bin/sh
set -eu
cd "$(dirname "$0")"
docker compose -f compose.yaml down
