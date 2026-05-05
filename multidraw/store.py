"""Three-tier filesystem store: Project → Test → Draw.

Layout:
    <base>/projects/<pid>/spec.json
    <base>/projects/<pid>/tests/<tid>/spec.json
    <base>/projects/<pid>/tests/<tid>/draws/<draw_id>.json
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from multidraw.models import DrawSummary, Project, Test


@dataclass(frozen=True)
class DrawLocator:
    """Where a draw lives. Used for cross-cutting lookups by run_id / draw_id."""
    project_id: str
    test_id: str
    draw_id: str


class ProjectStore:
    """Filesystem-backed store for projects, tests, and draws."""

    def __init__(self, base_dir: str | Path = ".multidraw") -> None:
        self.base_dir = Path(base_dir).expanduser()
        self._projects_dir = self.base_dir / "projects"
        self._projects_dir.mkdir(parents=True, exist_ok=True)
        self._locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)

    # ------------------------------------------------------------------
    # paths
    # ------------------------------------------------------------------

    def _project_dir(self, project_id: str) -> Path:
        return self._projects_dir / project_id

    def _project_spec(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "spec.json"

    def _tests_dir(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "tests"

    def _test_dir(self, project_id: str, test_id: str) -> Path:
        return self._tests_dir(project_id) / test_id

    def _test_spec(self, project_id: str, test_id: str) -> Path:
        return self._test_dir(project_id, test_id) / "spec.json"

    def _draws_dir(self, project_id: str, test_id: str) -> Path:
        return self._test_dir(project_id, test_id) / "draws"

    def _draw_path(self, project_id: str, test_id: str, draw_id: str) -> Path:
        return self._draws_dir(project_id, test_id) / f"{draw_id}.json"

    # ------------------------------------------------------------------
    # projects
    # ------------------------------------------------------------------

    def list_projects(self) -> list[Project]:
        out: list[Project] = []
        for spec_file in sorted(self._projects_dir.glob("*/spec.json")):
            try:
                out.append(Project.model_validate_json(spec_file.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return out

    def get_project(self, project_id: str) -> Project:
        path = self._project_spec(project_id)
        if not path.exists():
            raise KeyError(project_id)
        return Project.model_validate_json(path.read_text(encoding="utf-8"))

    def save_project(self, project: Project) -> Project:
        proj_dir = self._project_dir(project.id)
        proj_dir.mkdir(parents=True, exist_ok=True)
        self._tests_dir(project.id).mkdir(parents=True, exist_ok=True)
        with self._locks[f"project:{project.id}"]:
            self._project_spec(project.id).write_text(
                project.model_dump_json(indent=2),
                encoding="utf-8",
            )
        return project

    def delete_project(self, project_id: str) -> None:
        proj_dir = self._project_dir(project_id)
        if not proj_dir.exists():
            raise KeyError(project_id)
        with self._locks[f"project:{project_id}"]:
            for child in sorted(proj_dir.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink()
                else:
                    child.rmdir()
            proj_dir.rmdir()

    # ------------------------------------------------------------------
    # tests
    # ------------------------------------------------------------------

    def list_tests(self, project_id: str) -> list[Test]:
        tests_dir = self._tests_dir(project_id)
        if not tests_dir.exists():
            return []
        out: list[Test] = []
        for spec_file in sorted(tests_dir.glob("*/spec.json")):
            try:
                out.append(Test.model_validate_json(spec_file.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return out

    def get_test(self, project_id: str, test_id: str) -> Test:
        path = self._test_spec(project_id, test_id)
        if not path.exists():
            raise KeyError(f"{project_id}/{test_id}")
        return Test.model_validate_json(path.read_text(encoding="utf-8"))

    def save_test(self, test: Test) -> Test:
        if not self._project_spec(test.project_id).exists():
            raise KeyError(f"unknown project {test.project_id}")
        test_dir = self._test_dir(test.project_id, test.id)
        test_dir.mkdir(parents=True, exist_ok=True)
        self._draws_dir(test.project_id, test.id).mkdir(parents=True, exist_ok=True)
        with self._locks[f"test:{test.project_id}:{test.id}"]:
            self._test_spec(test.project_id, test.id).write_text(
                test.model_dump_json(indent=2),
                encoding="utf-8",
            )
        return test

    def delete_test(self, project_id: str, test_id: str) -> None:
        test_dir = self._test_dir(project_id, test_id)
        if not test_dir.exists():
            raise KeyError(f"{project_id}/{test_id}")
        with self._locks[f"test:{project_id}:{test_id}"]:
            for child in sorted(test_dir.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink()
                else:
                    child.rmdir()
            test_dir.rmdir()

    def find_test_by_id(self, test_id: str) -> tuple[Project, Test] | None:
        """Lookup by test_id only (test ids are unique within their project but
        the API may receive a raw test_id; we walk projects to find it)."""
        for project in self.list_projects():
            try:
                test = self.get_test(project.id, test_id)
            except KeyError:
                continue
            return project, test
        return None

    # ------------------------------------------------------------------
    # draws
    # ------------------------------------------------------------------

    def new_draw_id(self) -> str:
        return uuid4().hex[:12]

    def list_draws(self, project_id: str, test_id: str) -> list[DrawSummary]:
        draws_dir = self._draws_dir(project_id, test_id)
        if not draws_dir.exists():
            return []
        out: list[DrawSummary] = []
        for path in sorted(draws_dir.glob("*.json")):
            try:
                out.append(DrawSummary.model_validate_json(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        out.sort(key=lambda d: d.started_at, reverse=True)
        return out

    def get_draw(self, project_id: str, test_id: str, draw_id: str) -> DrawSummary:
        path = self._draw_path(project_id, test_id, draw_id)
        if not path.exists():
            raise KeyError(draw_id)
        return DrawSummary.model_validate_json(path.read_text(encoding="utf-8"))

    def save_draw(self, draw: DrawSummary) -> DrawSummary:
        path = self._draw_path(draw.project_id, draw.test_id, draw.draw_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._locks[f"draw:{draw.draw_id}"]:
            path.write_text(draw.model_dump_json(indent=2), encoding="utf-8")
        return draw

    def update_draw(
        self,
        project_id: str,
        test_id: str,
        draw_id: str,
        **changes,
    ) -> DrawSummary:
        draw = self.get_draw(project_id, test_id, draw_id)
        updated = draw.model_copy(update=changes)
        return self.save_draw(updated)

    def find_draw_by_id(self, draw_id: str) -> DrawSummary | None:
        """Walk projects/tests to locate a draw by its (globally-unique) id."""
        for project in self.list_projects():
            for test in self.list_tests(project.id):
                path = self._draw_path(project.id, test.id, draw_id)
                if path.exists():
                    return DrawSummary.model_validate_json(path.read_text(encoding="utf-8"))
        return None

    def find_draw_by_run(self, run_id: str) -> DrawSummary | None:
        for project in self.list_projects():
            for test in self.list_tests(project.id):
                for draw in self.list_draws(project.id, test.id):
                    if draw.run_id == run_id:
                        return draw
        return None

    def locate_draw(self, draw_id: str) -> DrawLocator | None:
        """Return the (pid, tid, did) tuple for a draw, useful for URLs."""
        for project in self.list_projects():
            for test in self.list_tests(project.id):
                if self._draw_path(project.id, test.id, draw_id).exists():
                    return DrawLocator(project.id, test.id, draw_id)
        return None
