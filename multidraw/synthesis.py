"""Synthesis agent — composes per-agent prompts via the pi CLI.

Inputs:
    Project (base_prompt + format_spec) + Test (agent_count + per_agent_overrides
    + model + tools).

Output:
    list[str] of length == Test.agent_count, one fully-rendered prompt per agent.

Strategy:
    1. If a per-agent override has prompt_override, that agent's prompt is taken
       verbatim — the synthesis agent is not asked to touch it.
    2. For all other slots, the synthesis agent produces prompts that satisfy
       the project's format_spec, weave in any hint, and reference the agent's
       own draw_id / position in the fleet.
    3. We use pi (zimo / gpt-5.4) with --mode text and parse the response by
       looking for a fenced JSON array between BEGIN/END markers.

Provider: hard-wired to ``zimo`` + ``zimo/gpt-5.4`` per Q-decision; a future
follow-up can lift this to Project config.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass

from multidraw.compiler import model_slug
from multidraw.models import AgentOverride, Project, Test


_log = logging.getLogger("multidraw.synthesis")

_BEGIN = "<<<BEGIN_PROMPTS_JSON>>>"
_END = "<<<END_PROMPTS_JSON>>>"

SYNTHESIS_PROVIDER = "zimo"
SYNTHESIS_MODEL = "zimo/gpt-5.4"


class SynthesisError(RuntimeError):
    """Raised when the synthesis agent fails to produce N usable prompts."""


@dataclass
class SynthesisInputs:
    """Snapshot of everything the synthesis agent needs."""
    project: Project
    test: Test


def _planned_node_id(model: str, index: int, total: int) -> str:
    """Match the node_id scheme used by compiler.py so previews align with runs."""
    width = max(3, len(str(total - 1)))
    return f"{model_slug(model)}_{index:0{width}d}"


def _build_meta_prompt(inputs: SynthesisInputs) -> str:
    project = inputs.project
    test = inputs.test
    overrides_for_agent: list[dict[str, str | int]] = []
    for i in range(test.agent_count):
        ov = test.override_for(i)
        if ov.prompt_override:
            continue
        item: dict[str, str | int] = {
            "index": i,
            "agent_id": _planned_node_id(test.model, i, test.agent_count),
        }
        if ov.hint:
            item["hint"] = ov.hint
        overrides_for_agent.append(item)

    skipped = [
        i for i in range(test.agent_count) if test.override_for(i).prompt_override
    ]
    skipped_note = (
        f"\n\n注意：索引 {skipped} 的 agent 用户已写死自己的 prompt（prompt_override），"
        "你不需要为它们生成。仍然请按 index 顺序输出 JSON 数组里的占位字符串 "
        "（这些位置写空字符串 \"\" 即可，调用方会填回 override）。"
        if skipped else ""
    )

    test_prompt_section = ""
    if test.test_prompt and test.test_prompt.strip():
        test_prompt_section = f"""

# 本测试的具体任务（来自 test_prompt，所有 agent 共享的题目 / 问题描述）

```
{test.test_prompt.strip()}
```
"""

    return f"""你是一名 multi-agent 抢答系统的 prompt 撰写员。任务：为一个由 {test.agent_count} 个并发选手 agent
组成的抢答任务，生成每个 agent 各自的最终 prompt。

# 项目通用指引（来自项目级 base_prompt）

```
{project.base_prompt.strip()}
```
{test_prompt_section}
# 你必须遵守的输出格式要求（来自项目）

```
{project.effective_format_spec().strip()}
```

# 选手 agent 的运行环境

- 模型：`{test.model}`，工具权限：`{test.tools}`
- 每个 agent 都有唯一的 agent_id（已预先决定，见下方列表的 `agent_id` 字段），prompt 中
  应该用这个 id 让它知道自己是谁。
- 它们彼此**不通信**——每个 agent 只看到自己这条 prompt，看不到任何其他上下文。
- **极其重要**：你生成的每条 prompt 必须是完全自包含的。选手 agent 除了收到的那一条
  prompt 之外什么都看不到——不会看到本测试的题目描述、不会看到项目指引、不会看到其他
  agent 的 prompt。所以你必须把完整的题目/任务内容直接写进每条 prompt 里。
  绝对不能写"请参考题目"、"随后给出"、"等待输入"之类的话。

# 每个 agent 的个性化输入（用户填的 hint）

下面列出**需要你生成 prompt** 的 agent。请按 `index` 顺序为每个 agent 写一条独立的最终
prompt。如果某 agent 给了 `hint`，自然地融入它的 prompt（不是机械追加一行，而是当作策略
/ 角度让它围绕这条 hint 展开）；没给 hint 就按项目目标写一条通用版本。

```json
{json.dumps(overrides_for_agent, ensure_ascii=False, indent=2)}
```{skipped_note}

# 输出格式（必须严格遵守）

请只输出**一段** JSON 数组，包在下面的两行标记之间。数组长度必须正好是 {test.agent_count}，
索引 i 对应 agent i 的最终 prompt 字符串。不要在标记之外有任何解释、思考、空行或其他文字。

{_BEGIN}
[
  "<agent 0 的最终 prompt>",
  "<agent 1 的最终 prompt>",
  ...
]
{_END}
"""


def _extract_prompts(stdout: str, expected_count: int) -> list[str]:
    if _BEGIN not in stdout or _END not in stdout:
        raise SynthesisError(
            f"synthesis agent reply lacks {_BEGIN}/{_END} markers; got:\n{stdout[:500]}"
        )
    body = stdout.split(_BEGIN, 1)[1].split(_END, 1)[0].strip()
    body = re.sub(r"^```(?:json)?\s*\n", "", body)
    body = re.sub(r"\n```\s*$", "", body)
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise SynthesisError(f"synthesis agent JSON parse error: {exc}\nbody:\n{body[:500]}") from exc
    if not isinstance(data, list):
        raise SynthesisError(f"synthesis output is not a JSON array; got {type(data).__name__}")
    if len(data) != expected_count:
        raise SynthesisError(
            f"synthesis produced {len(data)} prompts, expected {expected_count}"
        )
    out: list[str] = []
    for i, item in enumerate(data):
        if not isinstance(item, str):
            raise SynthesisError(f"synthesis item [{i}] is not a string ({type(item).__name__})")
        stripped = item.strip()
        if not stripped:
            raise SynthesisError(f"synthesis item [{i}] is empty")
        # Defensive: detect when the model produced a placeholder rather than
        # a real prompt. "None", "null", "TODO" etc. are all signs synthesis
        # silently failed and we should refuse to persist the result.
        if stripped.lower() in {"none", "null", "todo", "tbd", "n/a", "..."}:
            raise SynthesisError(
                f"synthesis item [{i}] looks like a placeholder ({item!r}); "
                "the synthesis agent likely failed — try again."
            )
        if len(stripped) < 30:
            raise SynthesisError(
                f"synthesis item [{i}] is too short ({len(stripped)} chars): {item!r} — "
                "the synthesis agent probably stubbed out the response."
            )
        out.append(item)
    return out


def _merge_overrides(test: Test, generated: list[str]) -> list[str]:
    """Splice prompt_override into the synthesis output."""
    final: list[str] = []
    for i in range(test.agent_count):
        ov = test.override_for(i)
        if ov.prompt_override:
            final.append(ov.prompt_override)
        else:
            final.append(generated[i])
    return final


def _build_pi_command() -> list[str]:
    npm_global = os.path.expanduser("~/.npm-global/bin/pi")
    executable = npm_global if os.path.exists(npm_global) else "pi"
    return [
        executable,
        "--print",
        "--mode", "text",
        "--no-session",
        "--no-skills",
        "--no-extensions",
        "--no-prompt-templates",
        "--no-context-files",
        "--tools", "read",
        "--provider", SYNTHESIS_PROVIDER,
        "--model", SYNTHESIS_MODEL,
    ]


async def synthesize_prompts(
    project: Project,
    test: Test,
    *,
    timeout_seconds: int = 180,
) -> list[str]:
    """Call the synthesis agent and return resolved_prompts of length agent_count."""

    if all(test.override_for(i).prompt_override for i in range(test.agent_count)):
        # Every slot is hand-written; nothing to synthesize.
        return [test.override_for(i).prompt_override or "" for i in range(test.agent_count)]

    inputs = SynthesisInputs(project=project, test=test)
    meta_prompt = _build_meta_prompt(inputs)
    cmd = _build_pi_command()

    _log.info(
        "synthesis: project=%s test=%s agents=%d via %s",
        project.id, test.id, test.agent_count, SYNTHESIS_MODEL,
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ,
        )
    except FileNotFoundError as exc:
        raise SynthesisError(f"pi CLI not found: {exc}") from exc

    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(meta_prompt.encode("utf-8")),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise SynthesisError(f"synthesis pi call timed out after {timeout_seconds}s") from exc

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise SynthesisError(
            f"pi exited {proc.returncode}\n--- stdout ---\n{stdout[:1000]}\n"
            f"--- stderr ---\n{stderr[:1000]}"
        )

    # Always dump pi's full reply for post-mortem when something goes wrong.
    try:
        from pathlib import Path
        debug_path = Path("/tmp/multidraw_synth_last.log")
        debug_path.write_text(
            f"=== synth at {os.environ.get('HOSTNAME', '?')} ===\n"
            f"=== project={project.id} test={test.id} agents={test.agent_count} ===\n"
            f"=== returncode={proc.returncode} stdout_bytes={len(stdout_b)} stderr_bytes={len(stderr_b)} ===\n"
            f"--- stderr ---\n{stderr}\n"
            f"--- stdout ---\n{stdout}\n",
            encoding="utf-8",
        )
    except Exception:
        pass

    try:
        generated = _extract_prompts(stdout, test.agent_count)
    except SynthesisError:
        # Re-raise but make sure the debug file is preserved
        raise
    return _merge_overrides(test, generated)


def synthesize_prompts_sync(project: Project, test: Test, *, timeout_seconds: int = 180) -> list[str]:
    """Convenience wrapper for non-async callers (CLI etc.)."""
    return asyncio.run(synthesize_prompts(project, test, timeout_seconds=timeout_seconds))
