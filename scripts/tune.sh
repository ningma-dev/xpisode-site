#!/usr/bin/env bash
# Tune one follower arm of this machine's station (docs/guides/tune-arm.md): xpisode-i2rt's tuner
# on the arm's CAN channel, writing robots/<station>/tuned/can_<arm>.json, the file the station
# file reads. The station is .station's; the channel and the output follow from the arm.
# The arm moves (except with --plan): operators only, in an agreed window, the station stopped.
#     scripts/tune.sh ARM --plan                   where it would go; nothing moves
#     scripts/tune.sh ARM --i-am-at-the-arm        tune it
# ARM: follower_l or follower_r. Other options go to xpisode_i2rt.tune (not --out).
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$here/scripts/station_env.sh"
if [[ -z "$XPISODE_STATION" ]]; then
  echo "tune.sh: no station: write its name into .station (echo <station> > .station)" >&2
  exit 1
fi
arm="${1:-}"
case "$arm" in
  follower_l | follower_r) shift ;;
  *) echo "usage: scripts/tune.sh follower_l|follower_r --plan | --i-am-at-the-arm" >&2; exit 2 ;;
esac
plan=0
for arg in "$@"; do
  case "$arg" in
    --plan) plan=1 ;;
    --out | --out=*) echo "tune.sh: the output is always robots/$XPISODE_STATION/tuned/can_$arm.json" >&2; exit 2 ;;
  esac
done
out=()
if [[ $plan -eq 0 ]]; then
  out=(--out "$XPISODE_STATION_DIR/tuned/can_$arm.json")
fi
cd "$here"
exec "$here/.venv/bin/python" -m xpisode_i2rt.tune "can_$arm" "$@" ${out[@]+"${out[@]}"}
