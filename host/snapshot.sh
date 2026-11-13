#!/usr/bin/env bash
# Read-only: the facts of this machine as Markdown: OS, kernel, CPU and SMT map, memory, GPU,
# disks, the D405s and CAN adapters with serials and controllers, the xHCI interrupts and their
# CPUs, and the settings host/setup.sh touches. Changes nothing, opens no device.
# Saved as robots/<station>/machine.md at setup; after a reset, diff a new one against it:
#
#     host/snapshot.sh > robots/<station>/machine.md
#     host/snapshot.sh | diff robots/<station>/machine.md -
set -uo pipefail
host="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=usb_lib.sh
source "$host/usb_lib.sh"

block() {  # block TITLE COMMAND...: a section with the command's output
  local title="$1"
  shift
  printf '\n## %s\n\n```\n' "$title"
  "$@" 2>&1 || true
  printf '```\n'
}

printf '# %s\n\nhost/snapshot.sh, %s\n' "$(hostname -s)" "$(date -u +%Y-%m-%dT%H:%MZ)"
block "System" sh -c '. /etc/os-release; echo "$PRETTY_NAME"; uname -r; uname -v'
block "CPU" sh -c 'lscpu | grep -E "^(Model name|CPU\(s\)|Thread\(s\) per core|Core\(s\) per socket)"; echo; lscpu -e=CPU,CORE,SOCKET,ONLINE'
block "Memory" free -g
block "GPU" sh -c 'nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>/dev/null || echo "no nvidia-smi"'
block "Disks" sh -c 'lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINTS -e 7; echo; df -hT -x tmpfs -x devtmpfs -x squashfs -x efivarfs'
block "Cameras and CAN adapters" "$host/devices.sh"
block "USB controllers of those devices" sh -c '. "$0/usb_lib.sh"; { usb_devices 8086 0b5b; usb_devices 1d50 606f; } | while read -r d; do x=$(xhci_of "$d"); echo "$(basename "$d") $x irq:cpu $(irqs_of "$x")"; done' "$host"
block "Interrupts" sh -c 'systemctl is-active irqbalance; for f in /proc/irq/*/smp_affinity_list; do n=${f#/proc/irq/}; n=${n%%/*}; grep -q "^ *$n:.*xhci" /proc/interrupts && echo "irq $n affinity $(cat "$f") effective $(cat /proc/irq/$n/effective_affinity_list)"; done'
block "Settings host/setup.sh touches" sh -c 'echo "usbfs_memory_mb $(cat /sys/module/usbcore/parameters/usbfs_memory_mb)"; echo "usbcore.autosuspend $(cat /sys/module/usbcore/parameters/autosuspend)"; echo "user $(id -un): ulimit -r $(ulimit -r), ulimit -l $(ulimit -l), groups $(id -nG)"; ls /etc/udev/rules.d/ | grep -E "xpisode|ftdi" || echo "no xpisode udev rules"; echo "cpufreq governor $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo "?")"; for s in ModemManager cups unattended-upgrades; do echo "$s $(systemctl is-active "$s" 2>/dev/null)"; done'
