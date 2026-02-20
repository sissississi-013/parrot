import json
import logging
from typing import Dict, Optional, List

from ddtrace import tracer

logger = logging.getLogger("parrot.twin")

try:
    from ddtrace.llmobs.decorators import agent, tool
except ImportError:
    def agent(**kw):
        def _d(f): return f
        return _d
    tool = agent

class TwinAgent:
    """
    Twin (Coach) Agent guides new employees through expert workflows.
    
    Responsibilities:
    - Retrieve expert workflows from knowledge graph
    - Guide new hires step-by-step
    - Explain WHY each action is taken
    - Detect deviations and provide corrections
    - Calculate convergence scores
    """
    
    def __init__(self, bedrock_client, model_id: str):
        self.bedrock = bedrock_client
        self.model_id = model_id
    
    @tracer.wrap(service="parrot", resource="twin.guide_step")
    @agent(name="twin_agent")
    async def guide_step(
        self,
        expert_workflow: Dict,
        current_step: int,
        newbie_action: Optional[Dict] = None,
    ) -> Dict:
        span = tracer.current_span()
        total_steps = len(expert_workflow.get("steps", []))

        if span:
            span.set_tag("twin.workflow_name", expert_workflow.get("workflow_name", ""))
            span.set_metric("twin.current_step", current_step)
            span.set_metric("twin.total_steps", total_steps)
            span.set_tag("twin.has_newbie_action", newbie_action is not None)

        try:
            if current_step >= total_steps:
                return {
                    "status": "completed",
                    "message": "Workflow completed!"
                }
            
            step = expert_workflow['steps'][current_step]
            
            # Build prompt
            prompt = f"""You are coaching a new employee through a workflow.

Expert Workflow: {expert_workflow.get('workflow_name')}
Current Step: {current_step + 1} of {total_steps}

Expert's Step:
{json.dumps(step, indent=2)}

{"Newbie's Action: " + json.dumps(newbie_action, indent=2) if newbie_action else "Newbie hasn't acted yet."}

Provide coaching guidance in JSON format:
{{
  "expert_action": {{
    "step_name": "what to do",
    "actions": ["specific actions"],
    "expected_outcome": "what should happen"
  }},
  "reasoning": "WHY this step is important (explain like teaching)",
  "convergence_score": 0.0-1.0 (only if newbie_action provided, how well they matched),
  "feedback": "positive or corrective feedback (only if newbie_action provided)",
  "next_step_hint": "what comes next"
}}"""

            # Call Bedrock
            request_body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 2000,
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
            guidance_text = response_body['content'][0]['text']
            
            guidance = self._extract_json(guidance_text)
            guidance['step_number'] = current_step

            step_score = guidance.get("convergence_score")
            if span and step_score is not None:
                span.set_metric("twin.step_convergence_score", float(step_score))

            is_deviation = step_score is not None and float(step_score) < 0.5
            if span:
                span.set_tag("twin.deviation_detected", is_deviation)

            logger.info(
                "Guidance generated: step=%d/%d score=%s deviation=%s",
                current_step + 1,
                total_steps,
                step_score,
                is_deviation,
            )

            return guidance

        except Exception as e:
            if span:
                span.set_tag("error", True)
                span.set_tag("error.message", str(e))
            logger.error("Twin guidance failed: %s", e)
            raise Exception(f"Twin agent guidance failed: {str(e)}")
    
    @tracer.wrap(service="parrot", resource="twin.calculate_convergence")
    @tool(name="calculate_convergence")
    async def calculate_convergence(
        self,
        expert_workflow: Dict,
        newbie_actions: List[Dict],
    ) -> Dict:
        span = tracer.current_span()

        if span:
            span.set_tag("twin.workflow_name", expert_workflow.get("workflow_name", ""))
            span.set_metric("twin.newbie_action_count", len(newbie_actions))
            span.set_metric("twin.expert_step_count", len(expert_workflow.get("steps", [])))

        prompt = f"""Analyze how well a new employee followed an expert's workflow.

Expert Workflow:
{json.dumps(expert_workflow, indent=2)}

Newbie's Actions:
{json.dumps(newbie_actions, indent=2)}

Calculate convergence in JSON format:
{{
  "overall_score": 0.0-1.0,
  "step_scores": [
    {{"step": 1, "score": 0.0-1.0, "matched": true/false}}
  ],
  "deviations": [
    {{"step": 1, "issue": "what went wrong", "impact": "low/medium/high"}}
  ],
  "strengths": ["what they did well"],
  "areas_for_improvement": ["what to work on"]
}}"""

        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 3000,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        }
        
        try:
            response = self.bedrock.invoke_model(
                modelId=self.model_id,
                body=json.dumps(request_body),
            )

            response_body = json.loads(response['body'].read())
            analysis_text = response_body['content'][0]['text']
            analysis = self._extract_json(analysis_text)

            overall_score = analysis.get("overall_score")
            deviations = analysis.get("deviations", [])

            if span:
                if overall_score is not None:
                    span.set_metric("twin.overall_convergence_score", float(overall_score))
                span.set_metric("twin.deviation_count", len(deviations))

                high_impact = sum(1 for d in deviations if d.get("impact") == "high")
                span.set_metric("twin.high_impact_deviations", high_impact)

            logger.info(
                "Convergence calculated: score=%s deviations=%d high_impact=%d",
                overall_score,
                len(deviations),
                sum(1 for d in deviations if d.get("impact") == "high"),
            )

            return analysis

        except Exception as e:
            if span:
                span.set_tag("error", True)
                span.set_tag("error.message", str(e))
            logger.error("Twin convergence calculation failed: %s", e)
            raise
    
    def _extract_json(self, text: str) -> Dict:
        """Extract JSON from Claude's response"""
        text = text.strip()
        if text.startswith('```json'):
            text = text[7:]
        if text.startswith('```'):
            text = text[3:]
        if text.endswith('```'):
            text = text[:-3]
        
        return json.loads(text.strip())
