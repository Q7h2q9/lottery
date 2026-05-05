"""Synthesis tests — meta-prompt building + output parsing (no real pi calls)."""

from __future__ import annotations

import json

import pytest

from multidraw.models import AgentOverride, Project, Test
from multidraw.synthesis import (
    SynthesisError,
    SynthesisInputs,
    _build_meta_prompt,
    _extract_prompts,
    _merge_overrides,
    _planned_node_id,
)


def _project_test(agent_count: int = 3, **overrides) -> tuple[Project, Test]:
    p = Project(name="demo", base_prompt="solve x", format_spec="format me")
    t = Test(project_id=p.id, name="t", agent_count=agent_count, **overrides)
    return p, t


def test_meta_prompt_includes_base_prompt_and_format():
    p, t = _project_test()
    meta = _build_meta_prompt(SynthesisInputs(project=p, test=t))
    assert "solve x" in meta
    assert "format me" in meta
    assert str(t.agent_count) in meta


def test_meta_prompt_lists_agents_with_hints():
    p, t = _project_test(
        agent_count=3,
        per_agent_overrides=[AgentOverride(hint="DP"), AgentOverride(), AgentOverride(hint="greedy")],
    )
    meta = _build_meta_prompt(SynthesisInputs(project=p, test=t))
    assert "DP" in meta
    assert "greedy" in meta


def test_meta_prompt_mentions_skipped_overridden_indexes():
    p, t = _project_test(
        agent_count=3,
        per_agent_overrides=[AgentOverride(prompt_override="WIRED"), AgentOverride(), AgentOverride()],
    )
    meta = _build_meta_prompt(SynthesisInputs(project=p, test=t))
    assert "[0]" in meta or "0" in meta
    assert "prompt_override" in meta


def test_extract_prompts_parses_fenced_json():
    text = """\
some preamble
<<<BEGIN_PROMPTS_JSON>>>
[
  "agent0 prompt that is sufficiently long to pass the length check",
  "agent1 prompt that is sufficiently long to pass the length check",
  "agent2 prompt that is sufficiently long to pass the length check"
]
<<<END_PROMPTS_JSON>>>
trailing junk"""
    out = _extract_prompts(text, expected_count=3)
    assert len(out) == 3
    assert "agent0" in out[0]


def test_extract_prompts_strips_code_fence_inside_markers():
    text = """\
<<<BEGIN_PROMPTS_JSON>>>
```json
["this prompt is long enough to pass the placeholder check",
 "this second prompt is long enough to pass the placeholder check"]
```
<<<END_PROMPTS_JSON>>>"""
    out = _extract_prompts(text, expected_count=2)
    assert len(out) == 2


def test_extract_prompts_rejects_missing_markers():
    with pytest.raises(SynthesisError):
        _extract_prompts("no markers here", expected_count=2)


def test_extract_prompts_rejects_wrong_count():
    text = "<<<BEGIN_PROMPTS_JSON>>>\n[\"only-one\"]\n<<<END_PROMPTS_JSON>>>"
    with pytest.raises(SynthesisError):
        _extract_prompts(text, expected_count=3)


def test_extract_prompts_rejects_non_string_items():
    text = '<<<BEGIN_PROMPTS_JSON>>>\n[1, 2]\n<<<END_PROMPTS_JSON>>>'
    with pytest.raises(SynthesisError):
        _extract_prompts(text, expected_count=2)


def test_extract_prompts_rejects_placeholder_strings():
    """Real bug from the wild: synthesis agent returned ['None','None',...]."""
    text = '<<<BEGIN_PROMPTS_JSON>>>\n["None", "None", "None"]\n<<<END_PROMPTS_JSON>>>'
    with pytest.raises(SynthesisError, match="placeholder"):
        _extract_prompts(text, expected_count=3)


def test_extract_prompts_rejects_empty_strings():
    text = '<<<BEGIN_PROMPTS_JSON>>>\n["", ""]\n<<<END_PROMPTS_JSON>>>'
    with pytest.raises(SynthesisError, match="empty"):
        _extract_prompts(text, expected_count=2)


def test_extract_prompts_rejects_too_short_strings():
    text = '<<<BEGIN_PROMPTS_JSON>>>\n["hi", "ok"]\n<<<END_PROMPTS_JSON>>>'
    with pytest.raises(SynthesisError, match="too short"):
        _extract_prompts(text, expected_count=2)


def test_merge_overrides_splices_prompt_override():
    _p, t = _project_test(
        agent_count=3,
        per_agent_overrides=[AgentOverride(prompt_override="WIRED"), AgentOverride(), AgentOverride()],
    )
    generated = ["genA", "genB", "genC"]
    final = _merge_overrides(t, generated)
    assert final == ["WIRED", "genB", "genC"]


def test_planned_node_id_matches_compiler_format():
    """The synthesis-side id helper must agree with compiler.planned_node_id."""
    from multidraw.compiler import planned_node_id

    for total in (5, 10, 100):
        for i in (0, total - 1):
            assert _planned_node_id("zimo/gpt-5.4", i, total) == planned_node_id(
                "zimo/gpt-5.4", i, total
            )
