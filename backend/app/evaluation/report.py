"""评估报告格式化：JSON + Markdown。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any


def _fmt_metric(v: Any) -> str:
    if v is None:
        return "N/A"
    try:
        return f"{float(v):.4f}"
    except (TypeError, ValueError):
        return str(v)


def build_report(
    *,
    paper_result: dict[str, Any],
    skill_result: dict[str, Any],
    meta: dict[str, Any],
) -> dict[str, Any]:
    return {
        "generated_at": meta.get("generated_at"),
        "judge_model": meta.get("judge_model"),
        "embedding_model": meta.get("embedding_model"),
        "test_pdf": meta.get("paper_path"),
        "paper_rag": {
            "n": paper_result.get("n", 0),
            "metrics": paper_result.get("metrics", {}),
        },
        "skill_rag": {
            "n": skill_result.get("n", 0),
            "metrics": skill_result.get("metrics", {}),
        },
        "samples": {
            "paper": paper_result.get("samples", []),
            "skill": skill_result.get("samples", []),
        },
    }


def to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2)


def to_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# RAG 评估报告")
    lines.append("")
    lines.append(f"- 生成时间：{report.get('generated_at', 'N/A')}")
    lines.append(f"- Judge/合成模型：{report.get('judge_model', 'N/A')}")
    lines.append(f"- 合成嵌入模型：{report.get('embedding_model', 'N/A')}")
    lines.append(f"- 测试 PDF：`{report.get('test_pdf', 'N/A')}`")
    lines.append("")

    paper = report.get("paper_rag", {})
    skill = report.get("skill_rag", {})
    pm = paper.get("metrics", {})
    sm = skill.get("metrics", {})

    lines.append("## 1. 论文正文 RAG（向量+BM25 混合检索 + DeepSeek 生成）")
    lines.append("")
    lines.append(f"- 样本数：**{paper.get('n', 0)}**")
    lines.append("| 指标 | 值 |")
    lines.append("| --- | --- |")
    lines.append(f"| faithfulness | {_fmt_metric(pm.get('faithfulness'))} |")
    lines.append(f"| answer_relevancy | {_fmt_metric(pm.get('answer_relevancy'))} |")
    lines.append(f"| context_precision | {_fmt_metric(pm.get('context_precision'))} |")
    lines.append(f"| context_recall | {_fmt_metric(pm.get('context_recall'))} |")
    lines.append("")

    lines.append("## 2. Agent Skill 检索 RAG")
    lines.append("")
    lines.append(f"- 样本数：**{skill.get('n', 0)}**")
    lines.append("| 指标 | 值 |")
    lines.append("| --- | --- |")
    lines.append(f"| skill_recall@k | {_fmt_metric(sm.get('skill_recall_at_k'))} |")
    lines.append(f"| MRR | {_fmt_metric(sm.get('mrr'))} |")
    lines.append(f"| context_recall (LLMContextRecall) | {_fmt_metric(sm.get('context_recall'))} |")
    lines.append("")

    lines.append("## 指标说明")
    lines.append("- **faithfulness**：答案对 retrieved contexts 的忠实度（是否编造）。")
    lines.append("- **answer_relevancy**：答案对问题的相关性。")
    lines.append("- **context_precision**：检索结果中相关 context 的精度。")
    lines.append("- **context_recall**：金标准 context 被检索命中的覆盖率。")
    lines.append("- **skill_recall@k**：期望技能出现在 top-k 召回中的比例。")
    lines.append("- **MRR**：期望技能在召回中的平均倒数排名。")
    lines.append("")
    return "\n".join(lines)
