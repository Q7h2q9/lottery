"""Compile a (Project, Test) pair into an agentflow PipelineSpec.

v2: no jinja, no fanout, no FleetEntry. Each agent's prompt is just
``test.resolved_prompts[i]`` (produced upstream by synthesis or hand-written).
"""

from __future__ import annotations

import re
from typing import Any

from multidraw.models import Project, SuccessCriterion, Test


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def model_slug(model: str) -> str:
    return _SLUG_RE.sub("_", model.lower()).strip("_")[:40] or "agent"


def planned_node_id(model: str, index: int, total: int) -> str:
    """Stable agent id used in node ids and previews."""
    width = max(3, len(str(total - 1)))
    return f"{model_slug(model)}_{index:0{width}d}"


def expand_agents(project: Project, test: Test) -> list[dict[str, Any]]:
    """Materialize the agents this Test will spawn — for previews / UI."""
    if test.resolved_prompts and len(test.resolved_prompts) != test.agent_count:
        raise ValueError(
            f"resolved_prompts length {len(test.resolved_prompts)} != agent_count {test.agent_count}"
        )
    out: list[dict[str, Any]] = []
    criteria = test.effective_success_criteria(project)
    for i in range(test.agent_count):
        prompt = (
            test.resolved_prompts[i]
            if test.resolved_prompts
            else f"(prompt not yet synthesized for agent {i})"
        )
        out.append(
            {
                "id": planned_node_id(test.model, i, test.agent_count),
                "index": i,
                "model": test.model,
                "tools": test.tools,
                "prompt": prompt,
                "hint": test.override_for(i).hint,
                "has_override": bool(test.override_for(i).prompt_override),
                "success_criteria": [c.to_agentflow() for c in criteria],
                "extra_args": list(test.extra_args),
            }
        )
    return out


def compile_to_agentflow(project: Project, test: Test) -> "PipelineSpec":
    """Build an agentflow PipelineSpec for one Test of one Project."""

    from agentflow.specs import AgentKind, NodeSpec, PipelineSpec

    if not test.resolved_prompts:
        raise ValueError(f"test {test.id} has no resolved_prompts; synthesize/approve first")
    if len(test.resolved_prompts) != test.agent_count:
        raise ValueError(
            f"resolved_prompts length {len(test.resolved_prompts)} != agent_count {test.agent_count}"
        )

    agents = expand_agents(project, test)
    nodes: list[NodeSpec] = []
    for record in agents:
        node_payload: dict[str, Any] = {
            "id": record["id"],
            "agent": AgentKind.PI,
            "prompt": record["prompt"],
            "depends_on": [],
            "model": record["model"],
            "tools": record["tools"],
            "success_criteria": record["success_criteria"],
            "retries": test.retries,
            "retry_backoff_seconds": test.retry_backoff_seconds,
            "timeout_seconds": test.timeout_seconds,
        }
        if record["extra_args"]:
            node_payload["extra_args"] = record["extra_args"]
        nodes.append(NodeSpec.model_validate(node_payload))

    pipeline_payload = {
        "name": f"multidraw:{project.id}:{test.id}",
        "description": f"{project.name} / {test.name}",
        "working_dir": project.working_dir,
        "concurrency": test.concurrency,
        "fail_fast": False,
        "max_iterations": 1,
        "nodes": [node.model_dump(mode="json") for node in nodes],
    }
    return PipelineSpec.model_validate(pipeline_payload)
