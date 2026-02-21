"""
Datadog custom metrics via the HTTP API (agentless-compatible).

Uses `datadog.api.Metric.send()` so no Datadog Agent / DogStatsD daemon is required.
Metrics are submitted over HTTPS directly to the Datadog intake.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

logger = logging.getLogger("parrot.metrics")

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dd-metrics")
_initialized = False


def init(api_key: str, app_key: Optional[str] = None):
    """Call once at startup to configure the datadog HTTP client."""
    global _initialized
    if _initialized:
        return

    from datadog import initialize

    options = {"api_key": api_key}
    if app_key:
        options["app_key"] = app_key
    initialize(**options)
    _initialized = True
    logger.info("Datadog metrics HTTP client initialized")


def _send(metric: str, value: float, tags: List[str], metric_type: str):
    """Blocking send — runs in the thread pool."""
    if not _initialized:
        return
    try:
        from datadog import api

        api.Metric.send(
            metric=metric,
            points=[(time.time(), value)],
            type=metric_type,
            tags=tags,
        )
    except Exception as e:
        logger.warning("Failed to send metric %s: %s", metric, e)


def _default_tags() -> List[str]:
    from config import settings

    return [
        f"service:{settings.dd_service}",
        f"env:{settings.dd_env}",
        f"version:{settings.dd_version}",
    ]


def gauge(metric: str, value: float, tags: Optional[List[str]] = None):
    """Submit a gauge metric (non-blocking)."""
    all_tags = _default_tags() + (tags or [])
    _executor.submit(_send, metric, value, all_tags, "gauge")


def count(metric: str, value: float, tags: Optional[List[str]] = None):
    """Submit a count metric (non-blocking)."""
    all_tags = _default_tags() + (tags or [])
    _executor.submit(_send, metric, value, all_tags, "count")


def histogram(metric: str, value: float, tags: Optional[List[str]] = None):
    """Submit a distribution/histogram metric (non-blocking). Submitted as gauge since HTTP API doesn't support distribution."""
    all_tags = _default_tags() + (tags or [])
    _executor.submit(_send, metric, value, all_tags, "gauge")
