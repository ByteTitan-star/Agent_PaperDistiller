"""
   对外提供FastAPI路由，包括文件上传等接口
"""
import asyncio
import datetime as dt
import io
import json
import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pypdf import PdfReader

from .agent_skills import SkillRegistry
from .config import get_settings

from .pipeline.state_broker import TaskBroker
from .pipeline.workflow_graph import run_pipeline

from .schemas import (
    ChatRequest,
    ChatResponse,
    ContentResponse,
    PaperMeta,
    SystemInfoResponse,
    TemplateInfo,
    UploadResponse,
)
from .storage import Storage, domain_tag_from_template

# 应用级依赖
settings = get_settings()
backend_root = Path(__file__).resolve().parents[1]
project_root = backend_root.parent

storage = Storage(
    base_dir=backend_root / settings.data_dir,
    templates_dir=backend_root / settings.templates_dir,
    vector_provider=settings.vector_store_provider,
    vector_collection_name=settings.vector_collection_name,
    vector_db_subdir=settings.vector_db_subdir,
    embedding_model_name=settings.embedding_model_name,
    vector_distance_metric=settings.vector_distance_metric,
)
broker = TaskBroker()

skill_registry = SkillRegistry(
    skills_root=project_root / settings.agent_skills_dir,
    vector_db_dir=backend_root / settings.data_dir / settings.vector_db_subdir,
    embedding_model_name=settings.embedding_model_name,
    provider=settings.vector_store_provider,
    collection_name=settings.skills_collection_name,
)
skill_registry.load()

app = FastAPI(title=settings.app_name)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def utc_now_year() -> int:
    return dt.datetime.now(dt.timezone.utc).year


def tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-zA-Z0-9]+", text.lower()))


def try_extract_title(file_bytes: bytes, filename: str) -> str:
    """尝试从 PDF 元数据提取标题，失败回退文件名。"""
    try:
        pdf = PdfReader(io.BytesIO(file_bytes))
        if pdf.metadata and pdf.metadata.title:
            title = pdf.metadata.title.strip()
            if len(title) > 2 and "untitled" not in title.lower():
                return title
    except Exception:
        pass
    return Path(filename).stem


def retrieve_contexts_lexical(question: str, chunks: list[str], top_k: int) -> list[str]:
    if not chunks:
        return []

    q_tokens = tokenize(question)
    scored: list[tuple[float, str]] = []
    for chunk in chunks:
        c_tokens = tokenize(chunk)
        if not c_tokens:
            continue
        overlap = len(q_tokens.intersection(c_tokens))
        score = overlap / (len(q_tokens) + 1)
        scored.append((score, chunk))

    ranked = sorted(scored, key=lambda item: item[0], reverse=True)
    selected = [item[1] for item in ranked[:top_k] if item[0] > 0]
    if selected:
        return selected
    return chunks[:top_k]


def retrieve_contexts(question: str, paper_id: str, top_k: int) -> list[str]:
    """向量优先检索，失败时回退词法重叠。"""
    vector_contexts = storage.search_similar_chunks(paper_id=paper_id, question=question, top_k=top_k)
    if vector_contexts:
        return vector_contexts[:top_k]

    chunks = storage.load_chunks(paper_id)
    if settings.rag_fallback_to_lexical:
        return retrieve_contexts_lexical(question, chunks, top_k)
    return chunks[:top_k]


def build_fallback_answer(question: str, contexts: list[str], reason: str | None = None) -> str:
    lines = [
        "以下回答基于当前论文上下文生成：",
        "",
        f"问题：{question}",
        "",
        "关键依据：",
    ]
    for idx, context in enumerate(contexts, start=1):
        lines.append(f"{idx}. {context[:240]}")
    lines.extend(["", "结论：当前返回检索基线答案。"])
    if reason:
        lines.append(f"备注：未启用 DeepSeek Agent 原因：{reason}")
    return "\n".join(lines)


def build_deepseek_messages(question: str, contexts: list[str]) -> list[dict[str, str]]:
    context_block = "\n\n".join(
        f"[Context {idx}]\n{context[:2500]}" for idx, context in enumerate(contexts, start=1)
    )
    system_prompt = (
        "你是一个顶级的论文阅读分析 Agent。你拥有极强的学术理解能力。\n"
        "请结合提供的【全局核心摘要】与【局部正文片段】回答用户的问题。\n"
        "【策略指南】：\n"
        "1. 如果用户问的是宏观问题（如：本文做了哪些实验、用到什么数据集、核心思路是什么），请优先参考[Context 1]的全局摘要。\n"
        "2. 如果用户问的是具体细节（如：某个公式的意思、某个表格的数值），请仔细在后续的片段中寻找。\n"
        "3. 允许基于上下文进行合理的学术逻辑推断。如果提供的信息完全无法推导出答案，再回答“根据当前上下文无法确定”。"
    )
    user_prompt = (
        f"用户问题：{question}\n\n"
        "请基于以下给定的论文信息作答：\n"
        f"{context_block}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def requires_external_realtime_tool(question: str) -> bool:
    """仅当问题明确要求外部实时信息时才允许工具调用。"""
    low = question.lower().strip()
    realtime_keywords = [
        "最新",
        "实时",
        "今天",
        "近日",
        "news",
        "latest",
        "recent",
        "today",
        "github",
        "仓库",
        "repo",
        "arxiv",
        "下载代码",
        "download code",
    ]
    return any(token in low for token in realtime_keywords)


def sanitize_agent_output(text: str) -> str:
    """移除内部工具标签，确保只返回自然语言。"""
    if not text:
        return ""
    cleaned = re.sub(r"<｜DSML｜[^>]*>", "", text)
    cleaned = re.sub(r"<\|DSML\|[^>]*>", "", cleaned)
    return cleaned.strip()


def split_answer_and_reasoning(text: str) -> tuple[str, str | None]:
    text = sanitize_agent_output(text)
    if "<think>" not in text:
        return text.strip(), None
    think_blocks = re.findall(r"<think>(.*?)</think>", text, flags=re.DOTALL)
    reasoning = "\n\n".join(item.strip() for item in think_blocks if item.strip()) or None
    clean_text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    return clean_text, reasoning


def call_deepseek_chat(
    question: str,
    contexts: list[str],
    paper_id: str,
) -> tuple[str | None, str | None, str | None]:
    """DeepSeek Agent 调用，支持技能检索与工具循环。"""
    if not settings.deepseek_api_key.strip():
        return None, None, "DEEPSEEK_API_KEY 未配置"

    try:
        from openai import OpenAI
    except Exception:
        return None, None, "缺少 openai SDK，请先执行: pip install -r backend/requirements.txt"

    # 工具调用双重门控：
    # 1) 问题明确需要外部实时信息；
    # 2) 语义匹配分数 >= 0.8 的工具存在。
    allow_tools = settings.agent_enable_tools and requires_external_realtime_tool(question)
    selected_skills = (
        skill_registry.select_tools(question, top_k=settings.skill_retrieval_top_k, min_similarity=0.8)
        if allow_tools
        else []
    )
    tools_schema = skill_registry.build_openai_tools(selected_skills)
    skill_hint = skill_registry.build_skill_hint(selected_skills)

    try:
        client = OpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url.rstrip("/"),
            timeout=settings.deepseek_timeout_sec,
        )
        messages: list[dict[str, Any]] = build_deepseek_messages(question, contexts)
        if skill_hint:
            messages.insert(1, {"role": "system", "content": skill_hint})

        paper_chunks = storage.load_chunks(paper_id)
        tool_context: dict[str, Any] = {
            "paper_id": paper_id,
            "question": question,
            "chunks": paper_chunks,
            "vector_search": lambda q, k: storage.search_similar_chunks(
                paper_id=paper_id,
                question=str(q),
                top_k=max(1, int(k)),
            ),
        }

        reasoning_parts: list[str] = []
        max_rounds = max(1, settings.agent_max_tool_rounds)
        for _ in range(max_rounds):
            request_kwargs: dict[str, Any] = {
                "model": settings.deepseek_model,
                "messages": messages,
                "temperature": 0.2,
                "max_tokens": 1200,
            }
            if allow_tools and tools_schema:
                request_kwargs["tools"] = tools_schema
                request_kwargs["tool_choice"] = "auto"

            response = client.chat.completions.create(**request_kwargs)
            message = response.choices[0].message
            response_content = (message.content or "").strip()

            clean_content, think_reasoning = split_answer_and_reasoning(response_content)
            if think_reasoning:
                reasoning_parts.append(think_reasoning)
            explicit_reasoning = getattr(message, "reasoning_content", None)
            if explicit_reasoning:
                reasoning_parts.append(str(explicit_reasoning).strip())

            raw_tool_calls = getattr(message, "tool_calls", None) or []
            if raw_tool_calls and allow_tools and tools_schema:
                normalized_tool_calls: list[dict[str, Any]] = []
                for call in raw_tool_calls:
                    call_id = str(getattr(call, "id", ""))
                    function = getattr(call, "function", None)
                    function_name = str(getattr(function, "name", ""))
                    function_args = str(getattr(function, "arguments", "{}"))
                    normalized_tool_calls.append(
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": function_name, "arguments": function_args},
                        }
                    )

                messages.append(
                    {
                        "role": "assistant",
                        "content": message.content or "",
                        "tool_calls": normalized_tool_calls,
                    }
                )

                for call in normalized_tool_calls:
                    tool_name = call["function"]["name"]
                    raw_arguments = call["function"]["arguments"]
                    try:
                        arguments = json.loads(raw_arguments) if raw_arguments else {}
                    except json.JSONDecodeError:
                        arguments = {}

                    result = skill_registry.execute(
                        tool_name=tool_name,
                        arguments=arguments if isinstance(arguments, dict) else {},
                        context=tool_context,
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )
                continue

            if clean_content:
                reasoning_trace = "\n\n".join(item for item in reasoning_parts if item).strip() or None
                return clean_content, reasoning_trace, None

        # 工具轮次耗尽后收敛一次
        response = client.chat.completions.create(
            model=settings.deepseek_model,
            messages=messages,
            temperature=0.2,
            max_tokens=900,
            # stream = True
        )
        final_content = (response.choices[0].message.content or "").strip()
        clean_content, think_reasoning = split_answer_and_reasoning(final_content)
        if think_reasoning:
            reasoning_parts.append(think_reasoning)
        if clean_content:
            reasoning_trace = "\n\n".join(item for item in reasoning_parts if item).strip() or None
            return clean_content, reasoning_trace, None
        return None, None, "DeepSeek 返回空内容"
    except Exception as exc:  # pragma: no cover - network/runtime dependent
        return None, None, str(exc)


async def execute_pipeline(task_id: str, paper_id: str, title: str, target_language: str, template_name: str) -> None:
    try:
        tags = await run_pipeline(
            task_id=task_id,
            paper_id=paper_id,
            title=title,
            target_language=target_language,
            template_name=template_name,
            storage=storage,
            broker=broker,
            settings=settings,
        )
        storage.update_paper_status(paper_id, "completed", domain_tags=tags)
    except Exception as exc:
        storage.update_paper_status(paper_id, "failed")
        await broker.update(task_id, "failed", 100, f"任务失败: {exc}")


@app.get(f"{settings.api_prefix}/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get(f"{settings.api_prefix}/system/info", response_model=SystemInfoResponse)
async def get_system_info() -> SystemInfoResponse:
    use_deepseek = bool(settings.deepseek_api_key.strip())
    collaboration_mode = (
        f"Multi-Agent Collaboration: "
        f"{settings.generation_model_name} (Gen) + {settings.evaluation_model_name} (Eval)"
    )
    skill_status = skill_registry.status()
    return SystemInfoResponse(
        app_name=settings.app_name,
        model_provider="DeepSeek + DashScope(Qwen3)" if use_deepseek else settings.model_provider,
        llm_model_name=settings.deepseek_model if use_deepseek else settings.llm_model_name,
        generation_model_name=settings.generation_model_name,
        evaluation_model_name=settings.evaluation_model_name,
        collaboration_mode=collaboration_mode,
        embedding_model_name=settings.embedding_model_name,
        pipeline_mode=f"{settings.pipeline_mode} (skills={skill_status['skill_count']})",
    )


@app.get(f"{settings.api_prefix}/templates", response_model=list[TemplateInfo])
async def list_templates() -> list[TemplateInfo]:
    return [TemplateInfo(name=name) for name in storage.list_templates()]


@app.post(f"{settings.api_prefix}/upload", response_model=UploadResponse)
async def upload_paper(
    file: UploadFile = File(...),
    target_language: str = Form(default="Chinese"),
    summary_template: str = Form(default="tinghua.md"),
) -> UploadResponse:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持上传 PDF 文件。")

    file_content = await file.read()
    title_candidate = try_extract_title(file_content, file.filename)

    paper_id = storage.allocate_paper_id(title_candidate)
    task_id = uuid.uuid4().hex
    template_domain = domain_tag_from_template(summary_template)

    await file.seek(0)
    storage.save_upload(paper_id, file, source_filename=file.filename)
    await file.close()

    paper_meta = PaperMeta(
        paper_id=paper_id,
        title=title_candidate,
        source_filename=file.filename,
        created_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        target_language=target_language,
        summary_template=summary_template,
        status="processing",
        year=utc_now_year(),
        authors=[],
        domain_tags=[template_domain],
    )
    storage.upsert_paper(paper_meta)

    await broker.create(
        task_id,
        paper_id,
        generation_model_name=settings.generation_model_name,
        evaluation_model_name=settings.evaluation_model_name,
        collaboration_mode=(
            f"Multi-Agent Collaboration: "
            f"{settings.generation_model_name} (Gen) + {settings.evaluation_model_name} (Eval)"
        ),
    )
    asyncio.create_task(
        execute_pipeline(
            task_id=task_id,
            paper_id=paper_id,
            title=paper_meta.title,
            target_language=target_language,
            template_name=summary_template,
        )
    )
    return UploadResponse(task_id=task_id, paper_id=paper_id)


@app.get(f"{settings.api_prefix}/tasks/{{task_id}}")
async def get_task(task_id: str) -> dict:
    state = await broker.get(task_id)
    if not state:
        raise HTTPException(status_code=404, detail="任务不存在。")
    return state.model_dump()


@app.get(f"{settings.api_prefix}/tasks/{{task_id}}/events")
async def task_events(task_id: str):
    state = await broker.get(task_id)
    if not state:
        raise HTTPException(status_code=404, detail="任务不存在。")
    return StreamingResponse(
        broker.subscribe(task_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.get(f"{settings.api_prefix}/papers", response_model=list[PaperMeta])
async def list_papers() -> list[PaperMeta]:
    return storage.list_papers()


@app.get(f"{settings.api_prefix}/papers/{{paper_id}}", response_model=PaperMeta)
async def get_paper(paper_id: str) -> PaperMeta:
    paper = storage.get_paper(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="论文不存在。")
    return paper


@app.get(f"{settings.api_prefix}/papers/{{paper_id}}/content/{{kind}}", response_model=ContentResponse)
async def get_content(paper_id: str, kind: str) -> ContentResponse:
    if kind not in {"translation", "summary", "improvement"}:
        raise HTTPException(status_code=400, detail="不支持的内容类型。")

    paper = storage.get_paper(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="论文不存在。")

    summary_template = paper.summary_template if kind == "summary" else None
    content = storage.read_result(paper_id, kind, summary_template=summary_template)  # type: ignore[arg-type]
    return ContentResponse(paper_id=paper_id, kind=kind, content=content)  # type: ignore[arg-type]


@app.get(f"{settings.api_prefix}/papers/{{paper_id}}/pdf")
async def get_pdf(paper_id: str):
    pdf_path = storage.pdf_path(paper_id)
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF 文件不存在。")
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline"},
    )


@app.get(f"{settings.api_prefix}/papers/{{paper_id}}/pdf/download")
async def download_pdf(paper_id: str):
    paper = storage.get_paper(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="论文不存在。")

    pdf_path = storage.pdf_path(paper_id)
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF 文件不存在。")

    filename = paper.source_filename or f"{paper_id}.pdf"
    return FileResponse(pdf_path, media_type="application/pdf", filename=filename)


@app.get(f"{settings.api_prefix}/papers/{{paper_id}}/translation/layout")
async def get_translation_layout(paper_id: str):
    if not storage.get_paper(paper_id):
        raise HTTPException(status_code=404, detail="论文不存在。")

    layout_path = storage.paper_output_dir(paper_id) / "translated_layout.html"
    if not layout_path.exists():
        raise HTTPException(status_code=404, detail="双栏翻译版尚未生成。")

    return FileResponse(
        layout_path,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": "inline"},
    )


@app.post(f"{settings.api_prefix}/papers/{{paper_id}}/chat", response_model=ChatResponse)
async def chat(paper_id: str, payload: ChatRequest) -> ChatResponse:
    paper = storage.get_paper(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="论文不存在。")

    # 1. 获取局部切块 (把默认 top_k 调大点，不要抠抠搜搜只给3个)
    top_k = payload.top_k or max(8, settings.rag_default_top_k) 
    contexts = retrieve_contexts(payload.question, paper_id=paper_id, top_k=top_k)
    
    # 2. 核心改进：无论如何，都把这篇论文的“全局摘要”读取出来
    full_summary = storage.read_result(
        paper_id,
        "summary",
        summary_template=paper.summary_template,
    )
    
    # 3. 将全局摘要作为第一块 Context，让大模型先建立全局认知
    if full_summary:
        contexts.insert(0, f"【本文全局核心摘要与结构化信息】\n{full_summary}")
    elif not contexts:
        contexts = ["暂无可用上下文。"]

    answer, reasoning_trace, error = await asyncio.to_thread(
        call_deepseek_chat,
        payload.question,
        contexts,
        paper_id,
    )
    if not answer:
        answer = build_fallback_answer(payload.question, contexts, reason=error)
    return ChatResponse(answer=answer, contexts=contexts, reasoning_trace=reasoning_trace)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
