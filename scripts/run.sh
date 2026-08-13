#!/usr/bin/env bash
# Convenience launcher. Loads .env and starts the orchestrator loop.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo "No .env found — copy .env.example to .env first." >&2
  exit 1
fi

exec python -m stockforge.cli "${@:-run}"
