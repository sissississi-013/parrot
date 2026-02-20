"""
Simulator Agent: replays learned workflows by controlling a real browser via Playwright.

Takes a workflow from Neo4j and executes each step in a visible Chromium browser.
Claude interprets each step and generates browser commands (navigate, click, type).
Screenshots are captured after each action for streaming to the frontend.
"""

import asyncio
import base64
import json
import logging
import time
from typing import Dict, List, Optional, Callable
from uuid import uuid4

logger = logging.getLogger(__name__)


class SimulationSession:
    """Holds state for a running simulation."""

    def __init__(self, session_id: str, workflow: Dict):
        self.session_id = session_id
        self.workflow = workflow
        self.status = "starting"  # starting, running, paused, completed, failed
        self.current_step = 0
        self.total_steps = len(workflow.get("steps", []))
        self.action_log: List[Dict] = []
        self.screenshots: List[Dict] = []  # {step, timestamp, image_b64}
        self.browser = None
        self.page = None
        self.playwright = None


class SimulatorAgent:
    """
    Replays workflows in a real browser using Playwright.

    Flow:
    1. Launch visible Chromium browser
    2. For each workflow step:
       a. Send step info + current screenshot to Claude
       b. Claude returns browser actions
       c. Execute via Playwright
       d. Capture screenshot
    3. Stream results to frontend via callback
    """

    def __init__(self, bedrock_client, model_id: str):
        self.bedrock = bedrock_client
        self.model_id = model_id
        self._sessions: Dict[str, SimulationSession] = {}

    async def start_simulation(
        self,
        workflow: Dict,
        on_action: Optional[Callable] = None,
        on_screenshot: Optional[Callable] = None,
        start_url: str = "https://www.google.com",
    ) -> SimulationSession:
        """Launch browser and begin simulating a workflow."""
        from playwright.async_api import async_playwright

        session_id = str(uuid4())
        session = SimulationSession(session_id, workflow)
        self._sessions[session_id] = session

        try:
            session.playwright = await async_playwright().start()
            session.browser = await session.playwright.chromium.launch(
                headless=False,
                args=["--window-size=1280,800", "--disable-blink-features=AutomationControlled"],
            )
            context = await session.browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            )
            session.page = await context.new_page()
            await session.page.goto(start_url, wait_until="domcontentloaded")
            session.status = "running"

            # Capture initial screenshot
            initial_screenshot = await self._take_screenshot(session)
            session.screenshots.append({
                "step": 0,
                "type": "initial",
                "timestamp": time.time(),
                "image_b64": initial_screenshot,
            })

            logger.info(f"Simulation {session_id} started: {workflow.get('workflow_name', '?')}")

            # Run the simulation loop in the background
            asyncio.create_task(
                self._simulation_loop(session, on_action, on_screenshot)
            )

            return session

        except Exception as e:
            session.status = "failed"
            logger.error(f"Failed to start simulation: {e}")
            raise

    async def stop_simulation(self, session_id: str) -> Optional[SimulationSession]:
        """Stop a running simulation and close the browser."""
        session = self._sessions.get(session_id)
        if not session:
            return None

        session.status = "completed"
        try:
            if session.browser:
                await session.browser.close()
            if session.playwright:
                await session.playwright.stop()
        except Exception as e:
            logger.warning(f"Error closing browser: {e}")

        return session

    def get_session(self, session_id: str) -> Optional[SimulationSession]:
        return self._sessions.get(session_id)

    async def _simulation_loop(
        self,
        session: SimulationSession,
        on_action: Optional[Callable],
        on_screenshot: Optional[Callable],
    ):
        """Main loop: iterate through workflow steps and execute each one."""
        steps = session.workflow.get("steps", [])

        for i, step in enumerate(steps):
            if session.status != "running":
                break

            session.current_step = i + 1
            step_name = step.get("step_name", f"Step {i + 1}")
            logger.info(f"Simulating step {i + 1}/{len(steps)}: {step_name}")

            try:
                # Get current page state
                current_url = session.page.url
                screenshot_b64 = await self._take_screenshot(session)

                # Ask Claude what browser actions to perform
                actions = await self._plan_actions(step, current_url, screenshot_b64)

                # Execute each action
                for action in actions:
                    result = await self._execute_action(session, action)

                    # Brief pause for page to settle
                    await asyncio.sleep(1)

                    # Capture screenshot after action
                    post_screenshot = await self._take_screenshot(session)

                    action_entry = {
                        "step_number": i + 1,
                        "step_name": step_name,
                        "action": action,
                        "result": result,
                        "timestamp": time.time(),
                        "screenshot_b64": post_screenshot,
                        "expert_reasoning": step.get("reasoning", ""),
                    }

                    session.action_log.append(action_entry)
                    session.screenshots.append({
                        "step": i + 1,
                        "type": "post_action",
                        "timestamp": time.time(),
                        "image_b64": post_screenshot,
                    })

                    # Fire callbacks
                    if on_action:
                        try:
                            await on_action(action_entry)
                        except Exception:
                            pass
                    if on_screenshot:
                        try:
                            await on_screenshot(post_screenshot, action_entry)
                        except Exception:
                            pass

                # Pause between steps
                await asyncio.sleep(2)

            except Exception as e:
                logger.error(f"Step {i + 1} failed: {e}")
                session.action_log.append({
                    "step_number": i + 1,
                    "step_name": step_name,
                    "action": {"type": "error"},
                    "result": {"error": str(e)},
                    "timestamp": time.time(),
                })

        session.status = "completed"
        logger.info(f"Simulation {session.session_id} completed: {len(session.action_log)} actions")

    async def _plan_actions(
        self, step: Dict, current_url: str, screenshot_b64: str
    ) -> List[Dict]:
        """Ask Claude to plan browser actions for a workflow step."""
        prompt = f"""You are a browser automation agent replaying an expert's workflow step.

Current browser URL: {current_url}

Workflow Step:
- Name: {step.get('step_name', 'Unknown')}
- Context: {step.get('context', 'No context')}
- Reasoning: {step.get('reasoning', 'No reasoning provided')}
- Actions described: {json.dumps(step.get('actions', []))}

Based on the screenshot and step description, determine the browser actions to perform.
Respond with ONLY a JSON array of actions. Available action types:

- {{"type": "navigate", "url": "https://..."}}
- {{"type": "click", "selector": "CSS selector or text content"}}
- {{"type": "type", "selector": "CSS selector", "text": "text to type"}}
- {{"type": "scroll", "direction": "down|up", "amount": 300}}
- {{"type": "wait", "seconds": 2}}
- {{"type": "screenshot"}}

If the step is abstract (like "review code"), simulate it with reasonable browser actions (navigate to a relevant page, scroll through content, etc.).

Respond with ONLY the JSON array, no explanation."""

        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1000,
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
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.bedrock.invoke_model(
                    modelId=self.model_id,
                    body=json.dumps(request_body),
                ),
            )

            response_body = json.loads(response["body"].read())
            text = response_body["content"][0]["text"].strip()
            return self._extract_json_array(text)

        except Exception as e:
            logger.error(f"Action planning failed: {e}")
            return [{"type": "wait", "seconds": 2}]

    async def _execute_action(self, session: SimulationSession, action: Dict) -> Dict:
        """Execute a single browser action via Playwright."""
        page = session.page
        action_type = action.get("type", "")

        try:
            if action_type == "navigate":
                url = action.get("url", "")
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                return {"status": "ok", "url": page.url}

            elif action_type == "click":
                selector = action.get("selector", "")
                try:
                    # Try CSS selector first
                    await page.click(selector, timeout=5000)
                except Exception:
                    # Fall back to text-based click
                    try:
                        await page.get_by_text(selector, exact=False).first.click(timeout=5000)
                    except Exception:
                        return {"status": "failed", "error": f"Could not find: {selector}"}
                return {"status": "ok", "clicked": selector}

            elif action_type == "type":
                selector = action.get("selector", "")
                text = action.get("text", "")
                try:
                    await page.fill(selector, text, timeout=5000)
                except Exception:
                    try:
                        await page.get_by_role("textbox").first.fill(text, timeout=5000)
                    except Exception:
                        await page.keyboard.type(text, delay=50)
                return {"status": "ok", "typed": text}

            elif action_type == "scroll":
                direction = action.get("direction", "down")
                amount = action.get("amount", 300)
                delta = amount if direction == "down" else -amount
                await page.mouse.wheel(0, delta)
                return {"status": "ok", "scrolled": direction}

            elif action_type == "wait":
                seconds = action.get("seconds", 2)
                await asyncio.sleep(seconds)
                return {"status": "ok", "waited": seconds}

            elif action_type == "screenshot":
                return {"status": "ok", "type": "screenshot_only"}

            else:
                return {"status": "skipped", "reason": f"Unknown action type: {action_type}"}

        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def _take_screenshot(self, session: SimulationSession) -> str:
        """Capture current browser page as base64 JPEG."""
        try:
            png_bytes = await session.page.screenshot(type="jpeg", quality=70)
            return base64.b64encode(png_bytes).decode()
        except Exception as e:
            logger.warning(f"Screenshot failed: {e}")
            return ""

    def _extract_json_array(self, text: str) -> List[Dict]:
        """Extract JSON array from Claude's response."""
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        result = json.loads(text)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return [result]
        return []
