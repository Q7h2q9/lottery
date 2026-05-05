"""Tests for the v1 → v2 migration."""

from __future__ import annotations

import json
from pathlib import Path

from multidraw.migration import looks_like_v1, migrate_all, migrate_project_dir
from multidraw.store import ProjectStore


def _v1_spec(**overrides) -> dict:
    base = dict(
        id="legacy",
        name="Legacy Project",
        description="from v1",
        prompt="solve {{ task }}",
        fleet=[
            dict(model="zimo/gpt-5.4", count=3, tools="read_only",
                 variables=[{"strategy": "DP"}, {"strategy": "greedy"}, {"strategy": "brute"}]),
        ],
        default_success_criteria=[{"kind": "output_contains", "value": "ANSWER:"}],
        cancel_policy="immediate",
        cancel_delay_seconds=0,
        concurrency=5,
        retries=1,
        retry_backoff_seconds=3,
        timeout_seconds=300,
        working_dir="./challenges/legacy",
    )
    base.update(overrides)
    return base


def test_looks_like_v1_detects_old_shape():
    assert looks_like_v1(_v1_spec())
    assert not looks_like_v1({"name": "x", "base_prompt": "y"})


def _seed_v1(base: Path, project_id: str, spec: dict) -> Path:
    proj = base / "projects" / project_id
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    draws = proj / "draws"
    draws.mkdir(exist_ok=True)
    (draws / "old1.json").write_text("{}", encoding="utf-8")
    (draws / "old2.json").write_text("{}", encoding="utf-8")
    return proj


def test_migrate_creates_project_and_one_test_per_entry(tmp_path: Path):
    _seed_v1(tmp_path, "legacy", _v1_spec())
    results = migrate_all(tmp_path)
    assert len(results) == 1
    r = results[0]
    assert r.created_project
    assert r.test_ids == ["zimo-gpt-5-4-3"]
    assert r.dropped_draws == 2

    s = ProjectStore(tmp_path)
    project = s.get_project("legacy")
    assert project.base_prompt == "solve {{ task }}"
    assert project.working_dir == "./challenges/legacy"
    test = s.get_test("legacy", "zimo-gpt-5-4-3")
    assert test.agent_count == 3
    assert test.model == "zimo/gpt-5.4"
    assert test.tools == "read_only"
    assert [ov.hint for ov in test.per_agent_overrides] == ["DP", "greedy", "brute"]
    # resolved prompts pre-populated with the v1 prompt verbatim
    assert all(p == "solve {{ task }}" for p in test.resolved_prompts)
    assert test.approval_status == "draft"


def test_migrate_backs_up_v1_spec(tmp_path: Path):
    proj = _seed_v1(tmp_path, "legacy", _v1_spec())
    migrate_all(tmp_path)
    assert (proj / "spec.v1.json").exists()


def test_migrate_skips_already_v2(tmp_path: Path):
    proj = _seed_v1(tmp_path, "v2-already", {"id": "v2-already", "name": "n", "base_prompt": "x"})
    results = migrate_all(tmp_path)
    assert any("already v2" in (r.notes[0] if r.notes else "") for r in results)


def test_migrate_with_shared_variables_dict(tmp_path: Path):
    spec = _v1_spec(id="shared-legacy")
    spec["fleet"][0]["variables"] = {"strategy": "shared"}
    spec["fleet"][0]["count"] = 2
    _seed_v1(tmp_path, "shared-legacy", spec)
    migrate_all(tmp_path)
    s = ProjectStore(tmp_path)
    test = s.get_test("shared-legacy", "zimo-gpt-5-4-2")
    assert [ov.hint for ov in test.per_agent_overrides] == ["shared", "shared"]


def test_migrate_drops_old_draws(tmp_path: Path):
    proj = _seed_v1(tmp_path, "legacy", _v1_spec())
    migrate_all(tmp_path)
    assert not (proj / "draws").exists()
