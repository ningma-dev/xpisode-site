#!/usr/bin/env bash
# Host setup of a station machine: once per machine, and again whenever robots/<station>/ changes.
# Run by the operator, as root, from the station user's account; idempotent: it compares before
# it writes and prints "changed" or "unchanged" for every file.
# It never opens a camera, never sends on a CAN bus, never starts a station. The CAN rule is
# triggered at once (the adapters get their names and come up at 1 Mbit/s); the other udev rules
# apply when a device is plugged in again, or after a reboot.
#
#     sudo host/setup.sh [STATION] [--pin-irqs]
#
# STATION: robots/<STATION>/ (default: the one whose machine.env has this machine's hostname).
# It installs:
#   - real-time limits for the user (rtprio 90, memlock unlimited): xpisode-i2rt's own
#     scripts/setup_host.sh at the pinned rev, fetched as the user into local/build/xpisode-i2rt
#     (needs the user's GitHub access); not duplicated here
#   - the user in groups dialout, plugdev, video (log in again afterwards)
#   - /etc/udev/rules.d/90-xpisode-can.rules from robots/<station>/can.toml (host/can_rules.py);
#     skipped while a serial there is FILL
#   - /etc/udev/rules.d/91-xpisode-usb-power.rules (no USB autosuspend for D405s and CAN
#     adapters), 92-xpisode-d405.rules (the user may open the D405s), 99-ftdi-latency.rules
#   - xpisode-usbfs.service: usbcore usbfs_memory_mb -> 1000
# --pin-irqs (off by default): also pins the interrupts of the controllers the cameras and CAN
# adapters are on now (host/xhci_list.sh) to the CPUs machine.env's XPISODE_DEVICE_XHCI gives
# them, now and at every boot (xpisode-pin-irqs.service, keyed on PCI address, not IRQ number).
# It refuses when a controller in use is missing from XPISODE_DEVICE_XHCI.
# Check the result with host/doctor.sh (as the user, not root).
set -euo pipefail
site="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
host="$site/host"

pin=0
station=""
for arg in "$@"; do
  case "$arg" in
    --pin-irqs) pin=1 ;;
    -h | --help) sed -n '2,26p' "$0"; exit 0 ;;
    -*) echo "unknown option $arg" >&2; exit 2 ;;
    *) station="$arg" ;;
  esac
done
user="${SUDO_USER:-}"
if [[ $EUID -ne 0 || -z "$user" || "$user" == root ]]; then
  echo "usage: sudo $0 [STATION] [--pin-irqs]   (from the station user's account)" >&2
  exit 2
fi

XPISODE_STATION="$station"
# shellcheck source=../scripts/station_env.sh
source "$site/scripts/station_env.sh"
if [[ -z "$XPISODE_STATION" ]]; then
  echo "no station: write its name into .station (echo <station> > .station), or give it: sudo $0 <station>" >&2
  exit 2
fi
echo "station $XPISODE_STATION, user $user"

reboot=0
put() {  # put FILE TARGET [MODE]: install FILE at TARGET when it differs
  if [[ -f "$2" ]] && cmp -s "$1" "$2"; then
    echo "  unchanged  $2"
  else
    install -D -m "${3:-644}" "$1" "$2"
    echo "  changed    $2"
    reboot=1
  fi
}
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "real-time limits (xpisode-i2rt scripts/setup_host.sh)"
i2rt="$site/local/build/xpisode-i2rt"
limits=(/etc/security/limits.d/90-xpisode-i2rt.conf
        /etc/systemd/system/user@.service.d/90-xpisode-i2rt.conf
        /etc/systemd/user.conf.d/90-xpisode-i2rt.conf)
if sudo -u "$user" -H "$site/scripts/build_pinned.sh" fetch; then
  before="$(cat "${limits[@]}" 2>/dev/null || true)"
  "$i2rt/scripts/setup_host.sh" "$user" | sed 's/^/  /'
  after="$(cat "${limits[@]}" 2>/dev/null || true)"
  if [[ "$before" == "$after" ]]; then
    echo "  unchanged  limits"
  else
    echo "  changed    limits"
    reboot=1
  fi
else
  echo "  FAILED     could not fetch xpisode-i2rt as $user (GitHub access?); fix, then run this again"
fi

echo "groups"
for group in dialout plugdev video; do
  if id -nG "$user" | tr ' ' '\n' | grep -qx "$group"; then
    echo "  unchanged  $user in $group"
  else
    usermod -aG "$group" "$user"
    echo "  changed    $user added to $group (log in again)"
    reboot=1
  fi
done

echo "udev: CAN names (host/can_rules.py, robots/$XPISODE_STATION/can.toml)"
if ! python3 "$host/can_rules.py" "$XPISODE_STATION"; then
  echo "  skipped    map the adapters first (docs/guides/identify-devices.md), then run this again"
fi

echo "udev: USB power, D405 access, FTDI latency"
put "$host/91-xpisode-usb-power.rules" /etc/udev/rules.d/91-xpisode-usb-power.rules
put "$host/92-xpisode-d405.rules" /etc/udev/rules.d/92-xpisode-d405.rules
put "$host/99-ftdi-latency.rules" /etc/udev/rules.d/99-ftdi-latency.rules
udevadm control --reload

echo "usbfs memory"
put "$host/xpisode-usbfs.service" /etc/systemd/system/xpisode-usbfs.service
systemctl daemon-reload
systemctl enable --quiet xpisode-usbfs.service
systemctl restart xpisode-usbfs.service
echo "  now        usbfs_memory_mb $(cat /sys/module/usbcore/parameters/usbfs_memory_mb)"

if [[ $pin -eq 1 ]]; then
  echo "interrupts (--pin-irqs)"
  pairs=()
  for entry in "${XPISODE_DEVICE_XHCI[@]}"; do pairs+=("${entry%% *}=${entry##* }"); done
  missing=0
  while read -r pci; do
    [[ -n "$pci" ]] || continue
    if [[ " ${pairs[*]} " != *" $pci="* ]]; then
      echo "  refused    $pci carries a camera or CAN adapter but is not in XPISODE_DEVICE_XHCI"
      missing=1
    fi
  done < <("$host/xhci_list.sh" --pci)
  if [[ $missing -eq 1 ]]; then
    "$host/xhci_list.sh" | sed 's/^/  /'
    echo "add it to robots/$XPISODE_STATION/machine.env with its CPU, then run this again" >&2
    exit 1
  fi
  put "$host/xpisode-pin-irqs" /usr/local/sbin/xpisode-pin-irqs 755
  cat >"$tmp/xpisode-pin-irqs.service" <<UNIT
# Generated by host/setup.sh from robots/$XPISODE_STATION/machine.env (XPISODE_DEVICE_XHCI).
[Unit]
Description=xpisode: pin the camera and CAN controllers' interrupts
After=systemd-udev-settle.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/xpisode-pin-irqs ${pairs[*]}
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
UNIT
  put "$tmp/xpisode-pin-irqs.service" /etc/systemd/system/xpisode-pin-irqs.service
  systemctl daemon-reload
  systemctl enable --quiet xpisode-pin-irqs.service
  /usr/local/sbin/xpisode-pin-irqs "${pairs[@]}" | sed 's/^/  /'
fi

echo
echo "background CPUs (scripts/station_env.sh): ${XPISODE_BACKGROUND_CPUS:-all}"
echo "Not changed here (suggestions, host/README.md): ModemManager, cups, unattended-upgrades,"
echo "the cpufreq governor (powersave -> performance)."
if [[ $reboot -eq 1 ]]; then
  echo "NEXT: reboot (limits and groups take effect), then as $user: host/doctor.sh"
else
  echo "NEXT: as $user: host/doctor.sh"
fi
