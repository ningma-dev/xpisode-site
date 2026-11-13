#!/usr/bin/env bash
# Read-only: every gs_usb CAN adapter (candleLight 1d50:606f) plugged in now, with its serial,
# its network interface's current name, its USB path and host controller. Changes nothing.
# To tell which adapter is which arm: run it, unplug one adapter, run it again (docs/runbooks/map-can.md).
#     host/can_list.sh
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/usb_lib.sh"
printf '%-20s %-16s %-10s %-14s %s\n' serial interface usb xhci "irq:cpu"
found=0
while read -r dev; do
  [[ -n "$dev" ]] || continue
  found=1
  ifaces=()
  for net in "$dev"/*/net/*; do [[ -e "$net" ]] && ifaces+=("$(basename "$net")"); done
  xhci="$(xhci_of "$dev")"
  printf '%-20s %-16s %-10s %-14s %s\n' "$(cat "$dev/serial" 2>/dev/null || echo "?")" \
    "$(IFS=,; echo "${ifaces[*]:-none}")" "$(basename "$dev")" "$xhci" "$(irqs_of "$xhci")"
done < <(usb_devices 1d50 606f)
[[ $found -eq 1 ]] || echo "(no gs_usb adapter plugged in)"
