#!/usr/bin/env bash
# Development on the Mac only, never on a robot machine: put this repo's .venv on the sibling
# source checkouts (editable) instead of the pinned commits, without touching pyproject.toml or
# uv.lock. Undo with `uv sync --locked` (which any `uv run` without --no-sync also does).
#
#     scripts/dev_link.sh [SRC]        SRC: the folder holding the checkouts (default: ..)
#
# Links each of xpisode (and its packages/*), xpisode-realsense, xpisode-i2rt found in SRC.
# Afterwards run things with .venv/bin/... or `uv run --no-sync ...`; `scripts/dev_link.sh
# --status` says what .venv runs now.
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python="$here/.venv/bin/python"

if [[ "${1:-}" == "--status" ]]; then
  "$python" - <<'PY'
import importlib.metadata as m, json
for name in ("xpisode", "xpisode-format", "xpisode-device", "xpisode-env", "xpisode-realsense", "xpisode-i2rt"):
    try:
        text = m.distribution(name).read_text("direct_url.json") or "{}"
    except m.PackageNotFoundError:
        print(f"{name:18} not installed"); continue
    url = json.loads(text)
    where = url.get("url", "?")
    if url.get("dir_info", {}).get("editable"):
        print(f"{name:18} editable {where}")
    else:
        print(f"{name:18} pinned   {url.get('vcs_info', {}).get('commit_id', where)}")
PY
  exit 0
fi

src="$(cd -- "${1:-$here/..}" && pwd)"
[[ -x "$python" ]] || (cd "$here" && uv venv --quiet)
links=()
[[ -d "$src/xpisode/.git" ]] && links+=(
  "$src/xpisode/packages/xpisode-format"
  "$src/xpisode/packages/xpisode-device"
  "$src/xpisode/packages/xpisode-env"
  "$src/xpisode"
)
if [[ -d "$src/xpisode-i2rt/.git" ]]; then links+=("$src/xpisode-i2rt"); fi
late=()
if [[ -d "$src/xpisode-realsense/.git" ]]; then
  # pyrealsense2 has no macOS wheels: on a Mac the adapter is linked without its dependencies
  # (it can be edited and imported up to `import pyrealsense2`, not run).
  if [[ "$(uname -s)" == Darwin ]]; then late+=("$src/xpisode-realsense"); else links+=("$src/xpisode-realsense"); fi
fi
[[ ${#links[@]} -gt 0 ]] || { echo "dev_link: no checkouts in $src" >&2; exit 1; }
args=()
for link in "${links[@]}"; do args+=(-e "$link"); done
# --no-config: not this project's [tool.uv] (its overrides would pull the framework packages
# from an index); --no-sources: the framework packages come from these checkouts, not from the
# git revs the adapters pin for their own development.
uv pip install --no-config --no-sources --python "$python" "${args[@]}"
if [[ ${#late[@]} -gt 0 ]]; then
  uv pip install --no-config --no-sources --no-deps --python "$python" -e "${late[@]}"
  echo "dev_link: ${late[*]} linked without its dependencies (no pyrealsense2 on macOS)"
fi
"$0" --status
