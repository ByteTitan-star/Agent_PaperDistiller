"""评估编排入口：自建隔离的 Storage/SkillRegistry，跑两套 RAG，产出 JSON+Markdown 报告。

整个端到端流程在这里串起来：

    自建隔离 storage / skill_registry
        ↓
    入库论文 PDF（解析→切块→向量库）
        ↓
    合成两套测试集（论文用 RAGAS 生成器；技能用 LLM 手写合成）
        ↓
    跑论文 RAG 评估 + 技能检索评估
        ↓
    格式化报告 → 落盘 JSON + Markdown

为什么自建 storage：项目运行时用的嵌入模型是本地 ``models/bge-m3``（约 2GB，需手动下载），
本机没下载时原生向量库根本起不来。为了让评估自包含，这里另建一套 Storage/SkillRegistry，
改用公开小模型 ``all-MiniLM-L6-v2``（首次自动从 HuggingFace 下载），数据目录隔离到
``backend/data/eval_data``，不碰应用数据。

代价：评估用的嵌入和生产 bge-m3 不同，检索绝对分会有差异——所以这套评估主要用于
"回归对比"（改动检索/生成前后看指标变化），别拿它跟外部系统横比绝对值。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import Settings, get_settings
from . import datasets, paper_rag, report, skill_rag

logger = logging.getLogger("evaluation.runner")

# 评估侧统一使用的小型公开嵌入模型（避免依赖本机未下载的 bge-m3）
EVAL_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def _build_eval_storage(settings: Settings):
    """构造评估专用的 Storage（本地嵌入 + 隔离数据目录）。

    跟 app/dependencies.py 里的全局 storage 不同的点：
      - embedding_model_name 用 all-MiniLM-L6-v2（不是 bge-m3）
      - base_dir 指向 backend/data/eval_data（隔离，不污染应用数据）
      - oss_client=None（评估走本地，不传 OSS）
    """
    from ..storage import Storage

    backend_root = Path(__file__).resolve().parent.parent.parent  # .../backend
    eval_data_dir = backend_root / "data" / "eval_data"
    eval_data_dir.mkdir(parents=True, exist_ok=True)
    templates_dir = backend_root / settings.templates_dir
    return Storage(
        base_dir=eval_data_dir,
        templates_dir=templates_dir,
        vector_provider=settings.vector_store_provider,
        vector_collection_name=settings.vector_collection_name,
        vector_db_subdir=settings.vector_db_subdir,
        embedding_model_name=EVAL_EMBEDDING_MODEL,
        vector_distance_metric=settings.vector_distance_metric,
        oss_client=None,
    )


def _build_eval_skill_registry(settings: Settings):
    """构造评估专用的 SkillRegistry（本地嵌入 + 隔离向量库目录）。

    同样改用 all-MiniLM-L6-v2，向量库存到隔离目录。skills_root 指向项目自带的技能卡
    （backend/app/skills），所以评估的还是真实的技能库。
    """
    from ..agent_skills import SkillRegistry

    backend_root = Path(__file__).resolve().parent.parent.parent
    app_root = backend_root / "app"
    eval_vec_dir = backend_root / "data" / "eval_data" / settings.vector_db_subdir
    eval_vec_dir.mkdir(parents=True, exist_ok=True)
    reg = SkillRegistry(
        skills_root=app_root / settings.agent_skills_dir,
        vector_db_dir=eval_vec_dir,
        embedding_model_name=EVAL_EMBEDDING_MODEL,
        provider=settings.vector_store_provider,
        collection_name=settings.skills_collection_name,
    )
    reg.load()  # 扫描磁盘技能卡 + 建语义索引（首次会下载嵌入模型）
    return reg


def run_rag_evaluation(settings: Settings | None = None) -> dict[str, Any]:
    """端到端跑两套 RAG 评估，返回 report dict 并把 JSON + Markdown 报告落盘。

    这是对外主入口，scripts/run_rag_eval.py 和 tests/eval 端到端用例都调它。
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = settings or get_settings()

    # 前置检查
    if not s.eval_enabled:
        raise RuntimeError("评估已禁用（EVAL_ENABLED=false）。")
    if not s.eval_paper_path:
        raise RuntimeError("未配置评估 PDF 路径（EVAL_PAPER_PATH）。")

    # 让 RAGAS 合成器聚类也用同一小嵌入模型（覆盖 settings.eval_embedding_model，
    # 这样 llm.build_eval_embeddings 取到的就是 all-MiniLM-L6-v2）
    s.eval_embedding_model = EVAL_EMBEDDING_MODEL

    # 1. 构建评估侧的隔离 storage / skill_registry
    logger.info("== 构建评估侧 Storage / SkillRegistry ==")
    storage = _build_eval_storage(s)
    skill_registry = _build_eval_skill_registry(s)

    paper_id = s.eval_paper_id
    pdf_path = s.eval_paper_path

    # 2. 入库论文：解析 PDF → 切块 → 进 ChromaDB（同时拿到 chunks 供下一步合成测试集用）
    logger.info("== 入库论文 %s ==", pdf_path)
    chunks = paper_rag.ingest_paper_for_eval(paper_id, pdf_path, storage, s)

    # 3. 合成论文测试集（RAGAS TestsetGenerator，会多次调 DeepSeek，耗时随条数增长）
    logger.info("== 合成论文测试集（%d 条）==", s.eval_testset_size)
    paper_testset = datasets.synthesize_paper_testset(
        chunks, testset_size=s.eval_testset_size, settings=s
    )

    # 4. 合成技能测试集（手写 LLM 合成，每个技能生成 2 条 query）
    logger.info("== 合成 Skill 测试集 ==")
    skills_meta = skill_rag._list_skills(skill_registry)
    skill_testset = datasets.synthesize_skill_dataset(
        skills_meta, queries_per_skill=2, settings=s
    )

    # 5. 跑论文正文 RAG 评估（检索+生成+RAGAS 四指标）
    logger.info("== 跑论文正文 RAG 评估 ==")
    paper_result = paper_rag.run_paper_rag_eval(
        paper_id=paper_id, testset=paper_testset, storage=storage, settings=s
    )

    # 6. 跑技能检索评估（recall@k / MRR / LLMContextRecall）
    logger.info("== 跑 Skill 检索 RAG 评估 ==")
    skill_result = skill_rag.run_skill_rag_eval(
        skill_registry=skill_registry, testset=skill_testset, settings=s
    )

    # 7. 组装报告元数据 + 落盘
    meta = {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "judge_model": s.eval_judge_model,
        "embedding_model": EVAL_EMBEDDING_MODEL,
        "paper_path": pdf_path,
    }
    report_dict = report.build_report(
        paper_result=paper_result, skill_result=skill_result, meta=meta
    )

    backend_root = Path(__file__).resolve().parent.parent.parent
    report_dir = backend_root / s.eval_report_dir  # 默认 backend/data/eval_reports
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    json_path = report_dir / f"rag_eval_{ts}.json"
    md_path = report_dir / f"rag_eval_{ts}.md"
    json_path.write_text(report.to_json(report_dict), encoding="utf-8")
    md_path.write_text(report.to_markdown(report_dict), encoding="utf-8")
    logger.info("报告已写出：%s / %s", json_path, md_path)

    # 把报告路径挂到返回值上，方便调用方（脚本/测试）直接打印
    report_dict["_report_paths"] = {"json": str(json_path), "markdown": str(md_path)}
    return report_dict
