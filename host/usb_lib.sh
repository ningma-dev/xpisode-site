# Sourced by can_list.sh and xhci_list.sh: read-only lookups in sysfs. Nothing here opens a device.

usb_devices() {  # usb_devices VENDOR PRODUCT: the sysfs dirs of the matching USB devices
  local dev
  for dev in /sys/bus/usb/devices/*; do
    [[ -f "$dev/idVendor" && -f "$dev/idProduct" ]] || continue
    [[ "$(cat "$dev/idVendor")" == "$1" && "$(cat "$dev/idProduct")" == "$2" ]] && echo "$dev"
  done
  return 0
}

xhci_of() {  # xhci_of USB_DEV: the PCI address of the host controller it hangs off
  local path part found=""
  path="$(readlink -f "$1")"
  IFS=/ read -ra parts <<<"$path"
  for part in "${parts[@]}"; do
    [[ "$part" =~ ^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]$ ]] && found="$part"
    [[ "$part" == usb[0-9]* ]] && break
  done
  echo "${found:-?}"
}

irqs_of() {  # irqs_of PCI: "irq:cpus" for each of its interrupts, effective affinity
  local dev="/sys/bus/pci/devices/$1" irq out=()
  local irqs=()
  if [[ -d "$dev/msi_irqs" ]]; then
    for irq in "$dev"/msi_irqs/*; do [[ -e "$irq" ]] && irqs+=("$(basename "$irq")"); done
  fi
  [[ ${#irqs[@]} -gt 0 ]] || irqs=("$(cat "$dev/irq" 2>/dev/null || echo "?")")
  for irq in "${irqs[@]}"; do
    out+=("$irq:$(cat "/proc/irq/$irq/effective_affinity_list" 2>/dev/null || echo "?")")
  done
  echo "${out[*]}"
}
