<h1 align="center">⚗️ Agent Paper Distiller</h1>

<p align="center">
  <a href="https://github.com/ByteTitan-star/Agent_PaperDistiller/releases/tag/v4.0.0"><img src="https://img.shields.io/badge/PaperDistiller-v4.0.0-6e40c9" alt="PaperDistiller v4.0.0" /></a>
  <img src="https://img.shields.io/badge/python-3.12-3776AB" alt="Python 3.12" />
  <img src="https://img.shields.io/badge/Vue-3-42b883" alt="Vue 3" />
  <img src="https://img.shields.io/badge/FastAPI-009688" alt="FastAPI" />
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT" /></a>
  <img src="https://img.shields.io/badge/English-0A66C2" alt="English" />
  <a href="./README_zh-CN.md"><img src="https://img.shields.io/badge/%E4%B8%AD%E6%96%87-555555" alt="Chinese" /></a>
</p>

<p align="center">
  <img src="./UI_figures/HOME.png" alt="PaperDistiller home" width="90%" />
</p>

> Turn long academic PDFs into bilingual drafts, structured summaries, and innovation insights —
> with a native multi-agent runtime, template-driven extraction, and human-in-the-loop deep research.

## What is PaperDistiller?

**Agent Paper Distiller** is a full-stack research workspace for academic paper distillation.

Upload a paper (PDF / Markdown / DOCX), choose an extraction template (Skill), and the system runs parse → translate → summarize → improve. A dual-pane workspace keeps the original PDF beside generated Markdown (with LaTeX), while RAG chat and deep search let you ask follow-up questions with live sources and HITL checkpoints.

Built with **Vue 3** + **FastAPI**, powered by a production **AgentLoop** runtime (tools, sandbox, sub-agents) and a paper pipeline orchestrator.

## Product flow

| Stage | Key action | Stage output |
| --- | --- | --- |
| Upload & configure | Upload a PDF, pick a domain template, set model / API preferences | A distillation task |
| Parse & translate | Extract text structure and produce a bilingual reading draft | Translation / layout draft |
| Summarize & improve | Template-guided extraction plus ToT-style critique / improvement | Summary + innovation notes |
| Workspace review | Read PDF + Markdown side by side, manage papers in the library | Curated paper assets |
| Ask & deep search | RAG Q&A or deep research with HITL plan / report checkpoints | Grounded answers + sources |

## Product interface

| Home / library | Dual-pane workspace |
| --- | --- |
| <img src="./UI_figures/papers_center.png" alt="Paper library" /> | <img src="./UI_figures/paper_analyse.png" alt="Paper workspace" /> |
| Browse papers, tags, and distillation status. | PDF on the left, generated Markdown + chat on the right. |

| One-tap distillation | Settings |
| --- | --- |
| <img src="./UI_figures/OneTap.png" alt="One-tap distill" /> | <img src="./UI_figures/setting_api.png" alt="API settings" /> |
| Start an end-to-end pipeline from upload. | Configure providers, keys, and collaboration mode. |

## Core features

| Feature | Description |
| --- | --- |
| End-to-end distillation | PDF parse → bilingual draft → template extraction → innovation / improvement proposals |
| Immersive workspace | Native PDF + Markdown/LaTeX dual pane with floating RAG chat |
| Native Agent runtime | `AgentLoop`, tool registry, sandbox, and sub-agent orchestration for deep search |
| HITL deep research | Human approval at `pre_search` / `pre_report` checkpoints with SSE streaming |
| Live progress (SSE) | Task broker pushes 0–100% status for pipeline jobs |
| Skill / template cards | Hot-swappable Markdown/JSON templates that become agent extraction instructions |
| Paper library | Card dashboard with search, domain tags, and content kinds |
| Production engineering | uv + pre-commit + unit tests + Docker Compose (MySQL + app) |

## Tech stack

| Layer | Stack |
| --- | --- |
| Frontend | Vue 3, Vite, Element Plus |
| Backend | FastAPI, SQLAlchemy (async), SSE |
| Agents | Native AgentLoop, harness orchestrator, optional MCP / OTel |
| Retrieval | ChromaDB + hybrid RAG |
| Models | DeepSeek / Qwen (and compatible OpenAI-style providers) |
| Doc parsing | FileRouter: PDF (PyMuPDF main path -> pypdf fallback) / Markdown / DOCX; optional MinerU / PaddleOCR / Mathpix / GROBID |
| Deploy | Docker multi-stage build + `docker-compose.yml` |

### PDF parsing engine

Papers are parsed once into a canonical `DocumentIR` (sections + nodes + preflight report, persisted as `parse_artifact.json`); every later step reuses that artifact. Structure-aware chunking keeps `$$...$$` formulas and Markdown tables atomic, chunks carry `element_type / section / page` metadata, and reference chunks are excluded from retrieval (per-paper RAG and cross-paper deep search) by default. With a formula backend enabled, the PyMuPDF path detects math-dense regions, crops them, converts to LaTeX, and backfills `$$...$$` blocks — falling back to raw glyphs when recognition fails.

Optional engines (off by default, graceful degradation when not installed):

```env
parser_backend=auto            # auto | pymupdf | pypdf | mineru
parser_mineru_enabled=false    # complex papers: layout + formula LaTeX + table HTML (requires mineru CLI)
parser_ocr_enabled=false       # scanned PDFs (requires paddleocr)
formula_backend=off            # off | mathpix | pix2text | paddle (local PP-FormulaNet: cropped regions -> LaTeX -> $$..$$, free & offline)
layout_detector=off            # off | doclayout: PP-DocLayout model detects formula regions (replaces glyph heuristic; `./scripts/download_models.sh` + `pip install paddlepaddle pillow`)
vlm_enabled=false              # figure crops -> VLM description (qwen-vl) -> image_desc chunks
vlm_mode=sync                  # sync (wait in pipeline) | async (background: instant indexing, descriptions backfill)
vector_store_mode=local        # local (embedded) | server (standalone Chroma, set VECTOR_SERVER_URL)
vector_collection_versioned=false  # isolate collections per embedding model + schema version
grobid_enabled=false           # scholarly metadata enrichment (title/authors/DOI/references)
translation_provider=auto      # auto | llm | google (LLM keeps $...$ LaTeX intact)
```

Re-uploading the same file (content-level SHA256) reuses the stored parse artifact instead of re-parsing; Mathpix results are cached by image hash to avoid duplicate billing.

## Quick start

### Prerequisites

- Python **3.12+**
- Node.js **18+**
- MySQL **8** (local or via Docker Compose)
- Optional: LLM API keys for real generation (otherwise mock / deterministic modes may apply depending on config)

### 1. Backend

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS / Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env.dev   # then fill DB + API keys
python main.py
```

> Default API base used by the frontend: `http://127.0.0.1:8001`

### 2. Frontend

```bash
cd frontend
npm install
echo "VITE_API_BASE_URL=http://127.0.0.1:8001" > .env
npm run dev
```

> Frontend: `http://127.0.0.1:5173`

### 3. Docker Compose (optional)

```bash
docker compose up --build
```

### 4. Developer tooling (optional)

```bash
./scripts/setup_dev.sh
pre-commit run --all-files
PYTHONPATH=backend pytest tests/unit -q
```

## Configuration

| Item | Notes |
| --- | --- |
| `backend/.env.dev` / `.env.prod` | Loaded by `APP_ENV` (see `backend/main.py`) |
| `backend/.env.example` | Safe template for keys, agent runtime, sandbox |
| `frontend/.env` | `VITE_API_BASE_URL` pointing at the API |
| Collaboration mode | e.g. ToT / supervisor patterns via settings |
| HITL | Deep-search checkpoints require an authenticated session |

**Do not commit real `.env` files.** Examples stay in git; local secrets are gitignored.

## Repository layout

```text
backend/app/
  agent/          # Native AgentLoop runtime
  harness/        # Pipeline orchestrator, agents, MCP / HITL helpers
  services/       # Chat, deep search, HITL coordination
  tools/          # Web / arXiv / sandbox / sub-agent tools
  routers/        # FastAPI HTTP API
frontend/src/     # Vue 3 workspace & library UI
tests/            # Unit / integration tests
docs/             # Architecture notes, roadmaps, and analysis (see docs/README.md)
UI_figures/       # Product screenshots
```

## Changelog

See [CHANGELOG.md](./CHANGELOG.md) for release history (`v1.0` → `v4.0.0`).

## License

MIT © [ByteTitan-star](https://github.com/ByteTitan-star), 2026 — see [LICENSE](LICENSE).
