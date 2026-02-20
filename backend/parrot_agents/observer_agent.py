import json
import logging
import re
import uuid
from datetime import datetime
from typing import List, Dict

from ddtrace import tracer

logger = logging.getLogger("parrot.observer")

try:
    from ddtrace.llmobs.decorators import agent, tool
except ImportError:
    def agent(**kw):
        def _d(f): return f
        return _d
    tool = agent


def _sanitize_for_json(obj):
    """Remove control characters from strings in nested data structures."""
    if isinstance(obj, str):
        return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', obj)
    elif isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    return obj


class ObserverAgent:
    """
    Observer Agent watches expert employees and extracts workflow patterns.
    
    Responsibilities:
    - Process action sequences from recorded sessions
    - Extract discrete workflow steps
    - Generate reasoning for each action
    - Structure workflows for storage in Neo4j + MongoDB
    """
    
    def __init__(self, bedrock_client, model_id: str):
        self.bedrock = bedrock_client
        self.model_id = model_id
    
    @tracer.wrap(service="parrot", resource="observer.process_session")
    @agent(name="observer_agent")
    async def process_session(self, actions: List[Dict], session_metadata: Dict) -> Dict:
        span = tracer.current_span()
        task_type = session_metadata.get("task_type", "unknown")

        if span:
            span.set_tag("observer.task_type", task_type)
            span.set_tag("observer.session_id", session_metadata.get("session_id"))
            span.set_tag("observer.user_id", session_metadata.get("user_id"))
            span.set_metric("observer.action_count", len(actions))

        try:
            prompt = f"""You are analyzing an expert employee's workflow session.

Session Context:
- Task Type: {session_metadata.get('task_type', 'unknown')}
- User Role: {session_metadata.get('role', 'expert')}
- Number of Actions: {len(actions)}

Actions Sequence:
{json.dumps(_sanitize_for_json(actions), indent=2)}

Your task:
1. Identify discrete workflow steps (group related actions)
2. For each step, provide:
   - Step name (concise, action-oriented)
   - Actions involved
   - Context (what's happening)
   - Reasoning (WHY this step is taken)
3. Identify the overall workflow pattern

Respond in JSON format:
{{
  "workflow_name": "descriptive name",
  "steps": [
    {{
      "step_number": 1,
      "step_name": "name",
      "actions": ["action_ids"],
      "context": "what's happening",
      "reasoning": "why this is done"
    }}
  ],
  "workflow_pattern": "overall pattern description"
}}"""

            # Call Bedrock
            request_body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 4000,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            }
            
            response = self.bedrock.invoke_model(
                modelId=self.model_id,
                body=json.dumps(request_body)
            )
            
            response_body = json.loads(response['body'].read())
            workflow_text = response_body['content'][0]['text']
            
            # Parse JSON from response
            workflow_data = self._extract_json(workflow_text)
            
            # Add metadata
            workflow_data['workflow_id'] = str(uuid.uuid4())
            workflow_data['session_id'] = session_metadata.get('session_id')
            workflow_data['created_at'] = datetime.utcnow().isoformat()
            workflow_data['expert_user_id'] = session_metadata.get('user_id')
            
            steps_extracted = len(workflow_data.get("steps", []))
            if span:
                span.set_metric("observer.steps_extracted", steps_extracted)
                span.set_tag("observer.workflow_name", workflow_data.get("workflow_name", ""))

            logger.info(
                "Session processed: session_id=%s steps=%d task=%s",
                session_metadata.get("session_id"),
                steps_extracted,
                task_type,
            )

            return workflow_data

        except Exception as e:
            if span:
                span.set_tag("error", True)
                span.set_tag("error.message", str(e))
            logger.error("Observer processing failed: %s", e)
            raise Exception(f"Observer agent processing failed: {str(e)}")
    
    def _extract_json(self, text: str) -> Dict:
        """Extract JSON from Claude's response (handles markdown code blocks + control chars)"""
        import re
        # Remove markdown code blocks if present
        text = text.strip()
        if text.startswith('```json'):
            text = text[7:]
        if text.startswith('```'):
            text = text[3:]
        if text.endswith('```'):
            text = text[:-3]

        # Strip control characters that break JSON parsing
        text = re.sub(r'[\x00-\x1f\x7f]', lambda m: ' ' if m.group() not in '\n\r\t' else m.group(), text)

        return json.loads(text.strip())
    
    @tracer.wrap(service="parrot", resource="observer.generate_reasoning")
    @tool(name="generate_reasoning")
    async def generate_reasoning(self, action: Dict, context: Dict) -> str:
        span = tracer.current_span()
        if span:
            span.set_tag("observer.action_type", action.get("type", "unknown"))

        prompt = f"""Explain why this action was taken in the workflow:

Action: {json.dumps(action, indent=2)}
Context: {json.dumps(context, indent=2)}

Provide a concise explanation (1-2 sentences) of WHY this action makes sense in this workflow."""

        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 200,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        }
        
        response = self.bedrock.invoke_model(
            modelId=self.model_id,
            body=json.dumps(request_body)
        )
        
        response_body = json.loads(response['body'].read())
        return response_body['content'][0]['text'].strip()
