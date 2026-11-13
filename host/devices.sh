#!/usr/bin/env bash
# Read-only: the CAN adapters and RealSense cameras plugged in now, each with the serial that goes
# into robots/<station>/ (can.toml for adapters, station.toml for cameras). Changes nothing; no
# camera starts streaming. Unplug one device, run it again, and the missing line is that device.
#     host/devices.sh
#     host/devices.sh --camera-serials    just the camera serials, one per line (for doctor.sh)
#
# A D405's USB descriptor carries a different serial from the one librealsense opens it by, so
# cameras are listed through librealsense (the repo's .venv), never from sysfs.
set -euo pipefail
host="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python="$host/../.venv/bin/python"
source "$host/usb_lib.sh"

cameras() {
  "$python" - <<'PY'
import pyrealsense2 as rs
for device in rs.context().query_devices():
    port = device.get_info(rs.camera_info.physical_port)
    parts = [p for p in port.split("/") if p[:1].isdigit() and "-" in p and ":" not in p]
    print(device.get_info(rs.camera_info.serial_number), parts[-1] if parts else "?")
PY
}

if [[ "${1:-}" == "--camera-serials" ]]; then
  cameras | cut -d' ' -f1
  exit 0
fi

printf '%-8s %-20s %-16s %s\n' kind serial name usb
while read -r dev; do
  [[ -n "$dev" ]] || continue
  names=()
  for net in "$dev"/*/net/*; do [[ -e "$net" ]] && names+=("$(basename "$net")"); done
  printf '%-8s %-20s %-16s %s\n' can "$(cat "$dev/serial" 2>/dev/null || echo "?")" \
    "$(IFS=,; echo "${names[*]:-none}")" "$(basename "$dev")"
done < <(usb_devices 1d50 606f)
if [[ -x "$python" ]]; then
  while read -r serial usb; do
    [[ -n "$serial" ]] && printf '%-8s %-20s %-16s %s\n' camera "$serial" - "$usb"
  done < <(cameras)
else
  echo "(cameras are listed once xpisode is installed: uv sync --locked)"
fi
