# AgentMirror Backend

Multi-agent system backend using AWS Bedrock (Claude Sonnet) for workflow learning and replication.

## Quick Start

### 1. Install Dependencies

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your AWS credentials
```

Required variables:
- `AWS_ACCESS_KEY_ID` - Your AWS access key
- `AWS_SECRET_ACCESS_KEY` - Your AWS secret key
- `AWS_REGION` - AWS region (default: us-east-1)
- `BEDROCK_MODEL_ID` - Claude model ID

### 3. Run Server

```bash
ddtrace-run uvicorn main:app --reload --port 8000
```

`ddtrace-run` auto-instruments FastAPI and Bedrock calls for Datadog LLM Observability.

Server will start at `http://localhost:8000`

### 4. Test Bedrock Connection

```bash
curl -X POST http://localhost:8000/test \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello, confirm you are working"}'
```

Expected response:
```json
{
  "success": true,
  "response": "Hello! I'm working correctly...",
  "agent": "test_agent",
  "model": "anthropic.claude-3-5-sonnet-20241022-v2:0"
}
```

## API Endpoints

### Health Check
- `GET /health` - Service health status

### Test Agent
- `POST /test` - Test Bedrock connection

### Observer Agent
- `POST /observe/process` - Process recorded session and extract workflow

### Twin (Coach) Agent
- `POST /coach/guide` - Get step-by-step guidance
- `POST /coach/convergence` - Calculate convergence score

## Architecture

```
backend/
├── main.py              # FastAPI app and endpoints
├── config.py            # Configuration management
├── requirements.txt     # Python dependencies
├── agents/
│   ├── test_agent.py    # Test agent for Bedrock verification
│   ├── observer_agent.py # Watches and extracts workflows
│   └── twin_agent.py    # Guides and coaches new employees
```

## Datadog Integration

### What's instrumented

| Layer | How | What you get |
|-------|-----|--------------|
| FastAPI routes | `ddtrace-run` auto-instrumentation | Request rate, latency (P50/P95/P99), error rate per endpoint |
| AWS Bedrock / boto3 | `ddtrace-run` auto-instrumentation | LLM call latency, error count, invocation traces |
| LLM Observability | `LLMObs.enable()` in app startup | Prompt/completion tracking, model usage, token counts |
| Observer Agent | Custom `@tracer.wrap()` spans | Sessions processed, steps extracted, extraction duration, task type tags |
| Twin Agent | Custom `@tracer.wrap()` spans | Convergence scores, deviation counts, guidance latency |
| Test Agent | Custom `@tracer.wrap()` spans | Bedrock connectivity checks |

### Setup

1. Set `DD_API_KEY` in `.env` with your Datadog API key
2. Start the server with `ddtrace-run`:

```bash
ddtrace-run uvicorn main:app --reload --port 8000
```

3. (Optional) Provision the dashboard:

```bash
pip install datadog-api-client
DD_API_KEY=<key> DD_APP_KEY=<app-key> python ../infra/datadog/dashboard.py
```

Or preview the JSON first:

```bash
python ../infra/datadog/dashboard.py --dry-run
```

### Custom span metrics available

- `observer.action_count` — number of raw actions in a session
- `observer.steps_extracted` — workflow steps extracted per session
- `twin.step_convergence_score` — per-step convergence (0.0–1.0)
- `twin.overall_convergence_score` — full-session convergence (0.0–1.0)
- `twin.deviation_count` — total deviations per convergence analysis
- `twin.high_impact_deviations` — high-impact deviations per analysis

## Next Steps

1. ✅ Test Bedrock connection with `/test` endpoint
2. ✅ Add Datadog instrumentation
3. ⏳ Integrate data layer with Observer/Twin agents
4. ⏳ Test with real workflow data

## Development

View API docs at `http://localhost:8000/docs` (Swagger UI)
