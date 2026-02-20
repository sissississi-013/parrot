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
    "description": "Multi-agent workflow learning system (Observer + Twin agents on AWS Bedrock)",
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
                "title": "Observer Agent — Workflow Extraction",
                "type": "group",
                "layout_type": "ordered",
                "widgets": [
                    {
                        "definition": {
                            "title": "Sessions Processed",
                            "type": "timeseries",
                            "requests": [
                                {
                                    "q": f"sum:trace.parrot.observer.process_session.hits{{service:{SERVICE},{ENV_TAG}}}.as_count()",
                                    "display_type": "bars",
                                }
                            ],
                        }
                    },
                    {
                        "definition": {
                            "title": "Extraction Duration (s)",
                            "type": "timeseries",
                            "requests": [
                                {
                                    "q": f"avg:trace.parrot.observer.process_session.duration{{service:{SERVICE},{ENV_TAG}}}",
                                    "display_type": "line",
                                },
                                {
                                    "q": f"p95:trace.parrot.observer.process_session.duration{{service:{SERVICE},{ENV_TAG}}}",
                                    "display_type": "line",
                                    "style": {"palette": "orange"},
                                },
                            ],
                        }
                    },
                    {
                        "definition": {
                            "title": "Avg Steps Extracted per Session",
                            "type": "query_value",
                            "requests": [
                                {
                                    "q": f"avg:trace.parrot.observer.process_session.observer.steps_extracted{{service:{SERVICE},{ENV_TAG}}}",
                                }
                            ],
                            "precision": 1,
                        }
                    },
                    {
                        "definition": {
                            "title": "Observer Errors",
                            "type": "timeseries",
                            "requests": [
                                {
                                    "q": f"sum:trace.parrot.observer.process_session.errors{{service:{SERVICE},{ENV_TAG}}}.as_count()",
                                    "display_type": "bars",
                                    "style": {"palette": "warm"},
                                }
                            ],
                        }
                    },
                ],
            }
        },
        # ── Section: Twin (Coach) Agent ─────────────────────────────────
        {
            "definition": {
                "title": "Twin Agent — Coaching & Convergence",
                "type": "group",
                "layout_type": "ordered",
                "widgets": [
                    {
                        "definition": {
                            "title": "Guidance Requests",
                            "type": "timeseries",
                            "requests": [
                                {
                                    "q": f"sum:trace.parrot.twin.guide_step.hits{{service:{SERVICE},{ENV_TAG}}}.as_count()",
                                    "display_type": "bars",
                                }
                            ],
                        }
                    },
                    {
                        "definition": {
                            "title": "Step Convergence Score Distribution",
                            "type": "distribution",
                            "requests": [
                                {
                                    "q": f"avg:trace.parrot.twin.guide_step.twin.step_convergence_score{{service:{SERVICE},{ENV_TAG}}}",
                                }
                            ],
                        }
                    },
                    {
                        "definition": {
                            "title": "Overall Convergence Score",
                            "type": "timeseries",
                            "requests": [
                                {
                                    "q": f"avg:trace.parrot.twin.calculate_convergence.twin.overall_convergence_score{{service:{SERVICE},{ENV_TAG}}}",
                                    "display_type": "line",
                                }
                            ],
                        }
                    },
                    {
                        "definition": {
                            "title": "Deviations Detected",
                            "type": "timeseries",
                            "requests": [
                                {
                                    "q": f"avg:trace.parrot.twin.calculate_convergence.twin.deviation_count{{service:{SERVICE},{ENV_TAG}}}",
                                    "display_type": "bars",
                                    "style": {"palette": "warm"},
                                }
                            ],
                        }
                    },
                    {
                        "definition": {
                            "title": "Avg Convergence (last hour)",
                            "type": "query_value",
                            "requests": [
                                {
                                    "q": f"avg:trace.parrot.twin.calculate_convergence.twin.overall_convergence_score{{service:{SERVICE},{ENV_TAG}}}.rollup(avg, 3600)",
                                }
                            ],
                            "precision": 2,
                        }
                    },
                    {
                        "definition": {
                            "title": "High-Impact Deviations",
                            "type": "query_value",
                            "requests": [
                                {
                                    "q": f"sum:trace.parrot.twin.calculate_convergence.twin.high_impact_deviations{{service:{SERVICE},{ENV_TAG}}}.rollup(sum, 3600)",
                                }
                            ],
                            "precision": 0,
                        }
                    },
                ],
            }
        },
        # ── Section: Bedrock / LLM Performance ─────────────────────────
        {
            "definition": {
                "title": "Bedrock / LLM Performance",
                "type": "group",
                "layout_type": "ordered",
                "widgets": [
                    {
                        "definition": {
                            "title": "Bedrock Call Latency",
                            "type": "timeseries",
                            "requests": [
                                {
                                    "q": f"avg:trace.botocore.command.duration{{service:{SERVICE},aws_service:bedrock-runtime}}",
                                    "display_type": "line",
                                },
                                {
                                    "q": f"p95:trace.botocore.command.duration{{service:{SERVICE},aws_service:bedrock-runtime}}",
                                    "display_type": "line",
                                    "style": {"palette": "orange"},
                                },
                            ],
                        }
                    },
                    {
                        "definition": {
                            "title": "Bedrock Error Count",
                            "type": "timeseries",
                            "requests": [
                                {
                                    "q": f"sum:trace.botocore.command.errors{{service:{SERVICE},aws_service:bedrock-runtime}}.as_count()",
                                    "display_type": "bars",
                                    "style": {"palette": "warm"},
                                }
                            ],
                        }
                    },
                    {
                        "definition": {
                            "title": "Total LLM Calls (24h)",
                            "type": "query_value",
                            "requests": [
                                {
                                    "q": f"sum:trace.botocore.command.hits{{service:{SERVICE},aws_service:bedrock-runtime}}.as_count().rollup(sum, 86400)",
                                }
                            ],
                            "precision": 0,
                        }
                    },
                ],
            }
        },
        # ── Section: Logs ───────────────────────────────────────────────
        {
            "definition": {
                "title": "Logs & Errors",
                "type": "group",
                "layout_type": "ordered",
                "widgets": [
                    {
                        "definition": {
                            "title": "Recent Error Logs",
                            "type": "log_stream",
                            "query": f"service:{SERVICE} status:error",
                            "columns": ["timestamp", "message"],
                            "show_date_column": True,
                            "show_message_column": True,
                            "message_display": "expanded-md",
                            "sort": {"column": "time", "order": "desc"},
                        }
                    },
                    {
                        "definition": {
                            "title": "Log Volume by Level",
                            "type": "timeseries",
                            "requests": [
                                {
                                    "q": f"sum:logs.count{{service:{SERVICE},{ENV_TAG}}} by {{status}}.as_count()",
                                    "display_type": "bars",
                                }
                            ],
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Provision the Parrot Datadog dashboard")
    parser.add_argument("--dry-run", action="store_true", help="Print JSON without creating")
    args = parser.parse_args()
    create_dashboard(dry_run=args.dry_run)
