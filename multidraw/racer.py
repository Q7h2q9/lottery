"""Race-to-finish watcher: cancel the run when one agent reports success.

Subscribe to agentflow's SSE stream; on the first ``node_completed`` event
where the underlying node has ``success=True``, cancel the run (immediately,
after a delay, or never — per the Test's cancel_policy).
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

from multidraw.models import CancelPolicy, Project, SuccessCriterion, Test


_log = logging.getLogger("multidraw.racer")


@dataclass
class RaceResult:
    winner_node_id: str | None
    payload: str | None
    cancelled: bool


def _output_regex_pattern(criteria: list[SuccessCriterion]) -> re.Pattern[str] | None:
    for crit in criteria:
        if crit.kind == "output_regex" and crit.value:
            flags = 0 if crit.case_sensitive else re.IGNORECASE
            try:
                return re.compile(crit.value, flags)
            except re.error:
                return None
    return None


async def race_to_finish(
    *,
    store: "RunStore",
    orchestrator: "Orchestrator",
    run_id: str,
    project: Project,
    test: Test,
) -> RaceResult:
    queue = await store.subscribe(run_id)
    criteria = test.effective_success_criteria(project)
    payload_regex = _output_regex_pattern(criteria)
    delay = test.cancel_delay_seconds if test.cancel_policy == CancelPolicy.DELAY else 0

    try:
        while True:
            event = await asyncio.to_thread(queue.get)
            if event.type == "node_completed":
                run = store.get_run(run_id)
                node = run.nodes.get(event.node_id)
                if node is None or not node.success:
                    continue
                payload = _extract_payload(node, payload_regex)
                _log.info(
                    "draw run=%s: winner=%s payload=%r",
                    run_id, event.node_id, payload[:80] if payload else None,
                )

                if test.cancel_policy == CancelPolicy.NONE:
                    return RaceResult(winner_node_id=event.node_id, payload=payload, cancelled=False)

                if delay > 0:
                    await asyncio.sleep(delay)

                from agentflow.specs import RunStatus

                fresh = store.get_run(run_id)
                if fresh.status in {RunStatus.RUNNING, RunStatus.PENDING, RunStatus.QUEUED}:
                    await orchestrator.cancel(run_id)
                    return RaceResult(
                        winner_node_id=event.node_id,
                        payload=payload,
                        cancelled=True,
                    )
                return RaceResult(winner_node_id=event.node_id, payload=payload, cancelled=False)

            if event.type == "run_completed":
                run = store.get_run(run_id)
                for node_id, node in run.nodes.items():
                    if node.success:
                        return RaceResult(
                            winner_node_id=node_id,
                            payload=_extract_payload(node, payload_regex),
                            cancelled=False,
                        )
                return RaceResult(winner_node_id=None, payload=None, cancelled=False)
    finally:
        await store.unsubscribe(run_id, queue)


def _extract_payload(node: "NodeResult", payload_regex: re.Pattern[str] | None) -> str | None:
    text = (node.output or node.final_response or "") if node else ""
    if not text:
        return None
    if payload_regex is not None:
        m = payload_regex.search(text)
        if m:
            return m.group(0)
    return text.strip()[:4000]
