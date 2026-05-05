# multidraw 中文使用手册

> 这份文档专门讲：**文件谁负责干啥** + **你每次想跑新任务要改哪些地方**。
> 设计原理在 `docs/architecture.md`，pi 后端配置在 `docs/pi_setup.md`，
> 三种 prompt 模式在 `docs/prompt_patterns.md`。

## 1. 项目文件树（按"你要不要动它"分层）

```
/home/user/q7h2q9/others/Lottery/
│
├── 🟢 你日常会改的（高频）
│   ├── .env                          ← API key（gitignored，本地专属）
│   └── examples/*.yaml               ← 任务定义。新问题 = 新 yaml 或改老 yaml
│
├── 🟡 你偶尔会改的（中频）
│   ├── ~/.pi/agent/models.json       ← 加新模型/新 provider 时改
│   └── .env.example                  ← 新增 env 变量时同步更新模板
│
├── 🔴 你几乎不用动的（框架内部）
│   ├── multidraw/                    ← 框架代码
│   │   ├── models.py                 # Project / FleetEntry / DrawSummary 数据结构
│   │   ├── store.py                  # 项目+draw 落盘到 .multidraw/
│   │   ├── compiler.py               # yaml → agentflow PipelineSpec 翻译
│   │   ├── racer.py                  # 看 agentflow 事件流，谁先成功就 cancel 整个 run
│   │   ├── runtime.py                # 把 compiler+racer+orchestrator 串起来
│   │   ├── api.py                    # FastAPI: 网页 + JSON API + SSE 转发
│   │   ├── cli.py                    # ctfdraw 命令行入口（new/save/run/serve）
│   │   ├── _envloader.py             # .env 自动加载（不污染 shell）
│   │   └── web/                      # 网页模板 + CSS + JS
│   ├── tests/                        # 32 个单元测试
│   ├── pyproject.toml                # 包元信息
│   ├── install.sh                    # 一键 venv + agentflow + multidraw
│   └── docs/                         # 你正在看的文档目录
│
└── ⚙️ 自动生成（别手工碰，框架自己管）
    ├── .multidraw/                   ← 项目存档 + 历史 draws
    │   └── projects/<id>/
    │       ├── spec.json             # ctfdraw save 之后的快照
    │       └── draws/<draw_id>.json  # 每次 run 的结果记录（winner/state/timing）
    ├── .agentflow/                   ← agentflow 的运行时状态
    │   └── runs/<run_id>/
    │       ├── run.json              # 整个 run 的状态机
    │       ├── events.jsonl          # SSE 事件流原始记录（racer 订阅这个）
    │       └── artifacts/<node>/     # 每个 agent 的 stdout/stderr/trace
    ├── .venv/                        # Python 虚拟环境
    └── .deps/agentflow/              # install.sh 拉的 agentflow 源码副本
```

## 2. 文件功能一句话速查

| 文件 | 一句话职责 | 你改它的时机 |
|---|---|---|
| `.env` | 装 ZIMO_API_KEY 等密钥 | 换 key / 加新 provider 的 key |
| `examples/<task>.yaml` | 一道题的完整定义（prompt、舰队、判据、超时） | **每次有新问题** |
| `~/.pi/agent/models.json` | pi CLI 知道的 provider + model 注册表 | 接入新模型/新中转/新本地引擎 |
| `multidraw/models.py` | Project/FleetEntry 字段定义（pydantic） | 想加新可配字段时 |
| `multidraw/compiler.py` | 把 yaml 渲染成 N 个独立 agent 的 NodeSpec | 想改默认 prompt 处理逻辑时 |
| `multidraw/racer.py` | 监听 SSE，命中 success 就 cancel | 想改取消时机时（比如延迟策略改逻辑） |
| `multidraw/cli.py` | 命令行入口；`multidraw new` 的脚手架模板也在这 | 改 CLI 行为或脚手架默认值 |
| `multidraw/api.py` + `web/` | 网页 UI + JSON API | 改 UI 样式/行为 |
| `tests/` | 32 个单元测试 | 改了 models/compiler/racer 后跑一下 |

## 3. 典型工作流：你有个新问题想抽卡

### 步骤 0：环境（一次性）

```bash
cd /home/user/q7h2q9/others/Lottery
source .venv/bin/activate
# .env 已经有 ZIMO_API_KEY 了，不用动
# pi 已经装好了，~/.pi/agent/models.json 已经配好 zimo 了
```

### 步骤 1：起个新 yaml（**这是你每次主要改的东西**）

最快路径——拷一份现成的改：

```bash
cp examples/pattern_a_same_prompt.yaml examples/my_new_task.yaml
$EDITOR examples/my_new_task.yaml
```

或者用脚手架（自带三种模式注释）：

```bash
multidraw new -o examples/my_new_task.yaml
```

### 步骤 2：改 yaml 的这几个字段

打开后，**你每次至少要动这四个地方**（其他字段可以先用默认）：

| 字段 | 改成 | 例子 |
|---|---|---|
| `id` | URL 安全的短名（不能有空格） | `lcs-leetcode` |
| `name` | 给人看的名字 | `LeetCode 最长公共子串` |
| `prompt` | 你要 agent 干的活 | `求字符串 X 和 Y 的 LCS，最后一行打印 ANSWER: <长度>` |
| `fleet[i].count` | 想要几个 agent 同时跑 | `5` |

### 步骤 3：选 prompt 模式（同/变量化/独立）

参考 `docs/prompt_patterns.md`。最常用是 Pattern A（全相同），其次 Pattern B（同模板 + per-agent 变量提示不同策略）。

### 步骤 4：选判据（`default_success_criteria`）

agent 输出符合什么条件算"赢"？

| 你想要 | 写啥 |
|---|---|
| 输出含某关键词（最常用） | `kind: output_contains, value: "ANSWER:"` |
| 输出能匹配某正则 | `kind: output_regex, value: "answer\\s*=\\s*\\d+"` |
| 工作目录里产生某文件 | `kind: file_exists, path: "solution.py"` |
| 某文件里有特定内容 | `kind: file_contains, path: "out.txt", value: "PASSED"` |
| 某文件非空 | `kind: file_nonempty, path: "result.txt"` |

### 步骤 5：跑

```bash
multidraw save examples/my_new_task.yaml      # 落盘到 .multidraw/projects/
multidraw run my-new-task                      # 阻塞直到结束
```

或者用网页：

```bash
multidraw serve                                # 浏览器开 http://127.0.0.1:8765
# 在网页点 "Start draw"，看实时卡牌
```

### 步骤 6：看结果

```bash
# 终端会直接打印 WON by <agent_id> + payload
# 完整记录在 .multidraw/projects/<id>/draws/<draw_id>.json
# 每个 agent 的原始 stdout/stderr 在 .agentflow/runs/<run_id>/artifacts/<node_id>/
```

## 4. yaml 字段全表 + 改不改建议

```yaml
id: <slug>                      # 必填。改名时同步改文件
name: <字符串>                  # 必填。展示用
description: <字符串>           # 可选。给自己看的备注

prompt: |                       # ★ 必填。每次新任务都要改
  ...jinja 模板...

default_success_criteria:       # ★ 必填。每次新任务一般都要调
  - kind: output_contains
    value: "ANSWER:"

cancel_policy: immediate        # 一般默认就行：immediate / delay / none
cancel_delay_seconds: 0         # 仅当 cancel_policy=delay 时有用

concurrency: 5                  # 同时活的 agent 数。默认 5-20 都行
retries: 1                      # 每个 agent 失败重试次数。0-3
retry_backoff_seconds: 3        # 重试退避秒数
timeout_seconds: 300            # 单个 agent 超时（秒）。简单题 60-120，复杂题 600-1800
working_dir: .                  # agent 工作目录。默认当前目录

fleet:                          # ★ 必填。一组或多组 agent 派系
  - model: zimo/gpt-5.4         # provider/model[:reasoning]
    count: 5                    # 这一派几个 agent
    tools: read_only            # read_only / read_write
    # variables: [...]          # 可选：per-agent jinja 变量
    # prompt: |                 # 可选：覆盖项目级 prompt
    # success_criteria: [...]   # 可选：覆盖项目级判据
    # extra_args: [...]         # 可选：透传给 pi CLI 的额外参数
```

带 ★ 的是**每次新任务必改**的，其他基本可以保持默认。

## 5. 调试：跑出来不对劲怎么办

```bash
# 看某次 draw 的所有 agent 状态
cat .multidraw/projects/<project_id>/draws/<draw_id>.json

# 看某个 agent 的完整原始输出
ls .agentflow/runs/<run_id>/artifacts/<node_id>/
cat .agentflow/runs/<run_id>/artifacts/<node_id>/stdout.log

# 离线检查 yaml 渲染出来到底什么样（不烧 token）
multidraw validate examples/my_new_task.yaml

# 单独 smoke test pi 后端能不能调通（绕过 multidraw）
( set -a; . ./.env; set +a; \
  echo "say PONG" | pi --print --mode json --no-session --tools read \
    --provider zimo --model zimo/gpt-5.4 )
```

## 6. 常见 "我想改 X，要动哪个文件" 速查

| 我想… | 改哪 |
|---|---|
| 加个新算法题 | 新建一个 `examples/<name>.yaml` |
| 同一题多试几次 | yaml 里把 `count` 调大 |
| 让不同 agent 试不同思路 | yaml 里加 `variables`（参考 Pattern B） |
| 接入新模型（比如本地 Ollama） | 改 `~/.pi/agent/models.json` 加 provider；yaml 里 `model:` 写新名字 |
| 换 API key | 改 `.env` |
| 让赢家出现后**不要**立刻杀别人 | yaml 里 `cancel_policy: delay` + `cancel_delay_seconds: 30`，或 `none` |
| 加判据：除了 stdout 关键词，还要某文件存在 | `default_success_criteria` 加多一项 |
| 网页 UI 颜色不喜欢 | 改 `multidraw/web/static/app.css` |
| 网页 UI 加新交互 | 改 `multidraw/web/static/app.js` + 对应模板 |
| 给 FleetEntry 加新字段 | 改 `multidraw/models.py` + `multidraw/compiler.py` + 写测试 |
| 改"赢家如何被认定"的判据评估逻辑 | 那是 agentflow 干的，看 `/tmp/agentflow/agentflow/success.py` |

## 7. 现成可跑的示例（直接 save & run）

| yaml 文件 | 场景 | 模式 | agent 数 |
|---|---|---|---|
| `examples/algo_two_sum_solo.yaml` | 1 个 agent 端到端冒烟测试 | 全相同 | 1 |
| `examples/algo_two_sum_race.yaml` | 同题 5 路抢答 | 全相同 | 5 |
| `examples/lc48_rotate_image.yaml` | LeetCode 48 矩阵旋转，**10 路按思路分组 + agent 自跑代码自验** | 变量化 | 10 |
| `examples/pattern_a_same_prompt.yaml` | 模式 A 的最小展示（fib(30)） | 全相同 | 5 |
| `examples/pattern_b_strategy_variants.yaml` | 模式 B 的展示（最长回文） | 变量化 | 5 |
| `examples/pattern_c_different_prompts.yaml` | 模式 C 的展示（n \| 2^n−2 数学题） | 多 prompt | 6 |

任意一个直接：

```bash
multidraw save examples/<文件名>.yaml
multidraw run <id>            # id 见 yaml 顶部
```

需要 working_dir 子目录的（例如 lc48），yaml 头部注释里会写明。

---

**底线**：90% 的日常使用就是**改 yaml**。框架代码改得越少越省心。
