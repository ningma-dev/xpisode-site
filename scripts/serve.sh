#!/usr/bin/env bash
# The panel on this machine, its background work kept off the cameras' CPUs.
#     scripts/serve.sh [xpisode serve options]
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$here/scripts/station_env.sh"
cd "$here"
exec "$here/.venv/bin/xpisode" serve "$@"
