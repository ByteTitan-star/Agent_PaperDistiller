#!/usr/bin/env python
"""RAG 评估一键脚本。

用法：
    python scripts/run_rag_eval.py
    # 或指定 PDF 与参数
    python scripts/run_rag_eval.py --pdf /path/to/paper.pdf --testset-size 8

输出：backend/data/eval_reports/rag_eval_<ts>.{json,md}

依赖：需安装 eval 依赖组 —— `uv sync --group eval`。
配置：见 backend/.env.dev（EVAL_JUDGE_API_KEY / EVAL_JUDGE_MODEL 等）。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 让脚本可以直接 `python scripts/run_rag_eval.py` 运行：把 backend/ 加入 sys.path
BACKEND_ROOT = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# config.py 的 env_file 是相对 CWD 的 ".env.<APP_ENV>"，脚本从仓库根运行时无法命中，
# 这里先把 backend/.env.<env> 显式载入 os.environ，再让 Settings 读取。
_env_file = BACKEND_ROOT / f".env.{os.getenv('APP_ENV', 'dev')}"
if _env_file.exists():
    try:
        from dotenv import load_dotenv

        load_dotenv(_env_file, override=False)
    except ImportError:
        # python-dotenv 未装时退化为手动解析
        for _line in _env_file.read_text().splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description="RAGAS RAG 评估：论文正文 RAG + Skill 检索 RAG")
    parser.add_argument("--pdf", default=None, help="评估用 PDF 路径（默认读 .env.dev 的 EVAL_PAPER_PATH）")
    parser.add_argument("--paper-id", default=None, help="论文入库用的 paper_id（默认 EVAL_PAPER_ID）")
    parser.add_argument("--testset-size", type=int, default=None, help="每套 RAG 合成的问题数")
    parser.add_argument("--judge-model", default=None, help="覆盖 judge/合成模型 id")
    parser.add_argument("--report-dir", default=None, help="报告输出目录")
    args = parser.parse_args()

    from app.config import get_settings
    from app.evaluation import run_rag_evaluation

    settings = get_settings()
    if args.pdf:
        settings.eval_paper_path = str(Path(args.pdf).resolve())
    if args.paper_id:
        settings.eval_paper_id = args.paper_id
    if args.testset_size is not None:
        settings.eval_testset_size = args.testset_size
    if args.judge_model:
        settings.eval_judge_model = args.judge_model
    if args.report_dir:
        settings.eval_report_dir = args.report_dir

    report = run_rag_evaluation(settings)
    print("\n=== 评估完成 ===")
    print(f"报告：{report['_report_paths']['json']}")
    print(f"      {report['_report_paths']['markdown']}")
    print("\n指标摘要：")
    pm = report["paper_rag"]["metrics"]
    sm = report["skill_rag"]["metrics"]
    print(f"  论文 RAG  : faithfulness={pm.get('faithfulness')} "
          f"answer_relevancy={pm.get('answer_relevancy')} "
          f"context_precision={pm.get('context_precision')} "
          f"context_recall={pm.get('context_recall')}")
    print(f"  Skill RAG : skill_recall@k={sm.get('skill_recall_at_k')} mrr={sm.get('mrr')} "
          f"context_recall={sm.get('context_recall')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
