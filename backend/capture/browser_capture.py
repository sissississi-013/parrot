"""
Browser-based capture: launches a Playwright Chromium browser, injects JS event
listeners to capture every click/type/navigate action, and streams screenshots
to the frontend.

This replaces the desktop screen capture approach. The user works inside the
Playwright browser — all their actions are captured precisely from the DOM,
not from noisy vision analysis of the desktop.
"""

import asyncio
import base64
import logging
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

# JS injected into every page to capture DOM events
CAPTURE_SCRIPT = """
(() => {
  if (window.__parrot_initialized) return;
  window.__parrot_initialized = true;
  window.__parrot_actions = [];

  // Capture clicks
  document.addEventListener('click', (e) => {
    const t = e.target;
    const rect = t.getBoundingClientRect();
    window.__parrot_actions.push({
      type: 'click',
      tag: t.tagName.toLowerCase(),
      id: t.id || '',
      className: (t.className && typeof t.className === 'string') ? t.className.slice(0, 100) : '',
      text: (t.textContent || '').trim().slice(0, 120),
      href: t.href || t.closest('a')?.href || '',
      x: Math.round(rect.x + rect.width / 2),
      y: Math.round(rect.y + rect.height / 2),
      url: location.href,
      timestamp: Date.now()
    });
  }, true);

  // Capture form input (debounced)
  let inputTimer = null;
  document.addEventListener('input', (e) => {
    clearTimeout(inputTimer);
    const t = e.target;
    inputTimer = setTimeout(() => {
      window.__parrot_actions.push({
        type: 'type',
        tag: t.tagName.toLowerCase(),
        id: t.id || '',
        name: t.name || '',
        placeholder: t.placeholder || '',
        value: (t.value || '').slice(0, 200),
        url: location.href,
        timestamp: Date.now()
      });
    }, 400);
  }, true);

  // Capture form submissions
  document.addEventListener('submit', (e) => {
    const form = e.target;
    window.__parrot_actions.push({
      type: 'submit',
      tag: 'form',
      id: form.id || '',
      action: form.action || '',
      url: location.href,
      timestamp: Date.now()
    });
  }, true);

  // Capture scroll (throttled)
  let scrollTimer = null;
  window.addEventListener('scroll', () => {
    clearTimeout(scrollTimer);
    scrollTimer = setTimeout(() => {
      window.__parrot_actions.push({
        type: 'scroll',
        scrollY: Math.round(window.scrollY),
        scrollX: Math.round(window.scrollX),
        url: location.href,
        timestamp: Date.now()
      });
    }, 800);
  }, true);
})();
"""


class BrowserSession:
    """Holds state for a browser capture session."""

    def __init__(self, session_id: str, user_id: str, task_type: str):
        self.session_id = session_id
        self.user_id = user_id
        self.task_type = task_type
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.stopped_at: Optional[str] = None
        self.status = "starting"  # starting, recording, stopped

        self.actions: List[Dict] = []         # Captured DOM events
        self.navigations: List[Dict] = []     # Page navigations
        self.screenshots: List[Dict] = []     # {timestamp, image_b64}
        self.current_url: str = ""

        # Playwright handles
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None


class BrowserCapture:
    """
    Launches a Playwright browser, injects JS event listeners,
    captures precise DOM actions + screenshots.
    """

    def __init__(self, screenshot_interval: float = 1.5):
        self.screenshot_interval = screenshot_interval
        self._sessions: Dict[str, BrowserSession] = {}

    async def start_session(
        self,
        user_id: str,
        task_type: str = "general",
        start_url: str = "https://www.google.com",
    ) -> BrowserSession:
        """Launch browser and begin capturing."""
        from playwright.async_api import async_playwright

        session_id = str(uuid4())
        session = BrowserSession(session_id, user_id, task_type)
        self._sessions[session_id] = session

        try:
            session.playwright = await async_playwright().start()
            session.browser = await session.playwright.chromium.launch(
                headless=False,
                args=[
                    "--window-size=1280,900",
                    "--window-position=100,50",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            session.context = await session.browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            )

            # Inject capture script on every new page/navigation
            await session.context.add_init_script(CAPTURE_SCRIPT)

            session.page = await session.context.new_page()

            # Listen to navigation
            session.page.on("framenavigated", lambda frame: self._on_navigate(session, frame))

            await session.page.goto(start_url, wait_until="domcontentloaded")
            session.current_url = start_url
            session.status = "recording"

            # Start background loops
            asyncio.create_task(self._screenshot_loop(session))
            asyncio.create_task(self._action_poll_loop(session))

            logger.info(f"Browser capture started: {session_id} → {start_url}")
            return session

        except Exception as e:
            session.status = "stopped"
            logger.error(f"Browser launch failed: {e}")
            raise

    async def stop_session(self, session_id: str) -> Optional[BrowserSession]:
        """Stop capture, close browser, return session data."""
        session = self._sessions.get(session_id)
        if not session:
            return None

        session.status = "stopped"
        session.stopped_at = datetime.now(timezone.utc).isoformat()

        try:
            if session.browser:
                await session.browser.close()
            if session.playwright:
                await session.playwright.stop()
        except Exception as e:
            logger.warning(f"Browser close error: {e}")

        logger.info(
            f"Browser capture stopped: {session_id} — "
            f"{len(session.actions)} actions, {len(session.navigations)} navigations"
        )
        return session

    def get_session(self, session_id: str) -> Optional[BrowserSession]:
        return self._sessions.get(session_id)

    async def get_screenshot(self, session_id: str) -> Optional[str]:
        """Take a fresh screenshot right now."""
        session = self._sessions.get(session_id)
        if not session or not session.page or session.status != "recording":
            return None
        try:
            png = await session.page.screenshot(type="jpeg", quality=70)
            return base64.b64encode(png).decode()
        except Exception:
            return None

    # ── Background Loops ──────────────────────────────────────────

    async def _screenshot_loop(self, session: BrowserSession):
        """Periodically capture screenshots."""
        while session.status == "recording":
            try:
                png = await session.page.screenshot(type="jpeg", quality=65)
                b64 = base64.b64encode(png).decode()
                session.screenshots.append({
                    "timestamp": time.time(),
                    "image_b64": b64,
                })
                session.current_url = session.page.url

                # Keep memory bounded
                if len(session.screenshots) > 60:
                    session.screenshots = session.screenshots[-30:]

            except Exception as e:
                if session.status == "recording":
                    logger.warning(f"Screenshot failed: {e}")
            await asyncio.sleep(self.screenshot_interval)

    async def _action_poll_loop(self, session: BrowserSession):
        """Poll the browser for captured DOM events."""
        while session.status == "recording":
            try:
                raw_actions = await session.page.evaluate("""
                    (() => {
                        const a = window.__parrot_actions || [];
                        window.__parrot_actions = [];
                        return a;
                    })()
                """)
                for action in raw_actions:
                    action["action_index"] = len(session.actions)
                    # Build a human-readable description
                    action["description"] = self._describe_action(action)
                    session.actions.append(action)

            except Exception as e:
                if session.status == "recording":
                    logger.debug(f"Action poll error: {e}")
            await asyncio.sleep(0.8)

    def _on_navigate(self, session: BrowserSession, frame):
        """Track page navigations."""
        if frame == session.page.main_frame:
            nav = {
                "type": "navigate",
                "url": frame.url,
                "timestamp": time.time(),
                "action_index": len(session.actions),
                "description": f"Navigated to {frame.url}",
            }
            session.navigations.append(nav)
            session.actions.append(nav)
            session.current_url = frame.url

    def _describe_action(self, action: Dict) -> str:
        """Generate a human-readable description of a DOM action."""
        t = action.get("type", "")
        if t == "click":
            target = action.get("text", "") or action.get("id", "") or action.get("tag", "element")
            target = target[:60]
            href = action.get("href", "")
            if href:
                return f"Clicked '{target}' (link to {href[:80]})"
            return f"Clicked '{target}' <{action.get('tag', '?')}>"

        elif t == "type":
            field = action.get("placeholder", "") or action.get("name", "") or action.get("id", "") or "input"
            value = action.get("value", "")
            return f"Typed '{value[:50]}' into {field}"

        elif t == "submit":
            return f"Submitted form {action.get('id', '') or action.get('action', '')}"

        elif t == "scroll":
            return f"Scrolled to y={action.get('scrollY', 0)}"

        elif t == "navigate":
            return f"Navigated to {action.get('url', '?')}"

        return f"{t}: {str(action)[:80]}"
