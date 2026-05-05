# multidraw

> *"一群 agent 同时跑同一道题，谁先做出来谁赢，其他立刻停。"*

一个搭在 [agentflow](https://github.com/berabuddies/agentflow) 上的薄层，
专门用来跑那种"单次成功率不高、想并发拉一把"的任务——算法题、codegen、bug 复现、
研究类问题、CTF 等等。

中文里叫"AI 抽卡"——同时召唤一队 agent 抢答，第一个达成完成条件的胜出。

---

## 工作原理

```
项目 (Project) — 任务的可重复利用容器
   ↓ 包含
测试 (Test)    — 一种具体跑法（多少 agent、什么题目、每个 agent 给什么 hint）
   ↓ 包含
抽卡 (Draw)    — 一次执行（撰写 agent 综合 → 用户审批 → 真的跑起来）
```

每次抽卡的关键流程：

```
撰写 agent 把三层信息综合：
  ├─ 项目通用指引（base_prompt）
  ├─ 本测试的具体题目（test_prompt）
  └─ 每个 agent 的 hint（per_agent_overrides）
        ↓
   生成 N 条最终 prompt（每条自包含完整任务描述）
        ↓
   用户审批 → 通过后真的跑
        ↓
   compile to agentflow → 提交 → 外层 racer 监听 SSE
        ↓
   第一个 node_completed && success=True
        ↓
   orchestrator.cancel(run_id) 砍掉其他还在跑的 agent
```

---

## 关键概念

| 概念 | 是什么 |
|---|---|
| 项目 (Project) | 一个可重复利用的"题库容器"，写自然语言通用指引，下面挂多个测试 |
| 测试 (Test) | 项目下的一种具体跑法，含具体题目 + agent 数量 + 每个 agent 的策略 hint |
| 撰写 agent | 一个独立 LLM（pi/zimo gpt-5.4），帮你把三层信息合成最终 prompt |
| 抽卡 (Draw) | 一次执行，跑出来 N 个 agent 抢答 |
| 选手 agent | 抢答的 N 个 agent，每个都是独立 pi 节点 |
| 胜者 (Winner) | 第一个满足 success_criteria 的 agent，其他立即被取消 |

---

## 5 分钟跑起来

详见 [`QUICKSTART.md`](QUICKSTART.md)。Windows 用户：双击 `install.bat` → 编辑 `.env` → 双击 `start.bat`。

```bash
# Linux / macOS
git clone https://github.com/Q7h2q9/lottery.git multidraw
cd multidraw
bash install.sh
cp .env.example .env  # 编辑填入 ZIMO_API_KEY
source .venv/bin/activate
multidraw serve       # 浏览器开 http://127.0.0.1:8765
```

---

## 用法（Web UI 主路径）

1. **新建项目**
   - 写 `base_prompt`：自然语言描述这一类任务的通用指引
   - 例：「你是做算法题的 agent，需要用 C 语言解，完成后输出 `ANSWER: <答案>`」

2. **项目下新建测试**
   - 设 agent 数量（5、10、50…）
   - 填 `test_prompt`：本次具体的题目描述（粘贴 LeetCode 题面之类）
   - 可选：给每个 agent 不同的 hint
     ```
     agent 0: "用动态规划"
     agent 1: "用双指针"
     agent 2: "暴力枚举试试"
     ```

3. **生成 prompt**
   - 点「生成 prompt」→ 撰写 agent 跑约 30s
   - 看生成的 N 条最终 prompt（每条都该包含完整题目）
   - 觉得不对可以「重新生成」或手改

4. **批准并抽卡**
   - 点「批准」（runtime 检查必须 approved 才能跑）
   - 点「开始抽卡」
   - 抽卡页：N 张卡片实时显示状态，点开任一张看那个 agent 的对话流
   - 第一个 success → 胜者卡片金色高亮，其他被砍掉

CLI 完全镜像同样流程：
```bash
multidraw save-project x.yaml && multidraw save-test t.yaml &&
multidraw synthesize PID TID && multidraw approve PID TID &&
multidraw run PID TID
```

---

## 成功条件

multidraw 沿用 agentflow 原生的四种 success_criteria：
- `output_contains` — 输出含某字符串
- `file_exists` / `file_contains` / `file_nonempty` — 文件类判断

外加一个 multidraw 自家的：
- `output_regex` — 正则匹配，胜者时还会用它从输出里抽干净的 payload

racer 监听 `node_completed` 事件，命中 `node.success is True` 就触发取消策略。

---

## 取消策略

| 策略 | 行为 |
|---|---|
| `immediate`（默认） | 一旦发现胜者，立刻 `orchestrator.cancel(run_id)` |
| `delay` | 发现胜者后等 `cancel_delay_seconds` 秒再取消（等第二个佐证） |
| `none` | 不取消，所有 agent 跑完为止（费 token，但可能有多个胜者） |

---

## 实时观察

抽卡页里点任意一张卡 → 右侧抽屉打开，订阅 `/api/draws/<id>/agents/<agent_id>/stream`：

- 重放该 agent 之前所有事件（`replay=true`）
- 然后切到实时 tail 模式
- 关闭抽屉时自动取消订阅（不会一开始就 50 路 SSE 挂在那里）

事件类型默认精选：`message_*` / `tool_execution_*` / `agent_start|end`。
项目级 `verbose_stream` 开关可以放出全部 22 种 pi 事件（含 `thinking` 内部推理）。

---

## 项目结构

```
multidraw/
├── models.py        # Project / Test / AgentOverride / DrawSummary
├── store.py         # 三层 FS 存储：projects/<pid>/{spec.json, tests/<tid>/{spec.json, draws/}}
├── compiler.py      # (Project, Test) → agentflow PipelineSpec
├── synthesis.py     # 撰写 agent（pi/zimo gpt-5.4）
├── agent_stream.py  # tail stdout.log + 解析 pi 事件
├── racer.py         # SSE watcher：第一个成功的就取消其他
├── runtime.py       # Orchestrator + RunStore 黏合层
├── api.py           # FastAPI：4 页面 + 17 JSON 端点 + 2 SSE
├── cli.py           # typer CLI
├── migration.py     # v1 → v2 一次性迁移
└── web/             # Jinja 模板 + 原生 JS / CSS（无构建步骤）
tests/               # 81 个单元测试
docs/                # 详细文档
```

---

## 开发

```bash
source .venv/bin/activate
pytest -q                       # 81 passed
multidraw list                  # 查看本地项目
multidraw serve                 # 启动 Web UI
```

---

## 状态

v0.2 — 三层模型 + 撰写 agent + 实时观察均已落地。81/81 测试通过。

下一步可选优化：
- 放开 `FleetEntry.agent` 支持 codex / claude（目前锁定 pi）
- judge 类 win condition（LLM 判官）
- Test 编辑（目前只能新建+删除）

---

## 文档

| 文档 | 看什么 |
|---|---|
| [`QUICKSTART.md`](QUICKSTART.md) | 5 分钟跑起来 |
| [`docs/Windows快速上手.md`](docs/Windows快速上手.md) | Windows 详细步骤 |
| [`docs/HANDOVER.md`](docs/HANDOVER.md) | **交接文档**：架构 + 历史 + 已知坑 + 5 分钟健康检查 |
| [`docs/重构_v2_项目-测试-抽卡分层.md`](docs/重构_v2_项目-测试-抽卡分层.md) | v2 三层模型设计文档 |
| [`docs/zh_使用手册.md`](docs/zh_使用手册.md) | 日常操作手册 |
| [`docs/architecture.md`](docs/architecture.md) | race semantics 设计原理 |
| [`docs/pi_setup.md`](docs/pi_setup.md) | pi CLI + 多 provider 配置 |
| [`CLAUDE.md`](CLAUDE.md) | 给未来 Claude session 的简版 orientation（自动加载） |

---

## 致谢

multidraw 只是一层薄壳；DAG 运行时、重试、追踪、并发、SSE、pi 的多 provider 路由——
这些都来自 [agentflow](https://github.com/berabuddies/agentflow)。
