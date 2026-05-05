"""multidraw v2 CLI — project / test / draw management.

Web UI is the primary interface; the CLI is here for one-off tooling
(serve, dump-prompt, run a specific test from a saved project).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.console import Console
from rich.table import Table

from multidraw.compiler import expand_agents
from multidraw.models import DEFAULT_FORMAT_SPEC, Project, Test
from multidraw.runtime import Runtime
from multidraw.store import ProjectStore


app = typer.Typer(help="Race-to-finish multi-agent fleet, built on agentflow.")
console = Console()


def _store() -> ProjectStore:
    return ProjectStore(os.getenv("MULTIDRAW_DIR", ".multidraw"))


def _load_yaml_or_json(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        return yaml.safe_load(raw)
    return json.loads(raw)


# ----------------------------------------------------------------------
# scaffolding
# ----------------------------------------------------------------------


@app.command("new")
def new(
    output: Path = typer.Option(Path("project.yaml"), "-o", "--output"),
    kind: str = typer.Option("project", "--kind", help="project or test"),
) -> None:
    """Scaffold a project or test YAML."""
    if kind not in {"project", "test"}:
        console.print("[red]--kind must be 'project' or 'test'[/red]")
        raise typer.Exit(2)
    if output.exists():
        console.print(f"[red]refusing to overwrite {output}[/red]")
        raise typer.Exit(1)

    if kind == "project":
        template = """\
# multidraw project — 一个可重复利用的容器，下面挂多个 test。
# base_prompt 是自然语言任务描述，撰写 agent 会综合每个 test 的 hint 生成 prompt。
name: 我的任务
description: 一两句话简介。

base_prompt: |
  写清楚要解决什么问题、约束、输入输出格式、必要时让 agent 自验。
  不需要 jinja，自然语言即可。

# format_spec: 可选，覆盖默认。一般留空走 multidraw 内置默认。
# format_spec: |
#   ...

default_success_criteria:
  - kind: output_contains
    value: "ANSWER:"

working_dir: .
verbose_stream: false
"""
    else:
        template = """\
# multidraw test — 一种具体跑法，挂在某个 project 下。
project_id: my-project           # 必填：父 project 的 id
name: 五个 agent 抢答
description: ""

agent_count: 5
model: zimo/gpt-5.4
tools: read_only

# 可选：每个 agent 的个性化（hint 给撰写 agent 看；prompt_override 直接覆盖最终 prompt）
per_agent_overrides:
  - { hint: "暴力枚举" }
  - { hint: "双指针" }
  - { hint: "二分" }
  - { hint: "动态规划" }
  - { hint: "贪心" }

cancel_policy: immediate
concurrency: 5
retries: 1
timeout_seconds: 600

# manual_mode: true   # 如果想跳过撰写 agent 全手写 prompt
"""
    output.write_text(template, encoding="utf-8")
    console.print(f"[green]wrote[/green] {output} (kind={kind})")


@app.command("validate")
def validate(spec: Path) -> None:
    """Load a project or test yaml; print diagnostic preview."""
    data = _load_yaml_or_json(spec)
    if isinstance(data, dict) and "project_id" in data:
        test = Test.model_validate(data)
        console.print(
            f"[green]ok[/green] test={test.id!r} project_id={test.project_id!r} "
            f"agents={test.agent_count}"
        )
    else:
        project = Project.model_validate(data)
        console.print(f"[green]ok[/green] project={project.id!r}")


# ----------------------------------------------------------------------
# save / list
# ----------------------------------------------------------------------


@app.command("save-project")
def save_project(spec: Path) -> None:
    project = Project.model_validate(_load_yaml_or_json(spec))
    _store().save_project(project)
    console.print(f"[green]saved project[/green] {project.id}")


@app.command("save-test")
def save_test(spec: Path) -> None:
    data = _load_yaml_or_json(spec)
    test = Test.model_validate(data)
    s = _store()
    try:
        s.get_project(test.project_id)
    except KeyError:
        console.print(f"[red]unknown project {test.project_id}[/red] — save the project first")
        raise typer.Exit(2)
    s.save_test(test)
    console.print(f"[green]saved test[/green] {test.project_id}/{test.id} ({test.agent_count} agents)")


@app.command("list")
def list_projects() -> None:
    s = _store()
    projects = s.list_projects()
    if not projects:
        console.print("(no projects yet — create one in the web UI or via `multidraw save-project`)")
        return
    table = Table(title="Projects + Tests")
    table.add_column("project")
    table.add_column("test")
    table.add_column("agents", justify="right")
    table.add_column("approval")
    for p in projects:
        tests = s.list_tests(p.id)
        if not tests:
            table.add_row(p.id, "[dim](no tests)[/dim]", "—", "—")
            continue
        for t in tests:
            label = "approved" if t.approval_status == "approved" else "draft"
            table.add_row(p.id, t.id, str(t.agent_count), label)
    console.print(table)


# ----------------------------------------------------------------------
# synthesis / run
# ----------------------------------------------------------------------


@app.command("synthesize")
def synthesize(
    project_id: str = typer.Argument(...),
    test_id: str = typer.Argument(...),
    timeout_seconds: int = typer.Option(180, "--timeout"),
) -> None:
    """Run the synthesis agent for a saved test; persist resolved_prompts."""
    runtime = Runtime(project_store=_store())

    async def _run() -> None:
        test = await runtime.synthesize_test(project_id, test_id, timeout_seconds=timeout_seconds)
        console.print(f"[green]synthesized[/green] {len(test.resolved_prompts)} prompts → draft")
        for i, p in enumerate(test.resolved_prompts):
            head = p.splitlines()[0][:80] if p else ""
            console.print(f"  [{i}] {head}")

    try:
        asyncio.run(_run())
    except KeyError as exc:
        console.print(f"[red]not found: {exc}[/red]")
        raise typer.Exit(2)


@app.command("approve")
def approve(project_id: str = typer.Argument(...), test_id: str = typer.Argument(...)) -> None:
    runtime = Runtime(project_store=_store())
    try:
        test = runtime.approve_test(project_id, test_id)
    except KeyError as exc:
        console.print(f"[red]not found: {exc}[/red]")
        raise typer.Exit(2)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2)
    console.print(f"[green]approved[/green] {test.project_id}/{test.id}")


@app.command("run")
def run_draw(
    project_id: str = typer.Argument(...),
    test_id: str = typer.Argument(...),
) -> None:
    """Start a new draw for a saved+approved test. Blocks until the draw finishes."""
    s = _store()
    runtime = Runtime(project_store=s)

    async def _run() -> None:
        try:
            project = s.get_project(project_id)
            test = s.get_test(project_id, test_id)
        except KeyError as exc:
            console.print(f"[red]not found: {exc}[/red]")
            raise typer.Exit(2)

        try:
            summary = await runtime.start_draw(project, test)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            console.print(
                "[dim]hint: run `multidraw synthesize` and `multidraw approve` first.[/dim]"
            )
            raise typer.Exit(2)

        console.print(
            f"[cyan]draw[/cyan] {summary.draw_id} "
            f"[dim](run_id={summary.run_id}, agents={summary.total_agents})[/dim]"
        )

        async with _SpinnerStatus(console) as status:
            while True:
                fresh = s.get_draw(project_id, test_id, summary.draw_id)
                status.set(f"draw {summary.draw_id} → {fresh.state.value}")
                if fresh.finished_at:
                    break
                await asyncio.sleep(1.0)

        final = s.get_draw(project_id, test_id, summary.draw_id)
        if final.winner_node_id:
            console.print(f"[bold green]WON[/bold green] by {final.winner_node_id}")
            console.print("[dim]payload:[/dim]")
            console.print(final.winner_payload or "(empty)")
        else:
            console.print(f"[yellow]{final.state.value}[/yellow] — no winner")

    asyncio.run(_run())


# ----------------------------------------------------------------------
# serve
# ----------------------------------------------------------------------


@app.command("migrate")
def migrate(
    base_dir: Path = typer.Option(Path(".multidraw"), "--base", help="multidraw 根目录"),
) -> None:
    """One-shot migrate v1 (flat Project) → v2 (Project / Test / Draw)."""
    from multidraw.migration import migrate_all

    results = migrate_all(base_dir)
    if not results:
        console.print("(no projects found to migrate)")
        return
    for r in results:
        if r.created_project:
            console.print(
                f"[green]migrated[/green] {r.project_id}  → tests: {r.test_ids}  "
                f"(dropped {r.dropped_draws} old draws)"
            )
            for note in r.notes:
                console.print(f"  [yellow]note[/yellow]: {note}")
        else:
            console.print(f"[dim]skip[/dim] {r.project_id}  ({'; '.join(r.notes) or 'no change'})")


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8765),
    reload: bool = typer.Option(False),
) -> None:
    import uvicorn

    uvicorn.run(
        "multidraw.api:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
    )


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


class _SpinnerStatus:
    def __init__(self, console: Console) -> None:
        self._console = console
        self._status = None

    async def __aenter__(self):
        from rich.status import Status

        self._status = Status("starting…", console=self._console)
        self._status.__enter__()
        return self

    async def __aexit__(self, *exc_info):
        if self._status is not None:
            self._status.__exit__(*exc_info)

    def set(self, message: str) -> None:
        if self._status is not None:
            self._status.update(message)


if __name__ == "__main__":
    app()
