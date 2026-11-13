# Sourced by run.sh, serve.sh and upload.sh: the settings of this machine's station.
#
# The station is XPISODE_STATION in the environment, else the name in the repo's .station file
# (one line, not committed, written once when the machine is set up). It exports
#   XPISODE_STATION           the station name = the directory name under robots/
#   XPISODE_STATION_DIR       robots/<name>
#   XPISODE_STATION_FILE      robots/<name>/station.toml
#   XPISODE_BACKGROUND_CPUS   where background work runs (proxies, uploads)
#
# Background CPUs: background work on a CPU that serves the cameras' USB interrupts costs camera
# frames even at idle priority (measured on the old station, 2026-11-12). So it runs on every CPU
# except those serving the USB controllers the cameras and CAN adapters are on right now, and
# their SMT siblings, all read from sysfs. XPISODE_BACKGROUND_CPUS in the environment wins.

_site="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../host/usb_lib.sh
source "$_site/host/usb_lib.sh"

if [[ -z "${XPISODE_STATION:-}" && -s "$_site/.station" ]]; then
  XPISODE_STATION="$(tr -d '[:space:]' < "$_site/.station")"
fi
export XPISODE_STATION="${XPISODE_STATION:-}"

if [[ -n "$XPISODE_STATION" ]]; then
  if [[ ! -f "$_site/robots/$XPISODE_STATION/station.toml" ]]; then
    echo "station_env: no robots/$XPISODE_STATION/station.toml" >&2
    return 1
  fi
  export XPISODE_STATION_DIR="$_site/robots/$XPISODE_STATION"
  export XPISODE_STATION_FILE="$XPISODE_STATION_DIR/station.toml"
fi
if [[ -z "${XPISODE_BACKGROUND_CPUS:-}" && -d /sys/devices/system/cpu/cpu0/topology ]]; then
  XPISODE_BACKGROUND_CPUS="$(background_cpus)"
fi
export XPISODE_BACKGROUND_CPUS="${XPISODE_BACKGROUND_CPUS:-}"
unset _site
