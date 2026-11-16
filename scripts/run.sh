#!/usr/bin/env bash
# This machine's station (robots/<station>/, chosen by scripts/station_env.sh). The only way to
# start a station: it also sets what xpisode run needs from station_env.sh (background CPUs).
# Operators only, in an agreed window: it starts the arms and the cameras. Ctrl+C stops it.
#     scripts/run.sh --who NAME [--file FILE] [xpisode run options]
# FILE is another station file in robots/<station>/ (default station.toml), such as a variant
# with a different leader.
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$here/scripts/station_env.sh"
if [[ -z "$XPISODE_STATION" ]]; then
  echo "run.sh: no station: write its name into .station (echo <station> > .station)" >&2
  exit 1
fi
file="$XPISODE_STATION_FILE"
args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --file) file="$XPISODE_STATION_DIR/${2:?run.sh: --file needs a file name}"; shift 2 ;;
    --file=*) file="$XPISODE_STATION_DIR/${1#--file=}"; shift ;;
    *) args+=("$1"); shift ;;
  esac
done
if [[ "$(dirname -- "$file")" != "$XPISODE_STATION_DIR" || ! -f "$file" ]]; then
  echo "run.sh: no station file $file (--file names a file in $XPISODE_STATION_DIR)" >&2
  exit 1
fi
cd "$here"
exec "$here/.venv/bin/xpisode" run "$file" ${args[@]+"${args[@]}"}
