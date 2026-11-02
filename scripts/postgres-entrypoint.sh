#!/bin/sh
# Adapt private host bootstrap files before the official image drops privileges.
# The SQL contains role passwords: never make it world-readable or log its contents.
set -eu

if [ "$(id -u)" != 0 ]; then
    printf '%s\n' 'BankSense PostgreSQL bootstrap must start as root' >&2
    exit 1
fi

init_dir=/docker-entrypoint-initdb.d
install -d -m 0700 -o postgres -g postgres "$init_dir"
install -m 0600 -o postgres -g postgres /run/bootstrap/roles.sql "$init_dir/01-roles.sql"
exec /usr/local/bin/docker-entrypoint.sh "$@"
