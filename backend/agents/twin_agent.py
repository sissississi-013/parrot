import boto3
import json
from typing import Dict, Optional, List

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
    
    async def guide_step(
        self,
        expert_workflow: Dict,
        current_step: int,
        newbie_action: Optional[Dict] = None
    ) -> Dict:
        """
        Guide newbie through a workflow step.
        
        Args:
            expert_workflow: The expert's workflow structure
            current_step: Current step number (0-indexed)
            newbie_action: Optional action taken by newbie (for comparison)
        
        Returns:
            Guidance dictionary with expert action, reasoning, convergence score
        """
        try:
            # Get the current step from expert workflow
            if current_step >= len(expert_workflow.get('steps', [])):
                return {
                    "status": "completed",
                    "message": "Workflow completed!"
                }
            
            step = expert_workflow['steps'][current_step]
            
            # Build prompt
            prompt = f"""You are coaching a new employee through a workflow.

Expert Workflow: {expert_workflow.get('workflow_name')}
Current Step: {current_step + 1} of {len(expert_workflow['steps'])}

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
            
            # Parse JSON
            guidance = self._extract_json(guidance_text)
            guidance['step_number'] = current_step
            
            return guidance
            
        except Exception as e:
            raise Exception(f"Twin agent guidance failed: {str(e)}")
    
    async def calculate_convergence(
        self,
        expert_workflow: Dict,
        newbie_actions: List[Dict]
    ) -> Dict:
        """
        Calculate overall convergence score for a session.
        
        Args:
            expert_workflow: Expert's workflow structure
            newbie_actions: List of actions taken by newbie
        
        Returns:
            Convergence analysis with score and breakdown
        """
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
        
        response = self.bedrock.invoke_model(
            modelId=self.model_id,
            body=json.dumps(request_body)
        )
        
        response_body = json.loads(response['body'].read())
        analysis_text = response_body['content'][0]['text']
        
        return self._extract_json(analysis_text)
    
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
