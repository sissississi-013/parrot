"""
Neo4j data layer for Parrot / AgentMirror.

Graph schema:

(:Expert {id, name})
  -[:AUTHORED]-> (:Workflow {id, name, description, pattern, task_type, created_at})
    -[:HAS_STEP {order}]-> (:Step {id, name, context, reasoning})
      -[:INVOLVES]-> (:Action {id, type, target, value, timestamp})
      -[:NEXT]-> (:Step)  [sequential chain]
      -[:DECIDED_BECAUSE]-> (:Reasoning {id, explanation})

(:Newbie {id, name})
  -[:ATTEMPTED]-> (:Session {id, workflow_id, started_at, status})
    -[:PERFORMED {order}]-> (:NewbieAction {id, action, result, timestamp})
    -[:SCORED]-> (:ConvergenceScore {overall, step_scores, deviations, timestamp})

(:NewbieAction) -[:ALIGNS_WITH {score}]-> (:Step)
(:NewbieAction) -[:DIVERGES_FROM {deviation, impact}]-> (:Step)
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from neo4j import GraphDatabase, Driver

logger = logging.getLogger(__name__)


class Neo4jClient:
    def __init__(self, uri: str, user: str, password: str):
        self._driver: Driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self):
        self._driver.close()

    def verify_connection(self) -> bool:
        try:
            with self._driver.session() as session:
                result = session.run("RETURN 1 AS n")
                record = result.single()
                assert record["n"] == 1
                logger.info("Neo4j connection verified")
                return True
        except Exception as e:
            logger.error(f"Neo4j connection failed: {e}")
            return False

    def setup_indexes(self):
        with self._driver.session() as session:
            for label, prop in [
                ("Workflow", "id"), ("Step", "id"), ("Expert", "id"),
                ("Session", "id"), ("Newbie", "id"), ("Action", "id"),
                ("Reasoning", "id"), ("ConvergenceScore", "id"),
                ("NewbieAction", "id"),
            ]:
                session.run(
                    f"CREATE INDEX {label.lower()}_{prop}_idx IF NOT EXISTS "
                    f"FOR (n:{label}) ON (n.{prop})"
                )
            # Full-text search on workflow names/descriptions
            try:
                session.run(
                    "CREATE FULLTEXT INDEX workflow_search IF NOT EXISTS "
                    "FOR (w:Workflow) ON EACH [w.name, w.description, w.task_type]"
                )
            except Exception:
                pass  # May already exist
            logger.info("Neo4j indexes created")

    # ── Store Workflow (from ObserverAgent output) ──────────────────

    def store_workflow(self, workflow_data: dict, expert_id: str) -> str:
        """
        Store an ObserverAgent-processed workflow into the graph.

        workflow_data is the exact output from ObserverAgent.process_session():
        {
          "workflow_name": "...",
          "steps": [{"step_number": 1, "step_name": "...", "actions": [...],
                     "context": "...", "reasoning": "..."}],
          "workflow_pattern": "...",
          "workflow_id": "uuid",
          "session_id": "...",
          "created_at": "...",
          "expert_user_id": "..."
        }
        """
        workflow_id = workflow_data.get("workflow_id", str(uuid4()))
        now = workflow_data.get("created_at", datetime.now(timezone.utc).isoformat())

        with self._driver.session() as session:
            # Create/merge expert
            session.run(
                "MERGE (e:Expert {id: $eid}) "
                "ON CREATE SET e.created_at = $now",
                eid=expert_id, now=now,
            )

            # Create workflow
            session.run(
                """
                MATCH (e:Expert {id: $eid})
                CREATE (w:Workflow {
                    id: $wid,
                    name: $name,
                    description: $desc,
                    pattern: $pattern,
                    task_type: $task_type,
                    session_id: $sid,
                    step_count: $step_count,
                    created_at: $now
                })
                CREATE (e)-[:AUTHORED]->(w)
                """,
                eid=expert_id,
                wid=workflow_id,
                name=workflow_data.get("workflow_name", "Unnamed"),
                desc=workflow_data.get("workflow_pattern", ""),
                pattern=workflow_data.get("workflow_pattern", ""),
                task_type=workflow_data.get("task_type", ""),
                sid=workflow_data.get("session_id", ""),
                step_count=len(workflow_data.get("steps", [])),
                now=now,
            )

            # Create steps with actions + reasoning
            steps = workflow_data.get("steps", [])
            prev_step_id = None

            for step in steps:
                step_id = str(uuid4())
                order = step.get("step_number", 0)

                # Create step node
                session.run(
                    """
                    MATCH (w:Workflow {id: $wid})
                    CREATE (s:Step {
                        id: $sid,
                        name: $name,
                        context: $context,
                        reasoning: $reasoning,
                        step_order: $order,
                        created_at: $now
                    })
                    CREATE (w)-[:HAS_STEP {order: $order}]->(s)
                    """,
                    wid=workflow_id, sid=step_id,
                    name=step.get("step_name", ""),
                    context=step.get("context", ""),
                    reasoning=step.get("reasoning", ""),
                    order=order, now=now,
                )

                # Create reasoning node (the WHY — separate for graph queries)
                reasoning_text = step.get("reasoning", "")
                if reasoning_text:
                    reasoning_id = str(uuid4())
                    session.run(
                        """
                        MATCH (s:Step {id: $sid})
                        CREATE (r:Reasoning {
                            id: $rid,
                            explanation: $explanation,
                            step_name: $step_name,
                            created_at: $now
                        })
                        CREATE (s)-[:DECIDED_BECAUSE]->(r)
                        """,
                        sid=step_id, rid=reasoning_id,
                        explanation=reasoning_text,
                        step_name=step.get("step_name", ""),
                        now=now,
                    )

                # Create action nodes (individual actions within the step)
                for action in step.get("actions", []):
                    action_id = str(uuid4())
                    # action can be a string (action_id) or a dict
                    action_desc = action if isinstance(action, str) else str(action)
                    session.run(
                        """
                        MATCH (s:Step {id: $sid})
                        CREATE (a:Action {
                            id: $aid,
                            description: $desc,
                            created_at: $now
                        })
                        CREATE (s)-[:INVOLVES]->(a)
                        """,
                        sid=step_id, aid=action_id,
                        desc=action_desc, now=now,
                    )

                # Chain steps sequentially
                if prev_step_id:
                    session.run(
                        """
                        MATCH (prev:Step {id: $prev_id})
                        MATCH (curr:Step {id: $curr_id})
                        CREATE (prev)-[:NEXT]->(curr)
                        """,
                        prev_id=prev_step_id, curr_id=step_id,
                    )

                prev_step_id = step_id

        logger.info(f"Stored workflow '{workflow_data.get('workflow_name')}' [{workflow_id}] with {len(steps)} steps")
        return workflow_id

    # ── Retrieve Workflows ──────────────────────────────────────────

    def get_workflow(self, workflow_id: str) -> Optional[dict]:
        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (e:Expert)-[:AUTHORED]->(w:Workflow {id: $wid})
                RETURN w, e.id AS expert_id
                """,
                wid=workflow_id,
            )
            record = result.single()
            if not record:
                return None

            w = record["w"]
            workflow = {
                "workflow_id": w["id"],
                "workflow_name": w["name"],
                "description": w.get("description", ""),
                "pattern": w.get("pattern", ""),
                "task_type": w.get("task_type", ""),
                "expert_id": record["expert_id"],
                "step_count": w.get("step_count", 0),
                "created_at": w.get("created_at", ""),
                "steps": [],
            }

            # Get steps with reasoning
            steps_result = session.run(
                """
                MATCH (w:Workflow {id: $wid})-[:HAS_STEP]->(s:Step)
                OPTIONAL MATCH (s)-[:DECIDED_BECAUSE]->(r:Reasoning)
                OPTIONAL MATCH (s)-[:INVOLVES]->(a:Action)
                WITH s, r, collect(DISTINCT a.description) AS actions
                RETURN s, r.explanation AS reasoning, actions
                ORDER BY s.step_order
                """,
                wid=workflow_id,
            )

            for rec in steps_result:
                s = rec["s"]
                workflow["steps"].append({
                    "step_number": s.get("step_order", 0),
                    "step_name": s.get("name", ""),
                    "step_id": s["id"],
                    "context": s.get("context", ""),
                    "reasoning": rec["reasoning"] or s.get("reasoning", ""),
                    "actions": rec["actions"],
                })

            return workflow

    def list_workflows(self, expert_id: Optional[str] = None, task_type: Optional[str] = None) -> list:
        with self._driver.session() as session:
            conditions = []
            params = {}

            if expert_id:
                conditions.append("e.id = $eid")
                params["eid"] = expert_id
            if task_type:
                conditions.append("toLower(w.task_type) CONTAINS toLower($tt)")
                params["tt"] = task_type

            where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

            result = session.run(
                f"""
                MATCH (e:Expert)-[:AUTHORED]->(w:Workflow)
                {where_clause}
                RETURN w.id AS id, w.name AS name, w.description AS description,
                       w.task_type AS task_type, w.step_count AS step_count,
                       w.created_at AS created_at, e.id AS expert_id
                ORDER BY w.created_at DESC
                """,
                **params,
            )
            return [dict(rec) for rec in result]

    def search_workflows(self, search_term: str) -> list:
        with self._driver.session() as session:
            # Try full-text search first
            try:
                result = session.run(
                    """
                    CALL db.index.fulltext.queryNodes('workflow_search', $term)
                    YIELD node AS w, score
                    MATCH (e:Expert)-[:AUTHORED]->(w)
                    RETURN w.id AS id, w.name AS name, w.description AS description,
                           w.task_type AS task_type, w.step_count AS step_count,
                           e.id AS expert_id, score
                    ORDER BY score DESC
                    LIMIT 10
                    """,
                    term=search_term,
                )
                return [dict(rec) for rec in result]
            except Exception:
                # Fallback to CONTAINS
                result = session.run(
                    """
                    MATCH (e:Expert)-[:AUTHORED]->(w:Workflow)
                    WHERE toLower(w.name) CONTAINS toLower($term)
                       OR toLower(w.description) CONTAINS toLower($term)
                       OR toLower(w.task_type) CONTAINS toLower($term)
                    RETURN w.id AS id, w.name AS name, w.description AS description,
                           w.task_type AS task_type, w.step_count AS step_count,
                           e.id AS expert_id
                    ORDER BY w.created_at DESC
                    LIMIT 10
                    """,
                    term=search_term,
                )
                return [dict(rec) for rec in result]

    # ── Session Tracking (Newbie Progress) ──────────────────────────

    def create_session(self, newbie_id: str, workflow_id: str) -> str:
        session_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()

        with self._driver.session() as session:
            session.run(
                "MERGE (n:Newbie {id: $nid}) ON CREATE SET n.created_at = $now",
                nid=newbie_id, now=now,
            )
            session.run(
                """
                MATCH (n:Newbie {id: $nid})
                MATCH (w:Workflow {id: $wid})
                CREATE (s:Session {
                    id: $sid,
                    workflow_id: $wid,
                    started_at: $now,
                    current_step: 0,
                    status: 'active'
                })
                CREATE (n)-[:ATTEMPTED]->(s)
                CREATE (s)-[:FOLLOWING]->(w)
                """,
                nid=newbie_id, wid=workflow_id, sid=session_id, now=now,
            )

        logger.info(f"Created session {session_id} for newbie {newbie_id}")
        return session_id

    def log_newbie_action(self, session_id: str, action: dict, step_number: int) -> str:
        action_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()

        with self._driver.session() as session:
            session.run(
                """
                MATCH (s:Session {id: $sid})
                CREATE (a:NewbieAction {
                    id: $aid,
                    action: $action,
                    step_number: $step_num,
                    timestamp: $now
                })
                CREATE (s)-[:PERFORMED {order: $step_num}]->(a)
                """,
                sid=session_id, aid=action_id,
                action=str(action), step_num=step_number, now=now,
            )

        return action_id

    # ── Convergence Tracking ────────────────────────────────────────

    def store_convergence(
        self,
        session_id: str,
        workflow_id: str,
        convergence_data: dict,
    ) -> str:
        """Store convergence analysis from TwinAgent.calculate_convergence()."""
        score_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()

        with self._driver.session() as session:
            session.run(
                """
                MATCH (s:Session {id: $sid})
                MATCH (w:Workflow {id: $wid})
                CREATE (c:ConvergenceScore {
                    id: $cid,
                    overall_score: $overall,
                    step_scores: $step_scores,
                    deviations: $deviations,
                    strengths: $strengths,
                    improvements: $improvements,
                    timestamp: $now
                })
                CREATE (s)-[:SCORED]->(c)
                CREATE (c)-[:FOR_WORKFLOW]->(w)
                """,
                sid=session_id, wid=workflow_id, cid=score_id,
                overall=convergence_data.get("overall_score", 0.0),
                step_scores=str(convergence_data.get("step_scores", [])),
                deviations=str(convergence_data.get("deviations", [])),
                strengths=str(convergence_data.get("strengths", [])),
                improvements=str(convergence_data.get("areas_for_improvement", [])),
                now=now,
            )

            # Create alignment/divergence edges per step
            for step_score in convergence_data.get("step_scores", []):
                step_num = step_score.get("step", 0)
                score = step_score.get("score", 0)
                matched = step_score.get("matched", False)

                # Link newbie actions to expert steps
                session.run(
                    """
                    MATCH (sess:Session {id: $sid})-[:FOLLOWING]->(w:Workflow)
                    MATCH (w)-[:HAS_STEP]->(step:Step {step_order: $step_num})
                    MATCH (sess)-[:PERFORMED]->(na:NewbieAction {step_number: $step_num})
                    MERGE (na)-[r:ALIGNS_WITH]->(step)
                    SET r.score = $score, r.matched = $matched
                    """,
                    sid=session_id, step_num=step_num,
                    score=score, matched=matched,
                )

            # Create divergence edges for deviations
            for deviation in convergence_data.get("deviations", []):
                step_num = deviation.get("step", 0)
                session.run(
                    """
                    MATCH (sess:Session {id: $sid})-[:FOLLOWING]->(w:Workflow)
                    MATCH (w)-[:HAS_STEP]->(step:Step {step_order: $step_num})
                    MATCH (sess)-[:PERFORMED]->(na:NewbieAction {step_number: $step_num})
                    MERGE (na)-[r:DIVERGES_FROM]->(step)
                    SET r.deviation = $issue, r.impact = $impact
                    """,
                    sid=session_id, step_num=step_num,
                    issue=deviation.get("issue", ""),
                    impact=deviation.get("impact", ""),
                )

        logger.info(f"Stored convergence score {convergence_data.get('overall_score', 0)} for session {session_id}")
        return score_id

    # ── Graph Visualization Queries ─────────────────────────────────

    def get_workflow_graph(self, workflow_id: str) -> dict:
        """Get full graph structure for visualization (nodes + edges)."""
        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (e:Expert)-[:AUTHORED]->(w:Workflow {id: $wid})
                OPTIONAL MATCH (w)-[hs:HAS_STEP]->(s:Step)
                OPTIONAL MATCH (s)-[:DECIDED_BECAUSE]->(r:Reasoning)
                OPTIONAL MATCH (s)-[:INVOLVES]->(a:Action)
                OPTIONAL MATCH (s)-[:NEXT]->(next_s:Step)
                RETURN e, w, s, r, a, next_s, hs.order AS step_order
                ORDER BY s.step_order
                """,
                wid=workflow_id,
            )

            nodes = {}
            edges = []

            for rec in result:
                # Expert node
                if rec["e"] and rec["e"]["id"] not in nodes:
                    nodes[rec["e"]["id"]] = {
                        "id": rec["e"]["id"],
                        "label": rec["e"]["id"],
                        "type": "Expert",
                        "color": "#4CAF50",
                    }

                # Workflow node
                if rec["w"] and rec["w"]["id"] not in nodes:
                    nodes[rec["w"]["id"]] = {
                        "id": rec["w"]["id"],
                        "label": rec["w"]["name"],
                        "type": "Workflow",
                        "color": "#2196F3",
                    }
                    edges.append({
                        "from": rec["e"]["id"],
                        "to": rec["w"]["id"],
                        "label": "AUTHORED",
                    })

                # Step node
                if rec["s"] and rec["s"]["id"] not in nodes:
                    nodes[rec["s"]["id"]] = {
                        "id": rec["s"]["id"],
                        "label": rec["s"].get("name", ""),
                        "type": "Step",
                        "color": "#FF9800",
                        "reasoning": rec["s"].get("reasoning", ""),
                        "context": rec["s"].get("context", ""),
                        "order": rec["s"].get("step_order", 0),
                    }
                    edges.append({
                        "from": rec["w"]["id"],
                        "to": rec["s"]["id"],
                        "label": f"STEP {rec.get('step_order', '')}",
                    })

                # Reasoning node
                if rec["r"] and rec["r"]["id"] not in nodes:
                    nodes[rec["r"]["id"]] = {
                        "id": rec["r"]["id"],
                        "label": rec["r"]["explanation"][:60] + "..." if len(rec["r"]["explanation"]) > 60 else rec["r"]["explanation"],
                        "type": "Reasoning",
                        "color": "#9C27B0",
                        "full_text": rec["r"]["explanation"],
                    }
                    edges.append({
                        "from": rec["s"]["id"],
                        "to": rec["r"]["id"],
                        "label": "DECIDED_BECAUSE",
                    })

                # Action node
                if rec["a"] and rec["a"]["id"] not in nodes:
                    nodes[rec["a"]["id"]] = {
                        "id": rec["a"]["id"],
                        "label": rec["a"].get("description", "")[:40],
                        "type": "Action",
                        "color": "#607D8B",
                    }
                    edges.append({
                        "from": rec["s"]["id"],
                        "to": rec["a"]["id"],
                        "label": "INVOLVES",
                    })

                # NEXT edge between steps
                if rec["next_s"] and rec["s"]:
                    edge_key = f"{rec['s']['id']}->{rec['next_s']['id']}"
                    if not any(e.get("_key") == edge_key for e in edges):
                        edges.append({
                            "_key": edge_key,
                            "from": rec["s"]["id"],
                            "to": rec["next_s"]["id"],
                            "label": "NEXT",
                            "style": "dashed",
                        })

            return {
                "nodes": list(nodes.values()),
                "edges": edges,
                "workflow_id": workflow_id,
            }

    def get_convergence_graph(self, session_id: str) -> dict:
        """Get convergence visualization: newbie actions vs expert steps."""
        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (n:Newbie)-[:ATTEMPTED]->(sess:Session {id: $sid})-[:FOLLOWING]->(w:Workflow)
                OPTIONAL MATCH (w)-[:HAS_STEP]->(s:Step)
                OPTIONAL MATCH (sess)-[:PERFORMED]->(na:NewbieAction)
                OPTIONAL MATCH (na)-[align:ALIGNS_WITH]->(s)
                OPTIONAL MATCH (na)-[div:DIVERGES_FROM]->(s)
                OPTIONAL MATCH (sess)-[:SCORED]->(c:ConvergenceScore)
                RETURN n, sess, w, s, na, align, div, c
                ORDER BY s.step_order
                """,
                sid=session_id,
            )

            nodes = {}
            edges = []

            for rec in result:
                # Expert step (green if matched, red if diverged)
                if rec["s"] and rec["s"]["id"] not in nodes:
                    has_align = rec["align"] is not None
                    has_div = rec["div"] is not None
                    color = "#4CAF50" if has_align and not has_div else "#F44336" if has_div else "#FF9800"

                    nodes[rec["s"]["id"]] = {
                        "id": rec["s"]["id"],
                        "label": f"Expert: {rec['s'].get('name', '')}",
                        "type": "ExpertStep",
                        "color": color,
                        "order": rec["s"].get("step_order", 0),
                    }

                # Newbie action
                if rec["na"] and rec["na"]["id"] not in nodes:
                    nodes[rec["na"]["id"]] = {
                        "id": rec["na"]["id"],
                        "label": f"Newbie: Step {rec['na'].get('step_number', '?')}",
                        "type": "NewbieAction",
                        "color": "#03A9F4",
                    }

                # Alignment edge (green)
                if rec["align"] and rec["na"] and rec["s"]:
                    edges.append({
                        "from": rec["na"]["id"],
                        "to": rec["s"]["id"],
                        "label": f"ALIGNS ({rec['align'].get('score', 0):.0%})",
                        "color": "#4CAF50",
                    })

                # Divergence edge (red)
                if rec["div"] and rec["na"] and rec["s"]:
                    edges.append({
                        "from": rec["na"]["id"],
                        "to": rec["s"]["id"],
                        "label": f"DIVERGES: {rec['div'].get('deviation', '')[:30]}",
                        "color": "#F44336",
                    })

            return {
                "nodes": list(nodes.values()),
                "edges": edges,
                "session_id": session_id,
            }

    def get_reasoning_chain(self, workflow_id: str) -> list:
        """Get the full reasoning chain for a workflow — the WHY behind every step."""
        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (w:Workflow {id: $wid})-[:HAS_STEP]->(s:Step)
                OPTIONAL MATCH (s)-[:DECIDED_BECAUSE]->(r:Reasoning)
                RETURN s.step_order AS step_number,
                       s.name AS step_name,
                       s.context AS context,
                       r.explanation AS reasoning
                ORDER BY s.step_order
                """,
                wid=workflow_id,
            )
            return [dict(rec) for rec in result]

    def get_all_convergence_scores(self, newbie_id: Optional[str] = None) -> list:
        """Get convergence scores over time (for Datadog dashboard)."""
        with self._driver.session() as session:
            if newbie_id:
                result = session.run(
                    """
                    MATCH (n:Newbie {id: $nid})-[:ATTEMPTED]->(s:Session)-[:SCORED]->(c:ConvergenceScore)
                    MATCH (s)-[:FOLLOWING]->(w:Workflow)
                    RETURN n.id AS newbie_id, s.id AS session_id,
                           w.name AS workflow_name, c.overall_score AS score,
                           c.timestamp AS timestamp
                    ORDER BY c.timestamp
                    """,
                    nid=newbie_id,
                )
            else:
                result = session.run(
                    """
                    MATCH (n:Newbie)-[:ATTEMPTED]->(s:Session)-[:SCORED]->(c:ConvergenceScore)
                    MATCH (s)-[:FOLLOWING]->(w:Workflow)
                    RETURN n.id AS newbie_id, s.id AS session_id,
                           w.name AS workflow_name, c.overall_score AS score,
                           c.timestamp AS timestamp
                    ORDER BY c.timestamp
                    """
                )
            return [dict(rec) for rec in result]
