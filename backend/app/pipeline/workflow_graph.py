# 	LangGraph工作流/线性流水线
import asyncio
import logging
from typing import Any, TypedDict

logger = logging.getLogger(__name__)

from ..config import Settings
from .document_parser import chunk_text, extract_text_from_pdf, split_text_into_sections
from .renderer import (
    make_improvement_markdown,
    make_summary_markdown,
    make_translation_layout_html,
    make_translation_markdown,
)
from .state_broker import TaskBroker
from ..storage import Storage, resolve_template_content
from .tot_generator import build_multi_agent_collaboration_label
from .translator import flatten_sections_to_chunks, translate_sections
from .llm_extractor import infer_domain_tags

try:
    from langgraph.graph import END, START, StateGraph

    LANGGRAPH_AVAILABLE = True
except Exception:
    END = "__end__"
    START = "__start__"
    StateGraph = None
    LANGGRAPH_AVAILABLE = False


class PaperState(TypedDict, total=False):
    """
    【论文处理状态】
    图状态机中在各个节点之间传递数据的"托盘"结构。

    字段说明：
    - task_id: 任务唯一标识
    - paper_id: 论文唯一标识
    - title: 论文标题
    - target_language: 目标翻译语言
    - template_name: 使用的模板名称
    - generation_model_name: 生成模型名称
    - evaluation_model_name: 评估模型名称
    - collaboration_mode: 协同模式描述
    - text: 提取的完整文本
    - sections: 切分后的章节列表 [(标题, 内容), ...]
    - chunks: 文本块列表
    - translated_sections: 翻译后的章节列表
    - translation_failures: 翻译失败段数
    - translation_retry_count: 翻译重试次数
    - translated_chunks: 翻译后的文本块
    - template_text: 模板文本内容
    - tags: 领域标签列表
    """

    task_id: str
    paper_id: str
    title: str
    target_language: str
    template_name: str
    user_id: int
    generation_model_name: str
    evaluation_model_name: str
    collaboration_mode: str
    text: str
    sections: list[tuple[str, str]]
    chunks: list[str]
    translated_sections: list[tuple[str, str]]
    translation_failures: int
    translation_retry_count: int
    translated_chunks: list[str]
    template_text: str
    tags: list[str]


def build_pipeline_graph(storage: Storage, broker: TaskBroker, settings: Settings):
    """
    【构建流水线图】
    构建 LangGraph 工作流（可选，LangGraph 不可用时返回 None）。

    节点流程：
    START -> 解析节点 -> 翻译节点 -> [重试判断是否到摘要节点] -> 摘要节点 -> 创新改进节点 -> END

    条件分支：
    - translate 节点后，如有失败且未超重试限制，返回 translate 节点重试
    - 否则进入 summarize 节点

    参数:
        storage: 存储管理器
        broker: 任务状态代理
        settings: 应用配置

    返回:
        编译后的 StateGraph 对象，或 None（LangGraph 不可用时）
    """
    if not settings.langgraph_enabled or not LANGGRAPH_AVAILABLE or StateGraph is None:
        return None

    workflow = StateGraph(PaperState)
    collaboration_mode = build_multi_agent_collaboration_label(settings)
    execution_order = "先生成 -> 后评估 -> 再 ToT 分支扩展与剪枝"

    async def parse_node(state: PaperState) -> PaperState:
        """
        【解析节点】
        解析 PDF 文本，切分章节和文本块。

        处理内容：
        1. 提取 PDF 纯文本
        2. 按章节标题切分
        3. 按固定窗口切分文本块
        4. 保存文本块到存储

        进度更新：15%（"正在解析 PDF 文本。"）
        """
        await broker.update(state["task_id"], "parsing", 15, "正在解析 PDF 文本。")
        text = await asyncio.to_thread(extract_text_from_pdf, storage.pdf_path(state["paper_id"]))
        sections = split_text_into_sections(text)
        chunks = await asyncio.to_thread(
            chunk_text,
            text,
            settings.max_chunk_chars,
            settings.chunk_overlap,
        )
        await asyncio.to_thread(storage.save_chunks, state["paper_id"], chunks)
        return {
            "text": text,
            "sections": sections,
            "chunks": chunks,
            "translation_retry_count": 0,
        }

    async def translate_node(state: PaperState) -> PaperState:
        """
        【翻译节点】
        执行全文翻译，生成 Markdown 和 HTML 版本。

        处理内容：
        1. 翻译所有章节
        2. 生成翻译 Markdown 报告
        3. 生成双栏 HTML 阅读版

        进度更新：45% 或 55%（根据重试次数）
        """
        retry_count = int(state.get("translation_retry_count", 0))
        progress = 45 if retry_count == 0 else 55
        await broker.update(
            state["task_id"],
            "translating",
            progress,
            f"正在进行全文翻译（第 {retry_count + 1} 轮）。",
        )

        translated_sections, translation_failures = await asyncio.to_thread(
            translate_sections,
            state.get("sections", []),
            state["target_language"],
        )
        translation_md = await asyncio.to_thread(
            make_translation_markdown,
            state["title"],
            state["target_language"],
            translated_sections,
            translation_failures,
        )
        await asyncio.to_thread(storage.write_result, state["paper_id"], "translation", translation_md)

        layout_html = await asyncio.to_thread(
            make_translation_layout_html,
            state["title"],
            state["target_language"],
            translated_sections,
        )
        layout_path = storage.paper_output_dir(state["paper_id"]) / "translated_layout.html"
        await asyncio.to_thread(layout_path.write_text, layout_html, "utf-8")

        return {
            "translated_sections": translated_sections,
            "translation_failures": translation_failures,
            "translation_retry_count": retry_count + 1,
        }

    def route_after_translate(state: PaperState) -> str:
        """
        【翻译后路由】
        决定翻译后是重试还是进入摘要阶段。

        重试条件：
        - 存在翻译失败段（translation_failures > 0）
        - 未超过重试限制（retry_count <= limit）

        返回:
            "translate" 或 "summarize"
        """
        failures = int(state.get("translation_failures", 0))
        retry_count = int(state.get("translation_retry_count", 0))
        if failures > 0 and retry_count <= settings.pipeline_translation_retry_limit:
            return "translate"
        return "summarize"

    async def summarize_node(state: PaperState) -> PaperState:
        """
        【摘要节点】
        提取核心摘要，推断领域标签。

        处理内容：
        1. 打平章节为文本块
        2. 推断领域标签
        3. 读取模板文本
        4. 生成摘要 Markdown

        进度更新：70%（"正在提取核心摘要。"）
        """
        await broker.update(state["task_id"], "summarizing", 70, "正在提取核心摘要。")
        translated_sections = state.get("translated_sections", []) or state.get("sections", [])
        translated_chunks = flatten_sections_to_chunks(translated_sections)
        tags = infer_domain_tags(state.get("text", ""), state["template_name"])
        # 读模版内容：优先从 DB，回退到文件系统
        template_text = await resolve_template_content(
            state["template_name"], state.get("user_id")
        ) or await asyncio.to_thread(storage.read_template, state["template_name"])

        summary_md = await asyncio.to_thread(
            make_summary_markdown,
            state["title"],
            state["template_name"],
            state["target_language"],
            tags,
            template_text,         # 模板内容传入
            state.get("chunks", []),
            translated_chunks,
            state.get("text", ""),
            settings,
        )
        await asyncio.to_thread(
            storage.write_result, state["paper_id"], "summary", summary_md, state["template_name"]
        )
        return {
            "translated_chunks": translated_chunks,
            "template_text": template_text,
            "tags": tags,
        }

    async def critique_node(state: PaperState) -> PaperState:
        """
        【 critique 节点】
        生成改进与创新方案。

        处理内容：
        1. 生成改进建议 Markdown（含 ToT 评估）
        2. 保存结果

        进度更新：90% -> 100%
        """
        state_collaboration = state.get("collaboration_mode", collaboration_mode)
        await broker.update(
            state["task_id"],
            "critiquing",
            90,
            f"{state_collaboration} | 正在生成改进与创新方案（{execution_order}）。",
        )
        improvement_md = await asyncio.to_thread(
            make_improvement_markdown,
            state["title"],
            state.get("tags", []),
            state.get("chunks", []),
            state.get("translated_chunks", []),
            settings,
        )
        await asyncio.to_thread(storage.write_result, state["paper_id"], "improvement", improvement_md)
        await broker.update(
            state["task_id"],
            "done",
            100,
            f"任务已完成。{state_collaboration}",
        )
        return {}

    workflow.add_node("parse_pdf", parse_node)
    workflow.add_node("translate", translate_node)
    workflow.add_node("summarize", summarize_node)
    workflow.add_node("critique", critique_node)

    workflow.add_edge(START, "parse_pdf")
    workflow.add_edge("parse_pdf", "translate")
    workflow.add_conditional_edges(
        "translate",
        route_after_translate,
        {"translate": "translate", "summarize": "summarize"},
    )
    workflow.add_edge("summarize", "critique")
    workflow.add_edge("critique", END)

    return workflow.compile()


async def run_pipeline_linear(
    task_id: str,
    paper_id: str,
    title: str,
    target_language: str,
    template_name: str,
    storage: Storage,
    broker: TaskBroker,
    settings: Settings,
    user_id: int | None = None,
) -> list[str]:
    """
    【线性流水线】
    LangGraph 不可用时的回退实现，按顺序执行各阶段。

    执行流程：
    1. parsing (15%): 解析 PDF
    2. translating (45%->55%): 翻译全文（支持重试）
    3. summarizing (70%): 提取摘要
    4. critiquing (90%->100%): 生成改进建议

    参数:
        task_id: 任务 ID
        paper_id: 论文 ID
        title: 论文标题
        target_language: 目标语言
        template_name: 模板名称
        storage: 存储管理器
        broker: 任务代理
        settings: 配置对象

    返回:
        领域标签列表
    """
    collaboration_mode = build_multi_agent_collaboration_label(settings)
    execution_order = "先生成 -> 后评估 -> 再 ToT 分支扩展与剪枝"
    logger.info("[Linear] Step 1/4 - Parsing PDF: task_id=%s paper_id=%s", task_id, paper_id)
    await broker.update(task_id, "parsing", 15, "正在解析 PDF 文本。")
    text = await asyncio.to_thread(extract_text_from_pdf, storage.pdf_path(paper_id))
    sections = split_text_into_sections(text)
    logger.info("[Linear] PDF parsed: text_len=%d sections=%d", len(text), len(sections))

    chunks = await asyncio.to_thread(
        chunk_text,
        text,
        settings.max_chunk_chars,
        settings.chunk_overlap,
    )
    await asyncio.to_thread(storage.save_chunks, paper_id, chunks)
    await asyncio.sleep(0.1)
    logger.info("[Linear] Step 2/4 - Translating: task_id=%s chunks=%d", task_id, len(chunks))
    await broker.update(task_id, "translating", 45, "正在进行全文翻译。")
    translated_sections, translation_failures = await asyncio.to_thread(
        translate_sections,
        sections,
        target_language,
    )
    if translation_failures > 0 and settings.pipeline_translation_retry_limit > 0:
        logger.warning("[Linear] Translation has %d failures, retrying: task_id=%s", translation_failures, task_id)
        await broker.update(task_id, "translating", 55, "翻译出现失败段，正在自动重试。")
        translated_sections, translation_failures = await asyncio.to_thread(
            translate_sections,
            sections,
            target_language,
        )

    translation_md = await asyncio.to_thread(
        make_translation_markdown,
        title,
        target_language,
        translated_sections,
        translation_failures,
    )
    await asyncio.to_thread(storage.write_result, paper_id, "translation", translation_md)

    layout_html = await asyncio.to_thread(
        make_translation_layout_html,
        title,
        target_language,
        translated_sections,
    )
    layout_path = storage.paper_output_dir(paper_id) / "translated_layout.html"
    await asyncio.to_thread(layout_path.write_text, layout_html, "utf-8")
    await asyncio.sleep(0.1)
    logger.info("[Linear] Translation done: failures=%d", translation_failures)

    translated_chunks = flatten_sections_to_chunks(translated_sections)
    tags = infer_domain_tags(text, template_name)
    template_text = await resolve_template_content(
        template_name, user_id
    ) or await asyncio.to_thread(storage.read_template, template_name)
    logger.info("[Linear] Step 3/4 - Summarizing: task_id=%s tags=%s template_loaded=%s",
                task_id, tags, bool(template_text))

    await broker.update(task_id, "summarizing", 70, "正在提取核心摘要。")
    summary_md = await asyncio.to_thread(
        make_summary_markdown,
        title,
        template_name,
        target_language,
        tags,
        template_text,
        chunks,
        translated_chunks,
        text,
        settings,
    )
    await asyncio.to_thread(storage.write_result, paper_id, "summary", summary_md, template_name)
    await asyncio.sleep(0.1)
    logger.info("[Linear] Step 4/4 - Critiquing: task_id=%s", task_id)

    await broker.update(
        task_id,
        "critiquing",
        90,
        f"{collaboration_mode} | 正在生成改进与创新方案（{execution_order}）。",
    )
    improvement_md = await asyncio.to_thread(
        make_improvement_markdown,
        title,
        tags,
        chunks,
        translated_chunks,
        settings,
    )
    await asyncio.to_thread(storage.write_result, paper_id, "improvement", improvement_md)
    await asyncio.sleep(0.1)

    await broker.update(task_id, "done", 100, f"任务已完成。{collaboration_mode}")
    logger.info("[Linear] Pipeline completed: task_id=%s tags=%s", task_id, tags)
    return tags


async def run_pipeline(
    task_id: str,
    paper_id: str,
    title: str,
    target_language: str,
    template_name: str,
    storage: Storage,
    broker: TaskBroker,
    settings: Settings,
    user_id: int | None = None,
) -> list[str]:
    """
    【执行主流水线】
    优先尝试 LangGraph，失败时回退到线性流程。

    决策逻辑：
    1. 尝试构建 LangGraph
    2. 如果构建成功，执行图流程
    3. 如果执行失败，回退到线性流程
    4. 如果构建失败，直接使用线性流程

    参数:
        task_id: 任务 ID
        paper_id: 论文 ID
        title: 论文标题
        target_language: 目标语言
        template_name: 模板名称
        storage: 存储管理器
        broker: 任务代理
        settings: 配置对象

    返回:
        领域标签列表
    """
    graph = build_pipeline_graph(storage=storage, broker=broker, settings=settings)
    if graph is None:
        logger.info("LangGraph not available, using linear pipeline: task_id=%s", task_id)
        return await run_pipeline_linear(
            task_id=task_id,
            paper_id=paper_id,
            title=title,
            target_language=target_language,
            template_name=template_name,
            storage=storage,
            broker=broker,
            settings=settings,
            user_id=user_id,
        )

    initial_state: PaperState = {
        "task_id": task_id,
        "paper_id": paper_id,
        "title": title,
        "target_language": target_language,
        "template_name": template_name,
        "user_id": user_id or 0,
        "generation_model_name": settings.generation_model_name,
        "evaluation_model_name": settings.evaluation_model_name,
        "collaboration_mode": build_multi_agent_collaboration_label(settings),
    }

    try:
        logger.info("Running LangGraph pipeline: task_id=%s", task_id)
        result = await graph.ainvoke(initial_state)
        tags = list(result.get("tags", [])) if isinstance(result, dict) else []
        logger.info("LangGraph pipeline completed: task_id=%s tags=%s", task_id, tags)
        return tags
    except Exception as exc:
        logger.warning(
            "LangGraph pipeline failed, falling back to linear: task_id=%s error=%s",
            task_id, exc, exc_info=True,
        )
        return await run_pipeline_linear(
            task_id=task_id,
            paper_id=paper_id,
            title=title,
            target_language=target_language,
            template_name=template_name,
            storage=storage,
            broker=broker,
            settings=settings,
        )


__all__ = ["PaperState", "build_pipeline_graph", "run_pipeline_linear", "run_pipeline"]