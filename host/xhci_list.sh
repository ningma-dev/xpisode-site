#!/usr/bin/env bash
# Read-only: which USB host controller (PCI address) each D405 and CAN adapter is on now, and the
# CPU that serves its interrupts. These are the values of XPISODE_DEVICE_XHCI in
# robots/<station>/machine.env. Changes nothing, opens no device.
#     host/xhci_list.sh            one line per device
#     host/xhci_list.sh --pci      just the controllers, one per line
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/usb_lib.sh"
rows=()
for kind in "d405 8086 0b5b" "can 1d50 606f"; do
  read -r name vendor product <<<"$kind"
  while read -r dev; do
    [[ -n "$dev" ]] || continue
    rows+=("$name $(cat "$dev/serial" 2>/dev/null || echo "?") $(basename "$dev") $(xhci_of "$dev")")
  done < <(usb_devices "$vendor" "$product")
done
if [[ "${1:-}" == "--pci" ]]; then
  for row in ${rows[@]+"${rows[@]}"}; do echo "${row##* }"; done | sort -u
  exit 0
fi
printf '%-5s %-20s %-10s %-14s %s\n' kind serial usb xhci "irq:cpu"
for row in ${rows[@]+"${rows[@]}"}; do
  read -r name serial usb xhci <<<"$row"
  printf '%-5s %-20s %-10s %-14s %s\n' "$name" "$serial" "$usb" "$xhci" "$(irqs_of "$xhci")"
done
[[ ${#rows[@]} -gt 0 ]] || echo "(no D405 or CAN adapter plugged in)"
grep -E 'xhci' /proc/interrupts | sed 's/^/  /' || true
