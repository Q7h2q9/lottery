"""FastAPI app: pages + JSON API + SSE streams (draw-level + per-agent)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from multidraw.agent_stream import (
    artifact_log_path,
    event_summary,
    stream_agent_events,
)
from multidraw.compiler import expand_agents, planned_node_id
from multidraw.models import (
    DEFAULT_FORMAT_SPEC,
    AgentOverride,
    DrawSummary,
    Project,
    Test,
)
from multidraw.runtime import Runtime
from multidraw.store import ProjectStore
from multidraw.synthesis import SynthesisError


_log = logging.getLogger("multidraw.api")
_WEB_DIR = Path(__file__).parent / "web"


def create_app(
    *,
    project_store: ProjectStore | None = None,
    runtime: Runtime | None = None,
) -> FastAPI:
    project_store = project_store or ProjectStore(os.getenv("MULTIDRAW_DIR", ".multidraw"))
    runtime = runtime or Runtime(project_store=project_store)

    app = FastAPI(title="multidraw", version="0.2.0")
    app.state.project_store = project_store
    app.state.runtime = runtime

    templates = Jinja2Templates(directory=str(_WEB_DIR / "templates"))
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR / "static")), name="static")

    # ==================================================================
    # HTML pages
    # ==================================================================

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        projects = project_store.list_projects()
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "projects": projects,
                "default_format_spec": DEFAULT_FORMAT_SPEC,
            },
        )

    @app.get("/projects/{project_id}", response_class=HTMLResponse)
    async def project_page(project_id: str, request: Request) -> HTMLResponse:
        try:
            project = project_store.get_project(project_id)
        except KeyError as exc:
            raise HTTPException(404, f"unknown project {project_id}") from exc
        tests = project_store.list_tests(project_id)
        return templates.TemplateResponse(
            request=request,
            name="project.html",
            context={
                "project": project,
                "tests": tests,
                "default_format_spec": DEFAULT_FORMAT_SPEC,
            },
        )

    @app.get("/projects/{project_id}/tests/{test_id}", response_class=HTMLResponse)
    async def test_page(project_id: str, test_id: str, request: Request) -> HTMLResponse:
        try:
            project = project_store.get_project(project_id)
            test = project_store.get_test(project_id, test_id)
        except KeyError as exc:
            raise HTTPException(404, f"unknown {exc}") from exc
        draws = project_store.list_draws(project_id, test_id)
        agents = expand_agents(project, test)
        return templates.TemplateResponse(
            request=request,
            name="test.html",
            context={
                "project": project,
                "test": test,
                "draws": draws,
                "agents": agents,
                "drifted": test.is_drifted(),
            },
        )

    @app.get("/draws/{draw_id}", response_class=HTMLResponse)
    async def draw_page(draw_id: str, request: Request) -> HTMLResponse:
        draw, project, test = _resolve_draw(project_store, draw_id)
        # Recompose the agents view from the draw's prompts_snapshot so even
        # if the test has changed since, the page shows what was actually run.
        agents = []
        for i in range(draw.total_agents):
            prompt = (
                draw.prompts_snapshot[i]
                if i < len(draw.prompts_snapshot)
                else "(prompt snapshot missing)"
            )
            agents.append(
                {
                    "id": planned_node_id(test.model, i, draw.total_agents),
                    "index": i,
                    "model": test.model,
                    "prompt": prompt,
                }
            )
        return templates.TemplateResponse(
            request=request,
            name="draw.html",
            context={"project": project, "test": test, "draw": draw, "agents": agents},
        )

    # ==================================================================
    # JSON: projects
    # ==================================================================

    @app.get("/api/projects")
    async def list_projects() -> JSONResponse:
        return JSONResponse([p.model_dump(mode="json") for p in project_store.list_projects()])

    @app.post("/api/projects")
    async def create_project(payload: dict) -> JSONResponse:
        project = Project.model_validate(payload)
        project_store.save_project(project)
        return JSONResponse(project.model_dump(mode="json"))

    @app.get("/api/projects/{project_id}")
    async def get_project(project_id: str) -> JSONResponse:
        try:
            project = project_store.get_project(project_id)
        except KeyError as exc:
            raise HTTPException(404, f"unknown project {project_id}") from exc
        return JSONResponse(project.model_dump(mode="json"))

    @app.put("/api/projects/{project_id}")
    async def update_project(project_id: str, payload: dict) -> JSONResponse:
        try:
            existing = project_store.get_project(project_id)
        except KeyError as exc:
            raise HTTPException(404, f"unknown project {project_id}") from exc
        # Allow partial updates: merge into existing dict.
        merged = existing.model_dump()
        merged.update(payload)
        merged["id"] = project_id  # never let id change here
        project = Project.model_validate(merged)
        project_store.save_project(project)
        return JSONResponse(project.model_dump(mode="json"))

    @app.delete("/api/projects/{project_id}")
    async def delete_project(project_id: str) -> JSONResponse:
        try:
            project_store.delete_project(project_id)
        except KeyError as exc:
            raise HTTPException(404, f"unknown project {project_id}") from exc
        return JSONResponse({"ok": True})

    # ==================================================================
    # JSON: tests
    # ==================================================================

    @app.get("/api/projects/{project_id}/tests")
    async def list_tests(project_id: str) -> JSONResponse:
        return JSONResponse([t.model_dump(mode="json") for t in project_store.list_tests(project_id)])

    @app.post("/api/projects/{project_id}/tests")
    async def create_test(project_id: str, payload: dict) -> JSONResponse:
        try:
            project_store.get_project(project_id)
        except KeyError as exc:
            raise HTTPException(404, f"unknown project {project_id}") from exc
        payload = dict(payload)
        payload["project_id"] = project_id
        test = Test.model_validate(payload)
        project_store.save_test(test)
        return JSONResponse(test.model_dump(mode="json"))

    @app.get("/api/projects/{project_id}/tests/{test_id}")
    async def get_test(project_id: str, test_id: str) -> JSONResponse:
        try:
            test = project_store.get_test(project_id, test_id)
        except KeyError as exc:
            raise HTTPException(404, f"unknown test {test_id}") from exc
        body = test.model_dump(mode="json")
        body["is_drifted"] = test.is_drifted()
        body["is_runnable"] = test.is_runnable()
        return JSONResponse(body)

    @app.put("/api/projects/{project_id}/tests/{test_id}")
    async def update_test(project_id: str, test_id: str, payload: dict) -> JSONResponse:
        try:
            existing = project_store.get_test(project_id, test_id)
        except KeyError as exc:
            raise HTTPException(404, f"unknown test {test_id}") from exc
        merged = existing.model_dump()
        # Updating these would corrupt the run history; ignore them in PUT.
        merged.update({k: v for k, v in payload.items()
                       if k not in ("project_id", "id", "resolved_prompts",
                                    "approval_status", "approved_config_hash",
                                    "last_synthesized_at", "last_approved_at")})
        merged["project_id"] = project_id
        merged["id"] = test_id
        test = Test.model_validate(merged)
        project_store.save_test(test)
        return JSONResponse(test.model_dump(mode="json"))

    @app.delete("/api/projects/{project_id}/tests/{test_id}")
    async def delete_test(project_id: str, test_id: str) -> JSONResponse:
        try:
            project_store.delete_test(project_id, test_id)
        except KeyError as exc:
            raise HTTPException(404, f"unknown test {test_id}") from exc
        return JSONResponse({"ok": True})

    @app.post("/api/projects/{project_id}/tests/{test_id}/synthesize")
    async def synthesize_test(project_id: str, test_id: str, payload: dict | None = None) -> JSONResponse:
        timeout = (payload or {}).get("timeout_seconds", 180)
        try:
            test = await runtime.synthesize_test(project_id, test_id, timeout_seconds=timeout)
        except KeyError as exc:
            raise HTTPException(404, f"unknown test {exc}") from exc
        except SynthesisError as exc:
            raise HTTPException(502, f"synthesis failed: {exc}") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return JSONResponse(test.model_dump(mode="json"))

    @app.post("/api/projects/{project_id}/tests/{test_id}/manual-prompts")
    async def set_manual_prompts(project_id: str, test_id: str, payload: dict) -> JSONResponse:
        prompts = payload.get("prompts")
        if not isinstance(prompts, list) or not all(isinstance(p, str) for p in prompts):
            raise HTTPException(400, "expected payload.prompts: list[str]")
        try:
            test = runtime.set_resolved_prompts(project_id, test_id, prompts)
        except KeyError as exc:
            raise HTTPException(404, f"unknown test {exc}") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return JSONResponse(test.model_dump(mode="json"))

    @app.post("/api/projects/{project_id}/tests/{test_id}/approve")
    async def approve_test(project_id: str, test_id: str) -> JSONResponse:
        try:
            test = runtime.approve_test(project_id, test_id)
        except KeyError as exc:
            raise HTTPException(404, f"unknown test {exc}") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return JSONResponse(test.model_dump(mode="json"))

    @app.post("/api/projects/{project_id}/tests/{test_id}/revoke")
    async def revoke_test(project_id: str, test_id: str) -> JSONResponse:
        try:
            test = runtime.revoke_approval(project_id, test_id)
        except KeyError as exc:
            raise HTTPException(404, f"unknown test {exc}") from exc
        return JSONResponse(test.model_dump(mode="json"))

    @app.get("/api/projects/{project_id}/tests/{test_id}/preview")
    async def preview_test(project_id: str, test_id: str) -> JSONResponse:
        try:
            project = project_store.get_project(project_id)
            test = project_store.get_test(project_id, test_id)
        except KeyError as exc:
            raise HTTPException(404, f"unknown {exc}") from exc
        return JSONResponse({"agents": expand_agents(project, test)})

    # ==================================================================
    # JSON: draws
    # ==================================================================

    @app.post("/api/projects/{project_id}/tests/{test_id}/draws")
    async def start_draw(project_id: str, test_id: str) -> JSONResponse:
        try:
            project = project_store.get_project(project_id)
            test = project_store.get_test(project_id, test_id)
        except KeyError as exc:
            raise HTTPException(404, f"unknown {exc}") from exc
        try:
            summary = await runtime.start_draw(project, test)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return JSONResponse(summary.model_dump(mode="json"))

    @app.get("/api/projects/{project_id}/tests/{test_id}/draws")
    async def list_draws(project_id: str, test_id: str) -> JSONResponse:
        return JSONResponse(
            [d.model_dump(mode="json") for d in project_store.list_draws(project_id, test_id)]
        )

    @app.get("/api/draws/{draw_id}")
    async def get_draw(draw_id: str) -> JSONResponse:
        draw, _, _ = _resolve_draw(project_store, draw_id)
        return JSONResponse(draw.model_dump(mode="json"))

    @app.post("/api/draws/{draw_id}/cancel")
    async def cancel_draw(draw_id: str) -> JSONResponse:
        try:
            draw = await runtime.cancel_draw(draw_id)
        except KeyError as exc:
            raise HTTPException(404, f"unknown draw {exc}") from exc
        return JSONResponse(draw.model_dump(mode="json"))

    @app.get("/api/draws/{draw_id}/stream")
    async def stream_draw(draw_id: str):
        """Forward agentflow's run-level SSE + multidraw winner event."""
        draw, _, _ = _resolve_draw(project_store, draw_id)
        af_store = runtime.af_store
        run_id = draw.run_id
        if run_id not in {run.id for run in af_store.list_runs()}:
            raise HTTPException(404, "agentflow run not found")
        queue = await af_store.subscribe(run_id)

        async def event_stream():
            try:
                cached = af_store.get_events(run_id)
                for ev in cached:
                    yield f"data: {ev.model_dump_json()}\n\n"
                if cached and cached[-1].type == "run_completed":
                    fresh = project_store.get_draw(draw.project_id, draw.test_id, draw_id)
                    yield f"event: multidraw_draw\ndata: {fresh.model_dump_json()}\n\n"
                    return
                while True:
                    ev = await asyncio.to_thread(queue.get)
                    yield f"data: {ev.model_dump_json()}\n\n"
                    if ev.type == "run_completed":
                        fresh = project_store.get_draw(draw.project_id, draw.test_id, draw_id)
                        yield f"event: multidraw_draw\ndata: {fresh.model_dump_json()}\n\n"
                        break
            finally:
                await af_store.unsubscribe(run_id, queue)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.get("/api/draws/{draw_id}/agents/{agent_id}/stream")
    async def stream_agent(draw_id: str, agent_id: str, request: Request):
        """Per-agent pi event SSE — replay then tail.

        Activated when the user opens a card; closed when they close it.
        """
        draw, project, _ = _resolve_draw(project_store, draw_id)
        log_path = artifact_log_path(runtime.runs_dir, draw.run_id, agent_id)

        async def event_stream():
            stop = asyncio.Event()
            try:
                async for ev in stream_agent_events(
                    log_path,
                    agent_id=agent_id,
                    verbose=project.verbose_stream,
                    is_run_terminal=lambda: runtime.is_run_terminal(draw.run_id),
                    stop_event=stop,
                ):
                    summary = event_summary(ev.raw)
                    payload = {
                        "agent_id": ev.agent_id,
                        "type": ev.type,
                        "replay": ev.replay,
                        "seq": ev.seq,
                        "summary": summary,
                        "raw": ev.raw,
                    }
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    if await request.is_disconnected():
                        stop.set()
                        return
                yield "event: agent_stream_end\ndata: {}\n\n"
            finally:
                stop.set()

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    # ==================================================================
    # health
    # ==================================================================

    @app.get("/api/health")
    async def health() -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "projects": len(project_store.list_projects()),
            }
        )

    return app


def _resolve_draw(store: ProjectStore, draw_id: str) -> tuple[DrawSummary, Project, Test]:
    locator = store.locate_draw(draw_id)
    if locator is None:
        raise HTTPException(404, f"unknown draw {draw_id}")
    draw = store.get_draw(locator.project_id, locator.test_id, locator.draw_id)
    project = store.get_project(locator.project_id)
    test = store.get_test(locator.project_id, locator.test_id)
    return draw, project, test
