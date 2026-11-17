#!/bin/zsh
cd "${0:A:h}"
if curl --silent --fail http://127.0.0.1:8765/api/health >/dev/null; then
  open http://127.0.0.1:8765
  exit 0
fi
open http://127.0.0.1:8765
if [[ -x .venv/bin/python ]]; then
  exec .venv/bin/python server.py
else
  printf 'AI environment missing. Run Setup AI.command for AI features. Starting basic engine.\n'
  exec python3 server.py
fi
