#!/bin/sh
set -eu

secret_file="${SECRET_KEY_FILE:-/app/instance/flask_secret_key}"

if [ ! -s "$secret_file" ]; then
    secret_directory="$(dirname "$secret_file")"
    mkdir -p "$secret_directory"
    temporary_file="${secret_file}.tmp.$$"
    trap 'rm -f "$temporary_file"' EXIT INT TERM
    umask 077
    python -c 'import secrets; print(secrets.token_hex(64), end="")' > "$temporary_file"
    chmod 0600 "$temporary_file"
    mv "$temporary_file" "$secret_file"
    trap - EXIT INT TERM
fi

exec "$@"
