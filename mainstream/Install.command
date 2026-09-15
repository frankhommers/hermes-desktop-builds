#!/bin/bash
# Finder entry point; does not install/update Homebrew or launch Hermes.
set -u
cd -- "$(dirname -- "$0")" || exit 1
export PATH="$HOME/.local/bin:$HOME/.hermes/node/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
python=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(not ((3,11) <= sys.version_info[:2] < (3,14)))' 2>/dev/null; then
    python="$candidate"
    break
  fi
done
if [ -z "$python" ]; then
  printf '%s\n' 'Install Python 3.11, 3.12 or 3.13 first, then retry. No changes made.'
  status=1
else
  "$python" installer.py "$@"
  status=$?
fi
printf '\nPress Return to close this window.\n'
read -r _
exit "$status"
