#!/usr/bin/env bash
# Adds an `invoice-agent` command to your shell so you can run the agent from any folder.
# Usage: npm run install:shortcut     (undo: npm run uninstall:shortcut)
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
case "$(basename "${SHELL:-zsh}")" in
  bash) RC="$HOME/.bashrc" ;;
  *)    RC="$HOME/.zshrc" ;;
esac
START="# >>> invoice-agent shortcut >>>"
END="# <<< invoice-agent shortcut <<<"
touch "$RC"
# remove any previous copy so re-running updates the path
sed -i.bak "/$START/,/$END/d" "$RC" && rm -f "$RC.bak"
if [ "$1" = "--remove" ]; then echo "Removed shortcut from $RC"; exit 0; fi
cat >> "$RC" <<BLOCK

$START
invoice-agent() {
  local dir="$DIR"
  if [ ! -d "\$dir" ]; then
    echo "invoice-agent: project folder not found at \$dir" >&2
    return 1
  fi
  ( cd "\$dir" || exit 1
    case "\$1" in
      ui)      npm run ui ;;
      run|"")  npm run agent -- "\${@:2}" ;;
      *)       npm run "\$@" ;;
    esac )
}
$END
BLOCK
echo "Installed 'invoice-agent' in $RC (project: $DIR)"
echo "Open a new terminal or run: source $RC"
