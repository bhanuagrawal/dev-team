#!/bin/bash
# nightly.sh — launchd entry point. Runs the AGENTIC orchestrator via
# `opencode run --auto` (a new OpenCode session each night), operating on
# the given project.
#
# usage: nightly.sh <ProjectName>
set -u
TEAM="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# No default project: the team is project-independent (a baked-in default was
# a project reference inside team code). Require the name explicitly.
PROJECT="${1:?usage: nightly.sh <ProjectName>}"
export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:$PATH"
# LLM provider/model + keys (llmctl se generate hota hai)
[ -f "$HOME/agentconfig/agentconfig.env" ] && . "$HOME/agentconfig/agentconfig.env"
[ -f "$HOME/agentconfig/secrets.env" ] && . "$HOME/agentconfig/secrets.env"
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

# Read the orchestrator model from runtimes.json
MODEL=$(python3 -c "
import json, pathlib
cfg = json.loads(pathlib.Path('$TEAM/config/runtimes.json').read_text())
print(cfg.get('roles', {}).get('orchestrator', {}).get('model', ''))
" 2>/dev/null)

# Role token -> qualified model id (agentconfig env se)
case "$MODEL" in
  llm)     MODEL="${LLM_MODEL:-}" ;;
  thinker) MODEL="${THINKER_LLM_MODEL:-}" ;;
esac

if [ -z "$MODEL" ]; then
  echo "ERROR: no orchestrator model in config/runtimes.json" >> "$TEAM/logs/launchd.log"
  rm -rf "$LOCK"
  exit 1
fi

PROMPT="Run workflow for $PROJECT."

# Emulator baseline (run start): snapshot what is ALREADY running so run end can
# tell the orchestrator's emulator apart from one the owner opened deliberately.
python3 "$TEAM/cli.py" emulators "$PROJECT" --record --reset >> "$TEAM/logs/launchd.log" 2>&1

# The orchestrator is ANY agentic tool — nothing vendor-specific is launched
# here. The command template comes from roles.orchestrator.command in
# config/runtimes.json, or from DEVTEAM_AGENT_CMD in the environment.

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

run_capped 5400 opencode run --title "dev-team $PROJECT $(date +%F)" --model "$MODEL" --auto "$PROMPT"
RC=$?
if [ $RC -ne 0 ]; then
  echo "=== orchestrator exited rc=$RC ===" >> "$TEAM/logs/launchd.log"
fi

# Emulator teardown (run end). A watchdog kill above can orphan an emulator the
# orchestrator started; this stops only what appeared after the run began, and
# only ever the ones this run started (see team/emulator.py).
python3 "$TEAM/cli.py" emulators "$PROJECT" >> "$TEAM/logs/launchd.log" 2>&1

# staleness + CI reconciliation + issue filing (deterministic)
python3 "$TEAM/cli.py" alarm "$PROJECT" >> "$TEAM/logs/launchd.log" 2>&1
echo "=== nightly done $(date '+%F %T') ===" >> "$TEAM/logs/launchd.log"
