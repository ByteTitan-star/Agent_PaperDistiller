import asyncio
import json
import logging
import re
from typing import Any

from ..config import get_settings
from ..dependencies import skill_registry
from ..schemas import ChatRequest, ChatResponse
from .token_utils import tokenize

settings = get_settings()
logger = logging.getLogger("chat")


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数（中文约 1.5 token/字，英文约 0.75 token/word）。"""
    if not text:
        return 0
    cn_chars = sum(1 for c in text if '一' <= c <= '鿿')
    other_len = len(text) - cn_chars
    return int(cn_chars * 1.5 + other_len * 0.4)


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
    # 如果有可用上下文，构建一个基于上下文的回答
    if contexts and contexts[0] != "暂无可用上下文。":
        lines = [
            f"关于「{question}」的回答：",
            "",
            "以下信息来自当前论文的检索结果，供参考：",
        ]
        for idx, context in enumerate(contexts[:3], start=1):
            lines.append(f"{idx}. {context[:300]}")
        return "\n".join(lines)
    return f"关于「{question}」：抱歉，当前无法获取足够信息来回答这个问题。请确认论文已完成解析，或尝试换个问法。"

def build_deepseek_messages(question: str, contexts: list[str], history: list[dict[str, str]] | None = None) -> list[dict[str, str]]:
    import datetime
    context_block = "\n\n".join(
        f"[Context {idx}]\n{context[:2500]}" for idx, context in enumerate(contexts, start=1)
    )
    today = datetime.date.today().strftime("%Y年%m月%d日")
    system_prompt = (
        f"你是「PaperDistiller 智能知识助手」，一个多功能 AI 助手。\n"
        f"当前日期：{today}。当用户提到「今天」、「最近」等时间相关表述时，以这个日期为准。\n"
        f"用户当前正在阅读一篇论文，下方提供了该论文的摘要与正文片段作为参考。\n\n"
        f"【核心规则】：\n"
        f"- 当你拥有可用工具（如 web_search）时，遇到任何需要实时信息、外部数据、你不确定的问题，**必须调用工具搜索**，绝不要说「我无法获取」「我没有联网能力」。\n"
        f"- 天气、新闻、最新研究、代码仓库、开源项目等实时信息 → 立即调用 web_search。\n"
        f"- 论文相关问题 → 优先参考论文上下文。\n"
        f"- 通用知识问题 → 直接用你的知识回答。\n"
        f"- 回答使用 Markdown 格式，结构清晰。\n"
        f"- 不要暴露你的底层模型名称。"
    )
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
    ]
    # 插入历史对话（上下文管理：短历史全保留，长历史压缩旧消息）
    if history:
        recent_n = 10  # 最近 N 条完整保留
        if len(history) <= recent_n * 2:
            # 历史不长，全部保留
            for msg in history:
                messages.append({"role": msg["role"], "content": msg["content"]})
        else:
            # 历史较长：旧消息压缩为摘要，最近 N 条完整保留
            old_msgs = history[: -recent_n]
            recent_msgs = history[-recent_n:]
            # 压缩旧消息：提取用户问题列表作为上下文摘要
            summary_parts = ["以下是早期对话的摘要（已压缩）："]
            for i, msg in enumerate(old_msgs):
                if msg["role"] == "user":
                    # 截断过长的问题
                    q = msg["content"][:100]
                    summary_parts.append(f"- 用户问：{q}")
            summary_text = "\n".join(summary_parts)
            messages.append({"role": "system", "content": summary_text})
            # 最近 N 条完整保留
            for msg in recent_msgs:
                messages.append({"role": msg["role"], "content": msg["content"]})
    user_prompt = (
        f"用户问题：{question}\n\n"
        f"以下是当前论文的参考信息（如有需要）：\n"
        f"{context_block}"
    )
    messages.append({"role": "user", "content": user_prompt})
    return messages


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
    user_settings: Any = None,
    user_id: int | None = None,
) -> tuple[str | None, str | None, str | None, int, int]:
    """DeepSeek Agent 调用，支持技能检索与工具循环。
    Returns: (answer, reasoning_trace, error, prompt_tokens, completion_tokens)
    """
    effective_settings = user_settings or settings
    if not effective_settings.deepseek_api_key.strip():
        return None, None, "请先在设置页面配置 DeepSeek API Key。", 0, 0

    try:
        from openai import OpenAI
    except ImportError:
        return None, None, "缺少 openai SDK，请先执行: pip install -r backend/requirements.txt", 0, 0

    allow_tools = effective_settings.agent_enable_tools
    selected_skills = (
        skill_registry.select_tools(question, top_k=effective_settings.skill_retrieval_top_k, min_similarity=effective_settings.skill_similarity_threshold)
        if allow_tools
        else []
    )
    # 仅当语义匹配到相关技能时才真正启用工具调用
    if not selected_skills:
        allow_tools = False
    logger.info("[TOOLS] query=%s skills=%s allow=%s", question[:50], [s.tool_name for s in selected_skills], allow_tools)
    tools_schema = skill_registry.build_openai_tools(selected_skills)
    skill_hint = skill_registry.build_skill_hint(selected_skills)

    try:
        client = OpenAI(
            api_key=effective_settings.deepseek_api_key,
            base_url=effective_settings.deepseek_base_url.rstrip("/"),
            timeout=effective_settings.deepseek_timeout_sec,
        )
        messages: list[dict[str, Any]] = build_deepseek_messages(question, contexts)
        if skill_hint:
            messages.insert(1, {"role": "system", "content": skill_hint})

        # 导入 storage 用于工具上下文
        from ..dependencies import storage

        # Token 用量追踪
        total_prompt = 0
        total_completion = 0

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
        max_rounds = max(1, effective_settings.agent_max_tool_rounds)
        for _ in range(max_rounds):
            request_kwargs: dict[str, Any] = {
                "model": effective_settings.deepseek_model,
                "messages": messages,
                "temperature": 0.2,
                "max_tokens": 1200,
            }
            if allow_tools and tools_schema:
                request_kwargs["tools"] = tools_schema
                request_kwargs["tool_choice"] = "auto"

            response = client.chat.completions.create(**request_kwargs)
            if response.usage:
                total_prompt += response.usage.prompt_tokens
                total_completion += response.usage.completion_tokens
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
                return clean_content, reasoning_trace, None, total_prompt, total_completion

        # 工具轮次耗尽后收敛一次
        response = client.chat.completions.create(
            model=effective_settings.deepseek_model,
            messages=messages,
            temperature=0.2,
            max_tokens=900,
        )
        if response.usage:
            total_prompt += response.usage.prompt_tokens
            total_completion += response.usage.completion_tokens
        final_content = (response.choices[0].message.content or "").strip()
        clean_content, think_reasoning = split_answer_and_reasoning(final_content)
        if think_reasoning:
            reasoning_parts.append(think_reasoning)
        if clean_content:
            reasoning_trace = "\n\n".join(item for item in reasoning_parts if item).strip() or None
            return clean_content, reasoning_trace, None, total_prompt, total_completion
        return None, None, "DeepSeek 返回空内容", total_prompt, total_completion
    except Exception as exc:
        return None, None, str(exc), 0, 0


def _sse_event(data: dict[str, Any]) -> str:
    """格式化 SSE 事件。"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def call_deepseek_chat_stream(
    question: str,
    contexts: list[str],
    paper_id: str,
    user_settings: Any = None,
    user_id: int | None = None,
    token_usage: dict[str, int] | None = None,
    history: list[dict[str, str]] | None = None,
):
    """流式版 DeepSeek 调用，逐 token yield SSE 事件。
    token_usage: 可选的可变 dict，执行完毕后写入 {"prompt": N, "completion": N}。
    history: 可选的历史对话列表 [{"role": "user/assistant", "content": "..."}]
    """
    effective_settings = user_settings or settings
    if not effective_settings.deepseek_api_key.strip():
        yield _sse_event({"type": "error", "text": "请先在设置页面配置 DeepSeek API Key。"})
        return

    try:
        from openai import OpenAI
    except ImportError:
        yield _sse_event({"type": "error", "text": "缺少 openai SDK"})
        return

    # 工具准备
    allow_tools = effective_settings.agent_enable_tools
    selected_skills = (
        skill_registry.select_tools(question, top_k=effective_settings.skill_retrieval_top_k, min_similarity=effective_settings.skill_similarity_threshold)
        if allow_tools
        else []
    )
    if not selected_skills:
        allow_tools = False
    logger.info("[TOOLS_STREAM] user_id=%s query=%s skills=%s allow=%s", user_id, question[:50], [s.tool_name for s in selected_skills], allow_tools)
    tools_schema = skill_registry.build_openai_tools(selected_skills)
    skill_hint = skill_registry.build_skill_hint(selected_skills)

    try:
        client = OpenAI(
            api_key=effective_settings.deepseek_api_key,
            base_url=effective_settings.deepseek_base_url.rstrip("/"),
            timeout=effective_settings.deepseek_timeout_sec,
        )
        messages = build_deepseek_messages(question, contexts, history)
        if skill_hint:
            messages.insert(1, {"role": "system", "content": skill_hint})

        from ..dependencies import storage as _storage

        paper_chunks = _storage.load_chunks(paper_id)
        tool_context = {
            "paper_id": paper_id, "question": question, "chunks": paper_chunks,
            "vector_search": lambda q, k: _storage.search_similar_chunks(
                paper_id=paper_id, question=str(q), top_k=max(1, int(k)),
            ),
        }

        full_text = ""
        max_rounds = max(1, effective_settings.agent_max_tool_rounds)
        total_prompt = 0
        total_completion = 0

        for _round in range(max_rounds):
            request_kwargs: dict[str, Any] = {
                "model": effective_settings.deepseek_model,
                "messages": messages,
                "temperature": 0.2,
                "max_tokens": 1200,
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if allow_tools and tools_schema:
                request_kwargs["tools"] = tools_schema
                request_kwargs["tool_choice"] = "auto"

            stream = client.chat.completions.create(**request_kwargs)
            round_content = ""
            tool_calls_acc: dict[int, dict] = {}

            for chunk in stream:
                # 提取 usage（最后一个 chunk 携带）
                if chunk.usage:
                    total_prompt += chunk.usage.prompt_tokens
                    total_completion += chunk.usage.completion_tokens
                delta = chunk.choices[0].delta if chunk.choices else None
                if not delta:
                    continue
                if delta.content:
                    token = delta.content
                    round_content += token
                    full_text += token
                    yield _sse_event({"type": "token", "text": token})
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {"id": tc.id or "", "name": "", "arguments": ""}
                        if tc.function:
                            if tc.function.name:
                                tool_calls_acc[idx]["name"] = tc.function.name
                            if tc.function.arguments:
                                tool_calls_acc[idx]["arguments"] += tc.function.arguments

            # 工具调用 → 执行后继续
            if tool_calls_acc and allow_tools and tools_schema:
                tool_calls_list = list(tool_calls_acc.values())
                messages.append({"role": "assistant", "content": round_content, "tool_calls": [
                    {"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                    for tc in tool_calls_list
                ]})
                for tc in tool_calls_list:
                    try:
                        args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                    except json.JSONDecodeError:
                        args = {}
                    result = skill_registry.execute(tc["name"], args if isinstance(args, dict) else {}, context=tool_context)
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(result, ensure_ascii=False)})
                    yield _sse_event({"type": "tool", "name": tc["name"], "query": args.get("query", "")})
                continue

            break  # 无工具调用，结束

        clean, _ = split_answer_and_reasoning(full_text)
        # DeepSeek 流式可能不返回 usage，用估算兜底
        if total_prompt + total_completion == 0:
            total_completion = _estimate_tokens(full_text)
            total_prompt = _estimate_tokens(" ".join(m.get("content", "") for m in messages if isinstance(m, dict)))
        if token_usage is not None:
            token_usage["prompt"] = total_prompt
            token_usage["completion"] = total_completion
        yield _sse_event({"type": "done", "answer": clean or full_text})

    except Exception as exc:
        yield _sse_event({"type": "error", "text": str(exc)})


async def chat_with_paper_stream(paper_id, payload, storage, summary_template="tinghua.md", settings=None, user_id=None, history=None):
    """流式版 chat_with_paper，支持普通模式和深度研究模式。"""
    effective_settings = settings or get_settings()
    logger.info("[CHAT_STREAM] user_id=%s paper_id=%s deep_search=%s question=%s", user_id, paper_id, payload.deep_search, payload.question[:80])
    top_k = payload.top_k or max(8, effective_settings.rag_default_top_k)
    contexts = retrieve_contexts(payload.question, paper_id=paper_id, top_k=top_k, storage=storage)
    full_summary = storage.read_result(paper_id, "summary", summary_template=summary_template)
    if full_summary:
        contexts.insert(0, f"[全局摘要]\n{full_summary}")
    elif not contexts:
        contexts = ["暂无可用上下文。"]

    if payload.deep_search:
        async for event in deep_search_stream(payload.question, contexts, effective_settings, user_id, history):
            yield event
    else:
        token_usage: dict[str, int] = {}
        for event in call_deepseek_chat_stream(payload.question, contexts, paper_id, effective_settings, user_id, token_usage, history):
            yield event
        # 记录 token 用量
        prompt_t = token_usage.get("prompt", 0)
        compl_t = token_usage.get("completion", 0)
        if prompt_t + compl_t > 0:
            from .token_logger import log_token_to_db
            await log_token_to_db(
                user_id=user_id,
                model_name=getattr(effective_settings, "deepseek_model", "deepseek-chat"),
                prompt_tokens=prompt_t,
                completion_tokens=compl_t,
                action_type="chat",
                detail={"paper_id": paper_id} if paper_id else None,
            )
        await asyncio.sleep(0)


async def deep_search_stream(question: str, contexts: list[str], settings: Any = None, user_id: int | None = None, history: list[dict[str, str]] | None = None):
    """流式深度研究：带阶段推进、来源卡片、结构化输出。"""
    import logging
    logger = logging.getLogger(__name__)

    settings = settings or get_settings()
    collected_sources = []

    # 将历史对话注入上下文，让深度搜索也能感知之前的对话
    agent_contexts = list(contexts)
    if history:
        hist_lines = ["【之前的对话历史】"]
        for msg in history[-10:]:
            role = "用户" if msg["role"] == "user" else "助手"
            hist_lines.append(f"{role}：{msg['content'][:200]}")
        agent_contexts.insert(0, "\n".join(hist_lines))

    # Phase 1: 规划
    yield _sse_event({"type": "phase", "phase": "planning", "label": "正在分析问题，制定研究计划..."})

    # Phase 2: 澄清（短问题跳过）
    clarification = None
    if settings.react_enable_clarification and len(question) > 15:
        clarification = await _try_clarify(question, contexts, settings)
        if clarification:
            yield _sse_event({"type": "phase", "phase": "clarifying", "label": "需要确认研究方向", "detail": clarification})

    # Phase 3: 检索
    yield _sse_event({"type": "phase", "phase": "searching", "label": "正在检索相关资源..."})

    from ..harness.react.langgraph_agent import run_react_search
    try:
        answer, thinking_chain, prompt_tokens, completion_tokens, sources = await run_react_search(
            question=question,
            paper_context=agent_contexts,
            settings=settings,
            clarify_hint=clarification,
        )
        # 记录 token 用量（async 上下文直接 await）
        if prompt_tokens + completion_tokens > 0:
            from .token_logger import log_token_to_db
            await log_token_to_db(
                user_id=user_id,
                model_name=getattr(settings, "deepseek_model", "deepseek-chat"),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                action_type="deep_search",
            )
        # 动态推送推理过程（搜索工具调用 + 来源卡片）
        for step in thinking_chain:
            if "搜索" in step and "：" in step:
                yield _sse_event({"type": "tool", "name": "web_search", "query": step.split("：")[-1]})
            elif "找到" in step or "相关结果" in step:
                yield _sse_event({"type": "source", "snippet": step})
            await asyncio.sleep(0.05)
        # 推送来源链接卡片
        for s in sources:
            yield _sse_event({"type": "source", "title": s["title"], "url": s["url"]})
            await asyncio.sleep(0.05)
    except Exception as exc:
        logger.error("Deep search failed: %s", exc, exc_info=True)
        answer = f"深度搜索遇到问题：{exc}\n\n以下是基于论文内容的基础回答：\n\n{build_fallback_answer(question, contexts)}"
        thinking_chain = [f"深度搜索遇到问题：{exc}"]

    # Phase 4: 生成报告
    yield _sse_event({"type": "phase", "phase": "generating", "label": "正在生成研究报告..."})

    # 流式输出最终答案
    for i in range(0, len(answer), 3):
        chunk = answer[i:i+3]
        yield _sse_event({"type": "token", "text": chunk})
        await asyncio.sleep(0.01)

    yield _sse_event({
        "type": "done",
        "answer": answer,
        "thinking_chain": thinking_chain,
        "sources": collected_sources,
    })


async def chat_with_paper(paper_id: str, payload: ChatRequest, storage, summary_template: str = "tinghua.md", settings: Any = None, user_id: int | None = None, history: list[dict[str, str]] | None = None) -> ChatResponse:
    effective_settings = settings or get_settings()
    logger.info("[CHAT] user_id=%s paper_id=%s deep_search=%s question=%s", user_id, paper_id, payload.deep_search, payload.question[:80])
    # 1. 获取局部切块
    top_k = payload.top_k or max(8, effective_settings.rag_default_top_k)
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
        return await _react_deep_search(payload.question, contexts, effective_settings)

    # 5. 普通对话模式（原有逻辑不变）
    answer, reasoning_trace, error, prompt_tokens, completion_tokens = await asyncio.to_thread(
        call_deepseek_chat,
        payload.question,
        contexts,
        paper_id,
        effective_settings,
        user_id,
    )
    # 记录 token 用量（在 async 上下文中直接 await，无事件循环问题）
    if prompt_tokens + completion_tokens > 0:
        from .token_logger import log_token_to_db
        await log_token_to_db(
            user_id=user_id,
            model_name=getattr(effective_settings, "deepseek_model", "deepseek-chat"),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            action_type="chat",
            detail={"paper_id": paper_id} if paper_id else None,
        )
    if not answer:
        answer = build_fallback_answer(payload.question, contexts, reason=error)
    return ChatResponse(answer=answer, contexts=contexts, reasoning_trace=reasoning_trace)


async def _react_deep_search(question: str, contexts: list[str], effective_settings: Any = None) -> ChatResponse:
    """ReAct 深度搜索模式：基于 LangGraph create_react_agent 实现 Reason→Act→Observe 循环。"""
    settings = effective_settings or get_settings()
    thinking_chain: list[str] = []

    # ---- Phase 1: CLARIFY（短问题或明确问题跳过，节省延迟） ----
    clarification = None
    needs_clarify = settings.react_enable_clarification and len(question) > 15
    if needs_clarify:
        clarification = await _try_clarify(question, contexts, settings)
        if clarification:
            thinking_chain.append(f"💡 澄清提示：{clarification}")

    # ---- Phase 2: LangGraph ReAct Agent ----
    from ..harness.react.langgraph_agent import run_react_search

    try:
        answer, agent_thinking, prompt_tokens, completion_tokens, sources = await run_react_search(
            question=question,
            paper_context=contexts,
            settings=settings,
            clarify_hint=clarification,
        )
        thinking_chain.extend(agent_thinking)
        # 记录 token 用量（async 上下文直接 await）
        if prompt_tokens + completion_tokens > 0:
            from .token_logger import log_token_to_db
            await log_token_to_db(
                user_id=None,
                model_name=getattr(settings, "deepseek_model", "deepseek-chat"),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                action_type="deep_search",
            )
    except Exception as exc:
        answer = build_fallback_answer(question, contexts, reason=str(exc))
        thinking_chain.append(f"⚠️ 深度搜索遇到问题，已回退为基础回答")

    return ChatResponse(
        answer=answer,
        contexts=contexts,
        reasoning_trace="\n\n".join(thinking_chain) if thinking_chain else None,
        thinking_chain=thinking_chain or None,
    )


async def _try_clarify(question: str, contexts: list[str], settings: Any) -> str | None:
    """纯 LLM 判断是否需要澄清，不阻塞。返回澄清提示文本或 None。"""
    from ..harness.react.prompts import CLARIFY_SYSTEM, CLARIFY_USER

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )
        context_summary = "\n".join(contexts[:3])[:2000]
        resp = await asyncio.to_thread(
            client.chat.completions.create,
            model=settings.deepseek_model,
            messages=[
                {"role": "system", "content": CLARIFY_SYSTEM},
                {"role": "user", "content": CLARIFY_USER.format(
                    context=context_summary, question=question,
                )},
            ],
            max_tokens=300,
            temperature=0.1,
        )
        answer = (resp.choices[0].message.content or "").strip()
        if answer.upper() == "NO" or len(answer) < 10:
            return None
        return answer
    except Exception:
        return None