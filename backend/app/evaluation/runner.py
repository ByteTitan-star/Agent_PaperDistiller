"""评估编排入口：自建隔离的 Storage/SkillRegistry，跑两套 RAG，产出 JSON+Markdown 报告。

为避免依赖本机未下载的 ``models/bge-m3``（项目运行时嵌入模型），评估侧自建一套
``Storage`` / ``SkillRegistry``，改用公开小模型 ``all-MiniLM-L6-v2``（首次自动下载），
数据目录隔离到 ``backend/data/eval_data``，不污染应用数据。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import Settings, get_settings
from . import datasets, paper_rag, report, skill_rag

logger = logging.getLogger("evaluation.runner")

# 评估侧统一使用的小型公开嵌入模型（避免依赖本机 bge-m3）
EVAL_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def _build_eval_storage(settings: Settings):
    """构造评估专用的 Storage（本地嵌入 + 隔离数据目录）。"""
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
    """构造评估专用的 SkillRegistry（本地嵌入 + 隔离向量库目录）。"""
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
    reg.load()
    return reg


def run_rag_evaluation(settings: Settings | None = None) -> dict[str, Any]:
    """端到端跑两套 RAG 评估，返回 report dict 并落盘 JSON + Markdown。"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = settings or get_settings()

    if not s.eval_enabled:
        raise RuntimeError("评估已禁用（EVAL_ENABLED=false）。")
    if not s.eval_paper_path:
        raise RuntimeError("未配置评估 PDF 路径（EVAL_PAPER_PATH）。")

    # 让 RAGAS 合成器也复用同一小嵌入模型（覆盖 settings.eval_embedding_model）
    s.eval_embedding_model = EVAL_EMBEDDING_MODEL

    logger.info("== 构建评估侧 Storage / SkillRegistry ==")
    storage = _build_eval_storage(s)
    skill_registry = _build_eval_skill_registry(s)

    paper_id = s.eval_paper_id
    pdf_path = s.eval_paper_path

    logger.info("== 入库论文 %s ==", pdf_path)
    chunks = paper_rag.ingest_paper_for_eval(paper_id, pdf_path, storage, s)

    logger.info("== 合成论文测试集（%d 条）==", s.eval_testset_size)
    paper_testset = datasets.synthesize_paper_testset(
        chunks, testset_size=s.eval_testset_size, settings=s
    )

    logger.info("== 合成 Skill 测试集 ==")
    skills_meta = skill_rag._list_skills(skill_registry)
    skill_testset = datasets.synthesize_skill_dataset(
        skills_meta, queries_per_skill=2, settings=s
    )

    logger.info("== 跑论文正文 RAG 评估 ==")
    paper_result = paper_rag.run_paper_rag_eval(
        paper_id=paper_id, testset=paper_testset, storage=storage, settings=s
    )

    logger.info("== 跑 Skill 检索 RAG 评估 ==")
    skill_result = skill_rag.run_skill_rag_eval(
        skill_registry=skill_registry, testset=skill_testset, settings=s
    )

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
    report_dir = backend_root / s.eval_report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    json_path = report_dir / f"rag_eval_{ts}.json"
    md_path = report_dir / f"rag_eval_{ts}.md"
    json_path.write_text(report.to_json(report_dict), encoding="utf-8")
    md_path.write_text(report.to_markdown(report_dict), encoding="utf-8")
    logger.info("报告已写出：%s / %s", json_path, md_path)

    report_dict["_report_paths"] = {"json": str(json_path), "markdown": str(md_path)}
    return report_dict
