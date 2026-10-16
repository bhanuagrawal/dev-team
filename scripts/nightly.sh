#!/bin/bash
# nightly.sh — launchd entry point. Runs the AGENTIC orchestrator (not Python):
# it boots the configured orchestrator runtime with the dev-team skill loaded,
# operating on the given project.
#
# usage: nightly.sh <ProjectName>
set -u
TEAM="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="${1:-MindfulLauncher}"
export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:$PATH"
mkdir -p "$TEAM/logs"
# Single-instance guard: launchd re-fires or a manual kickstart must never
# run two orchestrators against the same project (last night's race lesson).
LOCK="$TEAM/.nightly.lock"
cleanup_lock() { rm -rf "$LOCK"; }
if mkdir "$LOCK" 2>/dev/null; then
  echo $$ > "$LOCK/pid"
  trap cleanup_lock EXIT INT TERM
else
  pid=$(cat "$LOCK/pid" 2>/dev/null)
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    echo "=== skipped $(date '+%F %T'): previous nightly still running (pid $pid) ===" >> "$TEAM/logs/launchd.log"
    exit 0
  fi
  echo "=== stale lock removed (previous run died) ===" >> "$TEAM/logs/launchd.log"
  rm -rf "$LOCK"
  mkdir "$LOCK"; echo $$ > "$LOCK/pid"
  trap cleanup_lock EXIT INT TERM
fi
echo "=== nightly start $(date '+%F %T') project=$PROJECT ===" >> "$TEAM/logs/launchd.log"

MODEL=$(python3 -c "import json;print(json.load(open('$TEAM/config/runtimes.json'))['roles']['orchestrator'].get('model',''))" 2>/dev/null)
RT=$(python3 -c "import json;print(json.load(open('$TEAM/config/runtimes.json'))['roles']['orchestrator'].get('runtime','opencode'))" 2>/dev/null)

PROMPT="You are the dev-team orchestrator. Load and follow the dev-team skill in full. Run tonight's pipeline: status → pick the next safe unanimated surface → worktree-start → developer-prompt → spawn the developer as a native subagent (model from runtimes.json) → verify the worktree → release on green → record → alarm. Project: $PROJECT (absolute path: $(dirname "$TEAM")/$PROJECT). Report a compact summary of what shipped."

# watchdog: a hung orchestrator session must not block every future night (90 min cap)
run_capped() {
  local cap_s="$1"; shift
  "$@" >> "$TEAM/logs/launchd.log" 2>&1 &
  local pid=$!
  ( sleep "$cap_s"; kill "$pid" 2>/dev/null; echo "=== watchdog: killed orchestrator after ${cap_s}s ===" >> "$TEAM/logs/launchd.log" ) &
  local wd=$!
  wait "$pid"; local rc=$?
  kill "$wd" 2>/dev/null
  return $rc
}

case "$RT" in
  opencode)
    if [ -n "$MODEL" ]; then
      run_capped 5400 opencode run --auto --model "$MODEL" "$PROMPT"
    else
      run_capped 5400 opencode run --auto "$PROMPT"
    fi
    ;;
  claude)
    run_capped 5400 claude -p "$PROMPT" --permission-mode acceptEdits --max-turns 50
    ;;
  *)
    echo "unknown orchestrator runtime: $RT" >> "$TEAM/logs/launchd.log"
    exit 1
    ;;
esac

# staleness + CI reconciliation + issue filing (deterministic)
python3 "$TEAM/cli.py" alarm "$PROJECT" >> "$TEAM/logs/launchd.log" 2>&1
echo "=== nightly done $(date '+%F %T') ===" >> "$TEAM/logs/launchd.log"
