#!/usr/bin/env bash
# Phase 3 smoke — Temporal deep ReAct (requires full docker compose stack)
set -euo pipefail

GATEWAY="${GATEWAY_URL:-http://localhost:8080}"

echo "==> Health (phase 3)"
curl -sf "$GATEWAY/health" | tee /dev/stderr
echo

echo "==> Create session (deep react + max 5 iter)"
SESSION=$(curl -sf -X POST "$GATEWAY/v1/sessions" \
  -H 'Content-Type: application/json' \
  -d '{"title":"Phase 3 Temporal smoke","config":{"deep_react":true,"needs_tools":true,"max_iterations":5}}')
SESSION_ID=$(echo "$SESSION" | python3 -c "import sys,json; print(json.load(sys.stdin)['session_id'])")
echo "session=$SESSION_ID"

echo "==> Send message (Temporal route expected)"
TURN=$(curl -sf -X POST "$GATEWAY/v1/sessions/$SESSION_ID/messages" \
  -H 'Content-Type: application/json' \
  -d '{"content":"Check weather in Hanoi and summarize","deep_react":true,"needs_tools":true}')
echo "$TURN"
RUNTIME=$(echo "$TURN" | python3 -c "import sys,json; print(json.load(sys.stdin).get('runtime',''))")
TURN_ID=$(echo "$TURN" | python3 -c "import sys,json; print(json.load(sys.stdin)['turn_id'])")
WF_ID=$(echo "$TURN" | python3 -c "import sys,json; print(json.load(sys.stdin)['workflow_id'])")

if [ "$RUNTIME" != "temporal" ]; then
  echo "ERROR: expected runtime=temporal, got $RUNTIME" >&2
  exit 1
fi

echo "==> Poll Temporal workflow (max 90s)"
for i in $(seq 1 30); do
  STATUS=$(curl -sf "$GATEWAY/v1/workflows/$WF_ID/status")
  WF_STATUS=$(echo "$STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))")
  ITER=$(echo "$STATUS" | python3 -c "import sys,json; q=json.load(sys.stdin).get('query') or {}; print(q.get('iteration',0))")
  echo "  attempt $i: status=$WF_STATUS iter=$ITER"
  if [ "$WF_STATUS" = "COMPLETED" ] || [ "$WF_STATUS" = "FAILED" ]; then
    break
  fi
  sleep 3
done

echo "==> Turn result"
RESULT=$(curl -sf "$GATEWAY/v1/sessions/$SESSION_ID/turns/$TURN_ID?wait=true")
echo "$RESULT" | python3 -m json.tool

REACT_ITER=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('react_iteration') or 0)")
if [ "${REACT_ITER:-0}" -lt 3 ]; then
  echo "WARN: react_iteration=$REACT_ITER (expected >= 3 for deep demo)" >&2
fi

echo "==> Phase 3 smoke done"
