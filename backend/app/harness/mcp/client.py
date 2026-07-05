"""MCP client — 把外部 MCP server 加载为 LangChain 工具，供 ReAct agent 调用（对内）。

依赖 langchain-mcp-adapters + mcp。默认关闭（settings.mcp_inbound_enabled），
且 mcp 未安装时优雅降级为空工具列表，不影响 ReAct 正常运行。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager, suppress
from typing import Any

logger = logging.getLogger(__name__)


@asynccontextmanager
async def inbound_mcp_session(servers_csv: str):
    """打开外部 MCP server 连接，yield 可用 LangChain 工具列表，退出时自动关闭连接。

    用法：
        async with inbound_mcp_session(settings.mcp_inbound_servers) as mcp_tools:
            agent = create_react_agent(model, tools=[web_search, *mcp_tools], ...)
            ...

    - servers_csv 为空 / mcp 未安装 / 连接失败：yield []（不阻断主流程）。
    - 工具在上下文内有效；退出后连接关闭，工具不可再用。
    """
    urls = [u.strip() for u in (servers_csv or "").split(",") if u.strip()]
    if not urls:
        yield []
        return

    try:
        from langchain_mcp_adapters.tools import load_mcp_tools
        from mcp import ClientSession
        from mcp.client.sse import sse_client
    except Exception as exc:
        logger.warning("MCP inbound disabled: missing mcp/langchain-mcp-adapters (%s)", exc)
        yield []
        return

    opened: list[tuple[Any, Any]] = []  # (session, sse_ctx)
    tools: list[Any] = []
    try:
        for url in urls:
            try:
                sse_ctx = sse_client(url)
                read, write = await sse_ctx.__aenter__()
                session = ClientSession(read, write)
                await session.__aenter__()
                await session.initialize()
                opened.append((session, sse_ctx))
                tools.extend(await load_mcp_tools(session))
            except Exception as exc:
                logger.warning("MCP inbound: failed to load tools from %s: %s", url, exc)
        logger.info("MCP inbound loaded %d tools from %d servers", len(tools), len(urls))
        yield tools
    finally:
        for session, sse_ctx in opened:
            with suppress(Exception):
                await session.__aexit__(None, None, None)
            with suppress(Exception):
                await sse_ctx.__aexit__(None, None, None)
