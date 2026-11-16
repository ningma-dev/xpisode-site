#!/usr/bin/env bash
# Keep the bot running on a host without systemd (the dev container): restart it when it exits.
#     setsid nohup services/feishu/deploy/run.sh >> ~/.local/state/xirra/bot.log 2>&1 < /dev/null &
# On a server with systemd use deploy/xirra.service instead.
set -u
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"   # services/feishu
venv="${XIRRA_VENV:-$here/.venv}"
while true; do
  echo "== $(date -Is) start"
  "$venv/bin/xirra" bot
  echo "== $(date -Is) exit $?; restart in 10 s"
  sleep 10
done
