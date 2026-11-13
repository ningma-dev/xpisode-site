#!/usr/bin/env bash
# The upload service on this machine: pushes what may go up as it is judged, newest first,
# kept off the cameras' CPUs. Stop it with Ctrl-C or `kill` (TERM): it records its run.
# Credentials: ~/.ossutilconfig (mode 600), this machine's add-only key; never in this repo.
#     scripts/upload.sh [oss.py push options]
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$here/scripts/station_env.sh"
cd "$here"
exec "$here/.venv/bin/python" -u "$here/scripts/oss.py" push --follow "$@"
