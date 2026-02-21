"""
Create or update the Parrot (AgentMirror) Datadog dashboard.

Usage:
    python dashboard.py              # uses DD_API_KEY / DD_APP_KEY env vars
    python dashboard.py --dry-run    # print JSON without creating
"""

import argparse
import json
import os
import sys

try:
    from datadog_api_client import Configuration, ApiClient
    from datadog_api_client.v1.api.dashboards_api import DashboardsApi
    from datadog_api_client.v1.model.dashboard import Dashboard
except ImportError:
    sys.exit(
        "Missing dependency: pip install datadog-api-client\n"
        "This is only needed to provision the dashboard, not at runtime."
    )


SERVICE = "parrot"
ENV_TAG = f"env:{os.getenv('DD_ENV', 'development')}"

DASHBOARD_DEFINITION = {
    "title": "Parrot — AgentMirror Overview",
    "description": "Expert workflow capture, agent replay, and agent-expert divergence tracking",
    "layout_type": "ordered",
    "widgets": [
        # ── Section: System Health ──────────────────────────────────────
        {
            "definition": {
                "title": "System Health",
                "type": "group",
                "layout_type": "ordered",
                "widgets": [
                    {
                        "definition": {
                            "title": "Request Rate by Endpoint",
                            "type": "timeseries",
                            "requests": [
                                {
                                    "q": f"sum:trace.fastapi.request.hits{{service:{SERVICE},{ENV_TAG}}} by {{http.route}}.as_count()",
                                    "display_type": "bars",
                                }
                            ],
                        }
                    },
                    {
                        "definition": {
                            "title": "Error Rate",
                            "type": "timeseries",
                            "requests": [
                                {
                                    "q": f"sum:trace.fastapi.request.errors{{service:{SERVICE},{ENV_TAG}}}.as_count()",
                                    "display_type": "bars",
                                    "style": {"palette": "warm"},
                                }
                            ],
                        }
                    },
                    {
                        "definition": {
                            "title": "P50 / P95 / P99 Latency (ms)",
                            "type": "timeseries",
                            "requests": [
                                {
                                    "q": f"avg:trace.fastapi.request.duration{{service:{SERVICE},{ENV_TAG}}} by {{http.route}}",
                                    "display_type": "line",
                                },
                                {
                                    "q": f"p95:trace.fastapi.request.duration{{service:{SERVICE},{ENV_TAG}}}",
                                    "display_type": "line",
                                    "style": {"palette": "orange"},
                                },
                                {
                                    "q": f"p99:trace.fastapi.request.duration{{service:{SERVICE},{ENV_TAG}}}",
                                    "display_type": "line",
                                    "style": {"palette": "red"},
                                },
                            ],
                        }
                    },
                    {
                        "definition": {
                            "title": "Active Errors (last 5 min)",
                            "type": "query_value",
                            "requests": [
                                {
                                    "q": f"sum:trace.fastapi.request.errors{{service:{SERVICE},{ENV_TAG}}}.as_count().rollup(sum, 300)",
                                }
                            ],
                            "precision": 0,
                        }
                    },
                ],
            }
        },
        # ── Section: Observer Agent ─────────────────────────────────────
        {
            "definition": {
                "title": "Observer Agent — Workflow Capture",
                "type": "group",
                "layout_type": "ordered",
                "widgets": [
                    {
                        "definition": {
                            "title": "Sessions Processed",
                            "type": "timeseries",
                            "requests": [
                                {
                                    "q": f"sum:parrot.observer.sessions_processed{{service:{SERVICE},{ENV_TAG}}}",
                                    "display_type": "bars",
                                }
                            ],
                        }
                    },
                    {
                        "definition": {
                            "title": "Avg Steps Extracted per Session",
                            "type": "query_value",
                            "requests": [
                                {
                                    "q": f"avg:parrot.observer.steps_extracted{{service:{SERVICE},{ENV_TAG}}}",
                                }
                            ],
                            "precision": 1,
                        }
                    },
                    {
                        "definition": {
                            "title": "Raw Actions Captured",
                            "type": "query_value",
                            "requests": [
                                {
                                    "q": f"avg:parrot.observer.action_count{{service:{SERVICE},{ENV_TAG}}}",
                                }
                            ],
                            "precision": 0,
                        }
                    },
                    {
                        "definition": {
                            "title": "Observer Errors",
                            "type": "timeseries",
                            "requests": [
                                {
                                    "q": f"sum:parrot.observer.errors{{service:{SERVICE},{ENV_TAG}}}",
                                    "display_type": "bars",
                                    "style": {"palette": "warm"},
                                }
                            ],
                        }
                    },
                ],
            }
        },
        # ── Section: Simulator Agent ─────────────────────────────────────
        {
            "definition": {
                "title": "Simulator Agent — Workflow Replay",
                "type": "group",
                "layout_type": "ordered",
                "widgets": [
                    {
                        "definition": {
                            "title": "Simulation Runs (Started vs Completed)",
                            "type": "timeseries",
                            "requests": [
                                {
                                    "q": f"sum:parrot.simulator.runs_started{{service:{SERVICE},{ENV_TAG}}}",
                                    "display_type": "bars",
                                    "style": {"palette": "blue"},
                                },
                                {
                                    "q": f"sum:parrot.simulator.runs_completed{{service:{SERVICE},{ENV_TAG}}}",
                                    "display_type": "bars",
                                    "style": {"palette": "green"},
                                },
                            ],
                        }
                    },
                    {
                        "definition": {
                            "title": "Step Success Rate",
                            "type": "timeseries",
                            "requests": [
                                {
                                    "q": f"avg:parrot.simulator.step_success_rate{{service:{SERVICE},{ENV_TAG}}}",
                                    "display_type": "line",
                                }
                            ],
                        }
                    },
                    {
                        "definition": {
                            "title": "Step Failures by Step",
                            "type": "timeseries",
                            "requests": [
                                {
                                    "q": f"avg:parrot.simulator.step_action_failures{{service:{SERVICE},{ENV_TAG}}} by {{step}}",
                                    "display_type": "bars",
                                    "style": {"palette": "warm"},
                                }
                            ],
                        }
                    },
                    {
                        "definition": {
                            "title": "Simulator Errors",
                            "type": "timeseries",
                            "requests": [
                                {
                                    "q": f"sum:parrot.simulator.errors{{service:{SERVICE},{ENV_TAG}}}",
                                    "display_type": "bars",
                                    "style": {"palette": "warm"},
                                }
                            ],
                        }
                    },
                ],
            }
        },
        # ── Section: Agent-Expert Divergence ─────────────────────────────
        {
            "definition": {
                "title": "Agent-Expert Divergence",
                "type": "group",
                "layout_type": "ordered",
                "widgets": [
                    {
                        "definition": {
                            "title": "Avg Step Success Rate (last hour)",
                            "type": "query_value",
                            "requests": [
                                {
                                    "q": f"avg:parrot.simulator.step_success_rate{{service:{SERVICE},{ENV_TAG}}}.rollup(avg, 3600)",
                                }
                            ],
                            "precision": 2,
                        }
                    },
                ],
            }
        },
    ],
    "template_variables": [
        {"name": "env", "default": "development", "prefix": "env"},
        {"name": "service", "default": SERVICE, "prefix": "service"},
    ],
    "notify_list": [],
    "reflow_type": "auto",
}


def create_dashboard(dry_run: bool = False) -> dict:
    if dry_run:
        print(json.dumps(DASHBOARD_DEFINITION, indent=2))
        return DASHBOARD_DEFINITION

    configuration = Configuration()
    with ApiClient(configuration) as api_client:
        api = DashboardsApi(api_client)
        body = Dashboard(**DASHBOARD_DEFINITION)
        resp = api.create_dashboard(body=body)
        print(f"Dashboard created: {resp.url}")
        return resp.to_dict()


def update_dashboard(dashboard_id: str) -> dict:
    """Update an existing dashboard with the current DASHBOARD_DEFINITION."""
    configuration = Configuration()
    with ApiClient(configuration) as api_client:
        api = DashboardsApi(api_client)
        body = Dashboard(**DASHBOARD_DEFINITION)
        resp = api.update_dashboard(dashboard_id=dashboard_id, body=body)
        print(f"Dashboard updated: {resp.url}")
        return resp.to_dict()


def list_dashboards() -> list:
    """List all dashboards to find existing ones."""
    configuration = Configuration()
    with ApiClient(configuration) as api_client:
        api = DashboardsApi(api_client)
        resp = api.list_dashboards()
        return [
            {"id": d.id, "title": d.title, "url": d.url}
            for d in (resp.dashboards or [])
        ]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Provision the Parrot Datadog dashboard")
    parser.add_argument("--dry-run", action="store_true", help="Print JSON without creating")
    parser.add_argument("--update", type=str, default=None, help="Update existing dashboard by ID")
    parser.add_argument("--list", action="store_true", help="List existing dashboards")
    args = parser.parse_args()

    if args.list:
        try:
            dashboards = list_dashboards()
            for d in dashboards:
                print(f"  {d['id']}  {d['title']}  {d.get('url', '')}")
        except Exception as e:
            print(f"Error listing dashboards: {e}")
    elif args.update:
        try:
            update_dashboard(args.update)
        except Exception as e:
            print(f"Error updating dashboard: {e}")
    elif args.dry_run:
        create_dashboard(dry_run=True)
    else:
        try:
            create_dashboard(dry_run=False)
        except Exception as e:
            print(f"Error creating dashboard: {e}")
