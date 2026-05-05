# Windows 快速上手

> 5 分钟把 multidraw 跑起来。

## 1. 装前置（只装一次）

下载并安装：

| 软件 | 版本 | 链接 |
|---|---|---|
| Python | 3.10 或更高 | https://python.org/downloads/ （安装时务必勾选 **Add Python to PATH**） |
| Node.js | 20 或更高 | https://nodejs.org/ |
| Git | 任意 | https://git-scm.com/download/win |

装完打开命令提示符（cmd）或 PowerShell，验证：

```
python --version
node --version
git --version
```

三个都能输出版本号才行。

## 2. 拿到代码

```
git clone https://github.com/Q7h2q9/lottery.git multidraw
cd multidraw
```

## 3. 一键安装

**双击** `install.bat`（或者在 PowerShell 里跑 `.\install.ps1`）。

脚本会做：
- 创建 Python 虚拟环境 `.venv\`
- 克隆 agentflow 到 `.deps\agentflow\`
- 安装 multidraw + agentflow
- 全局装 pi CLI
- 写默认 `~/.pi/agent/models.json`（已配好 zimo provider）
- 拷贝 `.env.example` → `.env`
- 跑单元测试做 smoke 检查（应该 81 通过）

如果某一步失败，按提示装缺的东西再重跑。

## 4. 填 API Key

用记事本打开项目根目录下的 `.env`，把这行的占位符换成真 key：

```
ZIMO_API_KEY=sk-your-real-key-here
```

API key 找项目所有者要。

## 5. 启动

**双击** `start.bat`（或 PowerShell 跑 `.\start.ps1`）。

看到这行就启动好了：

```
INFO:     Uvicorn running on http://127.0.0.1:8765
```

浏览器访问 http://127.0.0.1:8765 开始用。关掉那个黑窗口就是停服。

## 6. 用法

1. 首页 → 「新建项目」→ 写任务通用指引（base_prompt）
2. 项目页 → 「新建测试」→ 设 agent 数量
3. 测试页 → 填具体题目（test_prompt）→ 可选：每个 agent 的 hint
4. 点「生成 prompt」→ 撰写 agent 综合 → 生成 N 条最终 prompt
5. 审一下 → 「批准」→ 「开始抽卡」
6. 抽卡页看 N 张卡，点开任意一张看那个 agent 的实时对话流
7. 第一个完成的 agent 胜出，其他立即取消

## 7. 出错排查

- **install.bat 报 "Python not found"** → 重装 Python，安装时**务必勾上 "Add Python to PATH"**，重启 cmd
- **install.bat 报 npm 装 pi 失败** → 跳过即可，install 脚本会继续。pi 不一定要全局装，缺了再说
- **start 后浏览器访问不了** → 看 cmd 窗口里有没有错误日志，常见的是 `.env` 里的 key 还是占位符没改
- **生成 prompt 时弹错误** → 检查 `.env` 里的 `ZIMO_API_KEY` 是否真的有效，配额够不够
- **更详细的坑表** → 看 `docs/HANDOVER.md` 第 9 节

## 8. 想完全卸载

直接删项目目录就行——所有依赖都在 `.venv\` 和 `.deps\` 里，没动你系统。
全局装的 pi 想删的话：`npm uninstall -g pi`
