# Prompt patterns

Three ways to drive a multi-agent draw, in order of complexity. The
framework supports all three with no code changes — only the YAML differs.

## A — every agent runs the identical prompt

The simplest case. You're betting that LLM sampling noise alone gives you
enough diversity for at least one agent to land the answer fast.

```yaml
prompt: |
  Solve X. When done, print: ANSWER: <result>

fleet:
  - model: zimo/gpt-5.4
    count: 5
```

No `variables`, no per-entry `prompt` override. All 5 agents see the same
rendered prompt. Diversity comes only from temperature / sampling.

Example: `examples/pattern_a_same_prompt.yaml`.

## B — one template, per-agent variables

Same prompt skeleton, but each agent gets a different value substituted
into a Jinja2 placeholder. Use this to nudge agents toward different
strategies, seeds, file slices, etc.

```yaml
prompt: |
  Solve X.
  {% if strategy %}Strategy you must try first: {{ strategy }}{% endif %}
  When done, print: ANSWER: <result>

fleet:
  - model: zimo/gpt-5.4
    count: 5
    variables:                       # list → one dict per agent
      - { strategy: "brute force" }
      - { strategy: "two pointers" }
      - { strategy: "binary search" }
      - { strategy: "sliding window" }
      - { strategy: "DP" }
```

Variables in scope inside the template:

- `model` — the fleet entry's model id
- `index` — 0..count-1 within this entry
- `count` — total agents in this entry
- `draw_id` — unique node id for this agent
- anything from `variables`

Two `variables` shapes:

- `list[dict]` — one dict per index (paired by position; index ≥ len gets an empty dict)
- `dict` — one shared dict for every agent in this entry

Example: `examples/pattern_b_strategy_variants.yaml`.

## C — different fleet entries, totally different prompts

When the angles are too different to fit one template (e.g. some agents
should reason analytically while others should write & run code), give
each fleet entry its own `prompt` override.

```yaml
prompt: |          # project-level default
  Solve X by reasoning. Print: ANSWER: <result>

fleet:
  - model: zimo/gpt-5.4
    count: 3                         # uses project-level prompt
  - model: zimo/gpt-5.4
    count: 3
    tools: read_write
    prompt: |                        # entry-level override
      Solve X by writing and running Python code.
      Print: ANSWER: <result>
```

Mix it freely with pattern B — the `variables` field can also be set on
the entry that has its own `prompt`.

Example: `examples/pattern_c_different_prompts.yaml`.

## Cheat-sheet: which pattern when

| You want… | Pattern |
|---|---|
| Cheapest possible parallelism, raw retry-style noise | A |
| Same task, nudge agents toward different solution strategies | B |
| Same goal, fundamentally different approaches (reason vs. code-and-run vs. search) | C |
| Mix multiple models against the same task | use multiple fleet entries (any of A/B/C) |

You can always edit the project YAML between draws — `multidraw save
my.yaml` overwrites the stored spec, and the next `multidraw run my-id`
picks up the new prompt.
