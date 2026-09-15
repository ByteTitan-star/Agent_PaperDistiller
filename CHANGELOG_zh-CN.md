# 更新日志

Agent Paper Distiller 的版本演进记录。

## v5.0.0 — 2026-09-15

**文档摄取管线 —— 统一 Document IR、分层解析器、本地公式识别、阶段状态机。**

### Phase 0-1 · 解析内核

- 统一 `DocumentIR`（可 JSON 持久化的章节 + 类型化节点 + 预检查报告），全管线共用
- Preflight 预分类（文本层/扫描比/加密/双栏），有文本层的 PDF 绝不走 OCR
- ParserRouter + 质量门禁：PyMuPDF 主通道（双栏阅读顺序、双通道标题识别、表格转 Markdown 并绑定题注）→ pypdf 兜底 → 可选 MinerU / PaddleOCR
- 结构感知分块：`$$..$$` 公式与表格原子不切断；块携带 element_type/section/page/is_reference 元数据

### Phase 2 · 本地公式链路

- PP-DocLayout 区域检测替代字形密度启发式；4MB 轻量档随仓库分发（2×2 滑窗补全页分辨率），V2 经 `scripts/download_models.sh` 下载
- PP-FormulaNet-S 本地识别（图片 → LaTeX，内嵌 tokenizer），免费离线替代 Mathpix；Mathpix/Pix2Text 仍可选
- 图片型公式直接裁剪识别为 equation 节点

### Phase 3-4 · 管线工程

- 解析一次/翻译一次（产物持久化）；SHA-256 内容去重，重复上传不重跑解析
- 类型化解析失败，错误文案不再流入下游
- `document_jobs` 阶段状态机（UPLOADED → PARSING → CHUNKING → EMBEDDING → INDEXED / FAILED）
- LLM 翻译通道保留 LaTeX/表格/术语；Google 免费接口降级为兜底

### Phase 5-6 · 多模态与多格式

- VLM 图表描述（题注绑定裁剪 → 结构化描述 → `image_desc` 检索块），支持同步/后台模式
- GROBID 元数据增强 + 参考文献合并去重
- FileRouter：Markdown / DOCX 与 PDF 产出同一 IR

### Phase 7-8 · 体验与检索

- 用户级管线偏好（`/api/settings/pipeline`）接入 SettingsView；上传组件支持 .md/.docx
- 参考文献块默认排除出单论文与跨论文检索

### Phase 9-11 · 运维

- 内容哈希确定性 chunk ID；按 embedding 模型版本隔离集合（可选）；独立 Chroma Server 模式
- 200 个单元测试（原 68），含真实模型集成测试

## v4.0.0 — 2026-07-05

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
