# Changelog

All notable releases of Agent Paper Distiller.

## v4.0.0 — 2026-07-05

**Native Agent runtime + bioagent HITL alignment — from LangGraph bypass to production AgentLoop.**

### Phase 0 · Native Agent runtime

- New `backend/app/agent/`: `AgentLoop`, `RuntimeBundle`, `InMemoryStreamBus`, auto-discovering `ToolRegistry`
- New `backend/app/tools/`: web_search, arxiv_search, spawn_sub_agent, wait_sub_agents, pipeline_steps, execute_code, shell_command
- New `backend/app/sandbox/` for isolated code-skill execution
- Deep search in `services/agent_chat.py` uses native AgentLoop streaming SSE

### Phase 1 · Pipeline consolidation

- `harness/pipeline/orchestrator.py` is the sole paper-distillation entry
- Removed dead code: `pipeline/workflow_graph.py`, legacy LangGraph ReAct path, unused harness adapters
- Business modules retained: document_parser, translator, tot_generator, renderer, etc.

### Phase 2 · P0 production fixes

- Per-user LLM config via task-scoped `TurnConfig.user_settings`
- Resilient `SubAgentStore` with graceful fallback; `AgentWorker` lifecycle isolation
- `user_settings.py` centralizes API keys and model config

### Phase 3 · HITL bioagent protocol

- `HitlCoordinator`: `HITL_REQUEST` / `HITL_RESPONSE` on StreamBus + waiter registry
- SSE type `hitl_request` (legacy `hitl_approval` alias retained)
- `POST /hitl/{id}/decide` with persistence to `chat_messages.contexts.hitl_part`
- Deep-search checkpoints: `pre_search` + `pre_report`
- Frontend workspace: inline HITL card, history replay, `session_id` on decide

### Phase 4 · Engineering quality

- `.pre-commit-config.yaml`, `.gitlab-ci.yml`, `pyproject.toml` (uv)
- Unit tests for agent loop, HITL, deep search, tools, sandbox

> Note: Pipeline `pre_critique` HITL is not yet fully migrated to `HitlCoordinator` (planned for later v4.x).

## v3.0 — 2026-06-28

**Harness overhaul — harness as the single execution spine.**

- Wire `AppHarness.startup()/shutdown()` into FastAPI lifespan
- Route pipeline LLM calls through harness agents
- MCP server/client, OpenTelemetry, true async streaming, retries / rate limits
- Remove unreachable session/debate paths; Supervisor pattern for deep-search planning

## v2.0 — 2026-05-31

- Deep search with live source cards and streaming answers
- Chat history persistence and context compression
- Token accounting fixes
- UX polish (admin templates, settings API reset)
- Docker multi-stage image + Compose (MySQL + health checks)

## v1.0 — 2026-05-24

- Initial release: parse, translate, summarize, innovation review
- Heterogeneous DeepSeek + Qwen3 ToT collaboration
- ChromaDB + BM25 hybrid RAG
- SSE progress · JWT + email verification
