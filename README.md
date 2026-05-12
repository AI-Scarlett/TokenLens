# TokenLens 🔍

**本地 AI Token 消耗监控仪表盘** — 无需官方 API，本地统计大模型 Token 用量与费用估算。

## ✨ 特性

- 🔢 **本地 Token 计数** — 使用 tiktoken + 字符估算，无需调用大模型官方 API
- 📊 **可视化仪表盘** — 环形图、进度条、统计卡片，一目了然
- 🏢 **17+ 厂商 / 40+ 模型** — 内置 OpenAI、Anthropic、Google、智谱、阿里云、MiniMax、DeepSeek 等
- 📦 **37+ 套餐计划** — Token Pack、Coding Plan、折扣优惠、免费额度
- 🔗 **Agent 自动发现** — 扫描本机 Claude Code、Codex、Hermes、QClaw 等 9+ Agent 配置，一键导入
- 🔍 **通用扫描器** — 自动发现未知 Agent，支持自定义扫描规则
- 🏷️ **Agent 来源追踪** — 按来源筛选统计，区分不同 Agent 的 Token 消耗
- 💰 **费用估算** — 按模型单价自动计算，支持 CNY/USD 多币种
- 🔌 **厂商/模型启用** — 层级开关，禁用厂商自动禁用旗下模型
- ⏱️ **时间筛选** — 今日/7天/30天/90天/自定义日期范围
- 📤 **数据导出** — JSON / CSV 格式导出

## 🚀 快速开始

```bash
# 安装依赖
pip install flask tiktoken

# 启动服务
python web.py

# 打开浏览器
open http://localhost:5170
```

首次访问会自动检测本地 Agent 配置，点击「立即导入」即可开始统计。

## 📁 项目结构

```
TokenLens/
├── web.py              # Flask Web 服务
├── token_counter.py    # 核心 Token 计数与统计引擎
├── agent_scanner.py    # Agent 配置自动扫描器
├── cli.py              # 命令行工具
├── models.json         # 内置厂商/模型/套餐配置
├── static/
│   └── index.html      # 前端仪表盘（单文件 SPA）
└── requirements.txt    # Python 依赖
```

## 🔗 支持的 Agent

| Agent | 图标 | 配置路径 |
|-------|------|---------|
| Claude Code | 🤖 | `~/.claude/settings.json` |
| OpenAI Codex | ⚡ | `~/.codex/config.toml` |
| CodeBuddy | 🦾 | `~/.codebuddy/models.json` |
| Qwen Code | 🔮 | `~/.qwen/settings.json` |
| Hermes | 🧙 | `~/.hermes/config.yaml` |
| QClaw (开爪) | 🐾 | `~/.qclaw/openclaw.json` |
| Kimi Code | 🌙 | `~/.kimi/config.toml` |
| EvoMorph | 🧬 | `~/.evomorph/config.json` |
| Cline | 🔌 | VS Code 扩展缓存 |

不在列表中的 Agent？通用扫描器会自动发现，也可以手动添加自定义扫描规则。

## 🛠️ API 接口

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/summary` | GET | 统计摘要 |
| `/api/stats?by=model\|vendor\|agent\|api\|date` | GET | 分组统计 |
| `/api/record` | POST | 记录调用 |
| `/api/count` | POST | 计算 Token |
| `/api/vendors` | GET/POST | 厂商管理 |
| `/api/models` | GET/POST | 模型管理 |
| `/api/plans` | GET | 套餐查询 |
| `/api/agents/scan` | GET | 扫描 Agent |
| `/api/agents/import` | POST | 导入 Agent |
| `/api/agents/custom` | GET/POST/DELETE | 自定义扫描器 |
| `/api/export?format=json\|csv` | GET | 数据导出 |

## 📄 License

MIT
