# Markdown/HTML报告生成
import html
import re
from typing import Any

from .common_utils import utc_now_iso
from ..config import Settings
from .llm_extractor import (
    collect_key_sentences,
    extract_backdoor_indicators,
    extract_backdoor_structured_info,
)
from ..storage import domain_tag_from_template
from .tot_generator import build_multi_agent_collaboration_label, generate_innovation_ideas


def make_translation_markdown(
    title: str,
    target_language: str,
    translated_sections: list[tuple[str, str]],
    translation_failures: int,
) -> str:
    """
    【生成翻译 Markdown】
    生成全文翻译的 Markdown 文件内容。

    内容结构：
    - 文件标题和元信息（论文标题、生成时间）
    - 翻译失败警告（如有）
    - 按章节组织的翻译内容

    参数:
        title: 论文标题
        target_language: 目标语言（如"中文"）
        translated_sections: 翻译后的章节列表 [(章节标题, 章节内容), ...]
        translation_failures: 翻译失败的段落数量

    返回:
        完整的 Markdown 格式字符串
    """
    lines = [
        f"# 全文翻译（{target_language}）",
        "",
        f"- 论文标题：{title}",
        f"- 生成时间：{utc_now_iso()}",
        "- 说明：以下按文章章节组织翻译结果，术语（如 MNIST、ResNet、ASR）保留英文。",
    ]

    if translation_failures > 0:
        lines.append(f"- 警告：有 {translation_failures} 段翻译服务失败，已回退原文。")

    lines.append("")

    if not translated_sections:
        lines.extend(["## 内容为空", "未提取到可读文本。", ""])
        return "\n".join(lines)

    for section_title, section_content in translated_sections:
        heading = section_title if section_title.strip().startswith("#") else f"## {section_title}"
        lines.append(heading)
        lines.append("")
        lines.append(section_content or "N/A")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def make_translation_layout_html(
    title: str,
    target_language: str,
    translated_sections: list[tuple[str, str]],
) -> str:
    """
    【生成翻译 HTML 页面】
    生成双栏阅读版 HTML，用于前端新窗口阅读。

    样式特点：
    - 双栏布局（类似学术论文版式）
    - 响应式设计（移动端自动变为单栏）
    - 页码标记转换为小标题

    参数:
        title: 论文标题
        target_language: 目标语言
        translated_sections: 翻译后的章节列表

    返回:
        完整的 HTML 字符串
    """
    rendered_blocks: list[str] = []
    for section_title, section_content in translated_sections:
        heading_mark = section_title.strip()
        heading_level = 2
        heading_text = heading_mark
        if heading_mark.startswith("#"):
            heading_level = min(4, max(2, heading_mark.count("#")))
            heading_text = heading_mark.lstrip("#").strip()
        safe_title = html.escape(heading_text)
        safe_text = html.escape(section_content)
        safe_text = safe_text.replace("\n", "<br>")
        safe_text = re.sub(r"\[Page\s+(\d+)\]", r"<h3>第 \1 页</h3>", safe_text)
        rendered_blocks.append(f"<h{heading_level}>{safe_title}</h{heading_level}><p>{safe_text}</p>")

    body = "\n".join(rendered_blocks) if rendered_blocks else "<p>未提取到可读文本。</p>"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)} - 翻译版式</title>
  <style>
    :root {{ color-scheme: light; }}
    body {{ margin: 0; background: #f1f5f9; font-family: 'Noto Serif SC', 'Source Han Serif SC', serif; color: #111827; }}
    .page {{ width: min(1200px, calc(100vw - 24px)); margin: 12px auto; background: white; border: 1px solid #cbd5e1; border-radius: 12px; }}
    .head {{ padding: 16px 20px; border-bottom: 1px solid #e2e8f0; }}
    .head h1 {{ margin: 0 0 6px; font-size: 22px; }}
    .head p {{ margin: 0; color: #475569; font-size: 13px; }}
    .cols {{ column-count: 2; column-gap: 26px; padding: 18px 20px 24px; line-height: 1.75; font-size: 14px; }}
    .cols h3 {{ margin: 12px 0 8px; font-size: 15px; color: #0f172a; break-inside: avoid; }}
    .cols p {{ margin: 0 0 10px; text-align: justify; break-inside: avoid; }}
    @media (max-width: 900px) {{ .cols {{ column-count: 1; }} }}
  </style>
</head>
<body>
  <section class="page">
    <div class="head">
      <h1>{html.escape(title)}</h1>
      <p>目标语言：{html.escape(target_language)}｜双栏阅读版（接近论文阅读版式）</p>
    </div>
    <article class="cols">
      {body}
    </article>
  </section>
</body>
</html>"""


def make_summary_markdown(
    title: str,
    template_name: str,
    target_language: str,
    tags: list[str],
    template_text: str,
    source_chunks: list[str],
    translated_chunks: list[str],
    text: str,
    settings: Settings | None = None,
) -> str:
    """
    【生成摘要 Markdown】
    生成核心摘要报告，针对后门攻击领域使用 LLM 结构化提取。

    两种模式：
    1. Backdoor Attack 模式：使用 LLM 提取详细结构化信息（数据集、ASR、CDA 等）
    2. 通用模式：提取关键句和证据片段

    参数:
        title: 论文标题
        template_name: 使用的模板名称
        target_language: 目标语言
        tags: 领域标签列表
        template_text: 模板文本内容
        source_chunks: 原始文本块
        translated_chunks: 翻译后的文本块
        text: 完整论文文本

    返回:
        Markdown 格式的摘要报告
    """
    template_domain = domain_tag_from_template(template_name)
    display_chunks = translated_chunks or source_chunks
    key_points = collect_key_sentences(display_chunks, max_items=8)
    evidence = [chunk[:260] for chunk in display_chunks[:4]]

    if template_domain == "Backdoor Attack":
        structured = extract_backdoor_structured_info(text, title, settings=settings)

        signals = extract_backdoor_indicators(text)
        poison_rate = (
            structured.get("poison_rates", [signals.get("Representative ASR", "N/A")])[0]
            if structured.get("poison_rates")
            else "N/A"
        )
        asr_metric = (
            structured.get("asr_values", ["N/A"])[0]
            if structured.get("asr_values")
            else signals.get("Representative ASR", "N/A")
        )
        clean_metric = (
            structured.get("clean_acc_drop", ["N/A"])[0]
            if structured.get("clean_acc_drop")
            else signals.get("Representative Clean Acc", "N/A")
        )

        lines = [
            "# 论文深度解析报告",
            "",
            "## 1. 速览 (TL;DR)",
            f"**所属领域**：{template_domain}",
            f"**两句话概括**：{structured.get('two_sentence_summary', key_points[0] if key_points else 'N/A')}",
            "",
            "## 2. 核心关注点 (Core Focus)",
            "",
            "### 2.1 数据集与模型",
            f"- **数据集**：{structured.get('datasets', signals.get('Primary Dataset', 'N/A'))}",
            f"- **目标模型**：{structured.get('target_models', 'N/A')}",
            "",
            "### 2.2 实验基线与防御",
            f"- **对比攻击方法**：{', '.join(structured.get('baselines', ['N/A']))}",
            "- **测试的防御方法**：N/A（可后续扩展）",
            "",
            "### 2.3 关键指标",
            f"- **中毒率 (Poison Rate)**：{poison_rate}",
            f"- **攻击成功率 (ASR)**：{asr_metric}",
            f"- **干净准确率下降 (CDA)**：{clean_metric}",
            "",
            "### 2.4 主要贡献",
        ]
        for contrib in structured.get("contributions", ["N/A"] * 3)[:3]:
            lines.append(f"- {contrib}")

        lines.extend(
            [
                "",
                "## 3. 扩展关注点",
                "",
                "### 3.1 攻击与触发器设计",
                f"- **攻击阶段**：{structured.get('attack_type', signals.get('Attack Setting', 'N/A'))}",
                f"- **触发器类型**：{structured.get('trigger_type', signals.get('Trigger Type', 'N/A'))}",
                "",
                "### 3.2 局限性与风险",
                "- **作者指出的局限**：N/A",
                "- **潜在风险**：该攻击可在低中毒率下实现高 ASR，具有较高现实部署风险。",
                "",
                "## 4. 核心效果对比表",
                "",
                "| 项目          | 本文数值                  | 对比最佳方法 | 提升/下降 |",
                "|---------------|---------------------------|--------------|-----------|",
                f"| 中毒率        | {poison_rate}            | N/A         | N/A      |",
                f"| ASR           | {asr_metric}             | N/A         | N/A      |",
                f"| CDA           | {clean_metric}           | N/A         | N/A      |",
                "",
                f"- 论文标题：{title}",
                f"- 生成时间：{utc_now_iso()}",
                "",
                "## 证据片段（中文）",
            ]
        )

        if evidence:
            for idx, item in enumerate(evidence, start=1):
                lines.append(f"- 片段 {idx}: {item}")
        else:
            lines.append("- 暂无证据片段。")

        return "\n".join(lines)

    # 通用模板：保持简洁结构，避免额外块干扰前端布局。
    lines = [
        f"# 论文摘要（{template_domain}）",
        "",
        "## 基本信息",
        f"- 论文标题：{title}",
        f"- 摘要模板：{template_name}",
        f"- 目标语种：{target_language}",
        f"- 领域标签：{', '.join(tags)}",
        f"- 生成时间：{utc_now_iso()}",
        "",
        "## 核心要点（中文）",
    ]
    if key_points:
        for idx, point in enumerate(key_points, start=1):
            lines.append(f"- 要点 {idx}: {point}")
    else:
        lines.append("- 未提取到稳定文本，建议检查 PDF 是否为可复制文本版。")

    lines.extend(["", "## 证据片段（中文）"])
    if evidence:
        for idx, item in enumerate(evidence, start=1):
            lines.append(f"- 片段 {idx}: {item}")
    else:
        lines.append("- 暂无证据片段。")

    lines.append("")
    return "\n".join(lines)


def make_improvement_markdown(
    title: str,
    tags: list[str],
    source_chunks: list[str],
    translated_chunks: list[str],
    settings: Settings | None = None,
) -> str:
    """
    【生成改进建议 Markdown】
    生成"改进与创新建议"报告，支持 ToT 多路评估生成。

    报告结构：
    1. 当前方案的主要短板
    2. 可执行创新点与具体方案（含 ToT 评分）
    3. 90 天落地计划
    4. 证据片段

    参数:
        title: 论文标题
        tags: 领域标签
        source_chunks: 原始文本块
        translated_chunks: 翻译后的文本块
        settings: 应用配置（包含模型选择、ToT 参数等）

    返回:
        Markdown 格式的改进建议报告
    """
    display_chunks = translated_chunks or source_chunks
    evidence = [chunk[:220] for chunk in display_chunks[:5]]
    highlights = collect_key_sentences(display_chunks, max_items=4)
    generation_agent = settings.generation_model_name if settings else "DeepSeek-V3"
    evaluation_agent = settings.evaluation_model_name if settings else "Qwen3"
    collaboration_mode = (
        build_multi_agent_collaboration_label(settings)
        if settings
        else "Multi-Agent Collaboration: DeepSeek-V3 (Gen) + Qwen3 (Eval)"
    )
    execution_order = "先生成 -> 后评估 -> 再 ToT 分支扩展与剪枝"
    innovations, tot_note = generate_innovation_ideas(
        title=title,
        tags=tags,
        evidence=evidence,
        settings=settings,
    )

    lines = [
        f"# 《{title}》改进与创新建议",
        "",
        f"- 关联领域：{', '.join(tags)}",
        f"- 当前模型列表：生成模型：{generation_agent} | 评估模型：{evaluation_agent}",
        f"- 协同模式：{collaboration_mode}",
        f"- 执行顺序：{execution_order}",
        f"- 生成时间：{utc_now_iso()}",
        (
            f"- 生成策略：ToT 分支评估（最佳分支输出）"
            if innovations and innovations[0].get("source") == "ToT"
            else f"- 生成策略：规则库回退（原因：{tot_note or 'ToT 未启用'}）"
        ),
        "",
        "## 一、当前方案的主要短板（中文）",
    ]

    if highlights:
        for idx, item in enumerate(highlights, start=1):
            lines.append(f"{idx}. {item}")
    else:
        lines.append("1. 暂未提取到足够文本证据，建议先检查 PDF 文本可读性。")

    lines.extend(["", "## 二、可执行创新点与具体方案"])
    for idx, idea in enumerate(innovations, start=1):
        lines.extend(
            [
                f"### 创新点 {idx}: {idea['name']}",
                f"- 具体方案：{idea['plan']}",
                f"- 实验验证：{idea['validation']}",
                f"- 风险与应对：{idea['risk']}",
            ]
        )
        if "asr_gain" in idea:
            lines.append(
                f"- 评审分项：ASR_Gain={idea.get('asr_gain')}, "
                f"Implementation_Cost={idea.get('implementation_cost')}, "
                f"Stealthiness={idea.get('stealthiness')}"
            )
        if "final_score" in idea:
            lines.append(f"- 综合评分：{idea.get('final_score')}")
        model_steps = idea.get("model_steps")
        if isinstance(model_steps, list):
            for step_text in model_steps:
                if isinstance(step_text, str) and step_text.strip():
                    lines.append(f"- {step_text}")
        if idea.get("execution_order"):
            lines.append(f"- 执行轨迹：{idea.get('execution_order')}")
        if idea.get("selection_reason"):
            lines.append(f"- 选择理由：{idea.get('selection_reason')}")
        if idea.get("review_comment"):
            lines.append(f"- Reviewer 备注：{idea.get('review_comment')}")
        lines.append("")

    lines.extend(
        [
            "## 三、90 天落地计划（建议）",
            "- 第 1-30 天：复现实验基线，统一数据处理与评价脚本，确定核心对比表。",
            "- 第 31-60 天：实现 1-2 个高优先级创新点，完成消融实验和防御评估。",
            "- 第 61-90 天：补齐跨模型/跨数据集验证，输出论文级图表与误差分析。",
            "",
            "## 四、证据片段（中文）",
        ]
    )

    if evidence:
        for idx, item in enumerate(evidence, start=1):
            lines.append(f"- 证据 {idx}: {item}")
    else:
        lines.append("- 暂无可引用证据片段。")

    lines.append("")
    return "\n".join(lines)


__all__ = [
    "make_translation_markdown",
    "make_translation_layout_html",
    "make_summary_markdown",
    "make_improvement_markdown",
]