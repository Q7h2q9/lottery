"""Process-wide multidraw runtime — synthesis + draw lifecycle + agent streaming.

The runtime owns one agentflow Orchestrator + RunStore for the whole process.
Three responsibilities:
  1. Synthesis: call the synthesis agent for a Test → fill resolved_prompts.
  2. Approval: flip Test.approval_status with config-hash bookkeeping.
  3. Draws:    submit the compiled pipeline to agentflow, watch for the winner,
              update DrawSummary state.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from multidraw.compiler import compile_to_agentflow
from multidraw.models import (
    AgentOverride,
    DrawState,
    DrawSummary,
    Project,
    Test,
)
from multidraw.racer import RaceResult, race_to_finish
from multidraw.store import ProjectStore
from multidraw.synthesis import synthesize_prompts


_log = logging.getLogger("multidraw.runtime")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class _ActiveDraw:
    draw_id: str
    project_id: str
    test_id: str
    run_id: str
    task: asyncio.Task[RaceResult]


@dataclass
class Runtime:
    project_store: ProjectStore
    runs_dir: str = ".agentflow/runs"
    max_concurrent_runs: int = 8

    _af_store: Any = field(default=None, init=False, repr=False)
    _orchestrator: Any = field(default=None, init=False, repr=False)
    _active: dict[str, _ActiveDraw] = field(default_factory=dict, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    # ------------------------------------------------------------------
    # agentflow lazy init
    # ------------------------------------------------------------------

    def _ensure_agentflow(self) -> None:
        if self._orchestrator is not None:
            return
        from agentflow.orchestrator import Orchestrator
        from agentflow.store import RunStore

        self._af_store = RunStore(self.runs_dir)
        self._orchestrator = Orchestrator(
            store=self._af_store,
            max_concurrent_runs=self.max_concurrent_runs,
        )

    @property
    def af_store(self) -> Any:
        self._ensure_agentflow()
        return self._af_store

    @property
    def orchestrator(self) -> Any:
        self._ensure_agentflow()
        return self._orchestrator

    def is_run_terminal(self, run_id: str) -> bool:
        """Cheap polling probe used by agent_stream tail loops."""
        try:
            from agentflow.specs import RunStatus

            run = self.af_store.get_run(run_id)
        except Exception:  # noqa: BLE001 — defensive: returns False on any error
            return False
        return run.status in {RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED}

    # ------------------------------------------------------------------
    # synthesis & approval
    # ------------------------------------------------------------------

    async def synthesize_test(
        self,
        project_id: str,
        test_id: str,
        *,
        timeout_seconds: int = 180,
    ) -> Test:
        project = self.project_store.get_project(project_id)
        test = self.project_store.get_test(project_id, test_id)

        if test.manual_mode:
            raise ValueError("manual_mode is set; synthesis is bypassed for this Test")

        prompts = await synthesize_prompts(project, test, timeout_seconds=timeout_seconds)
        if len(prompts) != test.agent_count:
            raise RuntimeError(
                f"synthesis returned {len(prompts)} prompts, expected {test.agent_count}"
            )
        updated = test.model_copy(
            update={
                "resolved_prompts": prompts,
                "approval_status": "draft",
                "last_synthesized_at": _utcnow(),
            }
        )
        return self.project_store.save_test(updated)

    def set_resolved_prompts(
        self,
        project_id: str,
        test_id: str,
        prompts: list[str],
    ) -> Test:
        """Manual-mode path: user pastes prompts directly. Always lands as draft."""
        test = self.project_store.get_test(project_id, test_id)
        if len(prompts) != test.agent_count:
            raise ValueError(
                f"got {len(prompts)} prompts, expected {test.agent_count}"
            )
        updated = test.model_copy(
            update={
                "resolved_prompts": list(prompts),
                "approval_status": "draft",
                "last_synthesized_at": _utcnow(),
            }
        )
        return self.project_store.save_test(updated)

    def approve_test(self, project_id: str, test_id: str) -> Test:
        test = self.project_store.get_test(project_id, test_id)
        if not test.resolved_prompts or len(test.resolved_prompts) != test.agent_count:
            raise ValueError("cannot approve: resolved_prompts incomplete")
        updated = test.model_copy(
            update={
                "approval_status": "approved",
                "approved_config_hash": test.config_hash(),
                "last_approved_at": _utcnow(),
            }
        )
        return self.project_store.save_test(updated)

    def revoke_approval(self, project_id: str, test_id: str) -> Test:
        test = self.project_store.get_test(project_id, test_id)
        updated = test.model_copy(
            update={
                "approval_status": "draft",
                "approved_config_hash": None,
                "last_approved_at": None,
            }
        )
        return self.project_store.save_test(updated)

    # ------------------------------------------------------------------
    # draws
    # ------------------------------------------------------------------

    async def start_draw(self, project: Project, test: Test) -> DrawSummary:
        if not test.is_runnable():
            raise ValueError(
                f"test {test.id} is not runnable: approval_status={test.approval_status}, "
                f"resolved_prompts={len(test.resolved_prompts)}/{test.agent_count}"
            )

        self._ensure_agentflow()
        pipeline = compile_to_agentflow(project, test)
        run = await self._orchestrator.submit(pipeline)
        draw_id = self.project_store.new_draw_id()
        summary = DrawSummary(
            draw_id=draw_id,
            project_id=project.id,
            test_id=test.id,
            run_id=run.id,
            state=DrawState.QUEUED,
            started_at=_utcnow(),
            total_agents=test.agent_count,
            cancel_policy=test.cancel_policy,
            cancel_delay_seconds=test.cancel_delay_seconds,
            prompts_snapshot=list(test.resolved_prompts),
        )
        self.project_store.save_draw(summary)

        task = asyncio.create_task(self._watch_draw(project, test, summary))
        async with self._lock:
            self._active[draw_id] = _ActiveDraw(
                draw_id=draw_id,
                project_id=project.id,
                test_id=test.id,
                run_id=run.id,
                task=task,
            )
        return summary

    async def _watch_draw(
        self,
        project: Project,
        test: Test,
        summary: DrawSummary,
    ) -> RaceResult:
        try:
            self.project_store.update_draw(
                summary.project_id,
                summary.test_id,
                summary.draw_id,
                state=DrawState.RUNNING,
            )
            result = await race_to_finish(
                store=self._af_store,
                orchestrator=self._orchestrator,
                run_id=summary.run_id,
                project=project,
                test=test,
            )
            try:
                await self._orchestrator.wait(summary.run_id, timeout=60)
            except (TimeoutError, asyncio.TimeoutError):
                _log.warning("draw %s: orchestrator wait timed out", summary.draw_id)

            state = _final_state(result, self._af_store, summary.run_id)
            self.project_store.update_draw(
                summary.project_id,
                summary.test_id,
                summary.draw_id,
                state=state,
                finished_at=_utcnow(),
                winner_node_id=result.winner_node_id,
                winner_payload=result.payload,
            )
            return result
        except Exception:
            _log.exception("draw %s watcher crashed", summary.draw_id)
            self.project_store.update_draw(
                summary.project_id,
                summary.test_id,
                summary.draw_id,
                state=DrawState.FAILED,
                finished_at=_utcnow(),
            )
            raise
        finally:
            async with self._lock:
                self._active.pop(summary.draw_id, None)

    async def cancel_draw(self, draw_id: str) -> DrawSummary:
        async with self._lock:
            active = self._active.get(draw_id)
        locator = self.project_store.locate_draw(draw_id)
        if locator is None:
            raise KeyError(draw_id)
        if active is not None:
            await self.orchestrator.cancel(active.run_id)
        return self.project_store.get_draw(locator.project_id, locator.test_id, draw_id)


def _final_state(result: RaceResult, af_store: Any, run_id: str) -> DrawState:
    from agentflow.specs import RunStatus

    if result.winner_node_id is not None:
        return DrawState.WON
    record = af_store.get_run(run_id)
    if record.status == RunStatus.CANCELLED:
        return DrawState.CANCELLED
    if record.status == RunStatus.FAILED:
        return DrawState.FAILED
    return DrawState.EXHAUSTED
