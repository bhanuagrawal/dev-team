#!/usr/bin/env bash
# dev-team setup — the setup agent's entry point.
# usage: setup.sh [--project ProjectName] [--at HH:MM]
set -euo pipefail
TEAM="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
chmod +x "$TEAM"/scripts/*.sh 2>/dev/null || true
exec python3 "$TEAM/cli.py" setup "$@"
