#!/usr/bin/env bash
# demo_body.sh — drives the recorded demo session (deterministic, offline).
# Run from the repository root. Set PATH to the project venv so commands look clean.
# NOTE: edit this file to change the demo; then run scripts/demo/record.sh to regenerate.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
export PATH="$ROOT/.venv/bin:$PATH"
export AUTOPSY_NO_EMBEDDINGS=1
# --no-llm also prevents provider use when a caller has credentials configured.

PROMPT='\033[1;32m❯\033[0m '

header() { printf '\033[1;36m%s\033[0m\n' "$1"; sleep 0.5; }

# type_cmd: type a command with a keystroke delay so the recording shows real typing.
type_cmd() {
  local s="$1" i
  printf "${PROMPT}"
  for ((i = 0; i < ${#s}; i++)); do
    printf '%s' "${s:i:1}"
    sleep 0.055
  done
  printf '\n'
}

# throttle: re-print a command's output line by line so the video scrolls at
# a readable pace (idle time alone is collapsed by the renderer).
throttle() {
  local delay="$1"
  local line
  while IFS= read -r line; do
    printf '%s\n' "$line"
    sleep "$delay"
  done
}

run() {
  type_cmd "$*"
  sleep 0.4
  "$@" 2>&1 | throttle 0.05
  sleep 0.9
}

pause() { sleep "$1"; }

TRACE="examples/traces/hallucinated_tool.json"

header "Agent Autopsy — deterministic, fully offline forensics for AI-agent traces"
pause 1.0

header "1/3 — Validate: is this trace well-formed?"
type_cmd "autopsy validate $TRACE"
sleep 0.4
autopsy validate "$TRACE" 2>&1 | throttle 0.06
sleep 0.9

header "2/3 — Analyze: deterministic diagnosis, no LLM, no API keys"
type_cmd "autopsy analyze $TRACE --no-llm --no-embeddings --format text -q"
sleep 0.4
FORCE_COLOR=1 autopsy analyze "$TRACE" --no-llm --no-embeddings --format text -q 2>&1 | throttle 0.055
rc=$?
printf '\n\033[1;33mexit code %s — findings detected (CI gate: 0 = clean, 1 = findings, 2 = error)\033[0m\n' "$rc"
sleep 1.4

header "3/3 — Fixes: suggested patches for the top root causes"
run autopsy fixes "$TRACE"

pause 1.0
header "Diagnosis complete — offline, no LLM, no API keys"
pause 1.5
