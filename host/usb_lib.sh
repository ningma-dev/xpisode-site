# Sourced by devices.sh, doctor.sh, snapshot.sh and scripts/station_env.sh: read-only lookups in
# sysfs. Nothing here opens a device.

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
    # A controller allocates several vectors but requests only some; unrequested ones have no
    # /proc/irq entry and serve nothing.
    [[ "$irq" == "?" || -d "/proc/irq/$irq" ]] || continue
    out+=("$irq:$(cat "/proc/irq/$irq/effective_affinity_list" 2>/dev/null || echo "?")")
  done
  echo "${out[*]}"
}

device_cpus() {  # the CPUs serving the controllers that D405s and CAN adapters are on now
  local dev pair
  {
    while read -r dev; do
      [[ -n "$dev" ]] || continue
      for pair in $(irqs_of "$(xhci_of "$dev")"); do echo "${pair#*:}"; done
    done < <(usb_devices 8086 0b5b; usb_devices 1d50 606f)
  } | grep -E '^[0-9]+$' | sort -un
}

background_cpus() {  # every CPU except device_cpus and their SMT siblings, as "1,2,3"
  local avoid="," cpu part c dir list=()
  for cpu in $(device_cpus); do
    for part in $(tr , ' ' <"/sys/devices/system/cpu/cpu$cpu/topology/thread_siblings_list" 2>/dev/null || echo "$cpu"); do
      if [[ "$part" == *-* ]]; then
        for ((c = ${part%-*}; c <= ${part#*-}; c++)); do avoid+="$c,"; done
      else
        avoid+="$part,"
      fi
    done
  done
  for dir in /sys/devices/system/cpu/cpu[0-9]*; do
    c="${dir##*cpu}"
    [[ "$avoid" == *",$c,"* ]] || list+=("$c")
  done
  printf '%s\n' "${list[@]}" | sort -n | paste -sd, -
}
