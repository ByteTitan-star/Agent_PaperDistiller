import asyncio
import json
import re
from typing import Any

from ..config import get_settings
from ..dependencies import skill_registry
from ..schemas import ChatRequest, ChatResponse
from .token_utils import tokenize

settings = get_settings()

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

def retrieve_contexts(question: str, paper_id: str, top_k: int, storage) -> list[str]:
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
    except ImportError:
        return None, None, "缺少 openai SDK，请先执行: pip install -r backend/requirements.txt"

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

        # 导入 storage 用于工具上下文
        from ..dependencies import storage

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
        )
        final_content = (response.choices[0].message.content or "").strip()
        clean_content, think_reasoning = split_answer_and_reasoning(final_content)
        if think_reasoning:
            reasoning_parts.append(think_reasoning)
        if clean_content:
            reasoning_trace = "\n\n".join(item for item in reasoning_parts if item).strip() or None
            return clean_content, reasoning_trace, None
        return None, None, "DeepSeek 返回空内容"
    except Exception as exc:
        return None, None, str(exc)

async def chat_with_paper(paper_id: str, payload: ChatRequest, storage, summary_template: str = "tinghua.md") -> ChatResponse:
    # 1. 获取局部切块
    top_k = payload.top_k or max(8, settings.rag_default_top_k)
    contexts = retrieve_contexts(payload.question, paper_id=paper_id, top_k=top_k, storage=storage)

    # 2. 读取全局摘要
    full_summary = storage.read_result(
        paper_id,
        "summary",
        summary_template=summary_template,
    )

    # 3. 将全局摘要作为第一块 Context
    if full_summary:
        contexts.insert(0, f"【本文全局核心摘要与结构化信息】\n{full_summary}")
    elif not contexts:
        contexts = ["暂无可用上下文。"]

    # 4. 深度搜索模式 — 走 ReAct 引擎
    if payload.deep_search:
        return await _react_deep_search(payload.question, contexts)

    # 5. 普通对话模式（原有逻辑不变）
    answer, reasoning_trace, error = await asyncio.to_thread(
        call_deepseek_chat,
        payload.question,
        contexts,
        paper_id,
    )
    if not answer:
        answer = build_fallback_answer(payload.question, contexts, reason=error)
    return ChatResponse(answer=answer, contexts=contexts, reasoning_trace=reasoning_trace)


async def _react_deep_search(question: str, contexts: list[str]) -> ChatResponse:
    """ReAct 深度搜索模式：Reason→Act→Observe 循环，输出 thinking chain。"""
    from ..harness.app import get_app_harness
    from ..harness.config import get_harness_settings

    harness = get_app_harness()
    harness_settings = get_harness_settings()

    # 如果 AppHarness 未初始化，先初始化
    if not harness.is_initialized:
        await harness.startup()

    from ..harness.agents.deepseek_agent import DeepSeekAgent
    from ..harness.react.engine import ReActEngine

    # 创建 Agent 和 Engine
    agent = DeepSeekAgent(event_bus=harness.event_bus, settings=harness_settings)

    # HITL: 如果配置了 react_clarify checkpoint，启用澄清
    hitl = harness.hitl_manager
    if hitl and harness_settings.react_enable_clarification:
        # 确保 react_clarify checkpoint 已注册
        if not hitl.has_checkpoint("react_clarify"):
            from ..harness.hitl._types import HITLCheckpoint
            hitl.checkpoints["react_clarify"] = HITLCheckpoint(
                step_name="react_clarify",
                description="ReAct deep search clarification",
                allow_edit=True,
            )

    engine = ReActEngine(
        agent=agent,
        tool_harness=harness.tool_harness,
        event_bus=harness.event_bus,
        hitl_manager=hitl if harness_settings.react_enable_clarification else None,
        max_rounds=harness_settings.react_max_rounds,
    )

    state = await engine.run(
        question=question,
        paper_context=contexts,
        clarify=harness_settings.react_enable_clarification,
    )

    answer = state.final_answer or "深度搜索未生成有效结果。"
    thinking_chain = state.thinking_chain if state.thinking_chain else None

    return ChatResponse(
        answer=answer,
        contexts=contexts,
        reasoning_trace="\n\n".join(state.thinking_chain) if state.thinking_chain else None,
        thinking_chain=thinking_chain,
    )