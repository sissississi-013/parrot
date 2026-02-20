# Parrot — Implementation Context

## Project Overview
Multi-agent "Onboarding Twin" hackathon project. An agent watches expert employees' screens, learns workflows, stores them in Neo4j, then replays them via browser automation.

## Tech Stack
- **Backend**: FastAPI (Python 3.12), AWS Bedrock (Claude 3.5 Sonnet v2: `anthropic.claude-3-5-sonnet-20241022-v2:0`)
- **Graph DB**: Neo4j Aura (`neo4j+s://a7178019.databases.neo4j.io`)
- **Browser Capture**: Playwright (chromium, headed mode) + JS DOM event injection
- **Screen Capture (fallback)**: mss + pynput + Claude multimodal vision
- **Browser Automation**: Playwright async API for simulator agent
- **Frontend**: Single HTML file, Tailwind CSS CDN, vis.js Network CDN (→ being replaced with 3d-force-graph)
- **Observability**: Datadog ddtrace (installed, not yet instrumented)
- **Python path**: `/Library/Frameworks/Python.framework/Versions/3.12/bin/python3`

## Completed Features

### Browser-based Capture (replaces desktop screenshot approach)
- `backend/capture/browser_capture.py` — Launches Playwright Chromium, injects JS DOM event listeners
- Captures: clicks, typing, scroll, navigation, form submissions with precise element metadata
- Polls browser for captured events every 0.8s, streams screenshots at 1.5fps
- Endpoints: `POST /browser/start`, `POST /browser/stop/{id}`, `GET /browser/status/{id}`, `WS /ws/browser/{id}`
- Auto-process pipeline: browser actions → ObserverAgent (Claude) → structured workflow → Neo4j

### Observer Agent
- `backend/agents/observer_agent.py` — Processes captured actions through Claude → structured workflows
- Sanitizes control characters in action data before sending to Claude
- Stores workflow in Neo4j: Expert → Workflow → Steps → Reasoning → Actions

### Simulator Agent
- `backend/agents/simulator_agent.py` — Replays learned workflows in a real browser
- Claude vision plans browser actions from workflow steps, Playwright executes them
- Streams screenshots + action log via WebSocket
- **LIMITATION**: Results only in memory, NOT stored to Neo4j (being fixed)

### Neo4j Data Layer
- `backend/db/neo4j_client.py` — Full graph CRUD with 10 workflows, 152 nodes, 136 relationships
- Methods: `store_workflow`, `get_workflow`, `list_workflows`, `search_workflows`, `get_workflow_graph`
- Session/convergence: `create_session`, `log_newbie_action`, `store_convergence`, `get_convergence_graph`
- Schema: Expert→AUTHORED→Workflow→HAS_STEP→Step→DECIDED_BECAUSE→Reasoning, Step→INVOLVES→Action

### Frontend
- `backend/static/index.html` — Three modes: Capture, Workflows, Simulate
- Capture: live browser screenshot stream + real-time action sidebar via WebSocket
- Workflows: vis.js 2D hierarchical graph (being replaced with 3D)
- Simulate: agent-controlled browser screenshots + action log

## Current Neo4j Data (confirmed via direct query)
- 10 Workflows, 37 Steps, 37 Reasoning nodes, 61 Actions, 5 Experts
- Workflows include: YC Job Application, GitHub Repository Access, Deploy to Staging, etc.
- The Aura Explore tab only shows a subset — run Cypher queries in Query tab to see all data

## Key Files
| File | Purpose |
|------|---------|
| `backend/main.py` | FastAPI app, all endpoints (1062 lines) |
| `backend/static/index.html` | Frontend single-page app (573 lines) |
| `backend/agents/observer_agent.py` | Watches expert → structured workflows |
| `backend/agents/twin_agent.py` | Coaches newbies, calculates convergence |
| `backend/agents/simulator_agent.py` | Playwright browser agent for replay |
| `backend/agents/test_agent.py` | Simple test agent for Bedrock calls |
| `backend/capture/browser_capture.py` | Playwright-based DOM event capture |
| `backend/capture/screen_recorder.py` | Desktop capture fallback (mss + pynput) |
| `backend/capture/action_detector.py` | Claude vision analysis fallback |
| `backend/db/neo4j_client.py` | Neo4j data layer (all graph operations) |
| `backend/config.py` | Settings from .env |
| `CONTEXT.md` | This file |

## API Endpoints
### Capture
- `POST /browser/start` — Launch Playwright browser, begin DOM capture
- `POST /browser/stop/{id}?auto_process=true` — Stop + ObserverAgent → Neo4j
- `GET /browser/status/{id}` — Status + all captured actions
- `GET /browser/screenshot/{id}` — Latest browser screenshot
- `WS /ws/browser/{id}` — Real-time screenshots + DOM actions

### Graph (Neo4j)
- `GET /graph/workflows` — List all workflows
- `GET /graph/workflows/{id}` — Full workflow detail
- `GET /graph/workflows/{id}/visualize` — Nodes + edges for graph viz
- `GET /graph/workflows/{id}/reasoning` — Reasoning chain
- `POST /graph/search` — Full-text search
- `GET /graph/sessions/{id}/convergence` — Convergence graph
- `GET /graph/convergence` — All convergence scores

### Simulate
- `POST /simulate/start` — Launch browser, simulate workflow step-by-step
- `POST /simulate/stop/{id}` — Stop simulation
- `GET /simulate/status/{id}` — Status + action log
- `WS /ws/simulate/{id}` — Real-time screenshots + actions

### Other
- `GET /health` — Health check with Neo4j status
- `POST /test` — Test Bedrock connectivity
- `POST /observe/process` — Process actions through ObserverAgent
- `POST /coach/guide` — Get coaching guidance from TwinAgent
- `POST /coach/convergence` — Calculate convergence score

## Neo4j Graph Schema
```
(:Expert)-[:AUTHORED]->(:Workflow)-[:HAS_STEP]->(:Step)-[:DECIDED_BECAUSE]->(:Reasoning)
(:Step)-[:NEXT]->(:Step)
(:Step)-[:INVOLVES]->(:Action)
(:Newbie)-[:ATTEMPTED]->(:Session)-[:FOLLOWING]->(:Workflow)
(:Session)-[:PERFORMED]->(:NewbieAction)
(:NewbieAction)-[:ALIGNS_WITH|DIVERGES_FROM]->(:Step)
(:Session)-[:SCORED]->(:ConvergenceScore)
```

## AWS Credentials
Bedrock model: `anthropic.claude-3-5-sonnet-20241022-v2:0` (Claude Sonnet 4 models DENIED by IAM)
Region: us-west-2, uses session tokens (STS assumed role)

---

## In Progress: Next Features

### Phase 1: 3D Inclined Knowledge Graph (Workflows Page)
- Replace vis.js with `3d-force-graph` (CDN: `https://cdn.jsdelivr.net/npm/3d-force-graph`)
- Rewrite `renderVisGraph()` for 3D: inclined camera at {x:0, y:150, z:300}
- Map edges `{from,to}` → `{source,target}` for 3d-force-graph format
- Add `GET /graph/full` endpoint + `neo4j_client.get_full_graph()` for "View All" button
- Node colors: Expert=#22c55e, Workflow=#3b82f6, Step=#f59e0b, Reasoning=#a855f7, Action=#9ca3af

### Phase 2: Simulator → Neo4j Pipeline
- After simulation completes, persist agent actions to Neo4j:
  1. `neo4j_client.create_session(newbie_id, workflow_id)`
  2. `neo4j_client.log_newbie_action()` for each action
  3. `twin_agent.calculate_convergence()` to compare agent vs expert
  4. `neo4j_client.store_convergence()` to store scores + alignment/divergence edges
- Add `_persist_simulation_results()` background task in main.py

### Phase 3: Newbie Workflow Dashboard
- New "Dashboard" tab in frontend (4th mode)
- Sidebar: list all simulation sessions with convergence score bars
- Main area: 3D convergence graph (green=aligned, red=diverged edges)
- Uses existing endpoints: `GET /graph/convergence`, `GET /graph/sessions/{id}/convergence`

### Phase 4: Datadog LLM Observability
- `patch_all(botocore=True)` auto-instruments all Bedrock calls
- `LLMObs.enable(ml_app="parrot", agentless=True)` for LLM Observability
- Decorators: `@agent`, `@workflow`, `@tool` from `ddtrace.llmobs.decorators` on all agents
- Env vars: `DD_API_KEY`, `DD_LLMOBS_ENABLED=1`, `DD_LLMOBS_ML_APP=parrot`
