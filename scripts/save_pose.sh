#!/usr/bin/env bash
# Save where the arms are now as a pose the station's move client can go to (its `target`
# prompt), such as `ready`: xpisode_env.move --save, with the station from .station and the
# groups those of the move client in the station file. The station must be running; nothing moves.
#     scripts/save_pose.sh POSE [--file FILE]
# FILE: the station file that is running, as given to scripts/run.sh --file (default station.toml).
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$here/scripts/station_env.sh"
if [[ -z "$XPISODE_STATION" ]]; then
  echo "save_pose.sh: no station: write its name into .station (echo <station> > .station)" >&2
  exit 1
fi
pose=""
file="$XPISODE_STATION_FILE"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --file) file="$XPISODE_STATION_DIR/${2:?save_pose.sh: --file needs a file name}"; shift 2 ;;
    --file=*) file="$XPISODE_STATION_DIR/${1#--file=}"; shift ;;
    -*) echo "save_pose.sh: unknown option $1" >&2; exit 2 ;;
    *) [[ -z "$pose" ]] || { echo "save_pose.sh: one pose name" >&2; exit 2; }; pose="$1"; shift ;;
  esac
done
if [[ -z "$pose" ]]; then
  echo "usage: scripts/save_pose.sh POSE [--file FILE]" >&2
  exit 2
fi
if [[ "$(dirname -- "$file")" != "$XPISODE_STATION_DIR" || ! -f "$file" ]]; then
  echo "save_pose.sh: no station file $file (--file names a file in $XPISODE_STATION_DIR)" >&2
  exit 1
fi
# The groups the station's move client moves: its --group arguments.
groups=()
while IFS= read -r group; do groups+=(--group "$group"); done < <(
  "$here/.venv/bin/python" - "$file" <<'PY'
import sys, tomllib
clients = tomllib.load(open(sys.argv[1], "rb")).get("clients", [])
command = next((c["command"] for c in clients if c.get("name") == "move"), None)
if command is None:
    sys.exit(f"{sys.argv[1]} has no move client")
print("\n".join(command[i + 1] for i, part in enumerate(command) if part == "--group"))
PY
)
if [[ ${#groups[@]} -eq 0 ]]; then
  echo "save_pose.sh: the move client in $file moves no group" >&2
  exit 1
fi
cd "$here"
exec "$here/.venv/bin/python" -m xpisode_env.move --station "$XPISODE_STATION" --name save \
  "${groups[@]}" --save "$pose"
