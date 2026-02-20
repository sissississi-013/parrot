"""
Action detector: uses Claude vision (multimodal) to interpret screenshots + events
into structured actions that the ObserverAgent can process.

This is the "eyes" of the system — it looks at what's on screen and understands
what the user is doing and WHY.
"""

import json
import logging
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class ActionDetector:
    """
    Uses Claude multimodal (via Bedrock) to analyze screenshots + input events
    and produce structured action descriptions.

    Each analysis produces:
    {
        "action_type": "click|type|navigate|scroll|select|submit|...",
        "target": "what UI element was interacted with",
        "value": "what was typed/selected",
        "application": "what app/website is shown",
        "description": "natural language description",
        "intent": "inferred reason for this action",
        "timestamp": 1234567890.0
    }
    """

    def __init__(self, bedrock_client, model_id: str):
        self.bedrock = bedrock_client
        self.model_id = model_id
        self._context_buffer: List[str] = []  # Rolling context of recent actions

    def analyze_frame(
        self,
        screenshot_b64: str,
        events: List[Dict],
        context: str = "",
    ) -> Dict:
        """
        Analyze a single screenshot + recent events.

        Args:
            screenshot_b64: Base64-encoded JPEG screenshot
            events: Recent mouse/keyboard events since last analysis
            context: Previous action descriptions for continuity

        Returns:
            Structured action dict
        """
        # Build event summary
        event_summary = self._summarize_events(events)

        # Build rolling context from recent detections
        if context:
            rolling_context = context
        elif self._context_buffer:
            rolling_context = " -> ".join(self._context_buffer[-5:])
        else:
            rolling_context = "Session just started"

        prompt = f"""You are analyzing a screenshot of a user's screen along with their recent input events.
Your job is to determine exactly what action the user just performed and WHY.

Recent Input Events:
{event_summary}

Previous Actions (context):
{rolling_context}

Analyze the screenshot and events. Respond with ONLY a JSON object:
{{
  "action_type": "click|type|navigate|scroll|select|submit|read|review|switch_app|other",
  "target": "specific UI element interacted with (e.g., 'Save button', 'search bar', 'PR #123 title')",
  "value": "what was typed, selected, or the URL navigated to (empty string if N/A)",
  "application": "the application or website visible (e.g., 'GitHub', 'VS Code', 'Terminal')",
  "description": "concise description of what the user did (e.g., 'Clicked the merge button on PR #45')",
  "intent": "inferred reason WHY this action was taken (e.g., 'Approving the code changes after review')"
}}"""

        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 500,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": screenshot_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": prompt,
                        },
                    ],
                }
            ],
        }

        try:
            response = self.bedrock.invoke_model(
                modelId=self.model_id,
                body=json.dumps(request_body),
            )

            response_body = json.loads(response["body"].read())
            result_text = response_body["content"][0]["text"]
            action = self._extract_json(result_text)

            # Add timestamp
            action["timestamp"] = time.time()
            action["event_count"] = len(events)

            # Update rolling context
            self._context_buffer.append(action.get("description", ""))
            if len(self._context_buffer) > 20:
                self._context_buffer = self._context_buffer[-10:]

            logger.info(f"Detected action: {action.get('description', 'unknown')}")
            return action

        except Exception as e:
            logger.error(f"Action detection failed: {e}")
            return {
                "action_type": "unknown",
                "target": "",
                "value": "",
                "application": "",
                "description": f"Detection failed: {str(e)}",
                "intent": "",
                "timestamp": time.time(),
                "error": str(e),
            }

    def analyze_batch(
        self,
        frames: List[Dict],
    ) -> List[Dict]:
        """
        Analyze multiple frames in sequence.
        Each frame: {"screenshot_b64": str, "events": List[Dict]}

        Returns list of detected actions.
        """
        actions = []
        for i, frame in enumerate(frames):
            context = " -> ".join(
                a.get("description", "") for a in actions[-5:]
            ) if actions else ""

            action = self.analyze_frame(
                screenshot_b64=frame["screenshot_b64"],
                events=frame.get("events", []),
                context=context,
            )
            action["frame_index"] = i
            actions.append(action)

        return actions

    def generate_workflow_summary(
        self,
        actions: List[Dict],
        task_type: str = "general",
    ) -> str:
        """
        Generate a natural language summary of detected actions
        suitable for ObserverAgent input.
        """
        action_descriptions = []
        for i, action in enumerate(actions):
            desc = (
                f"{i+1}. [{action.get('action_type', 'unknown')}] "
                f"{action.get('description', 'N/A')} "
                f"(App: {action.get('application', '?')}, "
                f"Intent: {action.get('intent', '?')})"
            )
            action_descriptions.append(desc)

        return "\n".join(action_descriptions)

    def _summarize_events(self, events: List[Dict]) -> str:
        """Summarize raw events into a readable format."""
        if not events:
            return "No input events detected"

        clicks = [e for e in events if e["type"] == "click"]
        keystrokes = [e for e in events if e["type"] == "keystroke"]

        parts = []
        if clicks:
            for c in clicks[-5:]:  # Last 5 clicks
                parts.append(f"- Click at ({c['x']}, {c['y']}) [{c.get('button', 'left')}]")

        if keystrokes:
            # Group consecutive keystrokes into typed text
            typed = "".join(
                k["key"] for k in keystrokes
                if len(k.get("key", "")) == 1
            )
            special = [
                k["key"] for k in keystrokes
                if len(k.get("key", "")) > 1
            ]
            if typed:
                parts.append(f"- Typed: \"{typed}\"")
            if special:
                parts.append(f"- Special keys: {', '.join(special[-5:])}")

        return "\n".join(parts) if parts else "Minor activity (no significant events)"

    def _extract_json(self, text: str) -> Dict:
        """Extract JSON from Claude's response."""
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        return json.loads(text.strip())
