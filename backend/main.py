from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Optional
import boto3
from agents import TestAgent, ObserverAgent, TwinAgent
from config import settings

app = FastAPI(
    title="AgentMirror Backend",
    description="Multi-agent system for workflow learning and replication",
    version="0.1.0"
)

# CORS middleware for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure properly for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Bedrock client
bedrock_client = boto3.client(
    service_name='bedrock-runtime',
    region_name=settings.aws_default_region,
    aws_access_key_id=settings.aws_access_key_id,
    aws_secret_access_key=settings.aws_secret_access_key,
    aws_session_token=settings.aws_session_token
)

# Initialize agents
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

# Request/Response Models
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
    expert_workflow: Dict
    current_step: int
    newbie_action: Optional[Dict] = None

class ConvergenceRequest(BaseModel):
    expert_workflow: Dict
    newbie_actions: List[Dict]

# Health check
@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "agentmirror-backend",
        "version": "0.1.0"
    }

# Test endpoint
@app.post("/test")
async def test_agent_endpoint(request: TestRequest):
    """Test endpoint to confirm Bedrock connection works"""
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

# Observer Agent endpoints
@app.post("/observe/process")
async def process_observation(request: ObserveSessionRequest):
    """
    Process a recorded session with Observer Agent.
    Extracts workflow structure, steps, and reasoning.
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
        
        # TODO: Store to Neo4j + MongoDB after Sissi sets up data layer
        
        return {
            "success": True,
            "workflow": workflow
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Twin Agent endpoints
@app.post("/coach/guide")
async def get_guidance(request: GuideStepRequest):
    """
    Get coaching guidance from Twin Agent for current step.
    Compares newbie action to expert pattern if provided.
    """
    try:
        guidance = await twin_agent.guide_step(
            expert_workflow=request.expert_workflow,
            current_step=request.current_step,
            newbie_action=request.newbie_action
        )
        
        return {
            "success": True,
            "guidance": guidance
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/coach/convergence")
async def calculate_convergence(request: ConvergenceRequest):
    """
    Calculate convergence score between newbie and expert workflow.
    Provides detailed analysis of match quality.
    """
    try:
        analysis = await twin_agent.calculate_convergence(
            expert_workflow=request.expert_workflow,
            newbie_actions=request.newbie_actions
        )
        
        return {
            "success": True,
            "analysis": analysis
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
