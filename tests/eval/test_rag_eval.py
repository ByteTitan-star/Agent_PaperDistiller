"""RAG 评估模块的 pytest 集成。

- 烟囱用例（始终运行）：模块可导入、配置字段存在、报告格式化器工作。
- 端到端用例（opt-in，需 --run-eval）：用 /Users/wangxin/Desktop/xin/IJCNN/paper.pdf 跑完整评估，
  会调用 DeepSeek API 产生费用，默认跳过。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.config import get_settings
from app.evaluation import datasets, paper_rag, report, runner, skill_rag
from app.evaluation.runner import EVAL_EMBEDDING_MODEL  # noqa: F401  确认可导入

BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend"
PAPER_PDF = Path("/Users/wangxin/Desktop/xin/IJCNN/paper.pdf")


def test_module_importable() -> None:
    """评估模块可正常导入（不触发 LLM/网络）。"""
    assert hasattr(runner, "run_rag_evaluation")
    assert hasattr(paper_rag, "run_paper_rag_eval")
    assert hasattr(skill_rag, "run_skill_rag_eval")


def test_eval_config_fields() -> None:
    """Settings 暴露评估相关配置项。"""
    s = get_settings()
    for attr in (
        "eval_enabled", "eval_judge_model", "eval_testset_size",
        "eval_paper_top_k", "eval_skill_top_k", "eval_report_dir",
        "eval_paper_path",
    ):
        assert hasattr(s, attr), f"Settings 缺少 {attr}"


def test_report_formatter() -> None:
    """报告格式化器对空结果也能产出合法 JSON / Markdown。"""
    paper = {"n": 0, "metrics": {}, "samples": []}
    skill = {"n": 0, "metrics": {}, "samples": []}
    meta = {"generated_at": "2026-07-14T00:00:00Z", "judge_model": "deepseek-chat",
            "embedding_model": "all-MiniLM-L6-v2", "paper_path": "/tmp/x.pdf"}
    rep = report.build_report(paper_result=paper, skill_result=skill, meta=meta)
    assert json.loads(report.to_json(rep))["paper_rag"]["n"] == 0
    md = report.to_markdown(rep)
    assert "RAG 评估报告" in md


def test_paper_ingest_smoke() -> None:
    """ingest_paper_for_eval 会把切块结果写回 storage（验证 wiring，不依赖真实 PDF 解析）。"""
    s = get_settings()
    fake_storage = MagicMock()
    fake_storage.save_chunks = MagicMock()
    fake_storage.upsert_chunks = MagicMock()
    # extract_text_from_pdf 对不存在路径会回退为错误提示文本而非抛错，但仍会走完切块+入库流程
    chunks = paper_rag.ingest_paper_for_eval("p", "/nonexistent/xxx.pdf", fake_storage, s)
    assert isinstance(chunks, list) and len(chunks) >= 1
    fake_storage.save_chunks.assert_called_once()


@pytest.mark.eval
def test_run_rag_evaluation_e2e(run_eval_opt: bool) -> None:
    """端到端跑两套 RAG 评估（需要 --run-eval + 真实 DeepSeek key + PDF 存在）。"""
    if not run_eval_opt:
        pytest.skip("加 --run-eval 以运行 RAG 评估端到端用例（会产生 LLM 调用费用）")
    if not PAPER_PDF.exists():
        pytest.skip(f"评估 PDF 不存在：{PAPER_PDF}")
    # config.py 的 env_file 相对 CWD（仓库根），pytest 从根运行时读不到 backend/.env.dev，
    # 这里显式载入并清除 lru_cache，让 get_settings() 返回带 key 的配置。
    env_file = BACKEND_ROOT / ".env.dev"
    if env_file.exists():
        try:
            from dotenv import load_dotenv

            load_dotenv(env_file, override=False)
        except ImportError:
            pass
    get_settings.cache_clear()
    s = get_settings()
    if not s.eval_paper_path or s.eval_judge_api_key in ("", "your-api-key"):
        pytest.skip("未配置 EVAL_JUDGE_API_KEY")
    s.eval_testset_size = 3  # 控制成本
    result = runner.run_rag_evaluation(s)
    assert "paper_rag" in result and "skill_rag" in result
    assert result["paper_rag"]["n"] >= 1
    assert result["skill_rag"]["n"] >= 1
