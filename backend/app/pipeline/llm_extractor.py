# 领域标签推断、关键句提取、模板引导摘要提取
import re
from typing import TYPE_CHECKING

from ..storage import domain_tag_from_template, unique_keep_order

if TYPE_CHECKING:
    from ..harness.agents.base import BaseAgent


def infer_domain_tags(text: str, template_name: str) -> list[str]:
    """
    【领域标签推断】
    根据论文内容和模板名称自动推断领域标签。

    推断逻辑：
    - 首先根据 template_name 提取基础标签
    - 扫描文本关键词匹配预定义的领域词汇表
    - 支持后门攻击、防御、计算机视觉、时序、NLP 等方向

    参数:
        text: 论文全文或摘要文本
        template_name: 使用的模板名称

    返回:
        去重后的领域标签列表，如 ["Backdoor Attack", "Computer Vision"]
    """
    low = text.lower()
    tags = [domain_tag_from_template(template_name)]

    vocab = {
        "Backdoor Attack": ["backdoor", "trigger", "clean-label", "trojan", "poison"],
        "Backdoor Defense": ["defense", "mitigation", "detection", "sanitization"],
        "Computer Vision": ["image", "vision", "cifar", "imagenet", "resnet", "vit"],
        "Time Series": ["time series", "forecast", "temporal", "sequence"],
        "NLP": ["bert", "token", "language model", "translation"],
    }

    for tag, keywords in vocab.items():
        if any(keyword in low for keyword in keywords):
            tags.append(tag)

    return unique_keep_order(tags) or ["General"]


def collect_key_sentences(chunks: list[str], max_items: int = 8) -> list[str]:
    """
    【关键句提取】
    从文本块中提取若干关键句，作为摘要要点候选。

    策略：
    - 优先从文本块中按句子边界切分
    - 过滤过短（<28 字符）的句子
    - 截取前 260 字符作为摘要

    参数:
        chunks: 文本块列表
        max_items: 最大提取句数（默认 8）

    返回:
        关键句列表
    """
    if not chunks:
        return []

    text = " ".join(chunks[:24])  # 拼接前 24 个文本块
    candidates = re.split(r"(?<=[.!?。！？])\s+", text)  # 按句子边界切分

    selected: list[str] = []
    for sentence in candidates:  # 过滤过短（<28 字符）的句子
        compact = re.sub(r"\s+", " ", sentence).strip()
        if len(compact) < 28:
            continue
        selected.append(compact[:260])  # 截取前 260 字符
        if len(selected) >= max_items:  # 如果提取的句子数达到最大值，则停止
            break

    if not selected:
        selected = [chunk[:260] for chunk in chunks[:max_items]]  # 回退：截取前 max_items 个文本块的前 260 字符
    return selected


async def extract_summary_by_template(
    translated_text: str,
    template_text: str,
    title: str,
    deepseek_agent: "BaseAgent",
    user_id: int | None = None,
) -> dict[str, str]:
    """
    【模板引导的 LLM 摘要提取】
    解析模板中的问题，用 DeepSeekAgent 从翻译后的文本中逐题提取答案。

    LLM 调用统一走 harness agent（DeepSeekAgent.execute），token 用量由
    BaseAgent.on_post_run 集中记账，本函数不再自建 OpenAI client、不再重复写库。

    参数:
        translated_text: 翻译后的中文文本
        template_text: 模板内容（Markdown）
        title: 论文标题
        deepseek_agent: 已注入用户配置的 DeepSeekAgent 实例
        user_id: 用户 ID（用于 token 追踪）

    返回:
        {section_heading: answer_content} 的字典
    """
    # 解析模板：按 ## 分段
    sections: list[tuple[str, str]] = []
    current_heading = ""
    current_body: list[str] = []
    for line in template_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            if current_heading:
                sections.append((current_heading, "\n".join(current_body).strip()))
            current_heading = stripped.lstrip("#").strip()
            current_body = []
        elif stripped.startswith("# "):
            continue
        else:
            current_body.append(stripped)
    if current_heading:
        sections.append((current_heading, "\n".join(current_body).strip()))

    if not sections:
        return {}

    # 截断翻译文本
    max_text_len = 12000
    truncated_text = translated_text[:max_text_len]
    if len(translated_text) > max_text_len:
        truncated_text += "\n\n[...文本过长，已截断...]"

    # 构建 prompt
    sections_prompt_parts = []
    for i, (heading, body) in enumerate(sections, 1):
        sections_prompt_parts.append(f"### {i}. {heading}\n{body}")
    sections_prompt = "\n\n".join(sections_prompt_parts)

    system_prompt = (
        "你是一个学术论文分析专家。请根据提供的论文翻译内容，按照模板中的每个章节逐一回答问题。\n"
        "要求：\n"
        "- 每个章节的回答必须基于论文内容，不要编造\n"
        "- 使用中文回答\n"
        "- 回答要简洁、结构化，使用 Markdown 格式\n"
        "- 如果论文中没有相关信息，明确标注「论文未提及」\n"
        "- 不要重复输出模板的标题，只输出回答内容"
    )

    user_prompt = (
        f"论文标题：{title}\n\n"
        f"## 论文翻译内容\n\n{truncated_text}\n\n"
        f"## 请按以下模板章节逐一回答\n\n{sections_prompt}\n\n"
        f"请严格按照上述章节顺序，用 Markdown 格式输出每个章节的回答内容。"
    )

    result = await deepseek_agent.execute(
        user_prompt,
        system_prompt=system_prompt,
        temperature=0.2,
        max_tokens=3000,
        user_id=user_id,
        action_type="summary",
    )
    if result.error or not result.content:
        return {}

    answer = str(result.content)

    # 解析 LLM 返回：按 ### 分段
    parsed: dict[str, str] = {}
    current_section = ""
    current_content: list[str] = []
    for line in answer.splitlines():
        stripped = line.strip()
        if stripped.startswith("### ") or (stripped.startswith("## ") and not stripped.startswith("## 论文")):
            if current_section:
                parsed[current_section] = "\n".join(current_content).strip()
            heading = re.sub(r"^#{2,3}\s*\d*\.?\s*", "", stripped).strip()
            current_section = heading
            current_content = []
        elif current_section:
            current_content.append(stripped)
    if current_section:
        parsed[current_section] = "\n".join(current_content).strip()

    return parsed


__all__ = [
    "collect_key_sentences",
    "extract_summary_by_template",
    "infer_domain_tags",
]
