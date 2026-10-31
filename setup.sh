#!/usr/bin/env bash
# dev-team setup — the setup agent's entry point.
# usage: setup.sh [--project ProjectName] [--at HH:MM]
set -euo pipefail
TEAM="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
chmod +x "$TEAM"/scripts/*.sh 2>/dev/null || true
exec python3 "$TEAM/cli.py" setup "$@"

# The doc-overlap gate lives in scripts/hooks (versioned, so every clone has it).
# Without this line a fresh checkout commits straight past an overlap, which is
# exactly how two copies of one rule survive to mislead the next run.
git config core.hooksPath scripts/hooks
echo "hooks: core.hooksPath -> scripts/hooks (doc overlap gate on every commit)"
