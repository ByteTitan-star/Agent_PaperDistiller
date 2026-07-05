<div align="right">

**English** | [简体中文](./README_zh-CN.md)

</div>

# ⚗️ PaperDistiller: Multi-Agent Academic Paper Distillation Platform

![PaperDistiller home screen](./UI_figures/HOME.png)

> **A heterogeneous multi-agent platform for targeted academic paper distillation — powered by DeepSeek-V3.2 and Qwen3**
>
> Stop reading papers aimlessly. Define your own extraction focus (Skills), then let a dual-model multi-agent pipeline distill long conference papers into the structure, code logic, and innovation insights you actually need.

## 🌟 Overview

![PaperDistiller workspace](./UI_figures/OneTap.png)

**PaperDistiller** is a full-stack research assistant built with **Vue 3** (frontend) and **FastAPI** (backend). It is more than a PDF viewer — it is a highly customizable **literature distillation engine**.

The system automates PDF parsing, full-text translation, structured summarization, and innovation analysis. Multi-agent collaboration combines DeepSeek-V3.2 for generation with Qwen3 for independent review, using Tree of Thoughts (ToT) to deliver deep paper analysis and actionable improvement suggestions.

Upload a PDF, pick an extraction template (e.g. `template.md`), and the pipeline runs parse → translate → summarize → improve — all inside an immersive dual-pane workspace with RAG chat.

## ✨ Key Features

- **🚀 End-to-end distillation pipeline**
  - PDF parsing → bilingual translation draft → focused extraction → innovation & improvement proposals
- **👁️ Immersive workspace**
  - Native PDF on the left; generated Markdown (with LaTeX) on the right
  - Floating RAG chat panel for in-context Q&A
- **📊 Live progress via SSE**
  - `TaskBroker` + Server-Sent Events show 0–100% task progress in real time
- **🗂️ Local paper library (Dashboard)**
  - Card-based management with search and domain tags (e.g. LLM, CV, Backdoor Attacks)
- **🛠️ Extensible Skill-Cards**
  - Hot-swappable Markdown/JSON templates — your reading habits become agent instructions

## 🚀 Quick Start

The default mock pipeline runs end-to-end without external LLM API keys.

### 1. Backend (FastAPI)

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

> Backend: `http://127.0.0.1:8000`

### 2. Frontend (Vue 3)

```bash
cd frontend
npm install
npm run dev
```

> Frontend: `http://127.0.0.1:5173`

### 3. Developer setup (optional)

```bash
./scripts/setup_dev.sh   # uv sync + pre-commit hooks
pre-commit run --all-files
PYTHONPATH=backend pytest tests/unit -q
```

## 📋 Changelog

### v4.0 (2026-07-05)

**Native Agent runtime + bioagent HITL alignment — from LangGraph bypass to production AgentLoop.** Built on the v3.0 harness foundation, v4.0 adds a standalone `agent/` runtime (Loop / Bootstrap / Worker / SubAgent), routes deep search and the paper pipeline through a unified orchestrator, aligns human-in-the-loop (HITL) with the bioagent protocol, and ships CI / pre-commit / 68 unit tests.

**Phase 0 · Native Agent runtime**

- New `backend/app/agent/`: `AgentLoop`, `RuntimeBundle`, `InMemoryStreamBus`, auto-discovering `ToolRegistry`
- New `backend/app/tools/`: web_search, arxiv_search, spawn_sub_agent, wait_sub_agents, pipeline_steps, execute_code, shell_command
- New `backend/app/sandbox/` for isolated code-skill execution
- Deep search in `services/agent_chat.py` uses native AgentLoop streaming SSE (replaces legacy LangGraph ReAct path)

**Phase 1 · Pipeline consolidation**

- `harness/pipeline/orchestrator.py` is the sole paper-distillation entry; `worker.py` only calls the orchestrator
- Removed dead code: `pipeline/workflow_graph.py`, `harness/react/langgraph_agent.py`, legacy harness pipeline adapters
- All business modules kept: document_parser, translator, tot_generator, renderer, etc.

**Phase 2 · P0 production fixes**

- Per-user LLM config: no global runtime mutation; task-scoped `TurnConfig.user_settings`
- Resilient `SubAgentStore` with graceful fallback; `AgentWorker` lifecycle and error isolation
- `user_settings.py` centralizes API keys and model config

**Phase 3 · HITL bioagent protocol (P1)**

- New `HitlCoordinator`: `HITL_REQUEST` / `HITL_RESPONSE` on StreamBus + `HitlWaiterRegistry`
- SSE type `hitl_request` (with `biomap_hil` wrapper; legacy `hitl_approval` alias for compatibility)
- `POST /hitl/{id}/decide` → store update + StreamBus response + persistence to `chat_messages.contexts.hitl_part`
- Deep search checkpoints: `pre_search` (plan modal) + `pre_report` (inline review while tokens stream; `done` deferred until approval)
- Frontend `WorkspaceView`: inline HITL card, history replay, `session_id` on decide

**Phase 4 · Engineering quality**

- `.pre-commit-config.yaml` (ruff, mypy, bandit, secret-scan, markdownlint, conventional commits)
- `.gitlab-ci.yml` + `pyproject.toml` (uv, `scripts/setup_dev.sh`)
- Test suite: 68 unit tests (agent loop, HITL coordinator, deep search, tools, sandbox)

> Note: Pipeline `pre_critique` HITL (`harness HITLManager`) is not yet on `HitlCoordinator` (planned for v4.x). Verify end-to-end with MySQL + API keys.

---

### v3.0 (2026-06-28)

**Harness overhaul — harness as the single execution spine.** Fixed a critical flaw where `AppHarness.startup()` was never called, leaving the entire harness layer dead at runtime while traffic bypassed it via `pipeline/workflow_graph`. v3.0 wires harness into all LLM and tool execution and adds MCP, OTel, true async streaming, and resilience.

**Phase 0 · Foundation**

- `AppHarness.startup()/shutdown()` in FastAPI lifespan
- Singleton consolidation in `dependencies.py`
- HITL fix: `interrupt()` + `wait_for_decision()`
- `worker.py` → `pipeline_orchestrator.run()`

**Phase 1 · Pipeline LLM via harness agents**

- Unified `DeepSeekAgent` / `ToTAgent` delegation
- Per-task API key fix
- Token logging via `BaseAgent.on_post_run`

**Phase 2 · Dead code + Supervisor**

- Removed `harness/session/`, `DebatePattern`
- `HarnessToolRegistry` as sole tool execution surface
- `SupervisorPattern` for deep-search planning (opt-in)

**Phase 3 · MCP**

- `harness/mcp/`: FastMCP server + ReAct client

**Phase 4 · OpenTelemetry**

- Self-hosted OTel (Jaeger / Tempo / console)

**Phase 5 · True async streaming**

- `AsyncOpenAI` token iteration without blocking the event loop

**Phase 6 · Resilience**

- tenacity retries, `BaseAgent.aclose()`, `RateLimiter` on tool registry

---

### v2.0 (2026-05-31)

**Deep search** · LangGraph ReAct · live source cards · streaming answers & thinking chain

**Chat history** · ChatSession / ChatMessage · context compression

**Token accounting** · fixed zero-write bug · stream token logging

**UX** · current date in system prompt · admin templates · API reset in settings

**Docker** · multi-stage Dockerfile + docker-compose (MySQL + health checks)

---

### v1.0 (2026-05-24)

- Initial release: parse, translate, summarize, innovation review
- LangGraph StateGraph pipeline
- DeepSeek + Qwen3 heterogeneous ToT
- ChromaDB + BM25 hybrid RAG
- SSE progress · JWT + email verification

---

*Developed with ❤️ by [ByteTitan-star](https://github.com/ByteTitan-star)*
