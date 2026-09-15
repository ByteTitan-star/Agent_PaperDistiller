"""Paper pipeline orchestrator — deterministic step runner through ToolRegistry.

Phase 0 重构（见 todo.md）：
- 解析一次：parse 步骤产出 DocumentIR 并持久化（parse_artifact.json），
  后续步骤只加载产物，缺失时才兜底重解析；
- 翻译一次：translate 步骤产物持久化（translated_sections.json），summarize/critique 复用；
- 错误传播：解析失败（扫描件/加密/损坏）抛 PipelineParseError，
  不再把错误文案当正文送翻译/摘要。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ...pipeline.document_ir import DocumentIR
from ...pipeline.document_parser import chunk_sections_with_meta, parse_document
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

# 解析失败的用户可读提示（error_kind -> message）
PARSE_ERROR_MESSAGES = {
    "scanned_pdf": (
        "该 PDF 疑似扫描件（无文本层），请在服务端启用 MinerU（parser_mineru_enabled）"
        "或 PaddleOCR（parser_ocr_enabled）后重试。"
    ),
    "encrypted_pdf": "该 PDF 已加密，无法解析，请解除保护后重新上传。",
    "corrupt_pdf": "该 PDF 无法打开或已损坏，请检查文件后重新上传。",
    "empty_text": "未能从该 PDF 提取到有效文本，请确认文件内容后重新上传。",
}


class PipelineParseError(Exception):
    """解析失败（扫描件/加密/损坏），管线应立即终止。"""

    def __init__(self, error_kind: str, detail: str = "") -> None:
        message = PARSE_ERROR_MESSAGES.get(error_kind, f"PDF 解析失败: {error_kind}")
        if detail:
            message = f"{message}（{detail}）"
        super().__init__(message)
        self.error_kind = error_kind


def _source_path(paper_id: str, storage: Storage | Any) -> Any:
    """源文件路径（PDF/MD/DOCX 通用）；旧 storage 替身回退 pdf_path。"""
    source_path = getattr(storage, "source_path", None)
    if callable(source_path):
        return source_path(paper_id)
    return storage.pdf_path(paper_id)


def _load_ir(paper_id: str, storage: Storage | Any, settings: Any) -> DocumentIR:
    """加载解析产物；不存在时先做 SHA256 去重（同文件复用旧产物），否则解析并持久化。"""
    ir = storage.load_parse_artifact(paper_id)
    if ir is not None:
        return ir

    pdf_path = _source_path(paper_id, storage)

    # SHA256 去重：同一文件重复上传时复用既有解析产物，不重跑解析引擎
    find_by_sha = getattr(storage, "find_artifact_by_sha256", None)
    if callable(find_by_sha):
        try:
            reused = find_by_sha(pdf_path)
        except Exception as exc:
            logger.warning("[解析] SHA256 去重查询失败（忽略）: %s", exc)
            reused = None
        if reused is not None:
            storage.save_parse_artifact(paper_id, reused)
            return reused

    ir = parse_document(pdf_path, settings)
    if ir.ok:
        _enrich_with_grobid(ir, pdf_path, settings)
        storage.save_parse_artifact(paper_id, ir)
        record_sha = getattr(storage, "record_sha256", None)
        if callable(record_sha):
            try:
                record_sha(paper_id, pdf_path)
            except Exception as exc:
                logger.warning("[解析] SHA256 索引记录失败（忽略）: %s", exc)
    return ir


def _enrich_with_grobid(ir: DocumentIR, source_path: Any, settings: Any) -> str:
    """GROBID 元数据增强（可选，仅 PDF）；失败只告警。"""
    try:
        from ...pipeline.grobid import enrich_ir_metadata

        enriched, note = enrich_ir_metadata(ir, source_path, settings)
        if not enriched and note not in {"grobid_disabled", "not_pdf"}:
            logger.info("[GROBID] 元数据增强跳过: %s", note)
        return note
    except Exception as exc:
        logger.warning("[GROBID] 增强异常（忽略）: %s", exc)
        return "grobid_error"


def _require_ir(ir: DocumentIR) -> DocumentIR:
    """解析失败即抛错终止（错误传播），不允许错误文案流入下游。"""
    if not ir.ok or not ir.sections:
        raise PipelineParseError(ir.error_kind or "empty_text", ir.error)
    return ir


async def translate_sections_smart(
    sections: list[tuple[str, str]],
    target_language: str,
    settings: Any,
) -> tuple[list[tuple[str, str]], int, str]:
    """翻译分发：LLM 通道（保留 LaTeX）优先，Google 接口兜底。

    返回 (translated_sections, failures, provider)。
    """
    from ...pipeline.translator import normalize_language_code

    if normalize_language_code(target_language).startswith("en"):
        return sections, 0, "none"

    provider = str(getattr(settings, "translation_provider", "auto") or "auto").lower()
    api_key = str(getattr(settings, "deepseek_api_key", "") or "")
    llm_ready = api_key and api_key != "your-api-key"

    if provider in {"llm", "auto"} and llm_ready:
        try:
            from ...pipeline.llm_translator import translate_sections_llm

            translated, failures = await translate_sections_llm(sections, target_language, settings)
            return translated, failures, "llm"
        except Exception as exc:
            logger.warning("[翻译] ⚠️ LLM 通道不可用，回退 Google: %s", exc)
            if provider == "llm":
                # 显式指定 llm 时不静默降级，直接抛出让上层重试逻辑处理
                raise

    translated, failures = await asyncio.to_thread(translate_sections, sections, target_language)
    return translated, failures, "google"


async def _load_or_translate(
    paper_id: str,
    target_language: str,
    storage: Storage | Any,
    settings: Any,
) -> tuple[list[tuple[str, str]], int]:
    """翻译产物复用：已有 translated_sections.json 时不重翻。"""
    cached = storage.load_translated_sections(paper_id, target_language)
    if cached is not None:
        sections, failures = cached
        return sections, failures
    ir = await asyncio.to_thread(_load_ir, paper_id, storage, settings)
    _require_ir(ir)
    translated, failures, provider = await translate_sections_smart(ir.sections, target_language, settings)
    logger.info("[翻译] provider=%s failures=%d sections=%d", provider, failures, len(translated))
    storage.save_translated_sections(paper_id, target_language, translated, failures)
    return translated, failures


async def _job_transition(paper_id: str, task_id: str, stage: str, **kwargs: Any) -> None:
    """document_jobs 阶段流转（best-effort，DB 不可用时只告警，不阻塞管线）。"""
    from ...services.document_jobs import transition_document_job

    await transition_document_job(paper_id, task_id, stage, **kwargs)


async def run_parse_step(
    *,
    paper_id: str,
    task_id: str,
    storage: Storage,
    broker: TaskBroker,
    settings: Any,
) -> dict[str, Any]:
    from ...services.document_jobs import create_document_job

    await create_document_job(paper_id, task_id)
    await _job_transition(paper_id, task_id, "PARSING")
    await broker.update(task_id, "parsing", 15, "正在解析 PDF 文本。")
    ir = await asyncio.to_thread(_load_ir, paper_id, storage, settings)
    if not ir.ok:
        # 错误传播：不把失败当成功继续，交给上层抛 PipelineParseError
        await _job_transition(paper_id, task_id, "FAILED", parser=ir.parser, error=ir.error)
        return {
            "ok": False,
            "error_kind": ir.error_kind,
            "error": ir.error,
            "parser": ir.parser,
            "sections": 0,
            "chunks": 0,
        }

    await _job_transition(paper_id, task_id, "CHUNKING", parser=ir.parser)
    # 章节标题 -> 起始页（来自标题节点），切块元数据带上页码以支持定位回原始页
    section_pages = {node.text: node.page for node in ir.nodes if node.type == "heading" and node.text}
    chunks, metas = await asyncio.to_thread(
        chunk_sections_with_meta,
        ir.sections,
        settings.max_chunk_chars,
        settings.chunk_overlap,
        section_pages,
    )
    await _job_transition(paper_id, task_id, "EMBEDDING")
    await asyncio.to_thread(storage.save_chunks, paper_id, chunks, metas)
    await asyncio.to_thread(storage.save_parse_artifact, paper_id, ir)
    await _job_transition(paper_id, task_id, "INDEXED")
    return {
        "ok": True,
        "parser": ir.parser,
        "sections": len(ir.sections),
        "chunks": len(chunks),
        "nodes": ir.node_stats(),
    }


async def run_figure_step(
    *,
    paper_id: str,
    task_id: str,
    storage: Storage,
    broker: TaskBroker,
    settings: Any,
    silent: bool = False,
) -> dict[str, Any]:
    """VLM 图表描述（可选步骤）：figure 节点裁剪 -> 多模态模型描述 -> 描述块入检索库。

    - vlm_enabled=False 或无 key 时直接跳过（零开销）；
    - 已有描述的节点（SHA256 复用产物）不重复调用；
    - 单图失败只降级该图，步骤整体异常也不阻塞主管线；
    - 描述块带 element_type="image_desc" 元数据，RAG 与图表证据技能均可命中。
    """
    from ...pipeline.vlm_describer import describe_figure_nodes, get_vlm_describer

    describer = get_vlm_describer(settings)
    if describer is None:
        return {"ok": True, "skipped": "vlm_disabled", "figures": 0, "described": 0}

    source = _source_path(paper_id, storage)
    if str(getattr(source, "suffix", "") or "").lower() != ".pdf":
        return {"ok": True, "skipped": "not_pdf", "figures": 0, "described": 0}  # 裁剪依赖 PDF

    ir = await asyncio.to_thread(_load_ir, paper_id, storage, settings)
    if not ir.ok:
        return {"ok": True, "skipped": "parse_failed", "figures": 0, "described": 0}

    max_figures = int(getattr(settings, "vlm_max_figures", 12) or 12)
    figures = [n for n in ir.nodes if n.type == "figure" and not n.text.strip()][:max_figures]
    if not figures:
        return {"ok": True, "skipped": "no_figures", "figures": 0, "described": 0}

    if not silent:
        await broker.update(task_id, "parsing", 30, f"正在生成 {len(figures)} 张图表的 VLM 描述。")
    try:
        described, _failed = await describe_figure_nodes(
            storage.pdf_path(paper_id),  # 已确认是 PDF
            figures,
            describer,
            concurrency=int(getattr(settings, "vlm_concurrency", 3) or 3),
        )
    except Exception as exc:
        logger.warning("[图表描述] 步骤失败（不阻塞管线）: %s", exc)
        return {"ok": True, "skipped": "vlm_error", "figures": len(figures), "described": 0}

    if described:
        # 产物回写 + 描述块入库（图注 + VLM 描述）
        await asyncio.to_thread(storage.save_parse_artifact, paper_id, ir)
        existing_chunks = storage.load_chunks(paper_id)
        existing_metas = storage.load_chunk_metas(paper_id)
        new_chunks: list[str] = []
        new_metas: list[dict[str, Any]] = []
        for node in figures:
            if not node.text.strip():
                continue
            caption = node.caption or "未命名图表"
            new_chunks.append(f"[图片描述 Page {node.page}] {caption}\n{node.text}")
            new_metas.append(
                {"element_type": "image_desc", "section": "图表", "page": node.page, "is_reference": False}
            )
        if new_chunks:
            await asyncio.to_thread(
                storage.save_chunks, paper_id, existing_chunks + new_chunks, existing_metas + new_metas
            )
    return {"ok": True, "figures": len(figures), "described": described}


# 进程内后台图表描述任务注册表（paper_id -> Task）。
# 生产可平滑替换为 Redis 队列（roadmap 改进 6）：调度器接口不变，换实现即可。
_background_figure_tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}


def pending_figure_tasks() -> list[str]:
    """仍在执行的图表描述任务对应的 paper_id（可观测/测试用）。"""
    return [pid for pid, task in _background_figure_tasks.items() if not task.done()]


def schedule_figure_step_async(
    *,
    paper_id: str,
    task_id: str,
    storage: Storage,
    broker: TaskBroker,
    settings: Any,
) -> bool:
    """调度后台 VLM 图表描述：立即返回，管线不等描述完成（极速解析入库的体验）。

    - 同一 paper 已有未完成任务时跳过（防重复描述/重复计费）；
    - 任务内自行捕获全部异常（只告警），不向任何人传播；
    - 完成后描述仍会回写产物并追加 image_desc 检索块。
    返回是否发起了新任务。
    """
    for pid, task in list(_background_figure_tasks.items()):
        if task.done():
            del _background_figure_tasks[pid]
    if paper_id in _background_figure_tasks:
        logger.info("[图表描述] paper_id=%s 后台任务已在执行，跳过重复调度", paper_id)
        return False

    async def runner() -> dict[str, Any]:
        try:
            return await run_figure_step(
                paper_id=paper_id,
                task_id=task_id,
                storage=storage,
                broker=broker,
                settings=settings,
                silent=True,
            )
        except Exception as exc:
            logger.warning("[图表描述] 后台任务失败（不影响已完成的主任务）: %s", exc)
            return {"ok": False, "skipped": "background_error"}

    _background_figure_tasks[paper_id] = asyncio.create_task(runner())
    logger.info("[图表描述] paper_id=%s 已转入后台执行（vlm_mode=async）", paper_id)
    return True


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
    translated_sections, translation_failures = await _load_or_translate(paper_id, target_language, storage, settings)

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
    ir = await asyncio.to_thread(_load_ir, paper_id, storage, settings)
    _require_ir(ir)
    # 翻译产物复用（translate 步骤已持久化），缺失时兜底翻译
    translated_sections, _ = await _load_or_translate(paper_id, target_language, storage, settings)
    translated_chunks = flatten_sections_to_chunks(translated_sections)
    chunks = storage.load_chunks(paper_id) or []
    tags = infer_domain_tags(ir.text, template_name)
    template_text = await resolve_template_content(template_name, user_id) or await asyncio.to_thread(
        storage.read_template, template_name
    )
    deepseek_agent = agent_factory.create_deepseek()
    summary_md = await make_summary_markdown(
        title=title or paper_id,
        template_name=template_name,
        target_language=target_language,
        tags=tags,
        template_text=template_text or "",
        source_chunks=chunks,
        translated_chunks=translated_chunks,
        text=ir.text,
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
    ir = await asyncio.to_thread(_load_ir, paper_id, storage, settings)
    _require_ir(ir)
    translated_sections, _ = await _load_or_translate(paper_id, "Chinese", storage, settings)
    translated_chunks = flatten_sections_to_chunks(translated_sections)
    chunks = storage.load_chunks(paper_id) or []
    tags = infer_domain_tags(ir.text, template_name)

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

        parse_result = await run_parse_step(
            paper_id=paper_id,
            task_id=task_id,
            storage=rt.storage,
            broker=rt.broker,
            settings=settings,
        )
        if not parse_result.get("ok"):
            # 错误传播：解析失败立即终止，给用户明确原因
            raise PipelineParseError(
                str(parse_result.get("error_kind", "empty_text")),
                str(parse_result.get("error", "")),
            )

        # VLM 图表描述（可选，内部自带开关与降级，失败不阻塞主管线）
        # vlm_mode=async：转后台执行，管线立即继续（极速解析入库）；sync：原地等待
        if str(getattr(settings, "vlm_mode", "sync") or "sync").lower() == "async":
            schedule_figure_step_async(
                paper_id=paper_id,
                task_id=task_id,
                storage=rt.storage,
                broker=rt.broker,
                settings=settings,
            )
        else:
            await run_figure_step(
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
