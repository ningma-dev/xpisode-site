#!/usr/bin/env bash
# This machine's OSS: the only way to run scripts/oss.py here, so every command runs off the
# cameras' CPUs and knows the station (scripts/station_env.sh). The upload service is
#     scripts/oss.sh push --follow
# which pushes what may go up as it is judged, newest first; stop it with Ctrl-C or `kill` (TERM):
# it records its run. Other commands: scripts/oss.sh --help.
# Credentials: ~/.ossutilconfig (mode 600), this machine's add-only key; never in this repo.
#     scripts/oss.sh COMMAND [options]
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$here/scripts/station_env.sh"
cd "$here"
exec "$here/.venv/bin/python" -u "$here/scripts/oss.py" "$@"
