#!/bin/bash
# The explainer-video toolchain: Manim Community Edition in a venv under the output root.
#
#   toolchain.sh check        list what is missing, each with the command that fixes it (exit 1 if any)
#   toolchain.sh setup        create or update the Manim venv
#   toolchain.sh run CMD...   run CMD with the venv and TeX on PATH and every cache in a writable place
#
# Works inside the Claude Code sandbox once ~/Movies/explainers is in sandbox.filesystem.allowWrite.
# The output root defaults to ~/Movies/explainers (override: EXPLAINER_HOME).
set -euo pipefail

MANIM_VERSION="0.22.0"
PYTHON_VERSION="3.13"
ROOT="${EXPLAINER_HOME:-$HOME/Movies/explainers}"
TOOLCHAIN="$ROOT/.toolchain"
VENV="$TOOLCHAIN/venv"
TEXBIN="/Library/TeX/texbin"
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"

# TeX from MacTeX lands on PATH only in shells started after the install, so add it here.
export PATH="$VENV/bin:$TEXBIN:$PATH"

have() { command -v "$1" >/dev/null 2>&1; }

check() {
  local missing=0
  ok() { printf 'ok       %-10s %s\n' "$1" "$2"; }
  bad() { printf 'MISSING  %-10s fix: %s\n' "$1" "$2"; missing=1; }

  local probe
  if mkdir -p "$ROOT" 2>/dev/null && probe="$(mktemp "$ROOT/.write-probe.XXXXXX" 2>/dev/null)"; then
    rm -f "$probe"
    ok output "$ROOT"
  else
    bad output "add \"~/Movies/explainers\" to sandbox.filesystem.allowWrite in ~/.claude/settings.json"
  fi
  for tool in ffmpeg ffprobe; do
    if have "$tool"; then ok "$tool" "$(command -v "$tool")"; else bad "$tool" "brew install ffmpeg"; fi
  done
  for tool in latex xelatex dvisvgm; do
    if have "$tool"; then ok "$tool" "$(command -v "$tool")"; else bad "$tool" "brew install --cask mactex-no-gui"; fi
  done

  local version
  if version="$("$VENV/bin/python" -c 'import manim, cairo, manimpango; print(manim.__version__)' 2>/dev/null)" \
      && [[ "$version" == "$MANIM_VERSION" ]]; then
    ok manim "$version in $VENV"
  else
    # Only setup needs these; once the venv works they no longer matter.
    if have uv; then ok uv "$(command -v uv)"; else bad uv "curl -LsSf https://astral.sh/uv/install.sh | sh"; fi
    if have pkg-config; then
      ok pkg-config "$(command -v pkg-config)"
      if pkg-config --exists cairo; then ok cairo "$(pkg-config --modversion cairo)"; else bad cairo "brew install cairo"; fi
    else
      bad pkg-config "brew install pkgconf"
    fi
    bad manim "\"$SELF\" setup   (after the items above)"
  fi
  return "$missing"
}

setup() {
  have uv || { echo "uv is missing: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2; exit 1; }
  have pkg-config || { echo "pkg-config is missing: brew install pkgconf" >&2; exit 1; }
  mkdir -p "$TOOLCHAIN"
  # uv's default cache (~/.cache/uv) is write-denied inside the sandbox.
  export UV_CACHE_DIR="$TOOLCHAIN/uv-cache"
  # pycairo has no macOS wheel and builds with meson. Inside the sandbox /usr/bin/ar is an
  # xcrun shim that fails on its cache, and meson then reports "Unknown linker(s)";
  # `xcrun --find` still prints the real path, so hand meson that.
  if [[ -z "${AR:-}" ]]; then
    AR="$(xcrun --find ar 2>/dev/null || true)"
    [[ -n "$AR" ]] && export AR
  fi
  [[ -x "$VENV/bin/python" ]] || uv venv --python "$PYTHON_VERSION" "$VENV"
  uv pip install --python "$VENV/bin/python" "manim==$MANIM_VERSION"
  "$VENV/bin/python" -c 'import manim, cairo, manimpango; print("manim", manim.__version__, "ready in", "'"$VENV"'")'
}

run() {
  [[ $# -gt 0 ]] || { echo "usage: toolchain.sh run CMD..." >&2; exit 64; }
  mkdir -p "$TOOLCHAIN/cache" "$TOOLCHAIN/texmf-var"
  # Fontconfig, Manim and TeX would otherwise write under ~/.cache and ~/Library, which the sandbox denies.
  export XDG_CACHE_HOME="$TOOLCHAIN/cache"
  export TEXMFVAR="$TOOLCHAIN/texmf-var"
  exec "$@"
}

case "${1:-}" in
  check) check ;;
  setup) setup ;;
  run) shift; run "$@" ;;
  *) sed -n '2,9p' "$SELF" | sed 's/^# \{0,1\}//' >&2; exit 64 ;;
esac
