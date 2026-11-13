# Sourced by run.sh, serve.sh and upload.sh: the settings of this machine's station.
#
# The station is the robots/<name>/ whose machine.env has XPISODE_HOST = this machine's
# `hostname -s`; XPISODE_STATION=<name> overrides that. It exports
#   XPISODE_STATION           the station name = the directory name (empty on a Mac)
#   XPISODE_STATION_DIR       robots/<name>
#   XPISODE_STATION_FILE      robots/<name>/station.toml
#   XPISODE_BACKGROUND_CPUS   where background work runs (proxies, uploads)
#
# Background CPUs: background work on a CPU that serves the cameras' USB interrupts costs camera
# frames even at idle priority (yam_abc, 2026-11-12: 1.9-2.6 % of frames, 0.06-0.42 % when kept
# off those CPUs). So it runs on every CPU except those that serve the device controllers
# (XPISODE_DEVICE_XHCI in machine.env: cameras and CAN) and their SMT siblings, read from sysfs.
# XPISODE_BACKGROUND_CPUS already in the environment, or set in machine.env, wins.
# Measure the controllers' CPUs on each machine with host/xhci_list.sh
# (docs/runbooks/add-machine.md).

_site="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -z "${XPISODE_STATION:-}" ]]; then
  _host="$(hostname -s)"
  for _env in "$_site"/robots/*/machine.env; do
    [[ -e "$_env" ]] || continue
    if grep -qx "XPISODE_HOST=$_host" "$_env"; then
      XPISODE_STATION="$(basename "$(dirname "$_env")")"
      break
    fi
  done
fi
export XPISODE_STATION="${XPISODE_STATION:-}"

if [[ -n "$XPISODE_STATION" ]]; then
  if [[ ! -f "$_site/robots/$XPISODE_STATION/machine.env" ]]; then
    echo "station_env: no robots/$XPISODE_STATION/machine.env" >&2
    return 1
  fi
  _preset="${XPISODE_BACKGROUND_CPUS:-}"
  # shellcheck source=/dev/null
  source "$_site/robots/$XPISODE_STATION/machine.env"
  export XPISODE_STATION_DIR="$_site/robots/$XPISODE_STATION"
  export XPISODE_STATION_FILE="$XPISODE_STATION_DIR/station.toml"
  if [[ -n "$_preset" ]]; then
    XPISODE_BACKGROUND_CPUS="$_preset"
  elif [[ -z "${XPISODE_BACKGROUND_CPUS:-}" && -d /sys/devices/system/cpu/cpu0/topology ]]; then
    # Every CPU minus the device CPUs and their siblings.
    _avoid=","
    for _entry in "${XPISODE_DEVICE_XHCI[@]}"; do
      _cpu="${_entry##* }"
      _siblings="$(cat "/sys/devices/system/cpu/cpu$_cpu/topology/thread_siblings_list" 2>/dev/null || echo "$_cpu")"
      for _part in ${_siblings//,/ }; do
        if [[ "$_part" == *-* ]]; then
          for ((_c = ${_part%-*}; _c <= ${_part#*-}; _c++)); do _avoid+="$_c,"; done
        else
          _avoid+="$_part,"
        fi
      done
    done
    _list=()
    for _dir in /sys/devices/system/cpu/cpu[0-9]*; do
      _c="${_dir##*cpu}"
      [[ "$_avoid" == *",$_c,"* ]] || _list+=("$_c")
    done
    XPISODE_BACKGROUND_CPUS="$(printf '%s\n' "${_list[@]}" | sort -n | paste -sd, -)"
    unset _avoid _list _dir _c _cpu _part _siblings _entry
  fi
  unset _preset
fi
export XPISODE_BACKGROUND_CPUS="${XPISODE_BACKGROUND_CPUS:-}"
unset _site _host _env
