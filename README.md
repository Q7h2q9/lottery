# multidraw

> *"Race a fleet of agents at one task. First one to finish wins. Stop the rest."*

A thin race-to-finish layer on top of [agentflow](https://github.com/berabuddies/agentflow).
Built for unstable / probabilistic tasks where you'd rather throw 50 attempts at
the wall than wait for one to slowly fail — algorithm problems, codegen,
bug repros, research queries (CTF was the original prompt but the
framework is task-agnostic).

## How it works

```
Project (one task, fleet spec, win condition)
   │
   ├── compile  ──→  agentflow PipelineSpec (N independent pi nodes)
   │
   ├── submit   ──→  agentflow Orchestrator runs the fleet
   │
   └── race     ──→  watcher subscribes to SSE; on first node_completed
                     with success=True, calls orchestrator.cancel(run_id)
                     according to the project's cancel_policy
```

You bring **one prompt template**, a **fleet** of model copies, and a
**success criterion**. multidraw expands every fleet entry into independent
agentflow nodes (each with its own pre-rendered prompt and own model), submits
them, and stops the run as soon as one wins.

| concept | maps to |
|---|---|
| project | reusable goal (prompt, fleet, win condition) — lives in `.multidraw/projects/<id>/spec.json` |
| draw | one execution attempt — wraps an agentflow run |
| agent | one node in the agentflow run (one model copy) |
| winner | the first agent whose `success_criteria` passes |

## Install

```bash
./install.sh         # creates .venv, clones agentflow into .deps/, pip installs both
source .venv/bin/activate
multidraw --help
```

Then **configure pi** (the LLM gateway every agent talks through):

```bash
curl -fsSL https://pi.dev/install.sh | sh         # installs the pi CLI
$EDITOR ~/.pi/agent/models.json                    # provider declarations — see docs/pi_setup.md
cp .env.example .env && $EDITOR .env               # API keys go here, NEVER in ~/.zshrc
```

multidraw auto-loads `.env` at startup; the keys flow into multidraw and its
pi subprocesses but never into your shell or other Claude Code instances
running in this terminal. Full walkthrough: [`docs/pi_setup.md`](docs/pi_setup.md).

## Five-minute tour

```bash
# 1. solo smoke (1 agent — verifies pi + relay + multidraw all wired)
multidraw save examples/algo_two_sum_solo.yaml
multidraw run algo-two-sum-solo

# 2. race (5 agents on the same problem; first to print ANSWER: wins,
#    the other four are cancelled the moment that happens)
multidraw save examples/algo_two_sum_race.yaml
multidraw run algo-two-sum-race

# 3. live card grid in the browser
multidraw serve                    # → http://127.0.0.1:8765

# 4. 实战示例：10 agent 抢做 LeetCode 48 矩阵旋转，agent 自跑代码自验
mkdir -p challenges/lc48
multidraw save examples/lc48_rotate_image.yaml
multidraw run lc48-rotate-image

# scaffold your own project
multidraw new -o my_project.yaml
$EDITOR my_project.yaml
multidraw validate my_project.yaml
multidraw save my_project.yaml
multidraw run my-project           # blocks until won/exhausted/cancelled
```

## Project spec at a glance

```yaml
id: ctf-pico-web1
name: picoCTF demo - admin login bypass

prompt: |
  You are agent {{ draw_id }} ({{ model }}). Solve the task and emit
  flag{<contents>} on a line by itself.
  {% if strategy %}Strategy hint: {{ strategy }}{% endif %}
  Task: ...

default_success_criteria:
  - kind: output_regex
    value: "flag\\{[^}]+\\}"

cancel_policy: immediate     # immediate | delay | none
cancel_delay_seconds: 0

concurrency: 25              # max simultaneous agents (agentflow concurrency)
retries: 2
timeout_seconds: 1800

fleet:
  - model: openai/gpt-5-codex
    count: 50
    tools: read_write
  - model: anthropic/claude-opus-4-7:high
    count: 5
    tools: read_write
    variables:
      - { strategy: "leaked-credential dumps" }
      - { strategy: "default-password permutations" }
      # …one per index for this entry
```

`prompt` is a Jinja2 template rendered **once per agent at compile time**.
Variables in scope: `model`, `index`, `count`, `draw_id`, plus anything from
`FleetEntry.variables` (a list = one dict per index, a dict = shared).

## Win conditions

multidraw delegates per-node success evaluation to agentflow, which already
ships `output_contains`, `file_exists`, `file_contains`, `file_nonempty`.
multidraw adds `output_regex` (compiles to a contains hint for agentflow plus
a regex extraction for the final winner payload).

The race itself watches `node_completed` events and triggers on the first one
where `node.success is True`.

## Cancel policy

| policy | behavior |
|---|---|
| `immediate` (default) | call `orchestrator.cancel(run_id)` the moment a winner is detected |
| `delay` | wait `cancel_delay_seconds`, then cancel — useful when you want a corroborating second winner |
| `none` | never cancel; siblings finish naturally (more cost, possibly multiple winners) |

## Web UI

`multidraw serve` exposes a FastAPI app on `127.0.0.1:8765`:

- `/` — list of projects, modal to paste a YAML/JSON spec
- `/projects/<id>` — project detail, fleet preview, draw history, "Start draw" button
- `/draws/<id>` — **live card grid**: every agent is a card, status updates via SSE,
  winner card glows gold and the rest dim out

Internally the draw page subscribes to `/api/draws/<draw_id>/stream`, which
multiplexes agentflow's run SSE feed with a final `multidraw_draw` event
carrying the resolved winner payload.

## Project layout

```
multidraw/
├── models.py        # Project / FleetEntry / DrawSummary (pydantic)
├── store.py         # filesystem store under .multidraw/
├── compiler.py      # Project → agentflow PipelineSpec (jinja-renders prompts)
├── racer.py         # SSE watcher: first node_success → orchestrator.cancel
├── runtime.py       # Project ↔ agentflow Orchestrator glue
├── api.py           # FastAPI app (pages + JSON + SSE proxy)
├── cli.py           # typer CLI: new / validate / save / list / run / serve
└── web/             # Jinja templates + tiny vanilla JS / CSS for the UI
```

## Dev

```bash
source .venv/bin/activate
pytest -q                              # unit tests (no agentflow needed for racer/store/compiler tests)
multidraw validate examples/ctf_pico_demo.yaml
```

## Status

MVP. The data model and race semantics are stable; the UI is intentionally
minimal (vanilla JS, single-file CSS). Things that exist as natural next steps
but aren't built yet: project edit-in-place, judge-style win conditions,
per-fleet shared scratchboard between agents, loosening
`FleetEntry.agent` beyond `"pi"`.

## More docs

- 🚀 [`docs/HANDOVER.md`](docs/HANDOVER.md) — **交接文档**：项目历史、关键决策、所有坑、5 分钟接手验证流程。新接手项目先看这个。
- 🇨🇳 [`docs/zh_使用手册.md`](docs/zh_使用手册.md) — **中文操作手册**：文件结构 + 你日常该改哪些东西。日常使用看这个。
- [`CLAUDE.md`](CLAUDE.md) — project orientation for future Claude sessions (auto-loaded).
- [`docs/architecture.md`](docs/architecture.md) — design rationale (why outer racer, why no fanout, etc.).
- [`docs/pi_setup.md`](docs/pi_setup.md) — pi CLI install + `models.json` templates + smoke test.
- [`docs/prompt_patterns.md`](docs/prompt_patterns.md) — three ways to vary prompts across the fleet (same / per-agent variable / per-entry override).

## Acknowledgements

This is a thin layer; all the heavy lifting (DAG runtime, retries, traces,
concurrency, SSE plumbing, Pi multi-provider model routing) belongs to
[agentflow](https://github.com/berabuddies/agentflow).
