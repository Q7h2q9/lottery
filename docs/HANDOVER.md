# multidraw 项目交接文档（v0.2 — 三层模型 + 撰写 agent + 实时观察）

> 给下一位接手这个项目的 Claude（或人类）。看完这一份就够了——
> 其它文档是细节展开，不必读完才能动手。

---

## 0. 一句话定位

> **多 agent 并发跑同一个任务，谁先达成完成条件就赢，其余立即停。**
>
> 用户给的中文叫"AI 抽卡"。底层是 [agentflow](https://github.com/berabuddies/agentflow) 这个 DAG agent 编排框架，multidraw 是搭在它上面的薄层。

适用场景：算法题、codegen、CTF、bug 复现、研究问题——任何"单次成功率不高、并发拉一把"的事。

---

## 1. v0.2 重构（2026-05-05 完成，跟 v0.1 完全不一样）

v0.1 是扁平模型：`Project = 一个完整任务规格`（base_prompt + fleet + success_criteria 全堆一起）。

**v0.2 拆成三层**，并加了一个撰写 agent 帮用户生成 prompt：

```
Project（容器）
├── base_prompt        ← 项目通用指引（自然语言，非 jinja）
├── format_spec        ← 格式要求（默认走内置，覆盖可选）
├── verbose_stream     ← 是否转发 thinking 等所有 pi 事件
└── Test×N（一种具体跑法）
    ├── test_prompt    ← 本测试的具体题目（所有 agent 共享）
    ├── agent_count
    ├── per_agent_overrides[]
    │   ├── hint         ← 撰写 agent 综合的策略提示
    │   └── prompt_override  ← 直接覆盖最终 prompt（撰写 agent 跳过此 agent）
    ├── resolved_prompts[]   ← 撰写 agent 产出，需用户审批
    ├── approval_status      ← draft / approved
    └── Draw×N（每次跑一次）
        └── prompts_snapshot ← 快照，避免后续改了影响回看
```

**撰写 agent 流程**：用户写 base_prompt + test_prompt + per-agent hint → 点"生成 prompt" →
撰写 agent（pi/zimo gpt-5.4）综合三层输出 N 条具体 prompt → 用户审 → 批准 → 跑。

**抽卡时实时观察**：每个 agent 一张卡片，点开就订阅那个 agent 的 SSE 流，看消息流 + 工具调用。
跑完仍能从 stdout.log 重放完整对话。

---

## 2. 当前状态（最后验证 2026-05-05）

| 检查 | 结果 |
|---|---|
| 单元测试 | **81 / 81 通过**（`pytest -q tests/`） |
| 三层模型 | Project / Test / Draw 落地 |
| 撰写 agent | 调 pi --provider zimo --model zimo/gpt-5.4，BEGIN/END 标记解析 |
| 实时观察 | tail stdout.log，22 种 pi 事件，verbose 开关默认精选 |
| 旧数据迁移 | 3 个 v1 项目（algo-two-sum-solo / -race / lc48-rotate-image）已转 v2 |
| Web UI | 4 页面（项目列表/项目/测试/抽卡）+ 17 JSON + 2 SSE |
| 实战 | 用户在 UI 跑 5-agent 旋转矩阵抢答（中途踩了几个坑，已修） |

---

## 3. 关键架构决策（不要破坏）

1. **Race semantics 在 agentflow 之外**——外层 racer 订阅 SSE，命中 `node_completed && success` 调 `orchestrator.cancel(run_id)`。不要改成内置 monitor 节点（fanout group 限制）。
2. **每个 agent 是独立 NodeSpec，不用 fanout**——fanout 共享 model 字段，破坏异构舰队。
3. **Prompt 编译期就是字符串，不是 jinja**——`NodeSpec.prompt = test.resolved_prompts[i]`，没有运行时渲染。
4. **Tests 必须 approved 才能跑**——`runtime.start_draw` 检查 `is_runnable()`。
5. **win 条件 = `node.success`（agentflow 原生）**——racer 不重新评估。
6. **`FleetEntry.agent` 锁定 `pi`**——放开是个跨多模块 ripple，目前没动。
7. **撰写 agent 当前硬编码 zimo/gpt-5.4**——见 `multidraw/synthesis.py:SYNTHESIS_PROVIDER` / `SYNTHESIS_MODEL`。
8. **每条 prompt 必须自包含**——选手 agent 只看到自己那条 prompt，看不到其他上下文。`DEFAULT_FORMAT_SPEC` 强约束这点（实战学到的教训）。

---

## 4. 文件结构

```
multidraw/
├── models.py        Project / Test / AgentOverride / DrawSummary（pydantic）
├── store.py         三层 FS store .multidraw/projects/<pid>/{spec.json, tests/<tid>/{spec.json, draws/}}
├── compiler.py      (Project, Test) → agentflow PipelineSpec（无 jinja）
├── synthesis.py     撰写 agent（pi/zimo gpt-5.4），BEGIN/END JSON 解析 + 占位符防护
├── agent_stream.py  tail .agentflow/runs/<rid>/artifacts/<node>/stdout.log + 解析 pi 事件
├── racer.py         接收 (project, test) 双参数；cancel_policy 从 Test 读
├── runtime.py       Orchestrator + RunStore 黏合层；synthesize_test / approve_test / start_draw
├── api.py           FastAPI: 4 页面 + 17 JSON + 2 SSE（draw + per-agent）
├── cli.py           typer: new / validate / save-project / save-test / list / synthesize / approve / run / migrate / serve
├── migration.py     v1 → v2 一次性迁移脚本（multidraw migrate）
├── _envloader.py    .env 自动加载
└── web/             5 templates + app.css + app.js
tests/               81 tests
```

---

## 5. 实战中踩到的坑（v0.2 实施期间）

按时间顺序记录，下次接手 / 报类似 bug 时直接看这一节。

### 5.1 撰写 agent 输出"等用户给题面"的废话 prompt
**症状**：跑出来的 agent 输出 `请提供题目完整描述（输入、输出、数据范围、样例）。我收到题面后会直接...`
**原因**：用户没填 `test.test_prompt`，base_prompt 又只是泛泛的"做算法题"，撰写 agent 没题目可嵌就输出了等待式 prompt。
**修法**：
- `DEFAULT_FORMAT_SPEC` 加强约束："每条 prompt 必须自包含完整题面，绝不能写'随后给出'"
- synthesis.py meta-prompt 同样强调
- 前端校验：test_prompt 为空时点"生成 prompt"会弹窗拒绝调用

### 5.2 "重新生成 prompt" 把好 prompt 都覆盖成字符串 "None"
**症状**：第一次 synthesize 成功，再次点"重新生成"后所有 prompts 变成 4 字符的 `"None"`，agent 输出乱七八糟。
**根因链**：
1. `Test.per_agent_overrides[i].hint` 在 store 里是 Python `None`
2. Jinja 模板 `{{ overrides.hint }}` 把 None 渲染成字符串 `"None"`，写进 `<input value="None">`
3. 前端 JS 用 `value || null` 读，但 `"None"` 是非空字符串，所以保存的是 `hint="None"`
4. `prompt_override` 字段同样中招——变成 `"None"`（非空，看着像用户写了完整覆盖）
5. `synthesize_prompts` 检查到所有 5 个 agent 都有 `prompt_override`，**走 short-circuit 直接返回这 5 个 "None"，根本不调 pi**
6. store 被污染，再 reload 看到的 textarea 还是 "None"（无限循环）

**修法**（多层防御）：
- 模板：所有 textarea/input 用 `| default('', true)` 把 None 转成空串
- JS `_normalize()`：读 textarea 时把字符串 `"None"`/`"null"`/`"undefined"` 当成 null
- `_extract_prompts`：拒绝 `"None"`/`"null"`/`"todo"` 等占位符 + < 30 字的过短 prompt
- `synthesize_prompts` 总是 dump pi 完整 stdout 到 `/tmp/multidraw_synth_last.log` 方便事后看

### 5.3 点"批准"会偷偷覆盖 resolved_prompts
**症状**：批准后 prompts 变成空白或污染状态。
**根因**：原 JS 的 approve action 会先 POST `manual-prompts` 把 textarea 内容存进 store，再调 approve。如果 textarea 因为 5.2 的渲染问题显示了垃圾，垃圾就进 store 了。
**修法**：approve 现在只调 `/approve` 翻转状态，不再无条件覆盖 prompts。用户想改先点"保存手写 prompt"。

### 5.4 模板 `class="Test"` 触发 pytest 警告
**症状**：pytest 输出大堆 `cannot collect test class 'Test'` 警告。
**原因**：`multidraw.models.Test` 是 pydantic BaseModel 但名字以 Test 开头，pytest 默认收集。
**修法**：`pyproject.toml` 设 `python_classes = ["TestCase", "Test_*"]`。

---

## 6. 端到端流程（Web UI 主路径）

```
1. 新建项目（name + base_prompt 自然语言 + format_spec 留默认）
2. 项目下新建测试（agent_count + test_prompt 题面）
3. 测试详情页：填每个 agent 的 hint（可选）
4. 点"生成 prompt" → 撰写 agent（180 秒超时）→ 5/10/N 条 resolved_prompts 写进 store（draft 状态）
5. 审 final_prompt textarea，必要时手改 → 批准
6. 点"开始抽卡" → runtime.start_draw → agentflow 跑 → racer 监 SSE
7. 抽卡页面卡片网格，点开某张卡 → 抽屉打开 → SSE 订阅那个 agent 的 stdout.log 重放 + tail
8. 第一个 success → racer cancel → 卡片金色高亮 + winner banner
```

CLI 镜像同样流程：
```bash
multidraw save-project x.yaml && multidraw save-test t.yaml &&
multidraw synthesize PID TID && multidraw approve PID TID && multidraw run PID TID
```

---

## 7. 环境隔离（不要碰 ~/.zshrc）

- **`.env` (gitignored)** 放 API key，`_envloader.py` 自动加载
- **`~/.npm-global/bin`** 自动 prepend 到 PATH（pi CLI 路径）
- 永远别建议 `export FOO=... # add to ~/.zshrc`
- 详见 `memory/feedback_project_local_env_only.md`

---

## 8. pi 后端配置

`~/.pi/agent/models.json` 已写入：
```json
{
  "providers": {
    "zimo": {
      "baseUrl": "https://llm.zimo.click/",
      "api": "openai-responses",
      "apiKey": "ZIMO_API_KEY",
      "models": [{ "id": "gpt-5.4", "reasoning": true, "contextWindow": 1000000 }]
    }
  }
}
```

`.env` 含 `ZIMO_API_KEY=sk-...`（gitignored）。pi `_extract_model_id` 会把 `zimo/gpt-5.4` 剥前缀变成 `gpt-5.4`，`models.json` 里的 `id` 必须匹配剥后值。

---

## 9. 已知坑（持续累积）

1. **CLI 单进程**——`multidraw run` 必须前台跑完。后台用 `multidraw serve`。
2. **撰写 agent 调用占用 1 次 pi**——每次"生成 prompt"约 5k-15k token。失败重试不会 double 计费但会再花一次。
3. **撰写 agent 偶尔产出占位符**——已加多层防御（5.2），但根因不明。怀疑 zimo 中转偶尔抽风。下次出问题看 `/tmp/multidraw_synth_last.log`。
4. **agent 被 cancel 时 stdout.log 不会有 `agent_end` 事件**——`agent_stream` 通过 `_QUIESCENCE_GRACE_SECONDS` 和 `is_run_terminal` 回调收尾。
5. **多次"重新生成"覆盖旧 prompts**——synthesize 成功就 save，旧的不可恢复。想保留先复制走。
6. **manual-prompts 端点不能在 approve 时被自动调**（5.3 教训）。

---

## 10. 5 分钟接手验证

```bash
cd /home/user/q7h2q9/others/Lottery
source .venv/bin/activate

# 1. 包结构、依赖正常
multidraw --help                                 # 列出 10 个子命令
pytest -q tests/                                 # 81 passed

# 2. .env / pi PATH 自动加载工作
python -c "
import multidraw, os
print('PATH has npm-global?', '.npm-global/bin' in os.environ['PATH'])
print('ZIMO_API_KEY loaded?', 'ZIMO_API_KEY' in os.environ)
"
# 期望：两个都 True

# 3. 列出现有数据（v1 已迁移到 v2）
multidraw list

# 4. pi 自身能调通
( set -a; . ./.env; set +a; \
  echo "say PONG" | pi --print --mode text --no-session --tools read \
    --provider zimo --model zimo/gpt-5.4 ) | grep PONG
# 期望：PONG

# 5. Web UI 启得来
multidraw serve &
sleep 2
curl -s http://127.0.0.1:8765/api/health
# 期望：{"ok":true,"projects":N}
kill %1
```

完整端到端 race 流程跑 web UI：
1. 启 `multidraw serve`，访问 http://127.0.0.1:8765
2. 新建项目 "测试项目"，base_prompt 写"做算法题，输出 ANSWER:" 之类
3. 项目下新建测试，agent_count = 3，test_prompt 粘进 LeetCode 1 (two sum) 题面
4. 点"生成 prompt"等约 30s，看 3 条 resolved_prompts 是否包含完整题面
5. 点"批准"→ 点"开始抽卡"→ 看抽卡页卡片状态变化
6. 点开某张卡看实时对话流

---

## 11. 文档导航

- `CLAUDE.md` — 短版 orientation（自动加载到 Claude 上下文）
- `docs/HANDOVER.md` — **这份**（详细历史 + 决策 + 坑）
- `docs/重构_v2_项目-测试-抽卡分层.md` — v2 设计文档（Q1–Q8 决策记录）
- `docs/architecture.md` — race semantics 设计原理
- `docs/pi_setup.md` — pi CLI 安装 + provider 配置
- `docs/zh_使用手册.md` — 日常操作（v1 时代写的，部分 v2 不适用）

---

## 12. 用户偏好（来自 memory）

- 中文沟通
- 倾向 Web UI 而非 CLI
- 文档随代码同步改，不要事后补
- 不要默认用 CTF 当例子，用算法题/codegen
- 不要污染 `~/.zshrc`，用 `.env`
- 直接做，不要总在那儿讨论方案

---

## 13. 待办

| 优先级 | 项 | 备注 |
|---|---|---|
| 🟡 | 撰写 agent 占位符 bug 调研 | 看 `/tmp/multidraw_synth_last.log` 找根因 |
| 🟡 | 放开 `FleetEntry.agent` 支持 codex / claude | 30 行改动 |
| 🟡 | Test 编辑（现在只能新建 + 删除） | |
| 🟢 | judge 类型 win-condition（LLM 判官） | |
| 🟢 | working_dir 自动清理 | 每次 race 前清旧 solve_*.py |
| 🟢 | UI 里直接切 verbose_stream（debug 用） | |
