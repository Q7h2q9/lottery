"""One-shot migration from v1 (flat Project) to v2 (Project / Test / Draw).

v1 spec (single spec.json per project): Project with embedded fleet[FleetEntry],
prompt jinja, draws/ subdir under the project.

v2 layout: Project (base_prompt natural-language) + Test (one per v1 FleetEntry,
agent-count = entry.count, per_agent_overrides from entry.variables) + draws
nested under each Test.

We keep the v1 prompt verbatim as base_prompt — that prompt may still be valid
natural language, even though it had jinja markers. Users can re-edit if they
want to drop the {{ }} braces. Old draws are dropped (they reference agentflow
runs and don't fit the new test_id schema).

Backups: each v1 spec.json is preserved as ``spec.v1.json`` next to the new
spec.json so nothing is lost.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from multidraw.models import (
    AgentOverride,
    CancelPolicy,
    Project,
    SuccessCriterion,
    Test,
)
from multidraw.store import ProjectStore


@dataclass
class MigrationResult:
    project_id: str
    created_project: bool
    test_ids: list[str]
    dropped_draws: int
    notes: list[str]


def looks_like_v1(spec: dict) -> bool:
    return "fleet" in spec and "base_prompt" not in spec


def _v1_entry_to_test(
    project_id: str,
    entry: dict,
    *,
    entry_index: int,
    v1: dict,
    fallback_prompt: str,
) -> Test:
    """Map one v1 FleetEntry to one v2 Test."""
    count = int(entry.get("count", 1))
    variables = entry.get("variables")
    overrides: list[AgentOverride] = []
    if isinstance(variables, list):
        for var_dict in variables[:count]:
            if isinstance(var_dict, dict):
                hint = var_dict.get("strategy") or _summarize_dict(var_dict)
                overrides.append(AgentOverride(hint=hint))
    elif isinstance(variables, dict):
        # shared dict: same hint for everyone
        hint = variables.get("strategy") or _summarize_dict(variables)
        overrides = [AgentOverride(hint=hint) for _ in range(count)]
    while len(overrides) < count:
        overrides.append(AgentOverride())

    entry_prompt = entry.get("prompt") or fallback_prompt
    name = (
        f"{entry.get('model', 'agent')} × {count}"
        if entry_index == 0
        else f"entry-{entry_index}"
    )

    success_criteria_raw = entry.get("success_criteria")
    success_criteria = (
        [SuccessCriterion.model_validate(c) for c in success_criteria_raw]
        if isinstance(success_criteria_raw, list)
        else None
    )

    return Test(
        project_id=project_id,
        name=name,
        description=f"migrated from v1 fleet entry #{entry_index}",
        model=entry.get("model", "zimo/gpt-5.4"),
        tools=_normalize_tools(entry.get("tools")),
        extra_args=list(entry.get("extra_args") or []),
        agent_count=count,
        per_agent_overrides=overrides,
        resolved_prompts=[entry_prompt] * count,  # kept verbatim; user may regenerate
        approval_status="draft",
        success_criteria=success_criteria,
        cancel_policy=CancelPolicy(v1.get("cancel_policy", "immediate")),
        cancel_delay_seconds=int(v1.get("cancel_delay_seconds", 0)),
        concurrency=int(v1.get("concurrency", 20)),
        retries=int(v1.get("retries", 1)),
        retry_backoff_seconds=int(v1.get("retry_backoff_seconds", 3)),
        timeout_seconds=int(v1.get("timeout_seconds", 600)),
    )


def _normalize_tools(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        v = value.strip()
        return "read_write" if v.lower() == "read_write" else "read_only"
    return "read_only"


def _summarize_dict(d: dict) -> str:
    parts = [f"{k}={v}" for k, v in d.items() if isinstance(k, str)]
    return ", ".join(parts)[:100]


def migrate_project_dir(proj_dir: Path, store: ProjectStore) -> MigrationResult:
    spec_path = proj_dir / "spec.json"
    if not spec_path.exists():
        return MigrationResult(proj_dir.name, False, [], 0, ["no spec.json"])

    raw = json.loads(spec_path.read_text(encoding="utf-8"))
    if not looks_like_v1(raw):
        return MigrationResult(raw.get("id", proj_dir.name), False, [], 0, ["already v2"])

    # Back up v1 spec next to the new one before any writes.
    backup = proj_dir / "spec.v1.json"
    if not backup.exists():
        backup.write_text(spec_path.read_text(encoding="utf-8"), encoding="utf-8")

    project = Project(
        id=raw.get("id", proj_dir.name),
        name=raw.get("name", proj_dir.name),
        description=raw.get("description"),
        base_prompt=raw.get("prompt", ""),
        format_spec=None,
        default_success_criteria=[
            SuccessCriterion.model_validate(c)
            for c in raw.get("default_success_criteria", [])
        ] or [SuccessCriterion(kind="output_contains", value="ANSWER:")],
        verbose_stream=False,
        working_dir=raw.get("working_dir", "."),
        created_at=raw.get("created_at") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    store.save_project(project)

    test_ids: list[str] = []
    fleet = raw.get("fleet") or []
    for i, entry in enumerate(fleet):
        if not isinstance(entry, dict):
            continue
        test = _v1_entry_to_test(
            project.id,
            entry,
            entry_index=i,
            v1=raw,
            fallback_prompt=raw.get("prompt", ""),
        )
        store.save_test(test)
        test_ids.append(test.id)

    # Drop old draws — they reference run_ids without a v2 test_id and would
    # confuse the new `find_draw_by_id` flow. Stored agentflow artifacts under
    # ``.agentflow/runs`` are untouched, so users can still grep history.
    old_draws_dir = proj_dir / "draws"
    dropped = 0
    if old_draws_dir.exists():
        for f in old_draws_dir.glob("*.json"):
            f.unlink()
            dropped += 1
        old_draws_dir.rmdir()

    notes = []
    if raw.get("prompt", "").find("{{") != -1:
        notes.append("base_prompt 含 jinja 占位符；v2 不再渲染，可在 UI 中改为自然语言")

    return MigrationResult(project.id, True, test_ids, dropped, notes)


def migrate_all(base_dir: str | Path = ".multidraw") -> list[MigrationResult]:
    base = Path(base_dir)
    projects_dir = base / "projects"
    if not projects_dir.exists():
        return []
    store = ProjectStore(base)
    out: list[MigrationResult] = []
    for proj_dir in sorted(projects_dir.iterdir()):
        if not proj_dir.is_dir():
            continue
        out.append(migrate_project_dir(proj_dir, store))
    return out
