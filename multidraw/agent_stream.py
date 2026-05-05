"""Tail a single agent's pi event log and yield filtered events.

agentflow already persists every pi event to:
    .agentflow/runs/<run_id>/artifacts/<node_id>/stdout.log

Each line is a JSON object with a ``type`` field. We re-emit the events the
UI cares about — assistant message stream + tool calls + lifecycle markers —
and (when ``verbose`` is on) the full firehose.

Public entry point: ``async for ev in stream_agent_events(...)``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator


_log = logging.getLogger("multidraw.agent_stream")


# Default ("精选") events forwarded when verbose_stream is False.
DEFAULT_FORWARDED_TYPES: frozenset[str] = frozenset({
    "agent_start",
    "agent_end",
    "message_start",
    "message_update",
    "message_end",
    "tool_execution_start",
    "tool_execution_end",
})

TERMINAL_TYPES: frozenset[str] = frozenset({"agent_end"})

# Poll cadence when actively tailing.
_TAIL_POLL_SECONDS = 0.2

# How long to keep tailing after the producer side appears idle, before we
# decide the agent is done even without an explicit ``agent_end``.
_QUIESCENCE_GRACE_SECONDS = 5.0


@dataclass
class StreamEvent:
    """One event delivered to the SSE consumer."""

    agent_id: str
    type: str
    replay: bool
    raw: dict
    seq: int


def parse_pi_event_line(line: str) -> dict | None:
    """Parse a single stdout.log line, returning None for blanks/garbage."""
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def should_forward(event_type: str, *, verbose: bool) -> bool:
    if verbose:
        return True
    return event_type in DEFAULT_FORWARDED_TYPES


def _select_summary_text(event: dict) -> str | None:
    """Best-effort: pull a short representative text snippet from an event.

    Used for the draw-level "card preview" summaries. None when this event
    type doesn't yield user-visible text.
    """
    et = event.get("type")
    if et in {"message_update", "message_end", "message_start"}:
        msg = event.get("message")
        if isinstance(msg, dict):
            content = msg.get("content")
            if isinstance(content, list):
                for chunk in reversed(content):
                    if isinstance(chunk, dict) and chunk.get("type") == "text":
                        text = chunk.get("text")
                        if isinstance(text, str) and text.strip():
                            return text
        ame = event.get("assistantMessageEvent")
        if isinstance(ame, dict):
            text = ame.get("text")
            if isinstance(text, str) and text.strip():
                return text
    if et == "tool_execution_start":
        name = event.get("toolName")
        if isinstance(name, str):
            return f"→ {name}"
    if et == "tool_execution_end":
        name = event.get("toolName")
        if isinstance(name, str):
            return f"← {name}"
    return None


def event_summary(event: dict) -> str | None:
    """Public wrapper for extracting a short preview text from any event."""
    return _select_summary_text(event)


async def _read_new_lines(handle, buffer: str) -> tuple[str, list[str]]:
    """Read whatever new bytes are available; return (carry, complete_lines)."""
    chunk = await asyncio.to_thread(handle.read)
    if not chunk:
        return buffer, []
    buffer += chunk
    lines = buffer.split("\n")
    carry = lines[-1]
    return carry, lines[:-1]


async def stream_agent_events(
    log_path: Path,
    *,
    agent_id: str,
    verbose: bool = False,
    is_run_terminal: callable | None = None,
    stop_event: asyncio.Event | None = None,
) -> AsyncIterator[StreamEvent]:
    """Yield filtered pi events for one agent.

    Replays everything currently in ``log_path``, then tails for new lines
    until the agent emits ``agent_end``, the run becomes terminal (if a
    callback is supplied) and goes quiescent, or ``stop_event`` is set.
    """
    seq = 0

    # Wait briefly for the file to exist (it's created when the agent starts).
    for _ in range(50):  # up to ~5s
        if log_path.exists():
            break
        if stop_event is not None and stop_event.is_set():
            return
        await asyncio.sleep(0.1)
    if not log_path.exists():
        _log.info("agent_stream: %s never appeared", log_path)
        return

    handle = log_path.open("r", encoding="utf-8", errors="replace")
    try:
        # ---- 1. replay existing bytes ----
        buffer = ""
        carry, lines = await _read_new_lines(handle, buffer)
        replay_terminal = False
        for raw_line in lines:
            event = parse_pi_event_line(raw_line)
            if event is None:
                continue
            et = event.get("type", "")
            if not should_forward(et, verbose=verbose):
                continue
            seq += 1
            yield StreamEvent(agent_id=agent_id, type=et, replay=True, raw=event, seq=seq)
            if et in TERMINAL_TYPES:
                replay_terminal = True

        if replay_terminal:
            return

        # ---- 2. tail mode ----
        idle_started_at: float | None = None
        loop = asyncio.get_running_loop()
        while True:
            if stop_event is not None and stop_event.is_set():
                return

            carry, lines = await _read_new_lines(handle, carry)
            had_new = False
            for raw_line in lines:
                event = parse_pi_event_line(raw_line)
                if event is None:
                    continue
                et = event.get("type", "")
                if should_forward(et, verbose=verbose):
                    seq += 1
                    yield StreamEvent(agent_id=agent_id, type=et, replay=False, raw=event, seq=seq)
                    had_new = True
                if et in TERMINAL_TYPES:
                    return

            if had_new:
                idle_started_at = None
            else:
                # If the run is over and we've been idle a while, the agent was
                # likely cancelled mid-flight (no agent_end will ever arrive).
                if is_run_terminal is not None and is_run_terminal():
                    now = loop.time()
                    if idle_started_at is None:
                        idle_started_at = now
                    elif now - idle_started_at >= _QUIESCENCE_GRACE_SECONDS:
                        return

            await asyncio.sleep(_TAIL_POLL_SECONDS)
    finally:
        handle.close()


def artifact_log_path(runs_dir: str | Path, run_id: str, agent_id: str) -> Path:
    """Resolve agentflow's ``stdout.log`` for one node of one run."""
    return Path(runs_dir) / run_id / "artifacts" / agent_id / "stdout.log"
