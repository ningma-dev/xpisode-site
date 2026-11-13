#!/usr/bin/env bash
# This machine's station (robots/<station>/station.toml, chosen by scripts/station_env.sh). Operators
# only, in an agreed window: it starts the arms and the cameras. Ctrl+C stops it.
#     scripts/run.sh --who NAME [xpisode run options]
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$here/scripts/station_env.sh"
if [[ -z "$XPISODE_STATION" ]]; then
  echo "run.sh: no station: write its name into .station (echo <station> > .station)" >&2
  exit 1
fi
cd "$here"
exec "$here/.venv/bin/xpisode" run "$XPISODE_STATION_FILE" "$@"
