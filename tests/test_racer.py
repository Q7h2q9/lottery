"""Racer behavior tests with a fake agentflow store + orchestrator."""

from __future__ import annotations

import asyncio
import queue
import sys
import types
from dataclasses import dataclass

import pytest


# --- agentflow stub: racer imports `agentflow.specs.RunStatus` lazily ---
if "agentflow" not in sys.modules:
    af_pkg = types.ModuleType("agentflow")
    af_specs = types.ModuleType("agentflow.specs")

    class RunStatus(str):
        QUEUED = "queued"
        PENDING = "pending"
        RUNNING = "running"
        CANCELLING = "cancelling"
        CANCELLED = "cancelled"
        COMPLETED = "completed"
        FAILED = "failed"

    af_specs.RunStatus = RunStatus
    af_pkg.specs = af_specs
    sys.modules["agentflow"] = af_pkg
    sys.modules["agentflow.specs"] = af_specs


from multidraw.models import CancelPolicy, Project, SuccessCriterion, Test  # noqa: E402
from multidraw.racer import race_to_finish  # noqa: E402


@dataclass
class _FakeNode:
    output: str | None
    success: bool
    final_response: str | None = None


@dataclass
class _FakeRun:
    id: str
    status: str
    nodes: dict[str, _FakeNode]


@dataclass
class _FakeEvent:
    type: str
    node_id: str | None = None


class _FakeStore:
    def __init__(self):
        self.runs: dict[str, _FakeRun] = {}
        self.queues: dict[str, queue.Queue[_FakeEvent]] = {}

    def get_run(self, run_id):
        return self.runs[run_id]

    async def subscribe(self, run_id):
        q = self.queues.setdefault(run_id, queue.Queue())
        return q

    async def unsubscribe(self, run_id, q):
        pass

    def push(self, run_id, event):
        self.queues.setdefault(run_id, queue.Queue()).put(event)


class _FakeOrchestrator:
    def __init__(self):
        self.cancelled: list[str] = []

    async def cancel(self, run_id):
        self.cancelled.append(run_id)


def _project_test(
    *,
    cancel_policy: CancelPolicy = CancelPolicy.IMMEDIATE,
    delay: int = 0,
) -> tuple[Project, Test]:
    project = Project(
        name="demo",
        base_prompt="x",
        default_success_criteria=[SuccessCriterion(kind="output_regex", value=r"flag\{[^}]+\}")],
    )
    test = Test(
        project_id=project.id,
        name="t",
        agent_count=3,
        cancel_policy=cancel_policy,
        cancel_delay_seconds=delay,
    )
    return project, test


@pytest.mark.asyncio
async def test_racer_cancels_on_first_success_immediate():
    store = _FakeStore()
    orch = _FakeOrchestrator()
    run_id = "r1"
    store.runs[run_id] = _FakeRun(
        id=run_id, status="running",
        nodes={
            "a": _FakeNode(output=None, success=False),
            "b": _FakeNode(output="prefix flag{xyz123} suffix", success=True),
            "c": _FakeNode(output=None, success=False),
        },
    )

    async def fire():
        await asyncio.sleep(0.01)
        store.push(run_id, _FakeEvent(type="node_started", node_id="b"))
        store.push(run_id, _FakeEvent(type="node_completed", node_id="b"))

    asyncio.create_task(fire())
    project, test = _project_test()
    result = await race_to_finish(store=store, orchestrator=orch, run_id=run_id, project=project, test=test)

    assert result.winner_node_id == "b"
    assert result.payload == "flag{xyz123}"
    assert result.cancelled is True
    assert orch.cancelled == [run_id]


@pytest.mark.asyncio
async def test_racer_does_not_cancel_when_policy_none():
    store = _FakeStore()
    orch = _FakeOrchestrator()
    run_id = "r2"
    store.runs[run_id] = _FakeRun(
        id=run_id, status="running",
        nodes={"x": _FakeNode(output="found flag{abc}", success=True)},
    )

    async def fire():
        await asyncio.sleep(0.01)
        store.push(run_id, _FakeEvent(type="node_completed", node_id="x"))

    asyncio.create_task(fire())
    project, test = _project_test(cancel_policy=CancelPolicy.NONE)
    result = await race_to_finish(store=store, orchestrator=orch, run_id=run_id, project=project, test=test)

    assert result.winner_node_id == "x"
    assert result.cancelled is False
    assert orch.cancelled == []


@pytest.mark.asyncio
async def test_racer_returns_none_when_run_completes_without_winner():
    store = _FakeStore()
    orch = _FakeOrchestrator()
    run_id = "r3"
    store.runs[run_id] = _FakeRun(
        id=run_id, status="failed",
        nodes={
            "a": _FakeNode(output="garbage", success=False),
            "b": _FakeNode(output=None, success=False),
        },
    )

    async def fire():
        await asyncio.sleep(0.01)
        store.push(run_id, _FakeEvent(type="node_completed", node_id="a"))
        store.push(run_id, _FakeEvent(type="node_completed", node_id="b"))
        store.push(run_id, _FakeEvent(type="run_completed"))

    asyncio.create_task(fire())
    project, test = _project_test()
    result = await race_to_finish(store=store, orchestrator=orch, run_id=run_id, project=project, test=test)

    assert result.winner_node_id is None
    assert result.cancelled is False
    assert orch.cancelled == []


@pytest.mark.asyncio
async def test_racer_skips_failed_completions():
    """A node_completed event with success=False must not trigger a win."""
    store = _FakeStore()
    orch = _FakeOrchestrator()
    run_id = "r4"
    store.runs[run_id] = _FakeRun(
        id=run_id, status="running",
        nodes={
            "a": _FakeNode(output="no match here", success=False),
            "b": _FakeNode(output="flag{ok}", success=True),
        },
    )

    async def fire():
        await asyncio.sleep(0.01)
        store.push(run_id, _FakeEvent(type="node_completed", node_id="a"))
        await asyncio.sleep(0.01)
        store.push(run_id, _FakeEvent(type="node_completed", node_id="b"))

    asyncio.create_task(fire())
    project, test = _project_test()
    result = await race_to_finish(store=store, orchestrator=orch, run_id=run_id, project=project, test=test)
    assert result.winner_node_id == "b"
    assert orch.cancelled == [run_id]
