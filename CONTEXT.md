# Parrot — Implementation Context

## Project Overview
Multi-agent "Onboarding Twin" hackathon project. An agent watches expert employees' screens, learns workflows, stores them in Neo4j, then replays them via browser automation.

## Tech Stack
- **Backend**: FastAPI (Python 3.14), AWS Bedrock (Claude 3.5 Sonnet v2: `anthropic.claude-3-5-sonnet-20241022-v2:0`)
- **Graph DB**: Neo4j Aura (`neo4j+s://a7178019.databases.neo4j.io`)
- **Screen Capture**: mss + pynput
- **Vision**: Claude multimodal via Bedrock
- **Browser Automation**: Playwright (chromium, headed mode)
- **Frontend**: Single HTML file, Tailwind CSS CDN, vis.js Network CDN

## Current State (working)
- Screen capture + Claude vision action detection ✅
- ObserverAgent processes actions → structured workflow ✅
- Neo4j stores workflows, steps, reasoning, convergence ✅
- All REST + WebSocket endpoints for capture ✅
- Basic frontend with tabbed layout ✅

## Three Changes Needed

### 1. Sidebar Layout for Capture
- Detected actions stream into a fixed right sidebar (~320px) in real-time via WebSocket
- User works normally on their desktop while sidebar shows what the agent sees
- Controls (start/stop/auto-analyze) live in the sidebar
- Layout: full-width, main area + fixed right sidebar

### 2. Real Neo4j Graph Visualization
- Use vis.js Network (CDN: `https://unpkg.com/vis-network/standalone/umd/vis-network.min.js`)
- Feed data from existing `/graph/workflows/{id}/visualize` endpoint (already returns {nodes, edges})
- Force-directed layout with physics
- Nodes colored/shaped by type: Expert=green, Workflow=blue, Step=orange, Reasoning=purple, Action=gray
- Edges labeled with relationship types
- Interactive: drag, click for details, zoom
- No direct Neo4j browser connection (CORS blocks it) — use backend API

### 3. Browser Simulator Agent (Playwright)
- New file: `backend/agents/simulator_agent.py`
- Uses `playwright` async API to launch visible Chromium browser
- Claude reads each workflow step → generates browser commands (navigate, click, type)
- After each action: capture screenshot → stream to frontend via WebSocket
- Frontend shows live screenshots in main area + action log in sidebar

**Execution flow:**
1. `POST /simulate/start {workflow_id}` → fetch workflow from Neo4j, launch browser
2. For each step: Claude interprets → Playwright executes → screenshot captured → streamed
3. `WS /ws/simulate/{session_id}` streams screenshots (base64) + actions to frontend
4. `POST /simulate/stop/{session_id}` closes browser

**Claude prompt pattern for browser commands:**
```
Given this workflow step, determine browser actions:
Step: {step_name}, Context: {context}, Reasoning: {reasoning}
Current URL: {url}
Respond JSON: [{"type":"navigate","url":"..."}, {"type":"click","selector":"..."}, {"type":"type","selector":"...","text":"..."}]
```

**Reference**: watch-and-learn repo (github.com/kfallah/watch-and-learn) uses Playwright MCP server + Gemini + RAG. Our approach is simpler: direct Playwright control via Claude tool-use style prompting.

## Key Files
| File | Purpose |
|------|---------|
| `backend/main.py` | FastAPI app, all endpoints |
| `backend/static/index.html` | Frontend (single HTML file) |
| `backend/agents/observer_agent.py` | Watches expert sessions → structured workflows |
| `backend/agents/twin_agent.py` | Coaches newbies through workflows |
| `backend/agents/simulator_agent.py` | **NEW** — Playwright browser agent |
| `backend/capture/screen_recorder.py` | Screen capture + input events |
| `backend/capture/action_detector.py` | Claude vision action detection |
| `backend/db/neo4j_client.py` | Neo4j data layer (all graph operations) |
| `backend/config.py` | Settings from .env |

## Neo4j Graph Schema
```
(:Expert)-[:AUTHORED]->(:Workflow)-[:HAS_STEP]->(:Step)-[:DECIDED_BECAUSE]->(:Reasoning)
(:Step)-[:NEXT]->(:Step)
(:Step)-[:INVOLVES]->(:Action)
(:Newbie)-[:ATTEMPTED]->(:Session)-[:PERFORMED]->(:NewbieAction)
(:NewbieAction)-[:ALIGNS_WITH|DIVERGES_FROM]->(:Step)
(:Session)-[:SCORED]->(:ConvergenceScore)
```

## API Endpoints (existing)
- `GET /health` — Health check with Neo4j status
- `POST /capture/start` — Start screen recording
- `POST /capture/stop/{id}` — Stop + auto-process → workflow → Neo4j
- `POST /capture/analyze/{id}` — Analyze latest screenshot with Claude vision
- `POST /capture/start-auto/{id}` — Background auto-analysis loop
- `WS /ws/capture/{id}` — Real-time capture stream
- `GET /graph/workflows` — List all workflows
- `GET /graph/workflows/{id}` — Full workflow detail
- `GET /graph/workflows/{id}/visualize` — Nodes + edges for graph viz
- `GET /graph/workflows/{id}/reasoning` — Reasoning chain
- `POST /graph/search` — Full-text search
- `POST /simulate/run` — Agent simulates workflow (current: text-only)

## AWS Credentials
Bedrock model: `anthropic.claude-3-5-sonnet-20241022-v2:0` (Claude Sonnet 4 models DENIED by IAM)
Region: us-west-2, uses session tokens (STS assumed role)
