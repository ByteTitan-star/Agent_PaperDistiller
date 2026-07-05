<div align="right">

[English](./README.md) | **简体中文**

</div>

# ⚗️ PaperDistiller: 异构多智能体定向学术论文蒸馏平台

![PaperDistiller 首页界面](./UI_figures/HOME.png)

> **基于 DeepSeek-V3.2 与 Qwen3 的异构多智能体定向学术论文蒸馏平台**
>
> 告别漫无目的的文献阅读。通过自定义“专属关注点（Skill）”，利用双顶级开源模型构建的异构多智能体流水线，将长篇顶会论文精准“蒸馏”为您需要的核心结构、代码逻辑与创新推演。

## 🌟 项目简介

![PaperDistiller 首页界面](./UI_figures/OneTap.png)

**PaperDistiller** 是一个基于 **Vue 3** (前端) 和 **FastAPI** (后端) 构建的全栈学术辅助工具。它不仅仅是一个 PDF 阅读器，更是一个高度定制化的**文献信息蒸馏引擎**。

本项目是一款专为学术论文设计的智能化处理系统，通过构建自动化流水线实现 PDF 解析、全文翻译、核心摘要提取及创新点生成。系统集成了多智能体协同（Multi-Agent Collaboration）机制，利用 DeepSeek-V3.2 进行方案生成并由 Qwen3 进行独立评审，配合 Tree of Thoughts (ToT) 策略，为科研人员提供深度论文解析与可执行的改进建议。

只需上传 PDF 文件并指定提取模板（如：`template.md`），系统即可自动执行解析、翻译、结构化总结以及改进方案推演，并提供一个支持 RAG 问答的沉浸式双屏工作台。

## ✨ 核心特性

- **🚀 全自动化“蒸馏”流水线 (Pipeline)**
  - PDF 结构解析 → 全文对照翻译草稿 → 核心思路定向提取 → 创新改进建议生成。
- **👁️ 沉浸式阅读工作台 (Workspace)**
  - 左侧原生 PDF 渲染，右侧智能生成内容（Markdown 支持 LaTeX 公式）。
  - 内置浮动式 RAG 问答助手 (Chat Panel)，随时针对当前文献进行局部提问。
- **📊 实时任务监控 (SSE 机制)**
  - 任务调度器 (`TaskBroker`) 结合 Server-Sent Events (SSE)，在前端实时展示从 0% 到 100% 的精确处理进度和状态反馈。
- **🗂️ 本地化文献管理 (Dashboard)**
  - 卡片式论文管理，支持按标题搜索、领域标签（如 "LLM", "CV", "Backdoor Attacks"）快速过滤筛选。
- **🛠️ 高度可扩展的 Skill-Cards 设计**
  - 支持热插拔的 Markdown/JSON 提取模板，你的“个人阅读习惯”即是 Agent 的提取指令。

## 🚀 快速开始

本项目默认使用确定性的本地模拟逻辑（Mock 流水线），无需配置外部 LLM API Key 即可完整跑通全流程进行测试。

### 1. 启动后端服务 (FastAPI)

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

> 后端服务默认运行在：`http://127.0.0.1:8000`

### 2. 启动前端服务 (Vue 3)

```bash
cd frontend
npm install
npm run dev
```

> 前端服务默认运行在：`http://127.0.0.1:5173`

## 📋 更新日志

### v4.0（2026-07-05）

**原生 Agent 运行时 + bioagent HITL 对齐 —— 从 LangGraph 旁路走向生产级 AgentLoop**。v4.0 在 v3.0 harness 地基上，引入独立 `agent/` 运行时（Loop / Bootstrap / Worker / SubAgent），深度搜索与论文流水线统一走 orchestrator；同时按 bioagent 协议对齐人机协同（HITL），并补齐 CI / pre-commit / 68 项单元测试。

**Phase 0 · 原生 Agent 运行时**

- 新增 `backend/app/agent/`：`AgentLoop`、`RuntimeBundle`、`InMemoryStreamBus`、`ToolRegistry` 自动发现
- 新增 `backend/app/tools/` 生产工具面：web_search / arxiv_search / spawn_sub_agent / wait_sub_agents / pipeline_steps / execute_code / shell_command
- 新增 `backend/app/sandbox/` 沙箱执行层，代码技能隔离运行
- `services/agent_chat.py` 深度搜索改走 native AgentLoop 流式 SSE（替代 legacy LangGraph ReAct 旁路）

**Phase 1 · 流水线编排收敛**

- `harness/pipeline/orchestrator.py` 成为论文蒸馏唯一编排入口；`worker.py` 仅走 orchestrator
- 删除死代码：`pipeline/workflow_graph.py`、`harness/react/langgraph_agent.py`、legacy harness pipeline 适配器残桩
- 保留全部业务模块：document_parser / translator / tot_generator / renderer 等

**Phase 2 · P0 生产修复**

- 修复 per-user LLM 配置：移除全局 runtime 突变，任务级 `TurnConfig.user_settings` 注入
- `SubAgentStore` 优雅降级 + 历史上下文 flag；`AgentWorker` 任务生命周期与异常隔离
- `user_settings.py` 统一读取用户 API Key / 模型配置

**Phase 3 · HITL bioagent 协议对齐（P1）**

- 新增 `HitlCoordinator`：`HITL_REQUEST` / `HITL_RESPONSE` 经 StreamBus 广播 + `HitlWaiterRegistry` 唤醒
- SSE 映射为 `hitl_request`（含 `biomap_hil` wrapper + legacy `hitl_approval` 兼容字段）
- `POST /hitl/{id}/decide` → store 更新 + StreamBus 响应 + 持久化到 `chat_messages.contexts.hitl_part`
- 深度搜索双检查点：`pre_search`（计划确认弹窗）+ `pre_report`（边生成边审，token 实时流式、done 延迟至审批后）
- 前端 `WorkspaceView`：内联 HITL 卡片、历史回放、`session_id` 随决策提交

**Phase 4 · 工程质量**

- 新增 `.pre-commit-config.yaml`（ruff / mypy / bandit / secret-scan / markdownlint / conventional commits）
- 新增 `.gitlab-ci.yml` + `pyproject.toml`（uv 依赖管理、`scripts/setup_dev.sh`）
- 测试套件：`tests/unit/` 68 passed（agent loop、HITL coordinator、deep_search、tools、sandbox 等）

> 注：Pipeline 内 `pre_critique` HITL（harness HITLManager）尚未迁入 HitlCoordinator，列为 v4.x 后续项；端到端请在 MySQL + API Key 环境联调确认。

---

### v3.0（2026-06-28）

**Harness 工程全量改造 —— 让 harness 成为唯一执行脊柱**。本次重构修复了一个根本性架构缺陷：此前 `AppHarness.startup()` 从未被调用，导致整个 harness 层在运行时全部是死代码，真实流量绕过它们直调 `pipeline/workflow_graph`。v3.0 让 harness 真正接管所有 LLM 调用与工具执行，并补齐 MCP / OTel / 真流式 / 健壮性。

**Phase 0 · 地基：harness 可达 + 单例唯一**

- `AppHarness.startup()/shutdown()` 接入 FastAPI lifespan
- 单例收敛到 `dependencies.py`
- 修复致命 HITL bug：`interrupt()` + `wait_for_decision()`
- `worker.py` 走 `pipeline_orchestrator.run()`

**Phase 1 · 流水线 LLM 调用全部走 harness agent**

- 统一委托 `DeepSeekAgent` / `ToTAgent`
- 修复 per-task API Key bug
- Token 记账收敛到 `BaseAgent.on_post_run`

**Phase 2 · 死代码裁决 + Supervisor 接入**

- 删除 `harness/session/`、`DebatePattern`
- `HarnessToolRegistry` 成为唯一工具执行面
- `SupervisorPattern` 接入深度搜索研究规划

**Phase 3 · MCP**

- 新增 `harness/mcp/`：FastMCP server + ReAct client

**Phase 4 · OpenTelemetry**

- OTel 自托管可观测（Jaeger/Tempo/console）

**Phase 5 · 真异步流式**

- `AsyncOpenAI` 异步迭代，SSE 契约不变

**Phase 6 · 健壮性**

- tenacity 重试、`BaseAgent.aclose()`、`RateLimiter` 接入工具注册表

---

### v2.0（2026-05-31）

**深度搜索重构** · LangGraph ReAct · 来源动态推送 · 流式答案与思考链

**对话历史持久化** · ChatSession / ChatMessage · 上下文压缩

**Token 统计修复** · 流式 token 记录

**用户体验优化** · 日期注入 · 管理员模板 · 设置页重置 API

**Docker 化部署** · 多阶段 Dockerfile + docker-compose

---

### v1.0（2026-05-24）

- 初始版本：PDF 解析、全文翻译、摘要提取、创新点评审
- LangGraph StateGraph 流水线编排
- DeepSeek + Qwen3 异构多智能体 ToT 策略
- ChromaDB + BM25 多路 RAG 检索
- SSE 实时进度推送 · JWT + 邮箱验证码认证

---

*Developed with ❤️ by [ByteTitan-star](https://github.com/ByteTitan-star)*
