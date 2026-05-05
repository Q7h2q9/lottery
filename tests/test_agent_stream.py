"""Agent stream tests: replay + tail of pi event log."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from multidraw.agent_stream import (
    DEFAULT_FORWARDED_TYPES,
    artifact_log_path,
    event_summary,
    parse_pi_event_line,
    should_forward,
    stream_agent_events,
)


def _write_events(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(ev, ensure_ascii=False) for ev in events) + "\n",
        encoding="utf-8",
    )


def test_parse_event_line_skips_blank_and_garbage():
    assert parse_pi_event_line("") is None
    assert parse_pi_event_line("not json") is None
    assert parse_pi_event_line("[1, 2]") is None  # not a dict
    assert parse_pi_event_line('{"type":"x"}') == {"type": "x"}


def test_should_forward_filters_by_default():
    assert should_forward("agent_end", verbose=False)
    assert should_forward("message_update", verbose=False)
    assert should_forward("tool_execution_start", verbose=False)
    # filtered when not verbose
    assert not should_forward("turn_start", verbose=False)
    assert not should_forward("session", verbose=False)


def test_should_forward_verbose_passes_everything():
    assert should_forward("turn_start", verbose=True)
    assert should_forward("session", verbose=True)


def test_default_forwarded_types_match_observed_pi_events():
    """Sanity check: the filter set covers all known UI-relevant types."""
    for t in ["agent_start", "agent_end", "message_start", "message_update",
              "message_end", "tool_execution_start", "tool_execution_end"]:
        assert t in DEFAULT_FORWARDED_TYPES


def test_event_summary_extracts_text_from_message_update():
    ev = {
        "type": "message_update",
        "assistantMessageEvent": {"text": "hello world"},
    }
    assert event_summary(ev) == "hello world"


def test_event_summary_extracts_tool_name():
    assert event_summary({"type": "tool_execution_start", "toolName": "write"}) == "→ write"
    assert event_summary({"type": "tool_execution_end", "toolName": "bash"}) == "← bash"


def test_event_summary_returns_none_for_lifecycle():
    assert event_summary({"type": "agent_start"}) is None


@pytest.mark.asyncio
async def test_stream_replays_existing_then_terminates_on_agent_end(tmp_path: Path):
    log = tmp_path / "stdout.log"
    _write_events(log, [
        {"type": "agent_start"},
        {"type": "message_update", "assistantMessageEvent": {"text": "hi"}},
        {"type": "tool_execution_start", "toolName": "write", "args": {}},
        {"type": "tool_execution_end", "toolName": "write", "result": "ok"},
        {"type": "message_end", "message": {}},
        {"type": "agent_end"},
    ])

    received = []
    async for ev in stream_agent_events(log, agent_id="agent_a", verbose=False):
        received.append(ev)

    assert [e.type for e in received] == [
        "agent_start", "message_update",
        "tool_execution_start", "tool_execution_end",
        "message_end", "agent_end",
    ]
    assert all(e.replay for e in received)


@pytest.mark.asyncio
async def test_stream_filters_out_thinking_and_session(tmp_path: Path):
    log = tmp_path / "stdout.log"
    _write_events(log, [
        {"type": "session", "id": "s1"},
        {"type": "agent_start"},
        {"type": "thinking", "text": "internal"},
        {"type": "message_update", "assistantMessageEvent": {"text": "shown"}},
        {"type": "agent_end"},
    ])
    received = [e async for e in stream_agent_events(log, agent_id="a", verbose=False)]
    types = [e.type for e in received]
    assert "session" not in types
    assert "thinking" not in types
    assert "agent_start" in types
    assert "message_update" in types


@pytest.mark.asyncio
async def test_stream_verbose_includes_thinking(tmp_path: Path):
    log = tmp_path / "stdout.log"
    _write_events(log, [
        {"type": "thinking", "text": "internal"},
        {"type": "message_update", "assistantMessageEvent": {"text": "shown"}},
        {"type": "agent_end"},
    ])
    received = [e async for e in stream_agent_events(log, agent_id="a", verbose=True)]
    types = [e.type for e in received]
    assert "thinking" in types


@pytest.mark.asyncio
async def test_stream_returns_quietly_when_log_never_appears(tmp_path: Path):
    """Bounded wait: if file never shows up, generator finishes without erroring."""
    missing = tmp_path / "never.log"
    received = [e async for e in stream_agent_events(missing, agent_id="a")]
    assert received == []


@pytest.mark.asyncio
async def test_stream_quiesces_after_run_terminal(tmp_path: Path):
    """Cancelled agents never emit agent_end; the stream should still terminate
    when the run is reported terminal and no new lines arrive."""
    log = tmp_path / "stdout.log"
    _write_events(log, [
        {"type": "agent_start"},
        {"type": "message_update", "assistantMessageEvent": {"text": "partial"}},
    ])

    # Compress the grace period to keep the test fast.
    import multidraw.agent_stream as mod
    orig_grace = mod._QUIESCENCE_GRACE_SECONDS
    orig_poll = mod._TAIL_POLL_SECONDS
    mod._QUIESCENCE_GRACE_SECONDS = 0.2
    mod._TAIL_POLL_SECONDS = 0.05
    try:
        received = []
        async with asyncio.timeout(3.0):
            async for ev in stream_agent_events(
                log, agent_id="a", verbose=False, is_run_terminal=lambda: True,
            ):
                received.append(ev)
    finally:
        mod._QUIESCENCE_GRACE_SECONDS = orig_grace
        mod._TAIL_POLL_SECONDS = orig_poll

    types = [e.type for e in received]
    assert "agent_start" in types
    assert "message_update" in types
    # No agent_end was ever written; the stream still ended.


def test_artifact_log_path_shape():
    p = artifact_log_path(".agentflow/runs", "RUN", "node_001")
    assert p == Path(".agentflow/runs/RUN/artifacts/node_001/stdout.log")
