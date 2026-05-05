"""Tests for v2 models (Project / Test / Draw)."""

from __future__ import annotations

import pytest

from multidraw.models import (
    AgentOverride,
    CancelPolicy,
    DEFAULT_FORMAT_SPEC,
    Project,
    SuccessCriterion,
    Test,
)


def test_project_id_derived_from_name():
    p = Project(name="My Cool Task!", base_prompt="solve x")
    assert p.id == "my-cool-task"


def test_project_explicit_id_wins():
    p = Project(id="custom-id", name="anything", base_prompt="x")
    assert p.id == "custom-id"


def test_project_requires_name_or_id():
    with pytest.raises(ValueError):
        Project(base_prompt="x")


def test_project_default_format_spec_used():
    p = Project(name="t", base_prompt="x")
    assert p.format_spec is None
    assert p.effective_format_spec() == DEFAULT_FORMAT_SPEC


def test_project_custom_format_spec_overrides_default():
    p = Project(name="t", base_prompt="x", format_spec="custom rules")
    assert p.effective_format_spec() == "custom rules"


def test_project_default_success_criteria():
    p = Project(name="t", base_prompt="x")
    assert len(p.default_success_criteria) == 1
    assert p.default_success_criteria[0].kind == "output_contains"
    assert p.default_success_criteria[0].value == "ANSWER:"


# --- Test (the model, not pytest.Test) ---


def test_test_id_derived_from_name():
    t = Test(project_id="proj", name="Five Agents", agent_count=5)
    assert t.id == "five-agents"


def test_test_runnable_requires_approval_and_prompts():
    t = Test(project_id="p", name="t", agent_count=3)
    assert not t.is_runnable()
    t.resolved_prompts = ["a", "b", "c"]
    assert not t.is_runnable()
    t.approval_status = "approved"
    assert t.is_runnable()


def test_test_drift_detection():
    t = Test(project_id="p", name="t", agent_count=2)
    assert not t.is_drifted()
    t.resolved_prompts = ["a", "b"]
    t.approval_status = "approved"
    t.approved_config_hash = t.config_hash()
    assert not t.is_drifted()
    t.agent_count = 3
    assert t.is_drifted()


def test_test_drift_on_override_change():
    t = Test(
        project_id="p", name="t", agent_count=2,
        per_agent_overrides=[AgentOverride(hint="a"), AgentOverride()],
    )
    t.resolved_prompts = ["x", "y"]
    t.approval_status = "approved"
    t.approved_config_hash = t.config_hash()
    assert not t.is_drifted()

    t.per_agent_overrides[0] = AgentOverride(hint="b")
    assert t.is_drifted()


def test_test_override_for_returns_empty_when_short():
    t = Test(
        project_id="p", name="t", agent_count=5,
        per_agent_overrides=[AgentOverride(hint="x")],
    )
    assert t.override_for(0).hint == "x"
    assert t.override_for(3).is_empty()


def test_test_effective_success_criteria_falls_back_to_project():
    project = Project(
        name="p", base_prompt="x",
        default_success_criteria=[SuccessCriterion(kind="output_contains", value="WIN")],
    )
    t = Test(project_id=project.id, name="t", agent_count=1)
    assert t.effective_success_criteria(project)[0].value == "WIN"


def test_test_effective_success_criteria_test_override_wins():
    project = Project(name="p", base_prompt="x")
    t = Test(
        project_id=project.id, name="t", agent_count=1,
        success_criteria=[SuccessCriterion(kind="output_contains", value="LOCAL")],
    )
    assert t.effective_success_criteria(project)[0].value == "LOCAL"


# --- SuccessCriterion ---


def test_output_regex_compiles_to_contains_hint():
    c = SuccessCriterion(kind="output_regex", value="flag\\{[^}]+\\}")
    af = c.to_agentflow()
    assert af["kind"] == "output_contains"
    assert "flag" in af["value"]


def test_file_exists_passes_path():
    c = SuccessCriterion(kind="file_exists", path="solve.py")
    af = c.to_agentflow()
    assert af["kind"] == "file_exists"
    assert af["path"] == "solve.py"


def test_agent_override_is_empty():
    assert AgentOverride().is_empty()
    assert not AgentOverride(hint="x").is_empty()
    assert not AgentOverride(prompt_override="y").is_empty()
