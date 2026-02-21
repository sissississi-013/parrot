#!/bin/bash
# Test script: fires all endpoints that emit Datadog metrics, then re-provisions the dashboard.
# Run from the repo root: bash backend/test_metrics.sh

BASE="http://localhost:8000"

echo "=== Step 1: Health check ==="
curl -s "$BASE/health" | python3 -m json.tool
echo ""

echo "=== Step 2: Observer (parrot.observer.* metrics) ==="
curl -s -X POST "$BASE/observe/process" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "metrics-test-1",
    "user_id": "expert-001",
    "role": "expert",
    "task_type": "customer_support_ticket",
    "actions": [
      {"id": "a1", "type": "click", "target": "dashboard", "timestamp": "2024-01-01T10:00:00Z"},
      {"id": "a2", "type": "click", "target": "filter_high_priority", "timestamp": "2024-01-01T10:00:05Z"},
      {"id": "a3", "type": "click", "target": "ticket_1234", "timestamp": "2024-01-01T10:00:10Z"}
    ]
  }' | python3 -m json.tool
echo ""

echo "=== Step 3: Twin guide (parrot.twin.* metrics) ==="
curl -s -X POST "$BASE/coach/guide" \
  -H "Content-Type: application/json" \
  -d '{
    "workflow_id": "test-wf-1",
    "expert_workflow": {
      "workflow_name": "Support Ticket Triage",
      "steps": [
        {"step_number": 1, "step_name": "Open dashboard", "actions": ["click dashboard"], "context": "Navigate to main view", "reasoning": "Need overview of tickets"},
        {"step_number": 2, "step_name": "Filter priority", "actions": ["click filter"], "context": "Focus on urgent items", "reasoning": "High priority first"}
      ]
    },
    "current_step": 0,
    "newbie_action": {"type": "click", "target": "dashboard"}
  }' | python3 -m json.tool
echo ""

echo "=== Step 4: Convergence (parrot.convergence.* metrics) ==="
curl -s -X POST "$BASE/coach/convergence" \
  -H "Content-Type: application/json" \
  -d '{"workflow_id":"test-wf-1","session_id":"metrics-session-2","expert_workflow":{"workflow_name":"Support Ticket Triage","steps":[{"step_number":1,"step_name":"Open dashboard","actions":["click dashboard"],"context":"Navigate to main view","reasoning":"Need overview"},{"step_number":2,"step_name":"Filter priority","actions":["click filter"],"context":"Focus on urgent items","reasoning":"High priority first"}]},"newbie_actions":[{"type":"click","target":"dashboard","step":1},{"type":"click","target":"wrong_button","step":2}]}' | python3 -m json.tool
echo ""

echo "=== Step 5: Wait for metrics to flush ==="
sleep 5

echo "=== Step 6: Query Datadog API for parrot.* metrics ==="
DD_API_KEY=$(grep DD_API_KEY backend/.env 2>/dev/null | cut -d= -f2 || echo "")
DD_APP_KEY=$(grep DD_APP_KEY backend/.env 2>/dev/null | cut -d= -f2 || echo "")

if [ -z "$DD_API_KEY" ]; then
  echo "DD_API_KEY not found in backend/.env — skipping API check"
else
  FROM=$(date -v-1H +%s 2>/dev/null || date -d '1 hour ago' +%s)
  curl -s "https://api.datadoghq.com/api/v1/metrics?from=$FROM" \
    -H "DD-API-KEY: $DD_API_KEY" \
    -H "DD-APPLICATION-KEY: $DD_APP_KEY" | python3 -c "
import json, sys
data = json.load(sys.stdin)
metrics = sorted(m for m in data.get('metrics', []) if 'parrot' in m)
print(f'Found {len(metrics)} parrot metrics in Datadog:')
for m in metrics:
    print(f'  {m}')
"
fi

echo ""
echo "=== Step 7: Re-provision Datadog dashboard ==="
echo "(Creating a NEW dashboard with updated metric queries)"
cd "$(dirname "$0")/../infra/datadog" && python3 dashboard.py
echo ""
echo "=== Done! Check Datadog UI in 1-2 minutes ==="
