#!/usr/bin/env bash
# Build the two things a git-pinned `uv sync` cannot install, at exactly the commits pinned in
# pyproject.toml. Run it after every `uv sync --locked` that changed a pin (docs/guides/update.md).
#
#   rtcore   xpisode-i2rt's Rust controller, built from local/build/xpisode-i2rt and installed
#            where the installed package looks for it (xpisode_i2rt.core.default_binary(); in
#            .venv/lib/python3.12/target/release/), with a .rev file beside it. The package,
#            its doctor and its tuner then find it; a git install has no build of its own.
#   panel    the framework's web frontend -> copied into .venv's xpisode/web/static. It is
#            git-ignored in the framework, so a package built from git serves the API only.
#
#   fetch    only check out xpisode-i2rt at its pinned rev (host/setup.sh uses its
#            scripts/setup_host.sh for the real-time limits)
#
#     scripts/build_pinned.sh [rtcore] [panel] [fetch]      (default: rtcore panel)
#
# The controller gets real-time priority from the user's limits (setup_host.sh), not from a
# capability on the binary, so nothing here needs root and no setcap is needed after a rebuild.
#
# Needs: git with access to the repos, cargo (rustup), uv. Touches no device.
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
build="$here/local/build"
mkdir -p "$build"

pinned() {  # the git URL and rev that pyproject.toml pins for a package
  python3 - "$here/pyproject.toml" "$1" <<'PY'
import sys, tomllib
source = tomllib.load(open(sys.argv[1], "rb"))["tool"]["uv"]["sources"][sys.argv[2]]
print(source["git"], source["rev"])
PY
}

checkout() {  # checkout NAME URL REV: a clean clone of URL at REV in local/build/NAME
  local dir="$build/$1"
  if [[ ! -d "$dir/.git" ]]; then
    git clone --quiet "$2" "$dir"
  fi
  git -C "$dir" fetch --quiet origin "$3" 2>/dev/null || git -C "$dir" fetch --quiet origin
  git -C "$dir" checkout --quiet --detach "$3"
  [[ -z "$(git -C "$dir" status --porcelain --untracked-files=no)" ]] || {
    echo "build_pinned: $dir has local changes; remove it and run again" >&2
    exit 1
  }
}

fetch() {
  read -r url rev < <(pinned xpisode-i2rt)
  checkout xpisode-i2rt "$url" "$rev"
  echo "fetch: local/build/xpisode-i2rt at $rev"
}

rtcore() {
  read -r url rev < <(pinned xpisode-i2rt)
  checkout xpisode-i2rt "$url" "$rev"
  (cd "$build/xpisode-i2rt" && cargo build --release --locked)
  local target
  target="$("$here/.venv/bin/python" -c 'from xpisode_i2rt.core import default_binary; print(default_binary())')"
  install -D -m 755 "$build/xpisode-i2rt/target/release/i2rt-rtcore" "$target"
  echo "$rev" >"$target.rev"
  echo "rtcore: $target at xpisode-i2rt $rev"
}

panel() {
  read -r url rev < <(pinned xpisode)
  checkout xpisode "$url" "$rev"
  local static
  static="$("$here/.venv/bin/python" -c 'import importlib.util as u, pathlib as p; print(p.Path(u.find_spec("xpisode").origin).parent / "web" / "static")')"
  (
    cd "$build/xpisode"
    uv sync --locked          # the framework's dev environment: node (nodejs-wheel) for npm
    cd frontend
    uv run npm ci --no-audit --no-fund
    uv run npm run build
  )
  rm -rf "$static"
  cp -R "$build/xpisode/src/xpisode/web/static" "$static"
  echo "$rev" >"$static/.rev"
  echo "panel: $static at xpisode $rev"
}

targets=("$@")
[[ ${#targets[@]} -gt 0 ]] || targets=(rtcore panel)
for target in "${targets[@]}"; do
  case "$target" in
    rtcore | panel | fetch) "$target" ;;
    *) echo "usage: $0 [rtcore] [panel] [fetch]" >&2; exit 2 ;;
  esac
done
