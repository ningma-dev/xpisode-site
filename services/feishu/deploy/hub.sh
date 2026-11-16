#!/usr/bin/env bash
# The central host's writable data root and its sync, kept running:
#   - every minute: pull every small thing from OSS and link each recording to the bucket
#     mount (xpisode-site scripts/oss.py pull --link), then push what was written here;
#   - a writable xpisode panel over it on 127.0.0.1:$HUB_PORT, which the bot writes through.
# Records are local; recordings are read where they are on the mount; nothing writes the mount.
#
#     setsid nohup services/feishu/deploy/hub.sh >> ~/.local/state/xirra/hub.log 2>&1 < /dev/null &
#
# Needs: ~/.ossutilconfig with a key that may upload (the user puts it there), the xpisode
# environment ($XPISODE_VENV), and this repo's scripts/oss.py.
set -u
site="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
HUB_ROOT="${HUB_ROOT:-$HOME/xirra/hub}"
HUB_PORT="${HUB_PORT:-8091}"
MOUNT="${MOUNT:-/mnt/data_oss/xpisode_data}"
XPISODE_VENV="${XPISODE_VENV:-$HOME/xpisode-view/venv}"
OSS_PY="${OSS_PY:-$site/scripts/oss.py}"
oss() { "$XPISODE_VENV/bin/python" -u "$OSS_PY" --root "$HUB_ROOT" "$@"; }

mkdir -p "$HUB_ROOT/store"
oss pull --link --mount "$MOUNT" || exit 1

panel() {
  while true; do
    echo "== $(date -Is) panel start"
    "$XPISODE_VENV/bin/xpisode" serve "$HUB_ROOT" --port "$HUB_PORT"
    echo "== $(date -Is) panel exit $?; restart in 10 s"
    sleep 10
  done
}
panel &

while true; do
  oss pull --link --mount "$MOUNT" || echo "== $(date -Is) pull failed"
  oss push || echo "== $(date -Is) push failed"
  sleep 60
done
