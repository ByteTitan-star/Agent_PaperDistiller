# 领域标签推断、后门指标提取、结构化信息提取
import json
import os
import re
from typing import Any

from .common_utils import log_token_usage, remove_surrogates
from ..config import get_settings
from ..storage import domain_tag_from_template, unique_keep_order


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


def extract_backdoor_indicators(text: str) -> dict[str, str]:
    """
    【后门指标提取】
    从文本中提取后门论文常见指标（ASR、触发器、数据集等）。

    提取字段：
    - Attack Setting: 攻击设置（clean-label、all-to-one 等）
    - Trigger Type: 触发器类型（patch、blended、semantic 等）
    - Primary Dataset: 主要数据集（CIFAR-10、ImageNet 等）
    - Representative ASR: 代表性攻击成功率
    - Representative Clean Acc: 代表性干净准确率

    参数:
        text: 论文文本内容

    返回:
        包含各项指标的字典，未找到则标记为 "N/A"
    """
    def find_first(patterns: list[str]) -> str:
        """【内部函数】按优先级匹配第一个正则模式"""
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return match.group(1)
        return "N/A"

    return {
        "Attack Setting": find_first(
            [
                r"(clean-label\s+backdoor)",
                r"(all-to-one|all-to-all)",
                r"(data\s+poisoning)",
            ]
        ),
        "Trigger Type": find_first(
            [
                r"(patch\s+trigger)",
                r"(blended\s+trigger)",
                r"(semantic\s+trigger)",
                r"(frequency\s+trigger)",
                r"(warping[- ]based\s+trigger)",
            ]
        ),
        "Primary Dataset": find_first(
            [r"\b(CIFAR-10|CIFAR-100|ImageNet|Tiny-ImageNet|GTSRB|MNIST|SVHN)\b"]
        ),
        "Representative ASR": find_first(
            [
                r"ASR[^0-9]{0,12}(\d{1,3}(?:\.\d+)?%)",
                r"attack success rate[^0-9]{0,12}(\d{1,3}(?:\.\d+)?%)",
            ]
        ),
        "Representative Clean Acc": find_first(
            [
                r"clean accuracy[^0-9]{0,12}(\d{1,3}(?:\.\d+)?%)",
                r"CA[^0-9]{0,12}(\d{1,3}(?:\.\d+)?%)",
            ]
        ),
    }


def extract_backdoor_structured_info(text: str, title: str, settings: Any = None, user_id: int | None = None) -> dict[str, Any]:
    """
    【结构化信息提取】
    使用 DeepSeek LLM 对论文进行结构化信息提取。

    提取字段（JSON Schema）：
    - two_sentence_summary: 两句话概括
    - datasets: 使用的数据集
    - target_models: 目标模型
    - baselines: 对比基线方法
    - poison_rates: 中毒率列表
    - asr_values: ASR 值列表
    - clean_acc_drop: 干净准确率下降
    - attack_type: 攻击类型
    - trigger_type: 触发器类型
    - contributions: 主要贡献列表

    参数:
        text: 论文全文（截取前 28000 字符）
        title: 论文标题
        settings: 可选配置对象，未提供时使用全局 settings

    返回:
        解析后的 JSON 字典，失败则返回空字典
    """
    try:
        from openai import OpenAI
    except Exception:
        return {}

    try:
        effective_settings = settings or get_settings()
        if not effective_settings.deepseek_api_key.strip():
            raise ValueError("DEEPSEEK_API_KEY 未配置")

        client = OpenAI(
            api_key=effective_settings.deepseek_api_key,
            base_url=effective_settings.deepseek_base_url.rstrip("/"),
            timeout=60,
        )

        # 系统提示词
        prompt = f"""你是一位顶会审稿人，正在分析一篇后门攻击（Backdoor Attack）论文。
        论文标题：{title}

        请严格依据全文内容，提取以下结构化信息。只输出合法 JSON，不要解释，不要 markdown。

        JSON Schema（必须严格遵守）：
        模板内容：
        {template_content}
        {{
        "two_sentence_summary": "用不超过两句话概括整篇论文做了什么、核心创新是什么",
        "datasets": "主要数据集（列出所有，如 CIFAR-10, ImageNet, 自定义 BadVideo-100K）",
        "target_models": "攻击的目标模型（ResNet-50, ViT-B/16 等）",
        "baselines": "对比的 SOTA 方法（列出 3-5 个，如 BadNet, Blend, WaNet 等）",
        "poison_rates": "所有实验中毒率（列表，如 [\"5%\", \"10%\", \"0.05\"]）",
        "asr_values": '攻击成功率 ASR（列表，带对应数据集）',
        "clean_acc_drop": "干净准确率下降 CDA（列表）",
        "attack_type": "攻击类型（data poisoning / model poisoning / train-time / inference-time）",
        "trigger_type": "触发器类型（patch, blended, semantic, natural, dynamic 等）",
        "contributions": ["贡献1", "贡献2", "贡献3"]
        }}

        全文内容：
        {text[:28000]}

        只返回 JSON，不要加任何其他文字。"""

        response = client.chat.completions.create(
            model=effective_settings.deepseek_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=1200,
        )

        if response.usage:
            log_token_usage(
                project_name=effective_settings.app_name,
                model_name=effective_settings.deepseek_model,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                user_id=user_id,
                action_type="pipeline",
            )

        content = (response.choices[0].message.content or "").strip()
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if not json_match:
            return {}
        return json.loads(json_match.group(0))
    except Exception as e:
        print(f"[Warning] LLM 提取失败，回退正则: {e}")
        return {}


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

    text = " ".join(chunks[:24])
    candidates = re.split(r"(?<=[.!?。！？])\s+", text)

    selected: list[str] = []
    for sentence in candidates:
        compact = re.sub(r"\s+", " ", sentence).strip()
        if len(compact) < 28:
            continue
        selected.append(compact[:260])
        if len(selected) >= max_items:
            break

    if not selected:
        selected = [chunk[:260] for chunk in chunks[:max_items]]
    return selected


def extract_template_headings(template_text: str, max_items: int = 8) -> list[str]:
    """
    【模板标题提取】
    提取模板中的标题层级，便于摘要对齐模板结构。

    参数:
        template_text: 模板文本内容
        max_items: 最大提取标题数

    返回:
        标题列表（去除 # 标记）
    """
    headings: list[str] = []
    for line in template_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            headings.append(stripped.lstrip("#").strip())
        if len(headings) >= max_items:
            break
    return headings


def extract_summary_by_template(
    translated_text: str,
    template_text: str,
    title: str,
    settings: Any = None,
    user_id: int | None = None,
) -> dict[str, str]:
    """
    【模板引导的 LLM 摘要提取】
    解析模板中的问题，用 LLM 从翻译后的文本中逐题提取答案。

    参数:
        translated_text: 翻译后的中文文本
        template_text: 模板内容（Markdown）
        title: 论文标题
        settings: 应用配置
        user_id: 用户 ID（用于 token 追踪）

    返回:
        {section_heading: answer_content} 的字典
    """
    effective_settings = settings or get_settings()
    if not effective_settings.deepseek_api_key.strip():
        return {}

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

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=effective_settings.deepseek_api_key,
            base_url=effective_settings.deepseek_base_url.rstrip("/"),
            timeout=effective_settings.deepseek_timeout_sec,
        )
        resp = client.chat.completions.create(
            model=effective_settings.deepseek_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=3000,
        )
        answer = (resp.choices[0].message.content or "").strip()

        if resp.usage:
            log_token_usage(
                resp.usage.prompt_tokens,
                resp.usage.completion_tokens,
                user_id=user_id,
                action_type="summary",
            )

        # 解析 LLM 返回：按 ### 分段
        result: dict[str, str] = {}
        current_section = ""
        current_content: list[str] = []
        for line in answer.splitlines():
            stripped = line.strip()
            if stripped.startswith("### ") or (stripped.startswith("## ") and not stripped.startswith("## 论文")):
                if current_section:
                    result[current_section] = "\n".join(current_content).strip()
                heading = re.sub(r"^#{2,3}\s*\d*\.?\s*", "", stripped).strip()
                current_section = heading
                current_content = []
            elif current_section:
                current_content.append(stripped)
        if current_section:
            result[current_section] = "\n".join(current_content).strip()

        return result

    except Exception:
        return {}


__all__ = [
    "infer_domain_tags",
    "extract_backdoor_indicators",
    "extract_backdoor_structured_info",
    "collect_key_sentences",
    "extract_template_headings",
    "extract_summary_by_template",
]