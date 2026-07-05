# ToT多路评估生成算法
import json
import re
from typing import TYPE_CHECKING, Any

from ..config import Settings

if TYPE_CHECKING:
    from ..harness.agents.tot_agent import ToTAgent


def generate_rule_based_innovation_ideas(tags: list[str]) -> list[dict[str, str]]:
    """
    【基于规则的创新建议生成】
    按领域标签生成可执行的创新建议库（ToT 失败时的回退路径）。

    支持领域：
    - Backdoor Attack: 生成 5 条后门攻击相关建议（触发器优化、防御感知投毒等）
    - 通用: 生成 3 条通用科研建议（数据覆盖、多目标训练、复现工程化）

    参数:
        tags: 领域标签列表（如 ["Backdoor Attack", "Computer Vision"]）

    返回:
        创新建议字典列表，每条包含 name/plan/validation/risk 字段
    """
    joined_tags = " ".join(tags).lower()
    if "backdoor attack" in joined_tags or "backdoor" in joined_tags:
        return [
            {
                "name": "分布偏移驱动的触发器自适应优化",
                "plan": "将样本分布偏移评分与触发器参数联合优化，按类别动态调整触发器强度和位置。",
                "validation": "在 CIFAR-10/CIFAR-100 上报告 ASR、CA、LPIPS，并与固定触发器对比。",
                "risk": "可能牺牲隐蔽性；通过加入感知约束和人审阈值缓解。",
            },
            {
                "name": "双层优化的防御感知投毒",
                "plan": "外层最大化攻击成功率，内层显式模拟常见检测器（频谱检测/激活聚类）并最小化可检测性。",
                "validation": "在至少 3 种防御下评估攻击前后 ASR 降幅与检测召回率。",
                "risk": "训练开销增大；通过低秩近似与子集采样控制成本。",
            },
            {
                "name": "跨模型迁移后门构造",
                "plan": "使用教师-学生特征一致性损失，提升后门在 ResNet/ViT 间的迁移稳定性。",
                "validation": "报告跨架构迁移矩阵（source→target）的 ASR/CA。",
                "risk": "迁移增强可能降低 clean 精度；引入权重退火平衡。",
            },
            {
                "name": "物理世界鲁棒触发器",
                "plan": "训练加入打印、压缩、模糊、视角变化增强，构建可落地触发器。",
                "validation": "仿真 + 实拍两阶段评估，输出真实场景 ASR。",
                "risk": "实验成本高；先在仿真管线筛选候选触发器。",
            },
            {
                "name": "后门风险评分与预警系统",
                "plan": "构建样本级风险分数（特征残差 + 决策边界不稳定性），用于训练前后门预筛查。",
                "validation": "在含后门与干净数据混合场景下评估 AUC/F1。",
                "risk": "误报率偏高；采用分层阈值和人工复核流程。",
            },
        ]

    return [
        {
            "name": "数据覆盖增强",
            "plan": "按难样本分层采样并补齐长尾分布，减少方法对单一数据分布的过拟合。",
            "validation": "对比扩充前后在 OOD 测试集上的指标变化。",
            "risk": "数据成本上升；优先引入高收益子集。",
        },
        {
            "name": "多目标训练策略",
            "plan": "把性能、鲁棒性、可解释性纳入统一损失函数并进行权重搜索。",
            "validation": "绘制 Pareto 前沿并给出权衡点。",
            "risk": "超参数敏感；采用网格+贝叶斯混合搜索。",
        },
        {
            "name": "复现工程化",
            "plan": "固定随机种子、配置模板化、日志结构化，降低实验不可重复风险。",
            "validation": "跨 3 次重复实验报告方差。",
            "risk": "工程改造周期增加；分阶段推进。",
        },
    ]


def extract_first_json_object(text: str) -> dict[str, Any] | None:
    """
    【提取首个 JSON 对象】
    从 LLM 输出文本中抽取第一个合法的 JSON 对象。

    处理策略：
    1. 尝试直接解析整个文本
    2. 去除 Markdown 代码块标记后再解析
    3. 搜索第一个 { 和最后一个 } 之间的内容

    参数:
        text: 可能包含 JSON 的原始文本

    返回:
        解析后的字典，失败则返回 None
    """
    if not text.strip():
        return None

    normalized = text.strip()
    if normalized.startswith("```"):
        normalized = re.sub(r"^```(?:json)?\s*", "", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\s*```$", "", normalized)

    try:
        payload = json.loads(normalized)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass

    left = normalized.find("{")
    right = normalized.rfind("}")
    if left < 0 or right < 0 or right <= left:
        return None

    candidate = normalized[left : right + 1]
    try:
        payload = json.loads(candidate)
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        return None


def to_float(value: Any, default: float) -> float:
    """
    【安全转换为浮点数】
    将任意值安全转换为浮点数，失败时返回默认值。

    参数:
        value: 待转换的值
        default: 转换失败时的默认值

    返回:
        转换后的浮点数或默认值
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_multi_agent_collaboration_label(settings: Settings) -> str:
    """
    【构建多智能体协同标签】
    生成描述多模型协同工作模式的字符串。

    格式：Multi-Agent Collaboration: {生成模型} (Gen) + {评估模型} (Eval)

    参数:
        settings: 应用配置对象

    返回:
        描述协同模式的字符串
    """
    return (
        f"Multi-Agent Collaboration: {settings.generation_model_name} (Gen) + {settings.evaluation_model_name} (Eval)"
    )


def normalize_tot_candidate(item: dict[str, Any], index: int) -> dict[str, Any]:
    """
    【标准化 ToT 候选方案】
    规范化 LLM 生成的候选方案，确保字段完整性和类型正确。

    处理字段：
    - index: 候选编号
    - name/plan/validation/risk: 字符串字段（提供默认值）
    - asr_gain/implementation_cost/stealthiness: 数值字段（默认 5.0）

    参数:
        item: 原始候选方案字典
        index: 候选编号

    返回:
        标准化后的候选方案字典
    """
    return {
        "index": index,
        "name": str(item.get("name", f"候选方案 {index + 1}")).strip() or f"候选方案 {index + 1}",
        "plan": str(item.get("plan", "N/A")).strip() or "N/A",
        "validation": str(item.get("validation", "N/A")).strip() or "N/A",
        "risk": str(item.get("risk", "N/A")).strip() or "N/A",
        "asr_gain": to_float(item.get("asr_gain", item.get("ASR_Gain", 5)), 5.0),
        "implementation_cost": to_float(
            item.get("implementation_cost", item.get("Implementation_Cost", 5)),
            5.0,
        ),
        "stealthiness": to_float(item.get("stealthiness", item.get("Stealthiness", 5)), 5.0),
    }


async def generate_tot_idea(
    title: str,
    tags: list[str],
    evidence: list[str],
    tot_agent: "ToTAgent",
    user_id: int | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """
    【严格 ToT 多路评估生成 —— 委托 ToTAgent】
    实现"先生成 -> 后评估 -> 再 ToT 扩展与剪枝"的完整流程。

    本函数不再自建 OpenAI client / 不再重复记账，而是统一交给 harness 的
    ToTAgent（内部组合 DeepSeekAgent 生成 + QwenAgent 评估），token 用量由
    各子 agent 的 BaseAgent.on_post_run 集中记录。

    参数:
        title: 论文标题
        tags: 领域标签
        evidence: 证据片段列表
        tot_agent: 已注入用户配置的 ToTAgent 实例
        user_id: 用户 ID（token 记账归属）

    返回:
        (创新方案列表, 错误信息)。成功时错误信息为 None
    """
    result = await tot_agent.execute(
        prompt=f"论文标题: {title}\n领域标签: {', '.join(tags)}",
        title=title,
        tags=tags,
        evidence=evidence,
        user_id=user_id,
    )

    # ToTAgent 在 ToT 关闭 / 生成失败时会返回规则回退内容（content 非空），
    # 此时直接采用；仅在完全没有内容时才视为失败。
    if result.content:
        content = result.content
        if isinstance(content, list):
            return content, None
        return [content], None

    return [], result.error or "ToT 生成失败"


async def generate_innovation_ideas(
    title: str,
    tags: list[str],
    evidence: list[str],
    tot_agent: "ToTAgent | None" = None,
    user_id: int | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """
    【创新建议生成入口】
    优先尝试 ToT 多路评估生成（经 ToTAgent），失败时回退到规则库。
    """
    if tot_agent is not None:
        ideas, reason = await generate_tot_idea(
            title=title, tags=tags, evidence=evidence, tot_agent=tot_agent, user_id=user_id
        )
        if ideas:
            return ideas, None
        return generate_rule_based_innovation_ideas(tags), reason

    return generate_rule_based_innovation_ideas(tags), "未提供 tot_agent，使用规则库"


__all__ = [
    "build_multi_agent_collaboration_label",
    "extract_first_json_object",
    "generate_innovation_ideas",
    "generate_rule_based_innovation_ideas",
    "generate_tot_idea",
    "normalize_tot_candidate",
    "to_float",
]
