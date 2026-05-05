# pi setup guide

multidraw spawns the [pi coding agent](https://pi.dev) per fleet member. Each
agent is one `pi --print --mode json --no-session …` subprocess fed the
rendered prompt over stdin. pi handles the actual LLM call — OpenAI,
Anthropic, OpenRouter, local Ollama / LMStudio, etc. — based on your
`~/.pi/agent/models.json`.

## 1. Install the pi CLI

```bash
# easiest: official installer
curl -fsSL https://pi.dev/install.sh | sh

# or via npm (Node 18+)
npm install -g @mariozechner/pi-coding-agent
```

If `npm install -g` fails with `EACCES` on `/usr/lib/node_modules/...`, point
npm at a user-owned prefix instead of using sudo:

```bash
mkdir -p ~/.npm-global
npm config set prefix ~/.npm-global
PATH="$HOME/.npm-global/bin:$PATH" npm install -g @mariozechner/pi-coding-agent
```

You do **not** need to persist `~/.npm-global/bin` on your shell PATH —
multidraw's `_envloader.py` auto-detects `~/.npm-global/bin/pi` and prepends
the directory to PATH inside its own process. Project-local; no shell rc
changes.

Verify:

```bash
pi --version
pi --help | head
```

## 2. Configure providers

File path: **`~/.pi/agent/models.json`**.

Pick one of the templates below (or merge them).

### Recommended for multidraw: OpenRouter

One key, broad model coverage, transparent pricing. Best when you want a
heterogeneous fleet without juggling multiple credentials.

```json
{
  "providers": {
    "openrouter": {
      "baseUrl": "https://openrouter.ai/api/v1",
      "api": "openai-completions",
      "apiKey": "OPENROUTER_API_KEY",
      "models": [
        { "id": "anthropic/claude-opus-4-7",   "name": "Claude Opus 4.7",   "reasoning": true, "contextWindow": 200000 },
        { "id": "anthropic/claude-sonnet-4-6", "name": "Claude Sonnet 4.6", "reasoning": true, "contextWindow": 200000 },
        { "id": "openai/gpt-5-codex",          "name": "GPT-5 Codex",       "reasoning": true, "contextWindow": 128000 }
      ]
    }
  }
}
```

Put the key in the project-local `.env` (gitignored, never enters your
shell or other Claude instances):

```bash
echo 'OPENROUTER_API_KEY=sk-or-...' >> .env
```

In a multidraw fleet entry:

```yaml
fleet:
  - model: openrouter/openai/gpt-5-codex
    count: 50
  - model: openrouter/anthropic/claude-opus-4-7:high
    count: 5
```

### Direct vendor APIs

```json
{
  "providers": {
    "openai": {
      "baseUrl": "https://api.openai.com/v1",
      "api": "openai-completions",
      "apiKey": "OPENAI_API_KEY",
      "models": [{ "id": "gpt-5-codex", "reasoning": true }]
    },
    "anthropic": {
      "baseUrl": "https://api.anthropic.com",
      "api": "anthropic-messages",
      "apiKey": "ANTHROPIC_API_KEY",
      "models": [
        { "id": "claude-opus-4-7",   "reasoning": true },
        { "id": "claude-sonnet-4-6", "reasoning": true }
      ]
    }
  }
}
```

Fleet: `model: openai/gpt-5-codex`, `model: anthropic/claude-opus-4-7:high`.

### OpenAI-compatible relay (the "zimo" example)

If you have a self-hosted or third-party gateway speaking the **OpenAI
Responses API** (the same wire protocol Codex CLI uses), declare it like
this. Verified end-to-end on 2026-05-04 against `https://llm.zimo.click/`.

`~/.pi/agent/models.json`:

```json
{
  "providers": {
    "zimo": {
      "baseUrl": "https://llm.zimo.click/",
      "api": "openai-responses",
      "apiKey": "ZIMO_API_KEY",
      "models": [
        { "id": "gpt-5.4", "name": "GPT-5.4 (zimo relay)", "reasoning": true, "contextWindow": 1000000 }
      ]
    }
  }
}
```

The `apiKey` field above is the **name of an environment variable**, not
the literal token. multidraw expects that variable in the project-local
`.env` file (gitignored), not in your shell rc:

```bash
# project root
echo 'ZIMO_API_KEY=sk-...your-relay-token...' >> .env
```

`multidraw/_envloader.py` reads `.env` at import time and pushes the keys
into the multidraw process (and any pi subprocesses it spawns). Your shell
and other Claude Code instances never see the value.

To smoke-test pi standalone (outside multidraw), source `.env` for one
command — still no shell pollution:

```bash
( set -a; . ./.env; set +a; \
  echo "say PONG" | pi --print --mode json --no-session --tools read \
    --provider zimo --model zimo/gpt-5.4 )
```

Fleet usage in multidraw:

```yaml
fleet:
  - model: zimo/gpt-5.4
    count: 5
    tools: read_only
```

### Local Ollama / LMStudio (free, slow)

```json
{
  "providers": {
    "ollama": {
      "baseUrl": "http://localhost:11434/v1",
      "api": "openai-completions",
      "apiKey": "OLLAMA_KEY",
      "models": [{ "id": "qwen2.5-coder:32b" }]
    }
  }
}
```

```bash
export OLLAMA_KEY=ignored
```

## 3. Smoke-test pi BEFORE running multidraw

A 55-agent draw that fails at the first subprocess is annoying. Verify pi
itself works first:

```bash
echo "Say hello in one short sentence." | pi --print --mode json --no-session \
  --tools read \
  --model openrouter/openai/gpt-5-codex
```

You should see JSON output containing the model's reply. If you get
`provider not found` or `unauthorized`, fix `models.json` / the API key env
var before running multidraw.

## Reference

### Model string format

```
<provider_name>/<model_id>[:<reasoning_level>]
```

- `<provider_name>` = key in `providers` object of `models.json`
- `<model_id>`      = `id` in that provider's `models` array
- `:<reasoning>`    = `low | medium | high | off`. Models that don't
                     support granular reasoning silently treat any non-`off`
                     level as "on".

### `apiKey` field semantics

- Plain string → environment variable name (`"OPENROUTER_API_KEY"` reads
  `$OPENROUTER_API_KEY`).
- `"!some-shell-command"` → runs the command, uses stdout.
- Literal key (not recommended) → used as-is.

### Per-run inline provider (advanced)

If you don't want a global `models.json` entry, agentflow supports passing
a full `ProviderConfig` per node. That path isn't surfaced in multidraw's
YAML schema today; if needed, add a `provider` field to `FleetEntry` and
forward it in `multidraw/compiler.py:compile_to_agentflow`.

### Common gotchas

1. **`gpt-5-codex` direct on OpenAI doesn't always exist.** OpenAI's Codex
   CLI talks to a special `/responses` endpoint. The public API serves
   `gpt-5`, `gpt-4o`, etc. Use OpenRouter to get the Codex variant
   reliably.
2. **agentflow's `pi.py:_extract_model_id` strips the provider prefix.**
   When you pass `model="openrouter/anthropic/claude-opus-4-7"`, pi sees
   model id `"anthropic/claude-opus-4-7"`. Make sure the `id` in your
   `models.json` matches what pi will look for after stripping.
3. **`mcps` is rejected on pi nodes.** Use `extra_args: ["--extension", "..."]`
   instead. (multidraw's FleetEntry exposes `extra_args`.)
4. **Reading the trace.** Per-agent stdout/stderr is persisted under
   `.agentflow/runs/<run_id>/artifacts/<node_id>/`. Look there if pi
   appears to "succeed" but with empty output.
