#!/bin/sh
set -eu

mkdir -p /data
chown -R neutralis:neutralis /data

exec gosu neutralis "$@"
