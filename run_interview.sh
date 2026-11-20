#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then
    exec .venv/bin/python cli_interview.py "$@"
fi
exec python3 cli_interview.py "$@"
