# Parrot

A multi-agent system that watches expert employees work, learns their workflows, stores them as a knowledge graph, and replays them via browser automation. Built for onboarding — capture what experts do, then coach newbies to converge on the same patterns.

## Architecture

```
┌──────────────────┐
│    Frontend       │  Single-page app (Capture / Workflows / Simulate)
│   (index.html)    │  Tailwind CSS, vis.js / 3d-force-graph
└────────┬─────────┘
         │ HTTP + WebSocket
         ▼
┌──────────────────────────────────────────────────────────────┐
│                  FastAPI Backend                              │
│                                                              │
│  ┌────────────┐  ┌─────────────┐  ┌───────────┐             │
│  │  Observer   │  │  Simulator  │  │   Twin    │             │
│  │  Agent      │  │  Agent      │  │   Agent   │             │
│  └──────┬─────┘  └──────┬──────┘  └─────┬─────┘             │
│         │               │               │                    │
│         └───────────────┼───────────────┘                    │
│                         ▼                                    │
│              ┌─────────────────────┐                         │
│              │  AWS Bedrock        │                         │
│              │  (Claude 3.5 v2)    │                         │
│              └─────────────────────┘                         │
│                         │                                    │
│              ┌──────────▼──────────┐                         │
│              │  Neo4j Knowledge    │                         │
│              │  Graph              │                         │
│              └─────────────────────┘                         │
│                                                              │
│  Observability: Datadog ddtrace + LLM Observability          │
└──────────────────────────────────────────────────────────────┘
```

## Agents

| Agent | What it does | Key endpoints |
|-------|-------------|---------------|
| **Observer** | Captures browser actions, sends them through Claude to extract structured workflows, stores in Neo4j | `POST /observe/process` |
| **Simulator** | Replays workflows in a real browser using Claude vision + Playwright | `POST /simulate/start`, `WS /ws/simulate/{id}` |
| **Twin** | Coaches newbies step-by-step, calculates convergence scores (expert vs newbie) | `POST /coach/guide`, `POST /coach/convergence` |
| **Test** | Verifies AWS Bedrock connectivity | `POST /test` |

## Data Flow

**Capture:** User browses → Playwright DOM event injection → action stream → Observer Agent → Claude extracts structured workflow → Neo4j

**Replay:** Neo4j workflow → Simulator Agent → Claude vision plans actions → Playwright executes in browser → screenshots streamed to frontend

**Coaching:** Expert workflow + newbie actions → Twin Agent → Claude calculates convergence → alignment/divergence edges stored in Neo4j

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

## Tech Stack

- **Backend:** Python 3.12, FastAPI, Uvicorn
- **LLM:** AWS Bedrock — Claude 3.5 Sonnet v2 (`anthropic.claude-3-5-sonnet-20241022-v2:0`)
- **Graph DB:** Neo4j Aura
- **Browser automation:** Playwright (capture + replay)
- **Frontend:** Single HTML file, Tailwind CSS, vis.js / 3d-force-graph
- **Observability:** Datadog ddtrace, LLM Observability, custom metrics
- **Voice coach:** MiniMax / ElevenLabs / Gemini TTS (optional)

## Setup

### Prerequisites

- Python 3.12+
- Neo4j Aura instance (or local Neo4j)
- AWS credentials with Bedrock access

### Install

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### Configure

Copy the example env and fill in your credentials:

```bash
cp .env.example .env
```

Required variables:

| Variable | Description |
|----------|-------------|
| `AWS_REGION` | AWS region for Bedrock (e.g. `us-east-1`) |
| `AWS_ACCESS_KEY_ID` | AWS access key |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key |
| `BEDROCK_MODEL_ID` | Bedrock model ID |
| `NEO4J_URI` | Neo4j connection URI |
| `NEO4J_USER` | Neo4j username |
| `NEO4J_PASSWORD` | Neo4j password |

Optional: `DD_API_KEY`, `DD_APP_KEY` (Datadog), `MINIMAX_API_KEY` (voice coach), `MONGODB_URI` (document store).

### Run

```bash
# With Datadog instrumentation
ddtrace-run uvicorn main:app --reload --port 8000

# Without Datadog
uvicorn main:app --reload --port 8000
```

Open `http://localhost:8000` for the frontend.

### Verify

```bash
# Health check
curl http://localhost:8000/health

# Test Bedrock connection
curl -X POST http://localhost:8000/test \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello, confirm you are working"}'
```

## API Reference

### Health & Testing
- `GET /health` — Service health + Neo4j status
- `POST /test` — Test Bedrock connectivity

### Browser Capture
- `POST /browser/start` — Launch Playwright browser, begin DOM capture
- `POST /browser/stop/{id}` — Stop capture (auto-processes through Observer → Neo4j)
- `GET /browser/status/{id}` — Session status + captured actions
- `GET /browser/screenshot/{id}` — Latest screenshot
- `WS /ws/browser/{id}` — Real-time screenshot + action stream

### Simulation
- `POST /simulate/start` — Replay a workflow in a real browser
- `POST /simulate/stop/{id}` — Stop simulation
- `GET /simulate/status/{id}` — Status + action log
- `GET /simulate/screenshot/{id}` — Latest screenshot
- `WS /ws/simulate/{id}` — Real-time screenshot + action stream

### Observer
- `POST /observe/process` — Process captured actions → structured workflow → Neo4j

### Twin / Coach
- `POST /coach/guide` — Step-by-step coaching guidance
- `POST /coach/convergence` — Calculate convergence score
- `POST /coach/voice` — Voice coach (TTS)

### Knowledge Graph
- `GET /graph/workflows` — List all workflows
- `GET /graph/workflows/{id}` — Workflow detail
- `GET /graph/workflows/{id}/visualize` — Graph visualization data (nodes + edges)
- `GET /graph/workflows/{id}/reasoning` — Reasoning chain
- `GET /graph/full` — Entire knowledge graph
- `POST /graph/search` — Full-text search
- `GET /graph/sessions/{id}/convergence` — Convergence graph for a session
- `GET /graph/convergence` — All convergence scores

### Sessions
- `POST /sessions` — Create newbie session
- `POST /sessions/action` — Log newbie action

### Datadog
- `GET /datadog/dashboards` — List dashboards
- `POST /datadog/dashboards/{id}/share` — Share a dashboard
- `GET /datadog/shared-dashboards` — List shared dashboards

## Project Structure

```
backend/
├── main.py                     # FastAPI app — all routes
├── config.py                   # Settings from .env
├── metrics.py                  # Datadog custom metrics
├── requirements.txt
├── .env.example
├── parrot_agents/
│   ├── observer_agent.py       # Captures expert workflows
│   ├── simulator_agent.py      # Replays workflows in browser
│   ├── twin_agent.py           # Coaches newbies, convergence scoring
│   └── test_agent.py           # Bedrock connectivity test
├── capture/
│   ├── browser_capture.py      # Playwright DOM event capture
│   ├── screen_recorder.py      # Desktop screenshot fallback
│   └── action_detector.py      # Claude vision analysis fallback
├── db/
│   └── neo4j_client.py         # Neo4j graph operations
└── static/
    └── index.html              # Frontend SPA

infra/
└── datadog/
    └── dashboard.py            # Datadog dashboard provisioning
```

## Datadog Dashboard

Provision a pre-configured dashboard:

```bash
DD_API_KEY=<key> DD_APP_KEY=<app-key> python infra/datadog/dashboard.py
```

Tracks: agent latency, LLM token usage, workflow extraction rates, convergence scores, and simulation success rates.
