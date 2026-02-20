# AgentMirror Backend

Multi-agent system backend using AWS Bedrock (Claude Sonnet) for workflow learning and replication.

## Quick Start

### 1. Install Dependencies

```bash
cd backend
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
uvicorn main:app --reload --port 8000
```

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

## Next Steps

1. ✅ Test Bedrock connection with `/test` endpoint
2. ⏳ Wait for Sissi to set up Neo4j + MongoDB
3. ⏳ Integrate data layer with Observer/Twin agents
4. ⏳ Add Datadog instrumentation
5. ⏳ Test with real workflow data

## Development

View API docs at `http://localhost:8000/docs` (Swagger UI)
