#!/bin/sh
set -eu
cd "$(dirname "$0")"
docker compose -f compose.yaml ps
docker compose -f compose.yaml logs --tail=40 neutralis-pools
