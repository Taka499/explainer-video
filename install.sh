#!/bin/bash
# Link this repository's skill/ to ~/.claude/skills/explainer-video.
# Refuses when something other than that link is already there.
# Run it outside the Claude Code sandbox: ~/.claude/skills is write-denied inside it.
set -euo pipefail

src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/skill"
dest="$HOME/.claude/skills/explainer-video"

if [[ -L "$dest" ]]; then
  current="$(readlink "$dest")"
  if [[ "$current" != "$src" ]]; then
    echo "refusing: $dest already links to $current, not $src" >&2
    exit 1
  fi
  echo "already installed: $dest -> $src"
elif [[ -e "$dest" ]]; then
  echo "refusing: $dest exists and is not a link; move it away first" >&2
  exit 1
else
  mkdir -p "$(dirname "$dest")"
  ln -s "$src" "$dest"
  echo "installed: $dest -> $src"
fi

cat <<EOF

Next, once:
  1. brew install pkgconf && brew install --cask mactex-no-gui
  2. security add-generic-password -a "\$USER" -s elevenlabs-api-key -w     (prompts for the key without echo)
  3. ~/.claude/settings.json:
       sandbox.filesystem.allowWrite  += "~/Movies/explainers"
       permissions.allow              += "Bash($dest/scripts/tts.py *)"
                                         "Bash($src/scripts/tts.py *)"
  4. $dest/scripts/toolchain.sh setup
  5. $dest/scripts/toolchain.sh check
EOF
