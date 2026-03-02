import asyncio
from typing import Any, TypedDict

from .config import Settings
from .document_parser import chunk_text, extract_text_from_pdf, split_text_into_sections
from .renderer import (
    make_improvement_markdown,
    make_summary_markdown,
    make_translation_layout_html,
    make_translation_markdown,
)
from .state_broker import TaskBroker
from .storage import Storage
from .tot_generator import build_multi_agent_collaboration_label
from .translator import flatten_sections_to_chunks, translate_sections
from .llm_extractor import infer_domain_tags

try:
    from langgraph.graph import END, START, StateGraph

    LANGGRAPH_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    END = "__end__"
    START = "__start__"
    StateGraph = None  # type: ignore[assignment]
    LANGGRAPH_AVAILABLE = False


class PaperState(TypedDict, total=False):
    """图状态机中在各个节点之间传递数据的“托盘”结构。"""

    task_id: str
    paper_id: str
    title: str
    target_language: str
    template_name: str
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
    """构建 LangGraph 工作流（可选）。"""
    if not settings.langgraph_enabled or not LANGGRAPH_AVAILABLE or StateGraph is None:
        return None

    workflow = StateGraph(PaperState)
    collaboration_mode = build_multi_agent_collaboration_label(settings)
    execution_order = "先生成 -> 后评估 -> 再 ToT 分支扩展与剪枝"

    async def parse_node(state: PaperState) -> PaperState:
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
        failures = int(state.get("translation_failures", 0))
        retry_count = int(state.get("translation_retry_count", 0))
        if failures > 0 and retry_count <= settings.pipeline_translation_retry_limit:
            return "translate"
        return "summarize"

    async def summarize_node(state: PaperState) -> PaperState:
        await broker.update(state["task_id"], "summarizing", 70, "正在提取核心摘要。")
        translated_sections = state.get("translated_sections", []) or state.get("sections", [])
        translated_chunks = flatten_sections_to_chunks(translated_sections)
        tags = infer_domain_tags(state.get("text", ""), state["template_name"])
        template_text = await asyncio.to_thread(storage.read_template, state["template_name"])

        summary_md = await asyncio.to_thread(
            make_summary_markdown,
            state["title"],
            state["template_name"],
            state["target_language"],
            tags,
            template_text,
            state.get("chunks", []),
            translated_chunks,
            state.get("text", ""),
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
) -> list[str]:
    """线性流水线（LangGraph 不可用时回退）。"""
    collaboration_mode = build_multi_agent_collaboration_label(settings)
    execution_order = "先生成 -> 后评估 -> 再 ToT 分支扩展与剪枝"
    await broker.update(task_id, "parsing", 15, "正在解析 PDF 文本。")
    text = await asyncio.to_thread(extract_text_from_pdf, storage.pdf_path(paper_id))
    sections = split_text_into_sections(text)

    chunks = await asyncio.to_thread(
        chunk_text,
        text,
        settings.max_chunk_chars,
        settings.chunk_overlap,
    )
    await asyncio.to_thread(storage.save_chunks, paper_id, chunks)
    await asyncio.sleep(0.1)

    await broker.update(task_id, "translating", 45, "正在进行全文翻译。")
    translated_sections, translation_failures = await asyncio.to_thread(
        translate_sections,
        sections,
        target_language,
    )
    if translation_failures > 0 and settings.pipeline_translation_retry_limit > 0:
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

    translated_chunks = flatten_sections_to_chunks(translated_sections)
    tags = infer_domain_tags(text, template_name)
    template_text = await asyncio.to_thread(storage.read_template, template_name)

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
    )
    await asyncio.to_thread(storage.write_result, paper_id, "summary", summary_md, template_name)
    await asyncio.sleep(0.1)

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
) -> list[str]:
    """执行论文处理主流水线（优先 LangGraph，失败时回退线性流程）。"""
    graph = build_pipeline_graph(storage=storage, broker=broker, settings=settings)
    if graph is None:
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

    initial_state: PaperState = {
        "task_id": task_id,
        "paper_id": paper_id,
        "title": title,
        "target_language": target_language,
        "template_name": template_name,
        "generation_model_name": settings.generation_model_name,
        "evaluation_model_name": settings.evaluation_model_name,
        "collaboration_mode": build_multi_agent_collaboration_label(settings),
    }

    try:
        result = await graph.ainvoke(initial_state)
        return list(result.get("tags", [])) if isinstance(result, dict) else []
    except Exception:
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

