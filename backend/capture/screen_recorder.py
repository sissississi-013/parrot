"""
Screen recorder: captures screenshots + mouse/keyboard events in real-time.

Uses mss for fast screen capture and pynput for input event tracking.
Designed to run as background threads while the user works normally.
"""

import base64
import io
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Callable
from uuid import uuid4

import mss
from PIL import Image
from pynput import mouse, keyboard

logger = logging.getLogger(__name__)


class CaptureSession:
    """Holds state for a single capture session."""

    def __init__(self, session_id: str, user_id: str, task_type: str = "general"):
        self.session_id = session_id
        self.user_id = user_id
        self.task_type = task_type
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.stopped_at: Optional[str] = None
        self.status = "recording"

        # Raw data
        self.events: List[Dict] = []          # Mouse/keyboard events
        self.screenshots: List[Dict] = []      # {timestamp, image_base64}
        self.detected_actions: List[Dict] = [] # Actions interpreted by Claude vision

        # Callbacks for real-time streaming
        self._on_action: Optional[Callable] = None
        self._on_event: Optional[Callable] = None


class ScreenRecorder:
    """
    Records screen + input events, builds action sequences.

    Flow:
    1. start_session() → begins capturing screenshots + events
    2. Background threads capture data continuously
    3. analyze_latest() → sends recent screenshot + events to ActionDetector
    4. stop_session() → returns all detected actions for ObserverAgent processing
    """

    def __init__(self, capture_interval: float = 3.0, screenshot_width: int = 1280):
        self.capture_interval = capture_interval
        self.screenshot_width = screenshot_width
        self._sessions: Dict[str, CaptureSession] = {}
        self._locks: Dict[str, threading.Lock] = {}

    @property
    def active_sessions(self) -> Dict[str, CaptureSession]:
        return {sid: s for sid, s in self._sessions.items() if s.status == "recording"}

    def start_session(
        self,
        user_id: str,
        task_type: str = "general",
        on_action: Optional[Callable] = None,
    ) -> CaptureSession:
        """Start a new capture session. Returns session object."""
        session_id = str(uuid4())
        session = CaptureSession(session_id, user_id, task_type)
        session._on_action = on_action
        self._sessions[session_id] = session
        self._locks[session_id] = threading.Lock()

        # Start capture threads
        threading.Thread(
            target=self._screenshot_loop,
            args=(session_id,),
            daemon=True,
            name=f"capture-screenshots-{session_id[:8]}",
        ).start()

        threading.Thread(
            target=self._input_listener,
            args=(session_id,),
            daemon=True,
            name=f"capture-input-{session_id[:8]}",
        ).start()

        logger.info(f"Started capture session {session_id} for user {user_id}")
        return session

    def stop_session(self, session_id: str) -> Optional[CaptureSession]:
        """Stop a capture session and return the session data."""
        session = self._sessions.get(session_id)
        if not session:
            return None

        session.status = "stopped"
        session.stopped_at = datetime.now(timezone.utc).isoformat()
        logger.info(
            f"Stopped capture session {session_id}: "
            f"{len(session.screenshots)} screenshots, "
            f"{len(session.events)} events, "
            f"{len(session.detected_actions)} detected actions"
        )
        return session

    def get_session(self, session_id: str) -> Optional[CaptureSession]:
        return self._sessions.get(session_id)

    def add_detected_action(self, session_id: str, action: Dict):
        """Add a detected action (from ActionDetector) to the session."""
        session = self._sessions.get(session_id)
        if not session:
            return

        with self._locks[session_id]:
            action["action_index"] = len(session.detected_actions)
            session.detected_actions.append(action)

        # Fire callback for real-time streaming
        if session._on_action:
            try:
                session._on_action(action)
            except Exception as e:
                logger.warning(f"Action callback failed: {e}")

    def get_latest_screenshot(self, session_id: str) -> Optional[Dict]:
        """Get the most recent screenshot for a session."""
        session = self._sessions.get(session_id)
        if not session or not session.screenshots:
            return None
        return session.screenshots[-1]

    def get_recent_events(self, session_id: str, since_timestamp: float = 0) -> List[Dict]:
        """Get events since a given timestamp."""
        session = self._sessions.get(session_id)
        if not session:
            return []
        return [e for e in session.events if e.get("timestamp", 0) > since_timestamp]

    def take_screenshot(self) -> str:
        """Take a single screenshot, return as base64 JPEG."""
        with mss.mss() as sct:
            monitor = sct.monitors[0]  # Full screen (all monitors)
            raw = sct.grab(monitor)

            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

            # Resize to target width maintaining aspect ratio
            ratio = self.screenshot_width / img.width
            new_height = int(img.height * ratio)
            img = img.resize((self.screenshot_width, new_height), Image.LANCZOS)

            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=65)
            return base64.b64encode(buffer.getvalue()).decode()

    # ── Private: Background Threads ──────────────────────────────────

    def _screenshot_loop(self, session_id: str):
        """Background thread: capture screenshots at regular intervals."""
        session = self._sessions.get(session_id)
        if not session:
            return

        while session.status == "recording":
            try:
                b64 = self.take_screenshot()
                with self._locks[session_id]:
                    session.screenshots.append({
                        "timestamp": time.time(),
                        "image_base64": b64,
                    })

                # Keep only last 100 screenshots to limit memory
                if len(session.screenshots) > 100:
                    session.screenshots = session.screenshots[-50:]

            except Exception as e:
                logger.warning(f"Screenshot capture failed: {e}")

            time.sleep(self.capture_interval)

    def _input_listener(self, session_id: str):
        """Background thread: capture mouse clicks and key presses."""
        session = self._sessions.get(session_id)
        if not session:
            return

        def on_click(x, y, button, pressed):
            if not pressed or session.status != "recording":
                return
            with self._locks[session_id]:
                session.events.append({
                    "type": "click",
                    "x": x,
                    "y": y,
                    "button": str(button),
                    "timestamp": time.time(),
                })

        def on_key_press(key):
            if session.status != "recording":
                return
            try:
                key_str = key.char if hasattr(key, "char") and key.char else str(key)
            except AttributeError:
                key_str = str(key)
            with self._locks[session_id]:
                session.events.append({
                    "type": "keystroke",
                    "key": key_str,
                    "timestamp": time.time(),
                })

        mouse_listener = mouse.Listener(on_click=on_click)
        keyboard_listener = keyboard.Listener(on_press=on_key_press)

        mouse_listener.start()
        keyboard_listener.start()

        # Wait until session stops
        while session.status == "recording":
            time.sleep(0.5)

        mouse_listener.stop()
        keyboard_listener.stop()
