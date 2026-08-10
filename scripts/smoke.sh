#!/usr/bin/env bash
# Phase 1 smoke test — requires docker compose up
set -euo pipefail

GATEWAY="${GATEWAY_URL:-http://localhost:8080}"

echo "==> Health"
curl -sf "$GATEWAY/health" | tee /dev/stderr
echo

echo "==> Create session"
SESSION=$(curl -sf -X POST "$GATEWAY/v1/sessions" \
  -H 'Content-Type: application/json' \
  -d '{"title":"Docker smoke test"}')
echo "$SESSION"
SESSION_ID=$(echo "$SESSION" | python3 -c "import sys,json; print(json.load(sys.stdin)['session_id'])")

echo "==> Send message"
TURN=$(curl -sf -X POST "$GATEWAY/v1/sessions/$SESSION_ID/messages" \
  -H 'Content-Type: application/json' \
  -d '{"content":"Xin chào, Phase 1 hoạt động chưa?"}')
echo "$TURN"
WF_ID=$(echo "$TURN" | python3 -c "import sys,json; print(json.load(sys.stdin)['workflow_id'])")
TURN_ID=$(echo "$TURN" | python3 -c "import sys,json; print(json.load(sys.stdin)['turn_id'])")

echo "==> Poll workflow (max 30s)"
for i in $(seq 1 15); do
  STATUS=$(curl -sf "http://localhost:8000/status/$WF_ID")
  WF_STATUS=$(echo "$STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin)['workflow']['status'])")
  echo "  attempt $i: workflow=$WF_STATUS"
  if [ "$WF_STATUS" = "COMPLETED" ] || [ "$WF_STATUS" = "FAILED" ]; then
    break
  fi
  sleep 2
done

echo "==> Turn result"
curl -sf "$GATEWAY/v1/sessions/$SESSION_ID/turns/$TURN_ID" | python3 -m json.tool

echo "==> Done"
