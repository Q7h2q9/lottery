"""Pydantic models for multidraw v2: Project / Test / Draw three-tier.

Project: reusable container — base_prompt (natural language) + format_spec
         + per-project defaults. Holds N Tests.
Test:    one fleet configuration under a Project — agent_count, per-agent
         overrides, runtime knobs, and the synthesized resolved_prompts +
         approval state.
Draw:    one execution of a Test (= one agentflow run). Held under its Test.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


_SLUG_RE = re.compile(r"[^a-z0-9_-]+")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(value: str, *, max_length: int = 40) -> str:
    lowered = value.strip().lower().replace(" ", "-")
    cleaned = _SLUG_RE.sub("-", lowered)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-_")
    return cleaned[:max_length] or "item"


# ---------------------------------------------------------------------------
# Success criteria (mostly unchanged from v1)
# ---------------------------------------------------------------------------


class CancelPolicy(str, Enum):
    IMMEDIATE = "immediate"
    DELAY = "delay"
    NONE = "none"


class SuccessCriterion(BaseModel):
    kind: Literal[
        "output_contains",
        "output_regex",
        "file_exists",
        "file_contains",
        "file_nonempty",
    ]
    value: str | None = None
    path: str | None = None
    case_sensitive: bool = True

    def to_agentflow(self) -> dict[str, Any]:
        if self.kind == "output_regex":
            anchor = self.value or ""
            literal = re.sub(r"[.^$*+?{}\[\]\\|()]", "", anchor)[:32] or "match"
            return {
                "kind": "output_contains",
                "value": literal,
                "case_sensitive": self.case_sensitive,
            }
        payload: dict[str, Any] = {"kind": self.kind, "case_sensitive": self.case_sensitive}
        if self.value is not None:
            payload["value"] = self.value
        if self.path is not None:
            payload["path"] = self.path
        return payload


# ---------------------------------------------------------------------------
# Project — reusable container
# ---------------------------------------------------------------------------


DEFAULT_FORMAT_SPEC = """\
最终交给选手 agent 的 prompt 应包含：
1. 角色与身份说明（"你是 agent <id>"）
2. 完整的任务描述——必须把题目/问题的全部内容（输入、输出、约束、示例）直接写进每条 prompt 里。
   选手 agent 只能看到你给它的这一条 prompt，看不到任何其他上下文。
   绝对不能写"请参考题目"或"随后给出"——题目必须就在 prompt 里面。
3. 输出格式约束（明确选手必须以何种形式宣告完成，如 "ANSWER: <result>" 或 "TESTS_PASS"）
4. 可选 hint / 策略提示（如果用户为该 agent 提供了 hint，自然融入而非生硬贴上）
5. 自验或检查指引（如适用，让 agent 自跑代码 / 例子做自我验证再报告结果）

关键约束：
- 每条 prompt 必须是**自包含的**——选手 agent 除了这条 prompt 什么都看不到。
- 保持简洁直接，不要重复装饰性废话。
- 任务用中文描述就用中文写 prompt，英文就用英文。
- 不要在 prompt 中告诉 agent 它在跟谁竞争——身份编号即可。
"""


class Project(BaseModel):
    """Reusable goal container. Multiple Tests can live under one Project."""

    id: str = Field(default="", description="URL-safe identifier; auto-derived from name when omitted.")
    name: str
    description: str | None = None

    base_prompt: str = Field(
        ...,
        description="自然语言项目 prompt，由撰写 agent 综合成最终选手 prompt。",
    )
    format_spec: str | None = Field(
        None,
        description="项目级格式要求（覆盖默认）；留空走内置 DEFAULT_FORMAT_SPEC。",
    )

    default_success_criteria: list[SuccessCriterion] = Field(
        default_factory=lambda: [SuccessCriterion(kind="output_contains", value="ANSWER:")],
        description="成功条件；Test 可以再覆盖。",
    )

    verbose_stream: bool = Field(
        False,
        description="是否把 thinking 等所有 pi 事件转发到前端实时流；默认精选事件。",
    )

    working_dir: str = Field(".", description="所有 Test 的 fleet agent 共享的工作目录。")

    created_at: str = Field(default_factory=_utcnow)

    @model_validator(mode="before")
    @classmethod
    def _derive_id_from_name(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        existing = data.get("id")
        if isinstance(existing, str) and existing.strip():
            data["id"] = slugify(existing)
            return data
        name = data.get("name")
        if isinstance(name, str) and name.strip():
            data["id"] = slugify(name)
            return data
        raise ValueError("project requires either id or name")

    def effective_format_spec(self) -> str:
        if self.format_spec and self.format_spec.strip():
            return self.format_spec
        return DEFAULT_FORMAT_SPEC


# ---------------------------------------------------------------------------
# Test — one fleet configuration under a Project
# ---------------------------------------------------------------------------


class AgentOverride(BaseModel):
    """Per-agent customization passed to the synthesis agent.

    `hint` is freeform text the synthesis agent weaves into the final prompt.
    `prompt_override` short-circuits synthesis entirely for this agent.
    """

    hint: str | None = Field(None, description="策略 / 角度 / 个性化提示，撰写 agent 综合进 prompt。")
    prompt_override: str | None = Field(
        None,
        description="如设置，此 agent 的最终 prompt 直接用此值，不经撰写 agent 综合。",
    )

    def is_empty(self) -> bool:
        return not (self.hint or self.prompt_override)


class Test(BaseModel):
    """One run-configuration under a Project. Owns its fleet + draws."""

    id: str = Field(default="", description="Test id within its project; auto-derived from name.")
    project_id: str
    name: str
    description: str | None = None

    # --- test-level common prompt (the actual problem / task for this test) ---
    test_prompt: str = Field(
        "",
        description=(
            "本测试所有 agent 共享的具体任务描述（如一道算法题的完整题面）。"
            "撰写 agent 会把 Project.base_prompt（通用指引）+ test_prompt（具体题目）"
            "+ per_agent hint（策略差异）三层综合成每个 agent 的最终 prompt。"
        ),
    )

    # --- fleet shape ---
    model: str = Field("zimo/gpt-5.4", description="选手 agent 的 pi model（所有 agent 同模型；多模型 → 多 Test）。")
    tools: Literal["read_only", "read_write"] = Field("read_only")
    extra_args: list[str] = Field(default_factory=list, description="透传 pi 的 extra_args。")
    agent_count: int = Field(..., ge=1, le=200)
    per_agent_overrides: list[AgentOverride] = Field(
        default_factory=list,
        description="索引 i 对应 list[i]；列表可短于 agent_count，未填走默认。",
    )

    # --- synthesis output + approval ---
    resolved_prompts: list[str] = Field(
        default_factory=list,
        description="撰写 agent 产出的 N 条最终 prompt（或手动模式下用户编辑的 N 条）。",
    )
    approval_status: Literal["draft", "approved"] = "draft"
    approved_config_hash: str | None = Field(None, description="批准时配置哈希，用于检测漂移。")
    last_synthesized_at: str | None = None
    last_approved_at: str | None = None
    manual_mode: bool = Field(False, description="true → 绕过撰写 agent，用户手写 resolved_prompts。")

    # --- runtime knobs (Test-level，可不同 Test 不同) ---
    success_criteria: list[SuccessCriterion] | None = Field(
        None,
        description="覆盖 Project.default_success_criteria；留空走 Project 默认。",
    )
    cancel_policy: CancelPolicy = CancelPolicy.IMMEDIATE
    cancel_delay_seconds: int = Field(0, ge=0)
    concurrency: int = Field(20, ge=1, le=200)
    retries: int = Field(1, ge=0, le=10)
    retry_backoff_seconds: int = Field(3, ge=0)
    timeout_seconds: int = Field(600, ge=10)

    created_at: str = Field(default_factory=_utcnow)

    @model_validator(mode="before")
    @classmethod
    def _derive_id_from_name(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        existing = data.get("id")
        if isinstance(existing, str) and existing.strip():
            data["id"] = slugify(existing)
            return data
        name = data.get("name")
        if isinstance(name, str) and name.strip():
            data["id"] = slugify(name)
            return data
        raise ValueError("test requires either id or name")

    def override_for(self, index: int) -> AgentOverride:
        if 0 <= index < len(self.per_agent_overrides):
            return self.per_agent_overrides[index]
        return AgentOverride()

    def config_hash(self) -> str:
        """Hash of the Test-level inputs that determine synthesis output.

        Used to detect when the user has changed something after approval.
        Q4 decision: drift is just a UI hint, never auto-invalidates.
        """
        payload = {
            "agent_count": self.agent_count,
            "model": self.model,
            "tools": self.tools,
            "test_prompt": self.test_prompt,
            "per_agent_overrides": [
                {"hint": ov.hint, "prompt_override": ov.prompt_override}
                for ov in self.per_agent_overrides
            ],
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        return digest[:16]

    def is_drifted(self) -> bool:
        """True iff approved + current config differs from the approved snapshot."""
        if self.approval_status != "approved":
            return False
        if self.approved_config_hash is None:
            return True
        return self.approved_config_hash != self.config_hash()

    def is_runnable(self) -> bool:
        """A Test can run only when prompts are present and approved."""
        return (
            self.approval_status == "approved"
            and len(self.resolved_prompts) == self.agent_count
        )

    def effective_success_criteria(self, project: "Project") -> list[SuccessCriterion]:
        if self.success_criteria:
            return self.success_criteria
        return project.default_success_criteria


# ---------------------------------------------------------------------------
# Draw — one execution of a Test
# ---------------------------------------------------------------------------


class DrawState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WON = "won"
    EXHAUSTED = "exhausted"
    CANCELLED = "cancelled"
    FAILED = "failed"


class DrawSummary(BaseModel):
    draw_id: str
    project_id: str
    test_id: str
    run_id: str
    state: DrawState
    started_at: str
    finished_at: str | None = None
    winner_node_id: str | None = None
    winner_payload: str | None = None
    total_agents: int = 0
    cancel_policy: CancelPolicy = CancelPolicy.IMMEDIATE
    cancel_delay_seconds: int = 0
    prompts_snapshot: list[str] = Field(
        default_factory=list,
        description="此次抽卡使用的 N 条 prompt 快照；之后即便 Test 改了，回看仍准。",
    )
