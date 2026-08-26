# 更新日志

Agent Paper Distiller 的版本演进记录。

## v4.0 — 2026-07-05

**原生 Agent 运行时 + bioagent HITL 对齐 —— 从 LangGraph 旁路走向生产级 AgentLoop。**

### Phase 0 · 原生 Agent 运行时

- 新增 `backend/app/agent/`：`AgentLoop`、`RuntimeBundle`、`InMemoryStreamBus`、`ToolRegistry` 自动发现
- 新增 `backend/app/tools/`：web_search / arxiv_search / spawn_sub_agent / wait_sub_agents / pipeline_steps / execute_code / shell_command
- 新增 `backend/app/sandbox/` 沙箱执行层
- `services/agent_chat.py` 深度搜索改走 native AgentLoop 流式 SSE

### Phase 1 · 流水线编排收敛

- `harness/pipeline/orchestrator.py` 成为论文蒸馏唯一编排入口
- 删除死代码：`pipeline/workflow_graph.py`、legacy LangGraph ReAct 路径、无用 harness 适配器
- 保留业务模块：document_parser / translator / tot_generator / renderer 等

### Phase 2 · P0 生产修复

- 任务级 `TurnConfig.user_settings`，避免全局 runtime 突变
- `SubAgentStore` 优雅降级；`AgentWorker` 生命周期与异常隔离
- `user_settings.py` 统一读取用户 API Key / 模型配置

### Phase 3 · HITL bioagent 协议对齐

- `HitlCoordinator`：`HITL_REQUEST` / `HITL_RESPONSE` + waiter 注册表
- SSE 映射为 `hitl_request`（兼容 legacy `hitl_approval`）
- `POST /hitl/{id}/decide` 并持久化到 `chat_messages.contexts.hitl_part`
- 深度搜索双检查点：`pre_search` + `pre_report`
- 前端工作台：内联 HITL 卡片、历史回放、决策携带 `session_id`

### Phase 4 · 工程质量

- `.pre-commit-config.yaml`、`.gitlab-ci.yml`、`pyproject.toml`（uv）
- 单元测试覆盖 agent loop、HITL、deep search、tools、sandbox

> 注：Pipeline 内 `pre_critique` HITL 尚未完全迁入 `HitlCoordinator`，列为后续 v4.x 项。

## v3.0 — 2026-06-28

**Harness 工程全量改造 —— 让 harness 成为唯一执行脊柱。**

- FastAPI lifespan 接入 `AppHarness.startup()/shutdown()`
- 流水线 LLM 调用统一走 harness agents
- 补齐 MCP、OpenTelemetry、真异步流式、重试与限流
- 清理不可达 session/debate 路径；Supervisor 接入深搜规划

## v2.0 — 2026-05-31

- 深度搜索：来源动态推送、流式答案与思考链
- 对话历史持久化与上下文压缩
- Token 统计修复
- UX 优化（管理员模板、设置页重置 API）
- Docker 多阶段构建 + Compose（MySQL + 健康检查）

## v1.0 — 2026-05-24

- 初始版本：解析、翻译、摘要、创新评审
- DeepSeek + Qwen3 异构 ToT 协作
- ChromaDB + BM25 混合 RAG
- SSE 进度 · JWT + 邮箱验证码认证
