#!/usr/bin/env bash
# Set up a GELLO leader of this machine's station (docs/guides/gello.md): its port, motor IDs,
# baud rate and calibration file are those its device has in the station file, nowhere else.
# Only the GELLO is touched: no arm moves, no camera opens; the station must be stopped (one
# program at a time on the port).
#     scripts/gello.sh DEVICE ids scan             every motor on its bus, set up or not
#     scripts/gello.sh DEVICE ids set Mk           the one new motor on the bus -> joint k's ID
#     scripts/gello.sh DEVICE calibrate            measure it: writes the device's calibration file
#     scripts/gello.sh DEVICE check [--seconds N]  open it as the station would, report the bus
# DEVICE: the device's name in the station file, such as leader_l. --file FILE picks the station
# file in robots/<station>/ that has it (default station.toml).
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$here/scripts/station_env.sh"
if [[ -z "$XPISODE_STATION" ]]; then
  echo "gello.sh: no station: write its name into .station (echo <station> > .station)" >&2
  exit 1
fi
file="$XPISODE_STATION_FILE"
args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --file) file="$XPISODE_STATION_DIR/${2:?gello.sh: --file needs a file name}"; shift 2 ;;
    --file=*) file="$XPISODE_STATION_DIR/${1#--file=}"; shift ;;
    *) args+=("$1"); shift ;;
  esac
done
set -- ${args[@]+"${args[@]}"}
if [[ $# -lt 2 ]]; then
  sed -n '6,11p' "$0" | sed 's/^# //' >&2
  exit 2
fi
if [[ "$(dirname -- "$file")" != "$XPISODE_STATION_DIR" || ! -f "$file" ]]; then
  echo "gello.sh: no station file $file (--file names a file in $XPISODE_STATION_DIR)" >&2
  exit 1
fi
name="$1"; command="$2"; shift 2
# The device's config in the station file: device, servo IDs, baud rate, calibration file.
config="$("$here/.venv/bin/python" - "$file" "$name" <<'PY'
import json, shlex, sys, tomllib
path, name = sys.argv[1], sys.argv[2]
devices = tomllib.load(open(path, "rb")).get("devices", [])
device = next((d for d in devices if d.get("name") == name), None)
if device is None or "xpisode_abc_gello:GelloLeader" not in device.get("command", []):
    sys.exit(f"gello.sh: {path} has no GELLO device {name!r}")
command = device["command"]
c = json.loads(command[command.index("--config") + 1])
print(shlex.join([c["device"], " ".join(str(i) for i in c["servo_ids"]),
                  str(c.get("baudrate", 2_000_000)), c["calibration"]]))
PY
)"
eval "set -- $config \"\$@\""
port="$1"; ids="$2"; baud="$3"; calibration="$XPISODE_STATION_DIR/$4"; shift 4
cd "$here"
python=("$here/.venv/bin/python" -m)
case "$command" in
  ids)
    # shellcheck disable=SC2086 # the IDs are words
    exec "${python[@]}" xpisode_abc_gello.ids --device "$port" --ids $ids --baudrate "$baud" "$@" ;;
  calibrate)
    # shellcheck disable=SC2086
    exec "${python[@]}" xpisode_abc_gello.calibrate --device "$port" --ids $ids --baudrate "$baud" \
      --out "$calibration" "$@" ;;
  check)
    # shellcheck disable=SC2086
    exec "${python[@]}" xpisode_abc_gello.check --device "$port" --ids $ids --baudrate "$baud" \
      --calibration "$calibration" "$@" ;;
  *) echo "gello.sh: unknown command $command (ids, calibrate, check)" >&2; exit 2 ;;
esac
