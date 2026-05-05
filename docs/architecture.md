# Architecture rationale

This is the "why" doc. The "what" lives in `CLAUDE.md` and inline docstrings.

## Big picture

```
                                     ┌───────────────────────┐
   YAML / API spec ──► Project ──►   │  multidraw.compiler     │
                                     │   render jinja × N    │
                                     │   build PipelineSpec  │
                                     └──────────┬────────────┘
                                                │ N flat NodeSpec (one per agent)
                                                ▼
                                     ┌───────────────────────┐
                                     │ agentflow.Orchestrator│  ◄── concurrency, retries,
                                     │  (unmodified library) │      success_criteria, traces
                                     └──────────┬────────────┘
                                                │ run.events (SSE)
                                                ▼
                                     ┌───────────────────────┐
                                     │   multidraw.racer       │  ── on node.success →
                                     │   subscribe + watch   │     orchestrator.cancel(run_id)
                                     └───────────────────────┘
```

## Why an outer racer (not an agentflow monitor node)

`agentflow` already supports periodic "monitor" nodes that emit JSON
`{"actions": [{"kind": "cancel", "node_ids": [...]}]}` to cancel siblings.
Looks made for our use case. We **deliberately do not use it**.

Reason: the orchestrator's `_apply_periodic_actions` enforces

```python
allowed_node_ids = set(record.pipeline.fanouts.get(watched_group, []))
```

Cancel actions are restricted to one fanout group. Our fleet is heterogeneous
(`50 codex + 5 opus` in `examples/ctf_pico_demo.yaml`); putting them in one
fanout doesn't work because `fanout` shares the `model` field across copies.
Putting them in two fanouts means one monitor cannot cancel across both.

The external racer pattern doesn't have that restriction. It calls
`orchestrator.cancel(run_id)` which terminates the whole run — exactly the
"one wins, all stop" semantic the user asked for. Bonus: the racer doesn't
burn LLM tokens like a monitor agent would.

## Why N flat NodeSpec, no fanout

Agentflow's `fanout(node, N)` deep-copies a single NodeBuilder N times. All
copies share the parent's `model` field. The compiler iterates the fleet and
emits one `NodeSpec` per agent with its own model — straightforward, and
agentflow's `concurrency` setting still bounds simultaneous executions.

## Why prompt is rendered at compile time

Two reasons:

1. **Diversity hooks.** Per-agent variables (`{ strategy: "SQLi" }`) live in
   the fleet entry. Rendering at compile time lets us bake them into each
   independent NodeSpec without depending on agentflow's runtime fanout
   context (which we don't use anyway).

2. **Failure isolation.** A bad jinja expression fails at `multidraw validate`
   time, before submitting anything to the orchestrator.

The trade-off: agentflow's runtime jinja context (`{{ nodes.foo.output }}`,
`{{ item.* }}`) is unavailable inside multidraw prompts. That's fine — the
agents in a draw are strictly siblings, never depend on each other.

## Why win condition = `node.success`, not stdout grepping

agentflow already evaluates `success_criteria` per node and stores
`node.success: bool`. We piggyback on that — racer just watches for
`node_completed && node.success`. This means:

- Every agentflow-native criterion (`output_contains`, `file_exists`,
  `file_contains`, `file_nonempty`) works for free.
- `output_regex` is multidraw-only sugar: it lowers to `output_contains` with
  the regex literal-stripped value (so agentflow has a coarse signal), and
  the racer separately runs `re.search` to extract a clean
  `winner.payload`.

## Why the FleetEntry.agent is locked to "pi"

User chose "全部走 pi" early in design — pi already routes to OpenAI /
Anthropic / OpenRouter / Bedrock / local LMStudio / Ollama via one config
file. Loosening to `Literal["pi", "codex", "claude"]` is a 30-line change
in `compiler.py` (dispatch on agent kind) but introduces a second
configuration surface (codex / claude have native CLI auth flows that don't
go through `models.json`). Hold off until the user asks.

## Cancel policy semantics

| policy | behavior |
|---|---|
| `immediate` | racer calls `orchestrator.cancel(run_id)` the instant `node.success` is observed |
| `delay` | sleep `cancel_delay_seconds`, then check the run is still running, then cancel. Lets a corroborating second winner emerge before pulling the plug. |
| `none` | racer records the winner but never cancels. Siblings run to completion. Useful when you want to compare multiple successful answers. |

The racer always returns the FIRST winner regardless of policy — `delay` and
`none` only change whether/when siblings get killed.

## Why a separate ProjectStore (not reusing agentflow's RunStore)

`agentflow.RunStore` is keyed by `run_id` (one execution). `multidraw` adds a
"project" abstraction: a reusable spec that produces many draws. The two
stores live side by side at `.multidraw/projects/<pid>/...` and
`.agentflow/runs/<run_id>/...`. We never duplicate the agentflow data; the
project store only holds the spec and per-draw summaries (winner_id,
winner_payload, state, timing). All raw artifacts stay in agentflow's run
directory.

## Test strategy

- `tests/test_racer.py` stubs `agentflow.specs` so it can run without
  agentflow installed — keeps that core file small + fast.
- Other tests use only multidraw-internal modules.
- No integration test against a real pi CLI; that's gated by user
  configuration (`~/.pi/agent/models.json` + API keys) and would burn money
  to run. Smoke-test manually with `multidraw run examples/ctf_pico_demo.yaml`
  once pi is configured.
