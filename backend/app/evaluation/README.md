# RAG 评估模块（RAGAS）

对项目内两套 RAG 做质量评估，框架选用 **RAGAS**，judge/合成 LLM 用项目内 **DeepSeek**（OpenAI 兼容端点），合成器聚类嵌入用本地 **sentence-transformers**。

## 评估对象

1. **论文正文 RAG** — chat 面板的 Q&A：向量(ChromaDB)+BM25 混合检索 + DeepSeek 生成。
   - 检索复用 `backend/app/services/chat.py:retrieve_contexts`
   - 解析/切块复用 `backend/app/pipeline/document_parser.py`
   - 指标：`faithfulness` / `answer_relevancy` / `context_precision` / `context_recall`

2. **Agent Skill 检索 RAG** — `SkillRegistry.select_tools` 的语义召回（检索即用，无生成）。
   - 指标：`skill_recall@k`（期望技能是否进 top-k）、`MRR`、`llm_context_recall`

## 为什么评估侧自建 Storage/SkillRegistry

项目运行时嵌入模型是本地 `models/bge-m3`（约 2GB，需手动下载），未下载时原生向量库无法初始化。
为让评估自包含，评估侧在 `runner.py` 自建一套 `Storage` / `SkillRegistry`，改用公开小模型
`sentence-transformers/all-MiniLM-L6-v2`（首次自动从 HuggingFace 镜像下载），数据目录隔离到
`backend/data/eval_data`，不污染应用数据。测试集合成的聚类嵌入也用同一小模型。

> 注意：评估用的嵌入模型与生产 bge-m3 不同，检索绝对分数会与生产略有差异；评估主要用于**回归对比**
> （改动检索/生成前后看指标变化），而非与外部系统横比。

## 测试集

- **论文**：用 RAGAS `TestsetGenerator` 从入库后的 paper chunks 合成 (question, reference, reference_contexts)。
- **Skill**：直接让 DeepSeek 为每个技能生成若干"应召回该技能"的自然语言 query，ground truth = 技能描述。

## 安装

```bash
uv sync --group eval
```

依赖组 `eval`（`pyproject.toml`）：`ragas`、`langchain-huggingface`、`langchain-community`。
本机为 macOS 13 时，`[tool.uv] override-dependencies` 已把 `onnxruntime`/`torch` 钉到兼容版本。

## 配置

`backend/.env.dev`（已被 `.gitignore`，不会提交）：

```
EVAL_ENABLED=true
EVAL_JUDGE_API_KEY=sk-...
EVAL_JUDGE_BASE_URL=https://api.deepseek.com
EVAL_JUDGE_MODEL=deepseek-chat          # 评估/合成模型
EVAL_TESTSET_SIZE=8                     # 每套 RAG 合成的问题数
EVAL_PAPER_TOP_K=4
EVAL_SKILL_TOP_K=5
EVAL_PAPER_ID=eval-paper
EVAL_PAPER_PATH=/Users/wangxin/Desktop/xin/IJCNN/paper.pdf
EVAL_REPORT_DIR=data/eval_reports
```

> Judge 模型可任意覆盖，例如 `EVAL_JUDGE_MODEL=deepseek-v4-flash`。空 `EVAL_JUDGE_API_KEY` 回落到 `DEEPSEEK_API_KEY`。

## 运行

### 独立脚本

```bash
.venv/bin/python scripts/run_rag_eval.py
# 或覆盖参数
.venv/bin/python scripts/run_rag_eval.py --pdf /path/to/paper.pdf --testset-size 5 --judge-model deepseek-chat
```

输出：`backend/data/eval_reports/rag_eval_<ts>.{json,md}`。

### pytest（默认跳过端到端用例，避免消耗 LLM）

```bash
# 烟囱用例（导入/配置/报告格式化），默认运行
.venv/bin/pytest tests/eval

# 端到端用例，需显式开启
.venv/bin/pytest tests/eval --run-eval -m eval
```

## 报告样例

```markdown
# RAG 评估报告
## 1. 论文正文 RAG
| 指标 | 值 |
| --- | --- |
| faithfulness | 0.8123 |
| answer_relevancy | 0.7654 |
| context_precision | 0.6900 |
| context_recall | 0.7200 |
## 2. Agent Skill 检索 RAG
| skill_recall@k | 0.8750 |
| MRR | 0.6667 |
| llm_context_recall | 0.8000 |
```

## 目录结构

```
backend/app/evaluation/
├── __init__.py        # 暴露 run_rag_evaluation
├── llm.py             # 构建 RAGAS 的 DeepSeek LLM + 本地 embeddings
├── datasets.py        # 合成论文测试集 + 技能测试集
├── paper_rag.py       # 论文入库 + 检索 + 生成 + RAGAS 四指标
├── skill_rag.py       # 技能检索评估（recall@k/MRR/LLMContextRecall）
├── report.py          # JSON + Markdown 报告
└── runner.py          # 编排入口（自建隔离 storage，端到端跑+落盘）
scripts/run_rag_eval.py # CLI 一键脚本
tests/eval/             # pytest 集成（含 --run-eval 端到端用例）
```
