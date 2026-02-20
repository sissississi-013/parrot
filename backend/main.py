import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

import boto3
from ddtrace import tracer
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Dict, Optional

from parrot_agents import TestAgent, ObserverAgent, TwinAgent, SimulatorAgent
from db import Neo4jClient
from capture import ScreenRecorder, ActionDetector, BrowserCapture
from config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _init_datadog():
    if not settings.datadog_enabled:
        logger.info("Datadog API key not set — skipping LLM Observability init")
        return

    from ddtrace.llmobs import LLMObs

    LLMObs.enable(
        ml_app=settings.dd_llmobs_ml_app,
        api_key=settings.dd_api_key,
        site=settings.dd_site,
        agentless_enabled=settings.dd_llmobs_agentless_enabled,
        env=settings.dd_env,
        service=settings.dd_service,
    )
    logger.info("Datadog LLM Observability enabled for app=%s", settings.dd_llmobs_ml_app)


def _shutdown_datadog():
    if not settings.datadog_enabled:
        return
    try:
        from ddtrace.llmobs import LLMObs
        LLMObs.disable()
    except Exception:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_datadog()
    yield
    _shutdown_datadog()


app = FastAPI(
    title="Parrot Backend",
    description="Multi-agent system for workflow learning and replication",
    version=settings.dd_version,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000

    span = tracer.current_span()
    if span:
        span.set_tag("http.route", request.url.path)
        span.set_metric("request.duration_ms", duration_ms)

    return response


# ── Initialize services ─────────────────────────────────────────

bedrock_client = boto3.client(
    service_name='bedrock-runtime',
    region_name=settings.aws_default_region,
    aws_access_key_id=settings.aws_access_key_id,
    aws_secret_access_key=settings.aws_secret_access_key,
    aws_session_token=settings.aws_session_token
)

test_agent = TestAgent(
    region=settings.aws_default_region,
    model_id=settings.bedrock_model_id,
    aws_access_key_id=settings.aws_access_key_id,
    aws_secret_access_key=settings.aws_secret_access_key,
    aws_session_token=settings.aws_session_token
)

observer_agent = ObserverAgent(
    bedrock_client=bedrock_client,
    model_id=settings.bedrock_model_id
)

twin_agent = TwinAgent(
    bedrock_client=bedrock_client,
    model_id=settings.bedrock_model_id
)

simulator_agent = SimulatorAgent(
    bedrock_client=bedrock_client,
    model_id=settings.bedrock_model_id
)

# Initialize Screen Capture system
screen_recorder = ScreenRecorder(capture_interval=3.0)
action_detector = ActionDetector(
    bedrock_client=bedrock_client,
    model_id=settings.bedrock_model_id
)
browser_capture = BrowserCapture(screenshot_interval=1.5)

# Initialize Neo4j (if configured)
neo4j_client: Optional[Neo4jClient] = None
if settings.neo4j_uri and settings.neo4j_user and settings.neo4j_password:
    try:
        neo4j_client = Neo4jClient(
            uri=settings.neo4j_uri,
            user=settings.neo4j_user,
            password=settings.neo4j_password
        )
        if neo4j_client.verify_connection():
            neo4j_client.setup_indexes()
            logger.info("Neo4j connected and indexes ready")
        else:
            logger.warning("Neo4j connection failed, running without graph storage")
            neo4j_client = None
    except Exception as e:
        logger.warning(f"Neo4j init failed: {e}, running without graph storage")
        neo4j_client = None
else:
    logger.info("Neo4j not configured, running without graph storage")

# ── Request/Response Models ──────────────────────────────────────

class TestRequest(BaseModel):
    message: str

class ObserveSessionRequest(BaseModel):
    session_id: str
    user_id: str
    role: str = "expert"
    task_type: str
    actions: List[Dict]

class GuideStepRequest(BaseModel):
    workflow_id: str
    expert_workflow: Optional[Dict] = None  # Now optional — can fetch from Neo4j
    current_step: int
    newbie_action: Optional[Dict] = None

class ConvergenceRequest(BaseModel):
    workflow_id: Optional[str] = None
    session_id: Optional[str] = None
    expert_workflow: Optional[Dict] = None  # Now optional — can fetch from Neo4j
    newbie_actions: List[Dict]

class SessionRequest(BaseModel):
    newbie_id: str
    workflow_id: str

class SearchRequest(BaseModel):
    query: str

class NewbieActionRequest(BaseModel):
    session_id: str
    action: Dict
    step_number: int

class CaptureStartRequest(BaseModel):
    user_id: str
    task_type: str = "general"
    capture_interval: float = 3.0

class BrowserCaptureStartRequest(BaseModel):
    user_id: str
    task_type: str = "general"
    start_url: str = "https://www.google.com"

class CaptureFrameRequest(BaseModel):
    session_id: str
    screenshot_b64: str
    events: List[Dict] = []

class SimulateRequest(BaseModel):
    workflow_id: str
    expert_workflow: Optional[Dict] = None
    start_url: str = "https://www.google.com"

# ── Frontend (static files) ──────────────────────────────────────

STATIC_DIR = Path(__file__).parent / "static"

@app.get("/")
async def serve_frontend():
    return FileResponse(STATIC_DIR / "index.html")

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ── Health check ─────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": settings.dd_service,
        "version": settings.dd_version,
        "datadog_enabled": settings.datadog_enabled,
        "neo4j": "connected" if neo4j_client else "not configured",
    }

# ── Test endpoint ────────────────────────────────────────────────

@app.post("/test")
async def test_agent_endpoint(request: TestRequest):
    try:
        response = await test_agent.test_call(request.message)
        return {
            "success": True,
            "response": response,
            "agent": "test_agent",
            "model": settings.bedrock_model_id
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── Observer Agent endpoints ─────────────────────────────────────

@app.post("/observe/process")
async def process_observation(request: ObserveSessionRequest):
    """
    Process a recorded session with Observer Agent.
    Extracts workflow structure, stores to Neo4j graph.
    """
    try:
        session_metadata = {
            "session_id": request.session_id,
            "user_id": request.user_id,
            "role": request.role,
            "task_type": request.task_type
        }

        workflow = await observer_agent.process_session(
            actions=request.actions,
            session_metadata=session_metadata
        )

        # Store to Neo4j knowledge graph
        stored_id = None
        if neo4j_client:
            workflow["task_type"] = request.task_type
            stored_id = neo4j_client.store_workflow(
                workflow_data=workflow,
                expert_id=request.user_id
            )
            logger.info(f"Workflow stored in Neo4j: {stored_id}")

        return {
            "success": True,
            "workflow": workflow,
            "stored_in_neo4j": stored_id is not None,
            "neo4j_workflow_id": stored_id
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── Twin Agent endpoints ─────────────────────────────────────────

@app.post("/coach/guide")
async def get_guidance(request: GuideStepRequest):
    """
    Get coaching guidance from Twin Agent.
    Can fetch workflow from Neo4j if expert_workflow is not provided.
    """
    try:
        expert_workflow = request.expert_workflow

        # If no workflow provided, fetch from Neo4j
        if not expert_workflow and neo4j_client and request.workflow_id:
            expert_workflow = neo4j_client.get_workflow(request.workflow_id)
            if not expert_workflow:
                raise HTTPException(status_code=404, detail=f"Workflow {request.workflow_id} not found in Neo4j")

        if not expert_workflow:
            raise HTTPException(status_code=400, detail="No expert_workflow provided and Neo4j not available")

        guidance = await twin_agent.guide_step(
            expert_workflow=expert_workflow,
            current_step=request.current_step,
            newbie_action=request.newbie_action
        )

        return {
            "success": True,
            "guidance": guidance,
            "workflow_source": "neo4j" if not request.expert_workflow else "request"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/coach/convergence")
async def calculate_convergence(request: ConvergenceRequest):
    """
    Calculate convergence score. Stores result in Neo4j.
    """
    try:
        expert_workflow = request.expert_workflow

        # Fetch from Neo4j if not provided
        if not expert_workflow and neo4j_client and request.workflow_id:
            expert_workflow = neo4j_client.get_workflow(request.workflow_id)

        if not expert_workflow:
            raise HTTPException(status_code=400, detail="No expert_workflow provided and Neo4j not available")

        analysis = await twin_agent.calculate_convergence(
            expert_workflow=expert_workflow,
            newbie_actions=request.newbie_actions
        )

        # Store convergence in Neo4j
        if neo4j_client and request.session_id and request.workflow_id:
            neo4j_client.store_convergence(
                session_id=request.session_id,
                workflow_id=request.workflow_id,
                convergence_data=analysis
            )
            logger.info(f"Convergence stored: {analysis.get('overall_score', 0)}")

        return {
            "success": True,
            "analysis": analysis
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── Neo4j Graph endpoints ────────────────────────────────────────

@app.get("/graph/workflows")
async def list_workflows(expert_id: Optional[str] = None, task_type: Optional[str] = None):
    """List all workflows stored in Neo4j."""
    if not neo4j_client:
        raise HTTPException(status_code=503, detail="Neo4j not configured")
    return {"workflows": neo4j_client.list_workflows(expert_id, task_type)}

@app.get("/graph/workflows/{workflow_id}")
async def get_workflow(workflow_id: str):
    """Get full workflow details from Neo4j."""
    if not neo4j_client:
        raise HTTPException(status_code=503, detail="Neo4j not configured")
    workflow = neo4j_client.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflow

@app.post("/graph/search")
async def search_workflows(request: SearchRequest):
    """Search workflows by name, description, or task type."""
    if not neo4j_client:
        raise HTTPException(status_code=503, detail="Neo4j not configured")
    return {"results": neo4j_client.search_workflows(request.query)}

@app.get("/graph/workflows/{workflow_id}/visualize")
async def visualize_workflow(workflow_id: str):
    """Get graph visualization data (nodes + edges) for a workflow."""
    if not neo4j_client:
        raise HTTPException(status_code=503, detail="Neo4j not configured")
    return neo4j_client.get_workflow_graph(workflow_id)

@app.get("/graph/workflows/{workflow_id}/reasoning")
async def get_reasoning_chain(workflow_id: str):
    """Get the full reasoning chain — WHY behind every step."""
    if not neo4j_client:
        raise HTTPException(status_code=503, detail="Neo4j not configured")
    return {"reasoning_chain": neo4j_client.get_reasoning_chain(workflow_id)}

@app.get("/graph/sessions/{session_id}/convergence")
async def get_session_convergence(session_id: str):
    """Get convergence visualization for a newbie session."""
    if not neo4j_client:
        raise HTTPException(status_code=503, detail="Neo4j not configured")
    return neo4j_client.get_convergence_graph(session_id)

@app.get("/graph/convergence")
async def get_convergence_scores(newbie_id: Optional[str] = None):
    """Get all convergence scores over time (for dashboards)."""
    if not neo4j_client:
        raise HTTPException(status_code=503, detail="Neo4j not configured")
    return {"scores": neo4j_client.get_all_convergence_scores(newbie_id)}

# ── Session management ───────────────────────────────────────────

@app.post("/sessions")
async def create_session(request: SessionRequest):
    """Start a new onboarding session for a newbie."""
    if not neo4j_client:
        raise HTTPException(status_code=503, detail="Neo4j not configured")
    session_id = neo4j_client.create_session(request.newbie_id, request.workflow_id)
    return {"session_id": session_id}

@app.post("/sessions/action")
async def log_action(request: NewbieActionRequest):
    """Log a newbie action during a session."""
    if not neo4j_client:
        raise HTTPException(status_code=503, detail="Neo4j not configured")
    action_id = neo4j_client.log_newbie_action(
        request.session_id, request.action, request.step_number
    )
    return {"action_id": action_id}

# ── Screen Capture (Watch & Learn) ──────────────────────────────

@app.post("/capture/start")
async def start_capture(request: CaptureStartRequest):
    """
    Start watching the expert's screen.
    Begins capturing screenshots + mouse/keyboard events in real-time.
    """
    screen_recorder.capture_interval = request.capture_interval
    session = screen_recorder.start_session(
        user_id=request.user_id,
        task_type=request.task_type,
    )
    return {
        "success": True,
        "session_id": session.session_id,
        "message": "Screen capture started. The agent is now watching your screen.",
        "capture_interval": request.capture_interval,
    }


@app.post("/capture/stop/{session_id}")
async def stop_capture(session_id: str, auto_process: bool = True):
    """
    Stop watching and process the captured session into a workflow.
    If auto_process=True, sends detected actions through ObserverAgent → Neo4j.
    """
    session = screen_recorder.stop_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Capture session not found")

    result = {
        "success": True,
        "session_id": session_id,
        "screenshots_captured": len(session.screenshots),
        "events_captured": len(session.events),
        "actions_detected": len(session.detected_actions),
        "started_at": session.started_at,
        "stopped_at": session.stopped_at,
    }

    # Auto-process: pipe detected actions through ObserverAgent → Neo4j
    if auto_process and session.detected_actions:
        try:
            session_metadata = {
                "session_id": session_id,
                "user_id": session.user_id,
                "role": "expert",
                "task_type": session.task_type,
            }

            workflow = await observer_agent.process_session(
                actions=session.detected_actions,
                session_metadata=session_metadata,
            )

            stored_id = None
            if neo4j_client:
                workflow["task_type"] = session.task_type
                stored_id = neo4j_client.store_workflow(
                    workflow_data=workflow,
                    expert_id=session.user_id,
                )
                logger.info(f"Captured workflow stored in Neo4j: {stored_id}")

            result["workflow"] = workflow
            result["stored_in_neo4j"] = stored_id is not None
            result["neo4j_workflow_id"] = stored_id
        except Exception as e:
            logger.error(f"Auto-process failed: {e}")
            result["auto_process_error"] = str(e)

    return result


@app.post("/capture/analyze/{session_id}")
async def analyze_capture(session_id: str):
    """
    Analyze the latest screenshot from an active capture session.
    Uses Claude vision to detect what the user just did.
    """
    session = screen_recorder.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Capture session not found")

    screenshot = screen_recorder.get_latest_screenshot(session_id)
    if not screenshot:
        raise HTTPException(status_code=400, detail="No screenshots captured yet")

    # Get events since the previous analysis
    last_action_ts = (
        session.detected_actions[-1]["timestamp"]
        if session.detected_actions
        else 0
    )
    recent_events = screen_recorder.get_recent_events(session_id, last_action_ts)

    # Run vision analysis in thread pool (Bedrock call is blocking)
    loop = asyncio.get_event_loop()
    action = await loop.run_in_executor(
        None,
        action_detector.analyze_frame,
        screenshot["image_base64"],
        recent_events,
        "",
    )

    screen_recorder.add_detected_action(session_id, action)

    return {
        "success": True,
        "action": action,
        "total_detected": len(session.detected_actions),
    }


@app.post("/capture/frame")
async def submit_frame(request: CaptureFrameRequest):
    """
    Submit a single frame for analysis (for browser-based capture).
    Use this when screen capture runs externally (e.g., browser extension).
    """
    session = screen_recorder.get_session(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Capture session not found")

    loop = asyncio.get_event_loop()
    action = await loop.run_in_executor(
        None,
        action_detector.analyze_frame,
        request.screenshot_b64,
        request.events,
        "",
    )

    screen_recorder.add_detected_action(request.session_id, action)

    return {
        "success": True,
        "action": action,
        "total_detected": len(session.detected_actions),
    }


@app.get("/capture/status/{session_id}")
async def capture_status(session_id: str):
    """Get the current status of a capture session."""
    session = screen_recorder.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Capture session not found")

    return {
        "session_id": session_id,
        "status": session.status,
        "user_id": session.user_id,
        "task_type": session.task_type,
        "started_at": session.started_at,
        "stopped_at": session.stopped_at,
        "screenshots_captured": len(session.screenshots),
        "events_captured": len(session.events),
        "actions_detected": len(session.detected_actions),
        "detected_actions": session.detected_actions,
    }


@app.get("/capture/sessions")
async def list_capture_sessions():
    """List all capture sessions (active and stopped)."""
    sessions = []
    for sid, session in screen_recorder._sessions.items():
        sessions.append({
            "session_id": sid,
            "status": session.status,
            "user_id": session.user_id,
            "task_type": session.task_type,
            "started_at": session.started_at,
            "actions_detected": len(session.detected_actions),
        })
    return {"sessions": sessions}


# ── WebSocket: Real-time Capture Stream ──────────────────────────

@app.websocket("/ws/capture/{session_id}")
async def capture_websocket(websocket: WebSocket, session_id: str):
    """
    Real-time WebSocket for capture streaming.

    The server periodically analyzes the latest screenshot and sends
    detected actions to the client as they happen.

    Client can also send {"command": "analyze"} to trigger immediate analysis.
    """
    await websocket.accept()

    session = screen_recorder.get_session(session_id)
    if not session:
        await websocket.send_json({"error": "Session not found"})
        await websocket.close()
        return

    await websocket.send_json({
        "type": "connected",
        "session_id": session_id,
        "message": "Connected to capture stream",
    })

    last_action_count = 0

    try:
        while session.status == "recording":
            # Check for new detected actions
            current_count = len(session.detected_actions)
            if current_count > last_action_count:
                new_actions = session.detected_actions[last_action_count:]
                for action in new_actions:
                    await websocket.send_json({
                        "type": "action_detected",
                        "action": action,
                        "total": current_count,
                    })
                last_action_count = current_count

            # Check for client commands (non-blocking)
            try:
                data = await asyncio.wait_for(
                    websocket.receive_json(), timeout=1.0
                )
                if data.get("command") == "analyze":
                    # Trigger immediate analysis
                    screenshot = screen_recorder.get_latest_screenshot(session_id)
                    if screenshot:
                        last_ts = (
                            session.detected_actions[-1]["timestamp"]
                            if session.detected_actions
                            else 0
                        )
                        events = screen_recorder.get_recent_events(session_id, last_ts)

                        loop = asyncio.get_event_loop()
                        action = await loop.run_in_executor(
                            None,
                            action_detector.analyze_frame,
                            screenshot["image_base64"],
                            events,
                            "",
                        )
                        screen_recorder.add_detected_action(session_id, action)

                        await websocket.send_json({
                            "type": "action_detected",
                            "action": action,
                            "total": len(session.detected_actions),
                            "triggered_by": "client",
                        })
                        last_action_count = len(session.detected_actions)

            except asyncio.TimeoutError:
                pass  # No client message, continue loop

        # Session stopped
        await websocket.send_json({
            "type": "session_stopped",
            "total_actions": len(session.detected_actions),
        })

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for session {session_id}")


# ── Simulate: Agent Replays Learned Workflow in Browser ──────────

@app.post("/simulate/start")
async def start_simulation(request: SimulateRequest):
    """
    Launch a real browser and simulate a learned workflow step-by-step.
    Returns session_id. Connect to WS /ws/simulate/{session_id} for live updates.
    """
    expert_workflow = request.expert_workflow

    if not expert_workflow and neo4j_client and request.workflow_id:
        expert_workflow = neo4j_client.get_workflow(request.workflow_id)

    if not expert_workflow:
        raise HTTPException(status_code=400, detail="Workflow not found")

    try:
        session = await simulator_agent.start_simulation(
            workflow=expert_workflow,
            start_url=request.start_url,
        )
        return {
            "success": True,
            "session_id": session.session_id,
            "workflow_name": expert_workflow.get("workflow_name", ""),
            "total_steps": session.total_steps,
            "message": "Browser launched. Simulation running.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/simulate/stop/{session_id}")
async def stop_simulation(session_id: str):
    """Stop a running simulation and close the browser."""
    session = await simulator_agent.stop_simulation(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Simulation session not found")
    return {
        "success": True,
        "session_id": session_id,
        "total_actions": len(session.action_log),
        "status": session.status,
    }


@app.get("/simulate/status/{session_id}")
async def simulation_status(session_id: str):
    """Get current simulation status including action log."""
    session = simulator_agent.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Simulation session not found")
    return {
        "session_id": session_id,
        "status": session.status,
        "current_step": session.current_step,
        "total_steps": session.total_steps,
        "actions_performed": len(session.action_log),
        "action_log": [
            {k: v for k, v in a.items() if k != "screenshot_b64"}
            for a in session.action_log
        ],
    }


@app.get("/simulate/screenshot/{session_id}")
async def simulation_screenshot(session_id: str):
    """Get the latest browser screenshot from a simulation."""
    session = simulator_agent.get_session(session_id)
    if not session or not session.screenshots:
        raise HTTPException(status_code=404, detail="No screenshots available")
    latest = session.screenshots[-1]
    return {
        "image_b64": latest["image_b64"],
        "step": latest.get("step", 0),
        "timestamp": latest.get("timestamp", 0),
    }


@app.post("/simulate/run")
async def simulate_workflow_text(request: SimulateRequest):
    """
    Text-only simulation (no browser). Agent describes what it would do.
    """
    expert_workflow = request.expert_workflow

    if not expert_workflow and neo4j_client and request.workflow_id:
        expert_workflow = neo4j_client.get_workflow(request.workflow_id)

    if not expert_workflow:
        raise HTTPException(status_code=400, detail="Workflow not found")

    simulated_steps = []
    for i, step in enumerate(expert_workflow.get("steps", [])):
        guidance = await twin_agent.guide_step(
            expert_workflow=expert_workflow,
            current_step=i,
        )
        simulated_steps.append({
            "step_number": i + 1,
            "expert_step": step,
            "agent_simulation": guidance,
            "expert_reasoning": step.get("reasoning", ""),
            "agent_reasoning": guidance.get("reasoning", ""),
        })

    return {
        "success": True,
        "workflow_name": expert_workflow.get("workflow_name", ""),
        "total_steps": len(simulated_steps),
        "simulated_steps": simulated_steps,
    }


# ── WebSocket: Real-time Simulation Stream ───────────────────────

@app.websocket("/ws/simulate/{session_id}")
async def simulate_websocket(websocket: WebSocket, session_id: str):
    """
    Real-time WebSocket for simulation streaming.
    Sends screenshots + action log as the agent performs actions in the browser.
    """
    await websocket.accept()

    session = simulator_agent.get_session(session_id)
    if not session:
        await websocket.send_json({"error": "Session not found"})
        await websocket.close()
        return

    await websocket.send_json({
        "type": "connected",
        "session_id": session_id,
        "workflow_name": session.workflow.get("workflow_name", ""),
        "total_steps": session.total_steps,
    })

    last_action_count = 0
    last_screenshot_count = 0

    try:
        while session.status in ("starting", "running"):
            # Send new actions
            current_actions = len(session.action_log)
            if current_actions > last_action_count:
                for entry in session.action_log[last_action_count:]:
                    await websocket.send_json({
                        "type": "action",
                        "step_number": entry.get("step_number"),
                        "step_name": entry.get("step_name"),
                        "action": entry.get("action"),
                        "result": entry.get("result"),
                        "expert_reasoning": entry.get("expert_reasoning", ""),
                        "timestamp": entry.get("timestamp"),
                    })
                last_action_count = current_actions

            # Send new screenshots
            current_screenshots = len(session.screenshots)
            if current_screenshots > last_screenshot_count:
                latest = session.screenshots[-1]
                await websocket.send_json({
                    "type": "screenshot",
                    "step": latest.get("step", 0),
                    "image_b64": latest["image_b64"],
                })
                last_screenshot_count = current_screenshots

            await websocket.send_json({
                "type": "status",
                "status": session.status,
                "current_step": session.current_step,
                "total_steps": session.total_steps,
            })

            await asyncio.sleep(1)

        # Simulation done
        await websocket.send_json({
            "type": "completed",
            "total_actions": len(session.action_log),
            "status": session.status,
        })

    except WebSocketDisconnect:
        logger.info(f"Simulate WebSocket disconnected: {session_id}")


# ── Browser Capture (Watch & Learn v2) ───────────────────────────

@app.post("/browser/start")
async def start_browser_capture(request: BrowserCaptureStartRequest):
    """
    Launch a Playwright browser for the user to work in.
    All DOM events (clicks, typing, navigation) are captured automatically.
    Screenshots stream at ~1.5fps for the live view.
    """
    try:
        session = await browser_capture.start_session(
            user_id=request.user_id,
            task_type=request.task_type,
            start_url=request.start_url,
        )
        return {
            "success": True,
            "session_id": session.session_id,
            "message": "Browser launched — work in it normally. Actions are captured automatically.",
            "start_url": request.start_url,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/browser/stop/{session_id}")
async def stop_browser_capture(session_id: str, auto_process: bool = True):
    """
    Stop browser capture, close browser, process actions into workflow.
    """
    session = await browser_capture.stop_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    result = {
        "success": True,
        "session_id": session_id,
        "actions_captured": len(session.actions),
        "navigations": len(session.navigations),
        "screenshots_captured": len(session.screenshots),
        "started_at": session.started_at,
        "stopped_at": session.stopped_at,
    }

    if auto_process and session.actions:
        try:
            session_metadata = {
                "session_id": session_id,
                "user_id": session.user_id,
                "role": "expert",
                "task_type": session.task_type,
            }
            workflow = await observer_agent.process_session(
                actions=session.actions,
                session_metadata=session_metadata,
            )
            stored_id = None
            if neo4j_client:
                workflow["task_type"] = session.task_type
                stored_id = neo4j_client.store_workflow(
                    workflow_data=workflow,
                    expert_id=session.user_id,
                )
            result["workflow"] = workflow
            result["stored_in_neo4j"] = stored_id is not None
            result["neo4j_workflow_id"] = stored_id
        except Exception as e:
            logger.error(f"Browser auto-process failed: {e}")
            result["auto_process_error"] = str(e)

    return result


@app.get("/browser/status/{session_id}")
async def browser_capture_status(session_id: str):
    """Get browser capture session status + all detected actions."""
    session = browser_capture.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session_id,
        "status": session.status,
        "current_url": session.current_url,
        "actions_captured": len(session.actions),
        "actions": session.actions,
        "navigations": session.navigations,
    }


@app.get("/browser/screenshot/{session_id}")
async def browser_screenshot(session_id: str):
    """Get the latest browser screenshot."""
    session = browser_capture.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.screenshots:
        return {"image_b64": session.screenshots[-1]["image_b64"]}
    b64 = await browser_capture.get_screenshot(session_id)
    if not b64:
        raise HTTPException(status_code=400, detail="No screenshot available")
    return {"image_b64": b64}


@app.websocket("/ws/browser/{session_id}")
async def browser_capture_ws(websocket: WebSocket, session_id: str):
    """
    Real-time WebSocket: streams screenshots + DOM actions as user works in the browser.

    Messages sent to client:
    - {"type":"screenshot","image_b64":"..."}
    - {"type":"action","action":{...}}
    - {"type":"status","actions":N,"url":"..."}
    - {"type":"stopped","total_actions":N}
    """
    await websocket.accept()

    session = browser_capture.get_session(session_id)
    if not session:
        await websocket.send_json({"error": "Session not found"})
        await websocket.close()
        return

    await websocket.send_json({"type": "connected", "session_id": session_id})

    last_action_count = 0
    last_screenshot_count = 0

    try:
        while session.status == "recording":
            # Send new actions
            current_actions = len(session.actions)
            if current_actions > last_action_count:
                for a in session.actions[last_action_count:]:
                    await websocket.send_json({"type": "action", "action": a})
                last_action_count = current_actions

            # Send latest screenshot (at ~1fps to the client)
            current_ss = len(session.screenshots)
            if current_ss > last_screenshot_count:
                latest = session.screenshots[-1]
                await websocket.send_json({
                    "type": "screenshot",
                    "image_b64": latest["image_b64"],
                })
                last_screenshot_count = current_ss

            # Status heartbeat
            await websocket.send_json({
                "type": "status",
                "actions": current_actions,
                "url": session.current_url,
            })

            await asyncio.sleep(1.2)

        await websocket.send_json({
            "type": "stopped",
            "total_actions": len(session.actions),
        })

    except WebSocketDisconnect:
        logger.info(f"Browser capture WS disconnected: {session_id}")


# ── Auto-analyze loop (background task) ─────────────────────────

async def _auto_analyze_loop(session_id: str, interval: float = 5.0):
    """Background task that periodically analyzes screenshots."""
    session = screen_recorder.get_session(session_id)
    if not session:
        return

    while session.status == "recording":
        await asyncio.sleep(interval)

        screenshot = screen_recorder.get_latest_screenshot(session_id)
        if not screenshot:
            continue

        last_ts = (
            session.detected_actions[-1]["timestamp"]
            if session.detected_actions
            else 0
        )
        events = screen_recorder.get_recent_events(session_id, last_ts)

        if not events:
            continue  # No new events, skip analysis

        try:
            loop = asyncio.get_event_loop()
            action = await loop.run_in_executor(
                None,
                action_detector.analyze_frame,
                screenshot["image_base64"],
                events,
                "",
            )
            screen_recorder.add_detected_action(session_id, action)
            logger.info(f"Auto-detected: {action.get('description', '?')}")
        except Exception as e:
            logger.warning(f"Auto-analyze failed: {e}")


@app.post("/capture/start-auto/{session_id}")
async def start_auto_analyze(session_id: str, interval: float = 5.0):
    """
    Start automatic analysis of screenshots for a capture session.
    Runs in the background, analyzing every `interval` seconds when new events exist.
    """
    session = screen_recorder.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Capture session not found")
    if session.status != "recording":
        raise HTTPException(status_code=400, detail="Session is not recording")

    asyncio.create_task(_auto_analyze_loop(session_id, interval))

    return {
        "success": True,
        "message": f"Auto-analysis started (every {interval}s when events detected)",
        "session_id": session_id,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
