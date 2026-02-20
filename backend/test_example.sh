#!/bin/bash

# Test script for AgentMirror backend

echo "=== Testing AgentMirror Backend ==="
echo ""

# Health check
echo "1. Health Check"
curl -s http://localhost:8000/health | jq
echo ""

# Test agent
echo "2. Test Bedrock Connection"
curl -s -X POST http://localhost:8000/test \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello, confirm you are working"}' | jq
echo ""

# Observer agent (example)
echo "3. Observer Agent - Process Session"
curl -s -X POST http://localhost:8000/observe/process \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-session-1",
    "user_id": "expert-001",
    "role": "expert",
    "task_type": "customer_support_ticket",
    "actions": [
      {"id": "a1", "type": "click", "target": "dashboard", "timestamp": "2024-01-01T10:00:00Z"},
      {"id": "a2", "type": "click", "target": "filter_high_priority", "timestamp": "2024-01-01T10:00:05Z"},
      {"id": "a3", "type": "click", "target": "ticket_1234", "timestamp": "2024-01-01T10:00:10Z"}
    ]
  }' | jq
echo ""

echo "=== Tests Complete ==="
