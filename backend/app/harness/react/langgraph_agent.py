"""LangGraph ReAct Agent — 使用 create_react_agent 替代手写 ReActEngine。

基于 LangGraph 预构建的 ReAct agent，通过 LangChain 原生 tool calling 实现
Reason→Act→Observe 循环，替代原有的手写循环+正则解析方案。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from .prompts import REACT_SYSTEM_PROMPT


def _build_web_search_tool(skill_registry: Any):
    """将 SkillRegistry 的 web_search 技能封装为 LangChain @tool。"""

    @tool
    def web_search(query: str, max_results: int = 3) -> str:
        """搜索互联网获取最新信息。用于查找论文相关的最新研究进展、开源代码、数据集、技术博客等。
        当问题涉及最新动态、实时信息、或论文上下文无法覆盖的内容时使用。"""
        result = skill_registry.execute(
            "web_search",
            {"query": query, "max_results": max_results},
        )
        if "error" in result:
            return f"搜索失败: {result['error']}"

        results = result.get("results", [])
        if not results:
            return "搜索完成但未找到相关结果。"

        parts = []
        for i, item in enumerate(results[:max_results], 1):
            title = item.get("title", "无标题")
            url = item.get("url", "")
            content = item.get("content", "")[:300]
            parts.append(f"{i}. [{title}]({url})\n{content}")
        return "\n\n".join(parts)

    return web_search


def _extract_answer(messages: list) -> str:
    """从 agent 返回的 messages 中提取最终答案。"""
    # 从后往前找最后一条无 tool_calls 的 AIMessage
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and not msg.tool_calls and msg.content:
            return str(msg.content)
    # 兜底：最后一条消息的 content
    if messages and hasattr(messages[-1], "content"):
        return str(messages[-1].content)
    return "无法生成答案。"


def _extract_thinking_chain(messages: list) -> list[str]:
    """从 agent 返回的 messages 中提取思考链，格式与前端展示兼容。"""
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
    """从 agent 返回的 messages 中提取 token 用量。

    LangChain ChatOpenAI 的 AIMessage 会携带 usage_metadata 字段。
    遍历所有 AIMessage 累加 input/output token。
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
    """从 ToolMessage 中提取搜索结果的标题和 URL。"""
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
    question: str,
    paper_context: list[str],
    settings: Any,
    clarify_hint: str | None = None,
) -> tuple[str, list[str], int, int, list[dict[str, str]]]:
    """执行 LangGraph ReAct agent 深度搜索。

    Returns:
        (answer, thinking_chain, prompt_tokens, completion_tokens, sources)
    """
    import logging
    logger = logging.getLogger(__name__)

    from ...dependencies import get_skill_registry

    # 1. 创建 ChatOpenAI 模型（指向 DeepSeek API）
    logger.info("DeepSearch: Creating LLM model=%s base_url=%s", settings.deepseek_model, settings.deepseek_base_url)
    llm = ChatOpenAI(
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        timeout=settings.deepseek_timeout_sec,
    )

    # 2. 创建 LangChain 工具
    logger.info("DeepSearch: Loading skill registry")
    skill_registry = get_skill_registry()
    web_search_tool = _build_web_search_tool(skill_registry)

    # 3. 构建 system prompt（含论文上下文）
    context_text = "\n\n".join(paper_context[:5])[:3000]
    system_prompt = REACT_SYSTEM_PROMPT.format(context_summary=context_text)
    logger.info("DeepSearch: System prompt length=%d", len(system_prompt))

    # 4. 创建 ReAct agent
    logger.info("DeepSearch: Creating ReAct agent")
    agent = create_react_agent(
        model=llm,
        tools=[web_search_tool],
        prompt=system_prompt,
    )

    # 5. 构建用户消息（含澄清提示）
    user_msg = question
    if clarify_hint:
        user_msg = f"{question}\n\n补充提示：{clarify_hint}"

    # 6. 执行 agent
    logger.info("DeepSearch: Invoking agent with question: %s", question[:100])
    try:
        result = await agent.ainvoke({"messages": [("user", user_msg)]})
    except Exception as exc:
        logger.error("DeepSearch: Agent invocation failed: %s", exc, exc_info=True)
        raise

    # 7. 提取结果
    messages = result["messages"]
    answer = _extract_answer(messages)
    thinking_chain = _extract_thinking_chain(messages)
    prompt_tokens, completion_tokens = _extract_token_usage(messages)
    sources = _extract_sources(messages)
    logger.info("DeepSearch: Got answer length=%d, thinking steps=%d, sources=%d", len(answer), len(thinking_chain), len(sources))

    return answer, thinking_chain, prompt_tokens, completion_tokens, sources
