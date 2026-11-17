#!/bin/zsh
set -e
cd "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  if [[ -x /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 ]]; then
    /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 -m venv .venv
  else
    python3 -m venv .venv
  fi
fi
.venv/bin/python -m pip install -r requirements-ai.txt
.venv/bin/python setup_ai.py
printf '\nAI setup complete. Open Start.command to launch Clipflow.\n'
