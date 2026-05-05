"""Compiler tests: (Project, Test) → agentflow PipelineSpec preview shape."""

from __future__ import annotations

import pytest

from multidraw.compiler import expand_agents, planned_node_id, model_slug
from multidraw.models import AgentOverride, Project, SuccessCriterion, Test


def _project(**overrides) -> Project:
    base = dict(name="demo", base_prompt="solve x")
    base.update(overrides)
    return Project(**base)


def _test(project_id: str = "demo", agent_count: int = 3, **overrides) -> Test:
    base = dict(project_id=project_id, name="t", agent_count=agent_count)
    base.update(overrides)
    return Test(**base)


def test_planned_node_id_uses_zero_padded_index():
    assert planned_node_id("zimo/gpt-5.4", 0, 10) == "zimo_gpt_5_4_000"
    assert planned_node_id("zimo/gpt-5.4", 7, 10) == "zimo_gpt_5_4_007"


def test_model_slug_strips_punctuation():
    assert model_slug("openai/gpt-5-codex") == "openai_gpt_5_codex"


def test_expand_agents_count_matches():
    p = _project()
    t = _test(agent_count=5, model="zimo/gpt-5.4")
    t.resolved_prompts = [f"p{i}" for i in range(5)]
    agents = expand_agents(p, t)
    assert len(agents) == 5
    assert agents[0]["id"] == "zimo_gpt_5_4_000"


def test_expand_agents_unique_ids():
    p = _project()
    t = _test(agent_count=10)
    t.resolved_prompts = [str(i) for i in range(10)]
    ids = [a["id"] for a in expand_agents(p, t)]
    assert len(set(ids)) == 10


def test_expand_agents_picks_resolved_prompt_per_index():
    p = _project()
    t = _test(agent_count=3)
    t.resolved_prompts = ["P0", "P1", "P2"]
    agents = expand_agents(p, t)
    assert [a["prompt"] for a in agents] == ["P0", "P1", "P2"]


def test_expand_agents_placeholder_when_unresolved():
    p = _project()
    t = _test(agent_count=2)
    agents = expand_agents(p, t)
    assert "not yet synthesized" in agents[0]["prompt"]


def test_expand_agents_mismatch_raises():
    p = _project()
    t = _test(agent_count=2)
    t.resolved_prompts = ["only-one"]
    with pytest.raises(ValueError):
        expand_agents(p, t)


def test_expand_agents_marks_override_flag():
    p = _project()
    t = _test(agent_count=2,
              per_agent_overrides=[AgentOverride(prompt_override="WIRED"), AgentOverride(hint="x")])
    t.resolved_prompts = ["WIRED", "synthesized"]
    agents = expand_agents(p, t)
    assert agents[0]["has_override"] is True
    assert agents[1]["has_override"] is False
    assert agents[1]["hint"] == "x"


def test_expand_agents_uses_test_success_criteria_override():
    p = _project()
    t = _test(agent_count=1,
              success_criteria=[SuccessCriterion(kind="output_contains", value="LOCAL")])
    t.resolved_prompts = ["x"]
    agents = expand_agents(p, t)
    assert agents[0]["success_criteria"][0]["value"] == "LOCAL"


def test_expand_agents_falls_back_to_project_success_criteria():
    p = _project(default_success_criteria=[SuccessCriterion(kind="output_contains", value="WIN")])
    t = _test(agent_count=1)
    t.resolved_prompts = ["x"]
    agents = expand_agents(p, t)
    assert agents[0]["success_criteria"][0]["value"] == "WIN"
