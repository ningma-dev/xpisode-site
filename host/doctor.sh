#!/usr/bin/env bash
# Read-only check of a station machine: one PASS / FAIL / TODO line per item. Changes nothing,
# opens no camera, sends nothing on a CAN bus. Run it as the station user (not root), in the
# terminal the station will run from (the limits are those of this session).
#
#     host/doctor.sh [STATION]          exit status: the number of FAIL lines
#
# Items: tools, rtprio, memlock, groups, udev-rules, can-rule, can-links, usbfs, autosuspend,
# data-root, background-cpus, irq-layout, venv, rtcore, panel, station-file, tuned.
# TODO is a step not done yet that depends on a fact still marked FILL (CAN map, camera roles,
# tuning); FAIL is something that should already be right.
set -uo pipefail
site="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
host="$site/host"
XPISODE_STATION="${1:-${XPISODE_STATION:-}}"
# shellcheck source=../scripts/station_env.sh
source "$site/scripts/station_env.sh" || exit 1
# shellcheck source=usb_lib.sh
source "$host/usb_lib.sh"

fails=0
pass() { printf 'PASS  %-16s %s\n' "$1" "$2"; }
fail() { printf 'FAIL  %-16s %s\n' "$1" "$2"; fails=$((fails + 1)); }
todo() { printf 'TODO  %-16s %s\n' "$1" "$2"; }

if [[ -z "$XPISODE_STATION" ]]; then
  fail station "no robots/*/machine.env has XPISODE_HOST=$(hostname -s)"
  exit 1
fi
echo "station $XPISODE_STATION on $(hostname -s), user $(id -un)"
[[ $EUID -ne 0 ]] || echo "(running as root: rtprio, memlock and groups below are root's, not the user's)"
dir="$XPISODE_STATION_DIR"

# tools
missing=()
for tool in git curl gcc make ffmpeg cansend candump uv cargo python3; do
  command -v "$tool" >/dev/null || missing+=("$tool")
done
ossutil="${OSSUTIL:-$(command -v ossutil || echo "$HOME/.local/bin/ossutil")}"
[[ -x "$ossutil" ]] || missing+=(ossutil)
if [[ ${#missing[@]} -eq 0 ]]; then pass tools "all found"; else fail tools "missing: ${missing[*]}"; fi

# real-time limits of this session
rt="$(ulimit -r 2>/dev/null || echo 0)"
if [[ "$rt" == unlimited || "$rt" -ge 90 ]]; then pass rtprio "ulimit -r = $rt"; else fail rtprio "ulimit -r = $rt (want 90: host/setup.sh, then reboot)"; fi
ml="$(ulimit -l)"
if [[ "$ml" == unlimited ]]; then pass memlock "ulimit -l = unlimited"; else fail memlock "ulimit -l = $ml (want unlimited)"; fi

# groups of this session (a new group needs a new login)
lacking=()
for group in dialout plugdev video; do id -nG | tr ' ' '\n' | grep -qx "$group" || lacking+=("$group"); done
if [[ ${#lacking[@]} -eq 0 ]]; then pass groups "dialout plugdev video"; else fail groups "not in: ${lacking[*]} (host/setup.sh, then log in again)"; fi

# udev rules
absent=()
for rule in 91-xpisode-usb-power.rules 92-xpisode-d405.rules 99-ftdi-latency.rules; do
  [[ -f "/etc/udev/rules.d/$rule" ]] || absent+=("$rule")
done
if [[ ${#absent[@]} -eq 0 ]]; then pass udev-rules "usb power, d405 access, ftdi latency"; else fail udev-rules "missing: ${absent[*]}"; fi

# the CAN rule, against robots/<station>/can.toml
roles=()
while read -r role; do [[ -n "$role" ]] && roles+=("$role"); done < <(python3 - "$dir/can.toml" <<'PY'
import sys, tomllib
for role in tomllib.load(open(sys.argv[1], "rb")):
    print(role)
PY
)
if grep -q '"FILL' "$dir/can.toml"; then
  todo can-rule "can.toml still has FILL (docs/guides/identify-devices.md)"
  todo can-links "after the CAN map"
else
  check="$(python3 "$host/can_rules.py" "$XPISODE_STATION" --check 2>&1)"
  if [[ "$check" == *unchanged* ]]; then pass can-rule "90-xpisode-can.rules matches can.toml"; else fail can-rule "differs from can.toml: sudo host/setup.sh"; fi
  bad=()
  for role in "${roles[@]}"; do
    link="$(ip -details link show "can_$role" 2>/dev/null)"
    if [[ -z "$link" ]]; then bad+=("can_$role:absent")
    elif [[ "$link" != *"state UP"* && "$link" != *"<"*UP*">"* ]]; then bad+=("can_$role:down")
    elif [[ "$link" != *"bitrate 1000000"* ]]; then bad+=("can_$role:not-1M")
    fi
  done
  if [[ ${#bad[@]} -eq 0 ]]; then pass can-links "${roles[*]/#/can_} up at 1 Mbit/s"; else fail can-links "${bad[*]}"; fi
fi

# usbfs memory
usbfs="$(cat /sys/module/usbcore/parameters/usbfs_memory_mb 2>/dev/null || echo 0)"
if [[ "$usbfs" -ge 1000 ]]; then pass usbfs "usbfs_memory_mb = $usbfs"; else fail usbfs "usbfs_memory_mb = $usbfs (want 1000: host/setup.sh)"; fi

# no USB autosuspend on the D405s and the CAN adapters plugged in now
asleep=()
count=0
while read -r dev; do
  [[ -n "$dev" ]] || continue
  count=$((count + 1))
  [[ "$(cat "$dev/power/control" 2>/dev/null)" == on ]] || asleep+=("$(basename "$dev")")
done < <(usb_devices 8086 0b5b; usb_devices 1d50 606f)
if [[ $count -eq 0 ]]; then todo autosuspend "no D405 or CAN adapter plugged in"
elif [[ ${#asleep[@]} -eq 0 ]]; then pass autosuspend "power/control = on for $count devices"
else fail autosuspend "autosuspend on: ${asleep[*]} (replug or reboot after host/setup.sh)"; fi

# data root: outside the repo, writable, not on NTFS
root="$(cd "$site" && python3 -c 'import tomllib, pathlib; d = tomllib.load(open("pyproject.toml", "rb"))["tool"]["xpisode"]["data"]; print(pathlib.Path(d).expanduser().resolve())')"
if [[ ! -d "$root" ]]; then fail data-root "$root does not exist (mkdir -p $root)"
elif [[ ! -w "$root" ]]; then fail data-root "$root not writable"
elif [[ "$root/" == "$site/"* ]]; then fail data-root "$root is inside the repo"
else
  fstype="$(df -PT "$root" | awk 'NR == 2 {print $2}')"
  case "$fstype" in
    ntfs* | fuseblk | vfat | exfat) fail data-root "$root is on $fstype" ;;
    *) pass data-root "$root ($fstype, $(df -Ph "$root" | awk 'NR == 2 {print $4}') free)" ;;
  esac
fi

# background CPUs and the interrupt layout they were derived from
if [[ -n "${XPISODE_BACKGROUND_CPUS:-}" ]]; then pass background-cpus "$XPISODE_BACKGROUND_CPUS"; else fail background-cpus "empty (XPISODE_DEVICE_XHCI in machine.env)"; fi
drift=()
while read -r pci; do
  [[ -n "$pci" ]] || continue
  want=""
  for entry in "${XPISODE_DEVICE_XHCI[@]}"; do [[ "${entry%% *}" == "$pci" ]] && want="${entry##* }"; done
  if [[ -z "$want" ]]; then drift+=("$pci:not-in-machine.env"); continue; fi
  for pair in $(irqs_of "$pci"); do
    [[ "${pair#*:}" == "$want" ]] || drift+=("$pci:irq${pair%%:*}->cpu${pair#*:}(machine.env:$want)")
  done
done < <("$host/xhci_list.sh" --pci)
if [[ ${#drift[@]} -eq 0 ]]; then pass irq-layout "controllers in use serve their interrupts where machine.env says"
else fail irq-layout "${drift[*]} (update machine.env, or sudo host/setup.sh --pin-irqs)"; fi

# the pinned environment
py="$site/.venv/bin/python"
if [[ ! -x "$py" ]]; then
  fail venv ".venv missing (uv sync --locked)"
else
  editable="$("$site/scripts/dev_link.sh" --status | grep -v ' pinned ' || true)"
  if [[ -z "$editable" ]]; then pass venv "every package pinned"; else fail venv "not pinned: $(echo "$editable" | awk '{print $1}' | paste -sd' ' -)"; fi
  pin_of() { python3 -c 'import sys, tomllib; print(tomllib.load(open(sys.argv[1], "rb"))["tool"]["uv"]["sources"][sys.argv[2]]["rev"])' "$site/pyproject.toml" "$1"; }
  binary="$("$py" -c 'from xpisode_i2rt.core import default_binary; print(default_binary())' 2>/dev/null)"
  if [[ -x "$binary" && "$(cat "$binary.rev" 2>/dev/null)" == "$(pin_of xpisode-i2rt)" ]]; then pass rtcore "$binary at the pinned rev"
  else fail rtcore "not built at the pinned rev (scripts/build_pinned.sh rtcore)"; fi
  static="$("$py" -c 'import importlib.util as u, pathlib as p; print(p.Path(u.find_spec("xpisode").origin).parent / "web" / "static")' 2>/dev/null)"
  if [[ -f "$static/index.html" && "$(cat "$static/.rev" 2>/dev/null)" == "$(pin_of xpisode)" ]]; then pass panel "frontend at the pinned rev"
  else fail panel "frontend not built at the pinned rev (scripts/build_pinned.sh panel)"; fi
fi

# the station file and the tuning
name="$(python3 -c 'import sys, tomllib; print(tomllib.load(open(sys.argv[1], "rb"))["station"])' "$dir/station.toml")"
if [[ "$name" != "$XPISODE_STATION" ]]; then fail station-file "station = \"$name\", directory robots/$XPISODE_STATION"
elif grep -q 'FILL_' "$dir/station.toml"; then todo station-file "camera serials still FILL (camera roles)"
else pass station-file "robots/$XPISODE_STATION/station.toml"; fi
untuned=()
for side in l r; do [[ -f "$dir/tuned/can_follower_$side.json" ]] || untuned+=("follower_$side"); done
if [[ ${#untuned[@]} -eq 0 ]]; then pass tuned "both followers"; else todo tuned "not measured: ${untuned[*]} (robots/README.md, operator window)"; fi

echo "$fails FAIL"
exit "$fails"
