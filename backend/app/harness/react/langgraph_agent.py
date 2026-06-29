"""LangGraph ReAct Agent — 使用 create_react_agent 实现深度搜索。

基于 LangGraph 预构建的 ReAct agent，通过 LangChain 原生 tool calling 实现
Reason→Act→Observe（推理→行动→观察）循环，替代原有的手写循环+正则解析方案。

核心功能：
- 构建 web_search 工具，连接 SkillRegistry 的搜索技能
- 使用 LangGraph 的 create_react_agent 创建 ReAct agent
- 从 agent 执行结果中提取：答案、思考链、Token 用量、来源链接
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from .prompts import REACT_SYSTEM_PROMPT


def _build_web_search_tool(tool_executor: Any):
    """将工具执行面（HarnessToolRegistry / SkillRegistry）的 web_search 技能封装为 LangChain @tool。

    LangGraph 的 create_react_agent 需要 LangChain Tool 格式的工具，
    此函数将统一的工具执行面 execute("web_search", ...) 包装为 LangChain @tool。

    Args:
        tool_executor: 工具执行面（HarnessToolRegistry 优先，回退 SkillRegistry），提供 execute()。

    Returns:
        function: LangChain @tool 装饰过的 web_search 函数。
    """

    @tool
    def web_search(query: str, max_results: int = 3) -> str:
        """搜索互联网获取最新信息。用于查找论文相关的最新研究进展、开源代码、数据集、技术博客等。
        当问题涉及最新动态、实时信息、或论文上下文无法覆盖的内容时使用。

        Args:
            query: 搜索关键词。
            max_results: 最大返回结果数，默认 3。

        Returns:
            str: 格式化的搜索结果（Markdown 格式）。
        """
        result = tool_executor.execute(
            "web_search",
            {"query": query, "max_results": max_results},
        )
        if "error" in result:
            return f"搜索失败: {result['error']}"

        results = result.get("results", [])
        if not results:
            return "搜索完成但未找到相关结果。"

        # 格式化为 Markdown 列表
        parts = []
        for i, item in enumerate(results[:max_results], 1):
            title = item.get("title", "无标题")
            url = item.get("url", "")
            content = item.get("content", "")[:300]  # 截断过长内容
            parts.append(f"{i}. [{title}]({url})\n{content}")
        return "\n\n".join(parts)

    return web_search


def _extract_answer(messages: list) -> str:
    """从 agent 返回的 messages 中提取最终答案。

    策略：从后往前找最后一条没有 tool_calls 的 AIMessage，
    即 agent 不再调用工具、直接给出最终答案的那条消息。

    Args:
        messages: LangGraph agent 返回的消息列表。

    Returns:
        str: 最终答案文本。
    """
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and not msg.tool_calls and msg.content:
            return str(msg.content)
    # 兜底：最后一条消息的 content
    if messages and hasattr(messages[-1], "content"):
        return str(messages[-1].content)
    return "无法生成答案。"


def _extract_thinking_chain(messages: list) -> list[str]:
    """从 agent 返回的 messages 中提取思考链（前端展示用）。

    遍历所有消息，提取 AIMessage 的 tool_calls（搜索动作）
    和 ToolMessage 的内容（搜索结果），格式化为带 emoji 的可读文本。

    Args:
        messages: LangGraph agent 返回的消息列表。

    Returns:
        list[str]: 思考链步骤列表，如 ["🔍 第1轮搜索：xxx", "✅ 找到 3 条相关结果"]。
    """
    chain: list[str] = []
    round_num = 0

    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            round_num += 1
            for tc in msg.tool_calls:
                tool_name = tc.get("name", "unknown")
                args = tc.get("args", {})
                query = args.get("query", str(args))
                if tool_name == "web_search":
                    chain.append(f"🔍 第{round_num}轮搜索：{query}")
                else:
                    chain.append(f"⚙️ 第{round_num}轮调用 {tool_name}：{query}")

        elif isinstance(msg, ToolMessage):
            content = str(msg.content)
            if content.startswith("搜索失败"):
                chain.append(f"❌ 搜索未成功：{content[:100]}")
            elif len(content) > 50:
                # 提取前几个结果的标题作为摘要
                lines = content.split("\n")
                titles = [l.strip() for l in lines if l.strip().startswith("1.") or l.strip().startswith("2.") or l.strip().startswith("3.")]
                if titles:
                    chain.append(f"✅ 找到 {len(titles)} 条相关结果")
                else:
                    chain.append(f"✅ 搜索完成，获取到相关信息")
            else:
                chain.append(f"✅ 搜索完成")

    return chain


def _extract_token_usage(messages: list) -> tuple[int, int]:
    """从 agent 返回的 messages 中提取 Token 用量。

    LangChain ChatOpenAI 的 AIMessage 会携带 usage_metadata 字段。
    遍历所有 AIMessage 累加 input/output token。

    Args:
        messages: LangGraph agent 返回的消息列表。

    Returns:
        tuple[int, int]: (总输入 token 数, 总输出 token 数)。
    """
    total_input = 0
    total_output = 0
    for msg in messages:
        if isinstance(msg, AIMessage):
            meta = getattr(msg, "usage_metadata", None)
            if meta:
                input_tokens = meta.get("input_tokens", 0) if isinstance(meta, dict) else getattr(meta, "input_tokens", 0)
                output_tokens = meta.get("output_tokens", 0) if isinstance(meta, dict) else getattr(meta, "output_tokens", 0)
                total_input += input_tokens
                total_output += output_tokens
    return total_input, total_output


def _extract_sources(messages: list) -> list[dict[str, str]]:
    """从 ToolMessage 中提取搜索结果的标题和 URL。

    解析 Markdown 链接格式 [title](url)，去重后返回。

    Args:
        messages: LangGraph agent 返回的消息列表。

    Returns:
        list[dict]: 来源列表，每项包含 "title" 和 "url" 字段。
    """
    import re
    sources: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        content = str(msg.content)
        # 匹配 markdown 链接格式：[title](url)
        for match in re.finditer(r'\[([^\]]+)\]\((https?://[^)]+)\)', content):
            title, url = match.group(1), match.group(2)
            if url not in seen_urls:
                seen_urls.add(url)
                sources.append({"title": title, "url": url})
    return sources


async def run_react_search(
    question: str,                   # 用户问题
    paper_context: list[str],        # 论文上下文片段列表
    settings: Any,                   # 框架配置（含 DeepSeek API 配置）
    clarify_hint: str | None = None, # 可选的澄清提示
) -> tuple[str, list[str], int, int, list[dict[str, str]]]:
    """执行 LangGraph ReAct agent 深度搜索。

    完整流程：
    1. 创建 ChatOpenAI 模型（指向 DeepSeek API）
    2. 将 SkillRegistry 的 web_search 封装为 LangChain Tool
    3. 构建包含论文上下文的 system prompt
    4. 使用 create_react_agent 创建 ReAct agent
    5. 执行 agent（Reason→Act→Observe 循环）
    6. 从结果中提取答案、思考链、Token 用量、来源

    Args:
        question: 用户提出的问题。
        paper_context: 论文相关上下文片段列表。
        settings: 框架配置对象（需要 deepseek_model/api_key/base_url/timeout_sec）。
        clarify_hint: 可选的澄清提示文本，附加到用户消息中。

    Returns:
        tuple: (answer, thinking_chain, prompt_tokens, completion_tokens, sources)
            - answer (str): 最终答案文本。
            - thinking_chain (list[str]): 思考链步骤列表。
            - prompt_tokens (int): 输入 token 总数。
            - completion_tokens (int): 输出 token 总数。
            - sources (list[dict]): 引用来源列表 [{"title": ..., "url": ...}]。
    """
    import logging
    logger = logging.getLogger(__name__)
    from ...services.deep_search_logger import (
        log_agent_message,
        log_final_result,
        log_request,
        log_error,
    )

    from ...dependencies import get_tool_executor

    # 记录请求开始
    log_request(question, paper_context, clarify_hint)

    # 1. 创建 ChatOpenAI 模型（指向 DeepSeek API）
    logger.info("DeepSearch: Creating LLM model=%s base_url=%s", settings.deepseek_model, settings.deepseek_base_url)

    # 2. 创建 LangChain 工具（封装统一工具执行面的 web_search）
    logger.info("DeepSearch: Loading tool executor")
    tool_executor = get_tool_executor()
    web_search_tool = _build_web_search_tool(tool_executor)

    # 3. 构建 system prompt（含论文上下文）
    context_text = "\n\n".join(paper_context[:5])[:3000]  # 最多 5 段，截断到 3000 字符
    system_prompt = REACT_SYSTEM_PROMPT.format(context_summary=context_text)
    logger.info("DeepSearch: System prompt length=%d", len(system_prompt))

    # 4. 构建用户消息（含澄清提示）
    user_msg = question
    if clarify_hint:
        user_msg = f"{question}\n\n补充提示：{clarify_hint}"

    # 5. 创建并执行 ReAct agent。
    # 若开启 mcp_inbound_enabled，在外部 MCP server 会话内加载额外工具（连接随用随关）。
    inbound_servers = (
        getattr(settings, "mcp_inbound_servers", "")
        if getattr(settings, "mcp_inbound_enabled", False)
        else ""
    )
    logger.info("DeepSearch: Creating ReAct agent (inbound MCP tools: %s)", bool(inbound_servers))

    from ..mcp.client import inbound_mcp_session
    async with inbound_mcp_session(inbound_servers) as mcp_tools:
        agent = create_react_agent(
            model=ChatOpenAI(
                model=settings.deepseek_model,
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
                timeout=settings.deepseek_timeout_sec,
            ),
            tools=[web_search_tool, *mcp_tools],
            prompt=system_prompt,
        )

        logger.info("DeepSearch: Invoking agent with question: %s", question[:100])
        try:
            result = await agent.ainvoke({"messages": [("user", user_msg)]})
        except Exception as exc:
            log_error("agent_invoke", exc)
            logger.error("DeepSearch: Agent invocation failed: %s", exc, exc_info=True)
            raise

    # 7. 提取结果
    messages = result["messages"]

    # 详细记录每一条 message
    for i, msg in enumerate(messages):
        log_agent_message(msg, i)

    answer = _extract_answer(messages)
    thinking_chain = _extract_thinking_chain(messages)
    prompt_tokens, completion_tokens = _extract_token_usage(messages)
    sources = _extract_sources(messages)

    # 记录最终结果
    log_final_result(answer, thinking_chain, prompt_tokens, completion_tokens, sources)

    logger.info("DeepSearch: Got answer length=%d, thinking steps=%d, sources=%d", len(answer), len(thinking_chain), len(sources))

    return answer, thinking_chain, prompt_tokens, completion_tokens, sources
