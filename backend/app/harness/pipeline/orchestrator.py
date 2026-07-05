"""Paper pipeline orchestrator — deterministic step runner through ToolRegistry."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ...pipeline.document_parser import chunk_text, extract_text_from_pdf, split_text_into_sections
from ...pipeline.llm_extractor import infer_domain_tags
from ...pipeline.renderer import (
    make_improvement_markdown,
    make_summary_markdown,
    make_translation_layout_html,
    make_translation_markdown,
)
from ...pipeline.state_broker import TaskBroker
from ...pipeline.tot_generator import build_multi_agent_collaboration_label
from ...pipeline.translator import flatten_sections_to_chunks, translate_sections
from ...storage import Storage, resolve_template_content

logger = logging.getLogger(__name__)


async def run_parse_step(
    *,
    paper_id: str,
    task_id: str,
    storage: Storage,
    broker: TaskBroker,
    settings: Any,
) -> dict[str, Any]:
    await broker.update(task_id, "parsing", 15, "正在解析 PDF 文本。")
    text = await asyncio.to_thread(extract_text_from_pdf, storage.pdf_path(paper_id))
    sections = split_text_into_sections(text)
    chunks = await asyncio.to_thread(chunk_text, text, settings.max_chunk_chars, settings.chunk_overlap)
    await asyncio.to_thread(storage.save_chunks, paper_id, chunks)
    return {"ok": True, "sections": len(sections), "chunks": len(chunks)}


async def run_translate_step(
    *,
    paper_id: str,
    task_id: str,
    target_language: str,
    storage: Storage,
    broker: TaskBroker,
    settings: Any,
    title: str = "",
    retry_count: int = 0,
) -> dict[str, Any]:
    progress = 45 if retry_count == 0 else 55
    await broker.update(task_id, "translating", progress, f"正在进行全文翻译（第 {retry_count + 1} 轮）。")
    text = await asyncio.to_thread(extract_text_from_pdf, storage.pdf_path(paper_id))
    sections = split_text_into_sections(text)
    translated_sections, translation_failures = await asyncio.to_thread(translate_sections, sections, target_language)
    translation_md = await asyncio.to_thread(
        make_translation_markdown,
        title or paper_id,
        target_language,
        translated_sections,
        translation_failures,
    )
    await asyncio.to_thread(storage.write_result, paper_id, "translation", translation_md)
    layout_html = await asyncio.to_thread(
        make_translation_layout_html, title or paper_id, target_language, translated_sections
    )
    layout_path = storage.paper_output_dir(paper_id) / "translated_layout.html"
    await asyncio.to_thread(layout_path.write_text, layout_html, "utf-8")
    storage._upload_to_oss(layout_path, paper_id, "translated_layout.html")
    ok = translation_failures == 0 or retry_count >= settings.pipeline_translation_retry_limit
    return {
        "ok": ok,
        "translation_failures": translation_failures,
        "retry_count": retry_count + 1,
        "translated_sections": len(translated_sections),
    }


async def run_summarize_step(
    *,
    paper_id: str,
    task_id: str,
    template_name: str,
    storage: Storage,
    broker: TaskBroker,
    settings: Any,
    agent_factory: Any,
    title: str = "",
    target_language: str = "Chinese",
    user_id: int | None = None,
) -> dict[str, Any]:
    await broker.update(task_id, "summarizing", 70, "正在提取核心摘要。")
    text = await asyncio.to_thread(extract_text_from_pdf, storage.pdf_path(paper_id))
    sections = split_text_into_sections(text)
    translated_sections, _ = await asyncio.to_thread(translate_sections, sections, target_language)
    translated_chunks = flatten_sections_to_chunks(translated_sections)
    chunks = storage.load_chunks(paper_id) or []
    tags = infer_domain_tags(text, template_name)
    template_text = await resolve_template_content(template_name, user_id) or await asyncio.to_thread(
        storage.read_template, template_name
    )
    deepseek_agent = agent_factory.create_deepseek()
    summary_md = await make_summary_markdown(
        title=title or paper_id,
        template_name=template_name,
        target_language=target_language,
        tags=tags,
        template_text=template_text,
        source_chunks=chunks,
        translated_chunks=translated_chunks,
        text=text,
        deepseek_agent=deepseek_agent,
        user_id=user_id,
    )
    await asyncio.to_thread(storage.write_result, paper_id, "summary", summary_md, template_name)
    return {"ok": True, "tags": tags}


async def run_critique_step(
    *,
    paper_id: str,
    task_id: str,
    template_name: str,
    storage: Storage,
    broker: TaskBroker,
    settings: Any,
    agent_factory: Any,
    collaboration_registry: Any | None = None,
    title: str = "",
    user_id: int | None = None,
) -> dict[str, Any]:
    collaboration_mode = build_multi_agent_collaboration_label(settings)
    await broker.update(
        task_id,
        "critiquing",
        90,
        f"{collaboration_mode} | 正在生成改进与创新方案。",
    )
    text = await asyncio.to_thread(extract_text_from_pdf, storage.pdf_path(paper_id))
    sections = split_text_into_sections(text)
    translated_sections, _ = await asyncio.to_thread(translate_sections, sections, "Chinese")
    translated_chunks = flatten_sections_to_chunks(translated_sections)
    chunks = storage.load_chunks(paper_id) or []
    tags = infer_domain_tags(text, template_name)

    tot_agent = agent_factory.create_tot()
    mode = getattr(settings, "default_collaboration_mode", "round_robin")
    if collaboration_registry is not None and mode == "supervisor":
        pattern = collaboration_registry.create(
            "supervisor",
            [agent_factory.create_deepseek(), agent_factory.create_qwen()],
        )
        collab = await pattern.run(
            f"Analyze innovation opportunities for paper «{title or paper_id}». Tags: {tags}",
            user_id=user_id,
        )
        improvement_md = collab.final_output or ""
        if collab.error and not improvement_md:
            improvement_md = await make_improvement_markdown(
                title=title or paper_id,
                tags=tags,
                source_chunks=chunks,
                translated_chunks=translated_chunks,
                settings=settings,
                tot_agent=tot_agent,
                user_id=user_id,
            )
    elif collaboration_registry is not None and mode == "round_robin":
        pattern = collaboration_registry.create(
            "round_robin",
            [agent_factory.create_deepseek(), agent_factory.create_qwen()],
            rounds=1,
        )
        collab = await pattern.run(
            f"Draft and refine innovation critique for «{title or paper_id}».",
            user_id=user_id,
        )
        improvement_md = collab.final_output or await make_improvement_markdown(
            title=title or paper_id,
            tags=tags,
            source_chunks=chunks,
            translated_chunks=translated_chunks,
            settings=settings,
            tot_agent=tot_agent,
            user_id=user_id,
        )
    else:
        improvement_md = await make_improvement_markdown(
            title=title or paper_id,
            tags=tags,
            source_chunks=chunks,
            translated_chunks=translated_chunks,
            settings=settings,
            tot_agent=tot_agent,
            user_id=user_id,
        )

    await asyncio.to_thread(storage.write_result, paper_id, "improvement", improvement_md)
    await broker.update(task_id, "done", 100, f"任务已完成。{collaboration_mode}")
    return {"ok": True, "collaboration_mode": collaboration_mode}


class PaperPipelineOrchestrator:
    """Production pipeline coordinator — all steps go through harness primitives."""

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime

    async def run(
        self,
        *,
        task_id: str,
        paper_id: str,
        title: str,
        target_language: str,
        template_name: str,
        settings: Any,
        user_id: int | None = None,
        hitl_manager: Any | None = None,
    ) -> list[str]:
        rt = self._runtime
        rt.settings = settings
        if hitl_manager and "critique" in getattr(settings, "hitl_checkpoints", []):
            hitl_state = await hitl_manager.interrupt("pre_critique", {"paper_id": paper_id, "task_id": task_id})
            await hitl_manager.wait_for_decision(hitl_state.id)

        await run_parse_step(
            paper_id=paper_id,
            task_id=task_id,
            storage=rt.storage,
            broker=rt.broker,
            settings=settings,
        )

        retry = 0
        while True:
            tr = await run_translate_step(
                paper_id=paper_id,
                task_id=task_id,
                target_language=target_language,
                storage=rt.storage,
                broker=rt.broker,
                settings=settings,
                title=title,
                retry_count=retry,
            )
            if tr.get("ok") or tr.get("translation_failures", 0) == 0:
                break
            if retry >= settings.pipeline_translation_retry_limit:
                break
            retry += 1

        sm = await run_summarize_step(
            paper_id=paper_id,
            task_id=task_id,
            template_name=template_name,
            storage=rt.storage,
            broker=rt.broker,
            settings=settings,
            agent_factory=rt.agent_factory,
            title=title,
            target_language=target_language,
            user_id=user_id,
        )
        tags = sm.get("tags") or []

        await run_critique_step(
            paper_id=paper_id,
            task_id=task_id,
            template_name=template_name,
            storage=rt.storage,
            broker=rt.broker,
            settings=settings,
            agent_factory=rt.agent_factory,
            collaboration_registry=rt.collaboration_registry,
            title=title,
            user_id=user_id,
        )
        return tags
