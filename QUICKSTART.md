# 快速启动 / Quick Start

> 5 分钟把 multidraw 跑起来。Windows / macOS / Linux 通用。

## 1. 装前置（一次性）

| 软件 | 版本 | 备注 |
|---|---|---|
| Python | 3.10+ | Windows 安装时勾选 **Add Python to PATH** |
| Node.js | 20+ | pi CLI 需要 |
| Git | 任意 | clone 仓库用 |

验证：
```bash
python --version  &&  node --version  &&  git --version
```

## 2. 拿代码

```bash
git clone https://github.com/Q7h2q9/lottery.git multidraw
cd multidraw
```

## 3. 一键安装

**Windows**：双击 `install.bat`（或 PowerShell 跑 `.\install.ps1`）

**macOS / Linux**：
```bash
bash install.sh
```

脚本会做：
- 创建 `.venv/`（Python 虚拟环境）
- 克隆 agentflow 到 `.deps/agentflow/`
- 安装所有依赖
- 全局装 pi CLI（npm）
- 写默认 `~/.pi/agent/models.json`（zimo provider）
- 拷贝 `.env.example` → `.env`
- 跑 81 个测试做 smoke check

## 4. 填 API Key

用任意编辑器打开项目根目录的 `.env`：

```
ZIMO_API_KEY=sk-填入真实 key
```

key 找项目所有者要。

## 5. 启动

**Windows**：双击 `start.bat`

**macOS / Linux**：
```bash
source .venv/bin/activate
multidraw serve
```

看到这行就启动好了：
```
INFO:     Uvicorn running on http://127.0.0.1:8765
```

浏览器访问 **http://127.0.0.1:8765**

---

## 三步使用

```
[1] 新建项目
    └─ 写一段自然语言的"项目通用指引"（base_prompt）
       例：你是做算法题的 agent，需要用 Python 解决问题，
           完成后输出 ANSWER: <答案>

[2] 项目里新建测试
    ├─ 设 agent 数量（5、10、50…）
    ├─ 填具体题目（test_prompt）
    │  例：粘贴一道 LeetCode 题面
    ├─（可选）给每个 agent 不同的 hint：
    │   agent 0: "用动态规划"
    │   agent 1: "用双指针"
    │   ...
    └─ 点「生成 prompt」让撰写 agent 综合
       三层信息（项目指引 + 题目 + hint）→
       生成 N 条具体 prompt

[3] 审 prompts → 批准 → 开始抽卡
    ├─ N 个 agent 同时在跑
    ├─ 每张卡片可点开看实时对话
    └─ 第一个完成的赢，其他立即停
```

---

## 卸载 / 迁移

直接删项目目录即可。所有依赖都在 `.venv/` 和 `.deps/` 里，没动系统。
全局装的 pi 想清掉：`npm uninstall -g pi`

---

## 出错？

- **`python: command not found`** → 重装 Python，**务必**勾选 "Add to PATH"
- **install 时 npm 装 pi 失败** → 跳过即可，能用主功能
- **生成 prompt 报错** → 检查 `.env` 里的 `ZIMO_API_KEY`，配额够不够
- **看不懂哪一步** → 看 `docs/Windows快速上手.md`（含截图说明）
- **想了解架构** → 看 `docs/HANDOVER.md`

---

## 文档导航

| 文档 | 看什么 |
|---|---|
| `QUICKSTART.md`（这份） | 5 分钟跑起来 |
| `docs/Windows快速上手.md` | Windows 详细步骤 |
| `docs/HANDOVER.md` | 架构 + 历史 + 已知坑 |
| `docs/zh_使用手册.md` | 日常操作手册 |
| `docs/architecture.md` | race semantics 设计 |
| `docs/pi_setup.md` | pi CLI + 多 provider 配置 |
| `docs/重构_v2_项目-测试-抽卡分层.md` | v2 三层模型设计文档 |
