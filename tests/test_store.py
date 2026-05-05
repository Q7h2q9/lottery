"""Three-tier filesystem store roundtrip tests."""

from __future__ import annotations

from pathlib import Path

from multidraw.models import (
    CancelPolicy,
    DrawState,
    DrawSummary,
    Project,
    Test,
)
from multidraw.store import ProjectStore


def _project(id: str = "demo") -> Project:
    return Project(id=id, name=id, base_prompt="solve x")


def _test(project_id: str = "demo", id: str = "t1", count: int = 2) -> Test:
    return Test(id=id, project_id=project_id, name=id, agent_count=count)


def _draw(project_id: str, test_id: str, draw_id: str, **kw) -> DrawSummary:
    base = dict(
        draw_id=draw_id,
        project_id=project_id,
        test_id=test_id,
        run_id="run-1",
        state=DrawState.QUEUED,
        started_at="2026-05-04T12:00:00+00:00",
        total_agents=2,
    )
    base.update(kw)
    return DrawSummary(**base)


def test_save_and_load_project(tmp_path: Path):
    s = ProjectStore(tmp_path)
    s.save_project(_project())
    restored = s.get_project("demo")
    assert restored.name == "demo"
    assert s.list_projects()[0].id == "demo"


def test_save_test_requires_project(tmp_path: Path):
    s = ProjectStore(tmp_path)
    import pytest
    with pytest.raises(KeyError):
        s.save_test(_test())


def test_save_and_load_test(tmp_path: Path):
    s = ProjectStore(tmp_path)
    s.save_project(_project())
    s.save_test(_test())
    assert s.get_test("demo", "t1").agent_count == 2
    assert [t.id for t in s.list_tests("demo")] == ["t1"]


def test_save_and_load_draw(tmp_path: Path):
    s = ProjectStore(tmp_path)
    s.save_project(_project())
    s.save_test(_test())
    d = _draw("demo", "t1", s.new_draw_id(), run_id="run-xyz")
    s.save_draw(d)
    assert s.get_draw("demo", "t1", d.draw_id).run_id == "run-xyz"
    assert s.find_draw_by_run("run-xyz").draw_id == d.draw_id
    assert s.find_draw_by_id(d.draw_id).draw_id == d.draw_id
    loc = s.locate_draw(d.draw_id)
    assert loc.project_id == "demo" and loc.test_id == "t1"


def test_update_draw_partial(tmp_path: Path):
    s = ProjectStore(tmp_path)
    s.save_project(_project())
    s.save_test(_test())
    d = _draw("demo", "t1", s.new_draw_id())
    s.save_draw(d)
    updated = s.update_draw(
        "demo", "t1", d.draw_id,
        state=DrawState.WON,
        winner_node_id="agent_001",
        winner_payload="flag{abc}",
    )
    assert updated.state == DrawState.WON
    assert updated.winner_payload == "flag{abc}"


def test_delete_project_cascades(tmp_path: Path):
    s = ProjectStore(tmp_path)
    s.save_project(_project())
    s.save_test(_test())
    s.save_draw(_draw("demo", "t1", "d1"))
    s.delete_project("demo")
    assert s.list_projects() == []
    assert not (tmp_path / "projects" / "demo").exists()


def test_delete_test_keeps_project(tmp_path: Path):
    s = ProjectStore(tmp_path)
    s.save_project(_project())
    s.save_test(_test())
    s.delete_test("demo", "t1")
    assert s.list_tests("demo") == []
    assert s.list_projects()[0].id == "demo"


def test_find_test_by_id(tmp_path: Path):
    s = ProjectStore(tmp_path)
    s.save_project(_project("p1"))
    s.save_project(_project("p2"))
    s.save_test(_test("p2", "found"))
    pair = s.find_test_by_id("found")
    assert pair is not None
    assert pair[0].id == "p2"
    assert pair[1].id == "found"
    assert s.find_test_by_id("missing") is None


def test_find_draw_by_id_returns_none_when_missing(tmp_path: Path):
    s = ProjectStore(tmp_path)
    assert s.find_draw_by_id("nope") is None
    assert s.locate_draw("nope") is None
