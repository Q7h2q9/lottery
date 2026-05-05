# multidraw — orientation for Claude

> 🚀 **Fresh-start? Read [`docs/HANDOVER.md`](docs/HANDOVER.md) first.** That doc has
> the full project history, decisions, gotchas, and a 5-minute接手验证流程.
> CLAUDE.md (this file) is the short auto-loaded orientation.

> Race-to-finish multi-agent fleet, **thin layer on top of [agentflow](https://github.com/berabuddies/agentflow)**.
> User scenario: throw 50+ agents at one CTF / codegen / repro task; first to finish wins; the rest stop immediately.

## Status (v0.2 refactor — last verified 2026-05-05)

- **All 81 unit tests green** (`pytest -q tests/`).
- **Three-tier model**: Project (container, natural-language base_prompt) → Test (one fleet config + per-agent overrides + resolved_prompts + approval; **also has its own `test_prompt`** for the specific task) → Draw (one execution).
- **Synthesis agent**: per-Test "撰写 agent" runs `pi --provider zimo --model zimo/gpt-5.4`, takes base_prompt + test_prompt + format_spec + per_agent_overrides → list[str] of N final prompts. Multi-layer defense against placeholder ("None"/empty) outputs. User audits + approves before the test can run.
- **Per-agent live event stream**: `/api/draws/<did>/agents/<aid>/stream` tails `.agentflow/runs/<rid>/artifacts/<node>/stdout.log`, replays + tails pi events. Frontend opens this on-demand when the user clicks a card.

## What lives where

```
multidraw/models.py         Project / Test / AgentOverride / DrawSummary (pydantic)
multidraw/store.py          Three-tier filesystem store under
                          .multidraw/projects/<pid>/{spec.json,
                                                     tests/<tid>/{spec.json, draws/}}
multidraw/compiler.py       (Project, Test) → agentflow PipelineSpec.
                          No jinja: each NodeSpec.prompt = test.resolved_prompts[i].
multidraw/synthesis.py      撰写 agent. Builds a meta-prompt from project + per-agent
                          overrides; spawns pi (zimo/gpt-5.4); parses fenced JSON
                          array between BEGIN/END markers.
multidraw/agent_stream.py   Tail .agentflow/runs/<rid>/artifacts/<node>/stdout.log;
                          parse pi events; filter (verbose vs default); replay-then-
                          tail async generator.
multidraw/racer.py          On node_completed && node.success → cancel run.
                          Cancel policy lives on Test, not Project.
multidraw/runtime.py        Process-wide owner of agentflow Orchestrator + RunStore.
                          synthesize_test / approve_test / start_draw / cancel_draw.
multidraw/api.py            FastAPI: 4 HTML pages + ~17 JSON endpoints + 2 SSE
                          (draw-level summary + per-agent stream).
multidraw/cli.py            typer CLI: new / validate / save-project / save-test
                          / list / synthesize / approve / run / migrate / serve.
multidraw/migration.py      One-shot v1 → v2 converter (multidraw migrate).
multidraw/web/templates/    layout / index / project / test / draw — 5 templates.
multidraw/web/static/       app.css + app.js (no build step).
tests/                    78 tests across models / store / compiler / racer
                          / synthesis / agent_stream / migration / envloader.
```

## Architecture invariants — do NOT break

1. **Race semantics live OUTSIDE agentflow.** External racer subscribes to
   agentflow SSE; on `node_completed && success` it calls
   `orchestrator.cancel(run_id)`. Don't try to embed as a periodic monitor
   node — `_apply_periodic_actions` only acts on a single fanout group.

2. **Each agent is an INDEPENDENT NodeSpec, no fanout.** Compiler emits N
   flat nodes per Test; agentflow's `concurrency` throttles. Fanout would
   force one shared `model` field across the group.

3. **Prompts are produced UPSTREAM by the synthesis agent, not at run time.**
   Each NodeSpec.prompt is just `test.resolved_prompts[i]`. There is no
   jinja rendering anywhere on the run path.

4. **Tests must be APPROVED before they can run.** `runtime.start_draw`
   refuses any Test where `approval_status != "approved"` or
   `len(resolved_prompts) != agent_count`. The UI enforces this with a
   disabled button, but the runtime is the source of truth.

5. **Win condition = `node.success` (agentflow native).** Racer doesn't
   re-implement criterion evaluation. `output_regex` is multidraw sugar that
   compiles to `output_contains` (literals stripped) for agentflow's coarse
   match, plus our own regex extraction for `winner.payload`.

6. **Selector locked to `pi` agent.** Multi-agent kind support is a future
   ripple (synthesis + compiler + provider config all need updates).

7. **The synthesis agent is currently hardwired to `zimo` + `zimo/gpt-5.4`.**
   See `multidraw/synthesis.py:SYNTHESIS_PROVIDER` / `SYNTHESIS_MODEL` to
   change. Future: project-level configuration.

## End-to-end flow (Web UI primary path)

```
1. New project: name + base_prompt (natural language) + format_spec (default ok)
2. New test under project: agent_count + per_agent_overrides (hint or full override)
3. "生成 prompt" → synthesis agent (zimo/gpt-5.4) → resolved_prompts as draft
4. User audits resolved_prompts, edits if needed, "批准"
5. "开始抽卡" → runtime.start_draw → agentflow run + racer watches
6. Click a card → opens side panel → /api/draws/<did>/agents/<aid>/stream
   replays prior events + tails new ones until agent_end / cancel
7. First agent with success_criteria pass → racer cancels rest
```

CLI mirrors this: `multidraw save-project x.yaml && multidraw save-test t.yaml &&
multidraw synthesize PID TID && multidraw approve PID TID && multidraw run PID TID`.

## Environment isolation — DON'T touch `~/.zshrc`

The user runs many Claude Code instances side by side and explicitly does
**not** want this project's secrets / PATH overrides leaking into the global
shell.

- **`.env` (gitignored)** holds API keys. `multidraw/_envloader.py` walks up
  from CWD looking for `.env`, merges into `os.environ` *without* overwriting
  shell-set variables. Variables flow into multidraw + its agentflow + pi
  subprocesses, never into the user's shell.
- **`~/.npm-global/bin` is auto-prepended to PATH** when `~/.npm-global/bin/pi`
  exists.
- Never recommend `export FOO=… # add to ~/.zshrc`.
  See `memory/feedback_project_local_env_only.md`.

## Known sharp edges

1. **CLI is single-process.** `multidraw run` blocks until the draw finishes;
   the agentflow Orchestrator is process-local. For fire-and-forget, use
   `multidraw serve` + submit through the web UI / API.

2. **Pi `_extract_model_id` strips provider prefix.** When `model="zimo/gpt-5.4"`
   and `provider="zimo"`, pi resolves to `gpt-5.4`. `~/.pi/agent/models.json`
   `id` field must match the *stripped* string.

3. **`multidraw migrate` is a one-shot.** It converts v1 spec.json → v2
   project + 1 test per FleetEntry, backs up the v1 file as `spec.v1.json`,
   and drops old draw records (run artifacts under `.agentflow/runs/` are
   untouched).

4. **Cancelled agents never emit `agent_end`.** `agent_stream` handles this:
   when a `is_run_terminal` callback returns True and no new bytes arrive
   for `_QUIESCENCE_GRACE_SECONDS` (5s), the stream finishes naturally.

5. **`models.Test` clashes with pytest's default class collection.**
   `pyproject.toml` sets `python_classes = ["TestCase", "Test_*"]` to dodge
   the warning.

6. **None → "None" string poisoning** (实战教训, 2026-05-05). Jinja renders
   Python `None` as the literal string `"None"` inside `value=` / textarea
   bodies. The JS `value || null` check then sees a non-empty string and
   PUTs it back as a real value. Mitigated with `| default('', true)` in
   templates, JS `_normalize()`, and `_extract_prompts` rejecting placeholder
   strings. See HANDOVER §5.2.

7. **每条 prompt 必须自包含**. The synthesis agent has access to base_prompt,
   test_prompt and per-agent hint, but the *fleet agents* it generates prompts
   for see ONLY their one assigned prompt — no shared context. So each
   resolved_prompt must contain the full task description. `DEFAULT_FORMAT_SPEC`
   and the synthesis meta-prompt both emphasize this. See HANDOVER §5.1.

8. **撰写 agent 偶尔产出占位符** ("None"/"null"/empty/<30 chars). Multi-layer
   defense in `_extract_prompts` will refuse and raise SynthesisError — the
   store is never polluted. Each call also dumps full pi stdout to
   `/tmp/multidraw_synth_last.log` for post-mortem.

## Open / next-step todos

- Loosen synthesis to accept `codex` / `claude` providers per-project.
- Project + Test edit-in-place in Web UI (project edit done, test edit TODO).
- `judge` win-condition (LLM-graded sibling outputs).
- Project-level `verbose_stream` toggle is wired but the UI has no debug
  helper to flip it from inside the draw page.

## Pointers

- `/tmp/agentflow/` — agentflow source clone for reading.
- `docs/HANDOVER.md` — full交接文档 (history, decisions, 5-min verification).
- `docs/重构_v2_项目-测试-抽卡分层.md` — v2 design doc (Q1–Q8 decisions).
- `docs/architecture.md` — original race-semantics rationale.
- `docs/pi_setup.md` — pi CLI install + models.json.
- `docs/zh_使用手册.md` — Chinese daily-use manual.
- `README.md` — user-facing intro.
