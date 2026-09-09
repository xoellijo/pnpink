#!/usr/bin/env bash
set +u
if [[ -s "$HOME/.nvm/nvm.sh" ]]; then
  source "$HOME/.nvm/nvm.sh"
fi
command -v node || true
node --version 2>/dev/null || true
find /home/xoel/dev/boardzilla-sandbox/node_modules -maxdepth 2 -type d \
  \( -name playwright -o -name playwright-core \) -print | head

