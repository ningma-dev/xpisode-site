#!/usr/bin/env bash
# The control group for camera frame loss (tools/camera/README.md): this station's D405s read by
# bare librealsense, set as the station file sets them, for SECONDS. Opens the cameras: in an
# agreed window, with the station stopped.
#     scripts/camera_baseline.sh SECONDS [--file FILE] [--copy] [--noglobal] [--keeper]
# FILE: another station file in robots/<station>/ (default station.toml).
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$here/scripts/station_env.sh"
if [[ -z "$XPISODE_STATION" ]]; then
  echo "camera_baseline.sh: no station: write its name into .station (echo <station> > .station)" >&2
  exit 1
fi
file="$XPISODE_STATION_FILE"
args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --file) file="$XPISODE_STATION_DIR/${2:?camera_baseline.sh: --file needs a file name}"; shift 2 ;;
    --file=*) file="$XPISODE_STATION_DIR/${1#--file=}"; shift ;;
    *) args+=("$1"); shift ;;
  esac
done
if [[ "$(dirname -- "$file")" != "$XPISODE_STATION_DIR" || ! -f "$file" ]]; then
  echo "camera_baseline.sh: no station file $file (--file names a file in $XPISODE_STATION_DIR)" >&2
  exit 1
fi
cd "$here"
exec "$here/.venv/bin/python" "$here/tools/camera/rs_bare.py" ${args[@]+"${args[@]}"} "$file"
